from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ags.config import Config
from ags.notify.balances import provider_balances
from ags.portfolio.stage import PortfolioConfig
from ags.storage import S3Storage, Storage
from ags.tools.sources.cot import SocrataCotClient
from ags.tools.sources.grinds import CocoaGrindsClient

LATE_START_LIMIT = timedelta(hours=1)
# A slot that was attempted but never reached the book (a held cycle) may be retried this late.
# Capped well inside the 12 h to the next cycle, so a retry can never overlap it.
DEFAULT_RETRY_WINDOW = timedelta(hours=3)
MAX_RETRY_WINDOW = timedelta(hours=6)
DEFAULT_MIN_CYCLES_OF_CREDIT = 4


class LateStartError(Exception):
    """A cycle that starts >1 h after its scheduled time would break comparability, so it aborts."""


@dataclass
class Runtime:
    config: Config | None
    storage: Storage
    chat_client: object
    model: str
    market_client: object
    now: Callable[[], datetime]
    news_client: object = None
    weather_client: object = None
    usda_client: object = None
    cot_client: object = None
    grinds_client: object = None
    portfolio_config: PortfolioConfig | None = None
    breaker_reset_at: Callable[[], datetime | None] = lambda: None
    alert: Callable[[str], None] = lambda message: None
    retry_window: timedelta = DEFAULT_RETRY_WINDOW
    # Provider balances for the low-credit check ({} when unknown, as in tests).
    balances: Callable[[], dict] = lambda: {}
    min_cycles_of_credit: int = DEFAULT_MIN_CYCLES_OF_CREDIT
    trading_gateway: Callable[[], object] | None = None  # connects to the demo account on first call


def retry_window_from(env) -> timedelta:
    hours = float(env.get("RETRY_WINDOW_HOURS") or DEFAULT_RETRY_WINDOW.total_seconds() / 3600)
    return min(timedelta(hours=hours), MAX_RETRY_WINDOW)


def parse_scheduled_time(value: str) -> datetime:
    # Scheduler emits a trailing 'Z'; fromisoformat accepts it from Python 3.11.
    return datetime.fromisoformat(value)


def cycle_id_for(scheduled_time: str) -> str:
    return "cycle_" + parse_scheduled_time(scheduled_time).astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def check_late_start(event: dict, now: datetime, *, retry_window: timedelta | None = None) -> None:
    """Abort a start more than 1 h late, or more than `retry_window` late for a retry.

    The caller passes `retry_window` only when this slot was already attempted and isn't in the
    book, i.e. it's retrying a held cycle (ARCHITECTURE.md § Retrying a held cycle).
    """
    scheduled = datetime.fromisoformat(event["scheduled_time"])
    limit = LATE_START_LIMIT if retry_window is None else max(retry_window, LATE_START_LIMIT)
    if now - scheduled > limit:
        kind = "retry" if retry_window is not None else "start"
        raise LateStartError(
            f"{kind} {now - scheduled} after scheduled_time {event['scheduled_time']} (limit {limit})"
        )


def lazy(make: Callable[[], object]) -> Callable[[], object]:
    """Call `make` once, on first use, and hand the same object back after that."""
    box: list = []

    def get():
        if not box:
            box.append(make())
        return box[0]

    get.made = lambda: box[0] if box else None  # type: ignore[attr-defined]
    return get


def build_runtime(
    env, *, config: Config, ssm_client, chat_factory, market_factory, alert_factory=None, trading_factory=None
) -> Runtime:
    """Wire a Lambda's dependencies. The real clients are passed in as factories so tests never connect out."""
    param = env["AGS_BREAKER_PARAM"]

    def breaker_reset_at() -> datetime | None:
        try:
            value = ssm_client.get_parameter(Name=param)["Parameter"]["Value"]
        except ssm_client.exceptions.ParameterNotFound:
            return None
        return datetime.fromisoformat(value) if value and value != "none" else None

    return Runtime(
        config=config, storage=S3Storage(env["AGS_BUCKET"]),
        chat_client=chat_factory(config), model=config.deepseek_model, market_client=market_factory(config),
        cot_client=SocrataCotClient(), grinds_client=CocoaGrindsClient(),
        now=lambda: datetime.now(timezone.utc), portfolio_config=PortfolioConfig.from_mapping(env),
        breaker_reset_at=breaker_reset_at, alert=alert_factory(config) if alert_factory else (lambda message: None),
        retry_window=retry_window_from(env), balances=lambda: provider_balances(config),
        min_cycles_of_credit=int(env.get("MIN_CYCLES_OF_CREDIT") or DEFAULT_MIN_CYCLES_OF_CREDIT),
        trading_gateway=lazy(lambda: trading_factory(config)) if trading_factory else None,
    )
