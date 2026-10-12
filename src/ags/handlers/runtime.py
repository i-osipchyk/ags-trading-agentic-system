from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ags.config import Config
from ags.portfolio.stage import PortfolioConfig
from ags.storage import S3Storage, Storage

LATE_START_LIMIT = timedelta(hours=1)


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
    portfolio_config: PortfolioConfig | None = None
    breaker_reset_at: Callable[[], datetime | None] = lambda: None
    alert: Callable[[str], None] = lambda message: None


def parse_scheduled_time(value: str) -> datetime:
    # Scheduler emits a trailing 'Z'; fromisoformat accepts it from Python 3.11.
    return datetime.fromisoformat(value)


def cycle_id_for(scheduled_time: str) -> str:
    return "cycle_" + parse_scheduled_time(scheduled_time).astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def check_late_start(event: dict, now: datetime) -> None:
    scheduled = datetime.fromisoformat(event["scheduled_time"])
    if now - scheduled > LATE_START_LIMIT:
        raise LateStartError(f"started {now - scheduled} after scheduled_time {event['scheduled_time']}")


def build_runtime(env, *, secrets_client, ssm_client, chat_factory, market_factory, alert_factory=None) -> Runtime:
    """Wire a Lambda's dependencies. The real clients are passed in as factories so tests never connect out."""
    config = Config.from_secret(secrets_client, env["AGS_SECRET_ID"])
    param = env.get("AGS_BREAKER_PARAM", "/ags/breaker_reset_at")

    def breaker_reset_at() -> datetime | None:
        try:
            value = ssm_client.get_parameter(Name=param)["Parameter"]["Value"]
        except ssm_client.exceptions.ParameterNotFound:
            return None
        return datetime.fromisoformat(value) if value and value != "none" else None

    return Runtime(
        config=config, storage=S3Storage(env["AGS_BUCKET"], region_name=env.get("AWS_REGION")),
        chat_client=chat_factory(config), model=config.deepseek_model, market_client=market_factory(config),
        now=lambda: datetime.now(timezone.utc), portfolio_config=PortfolioConfig.from_mapping(env),
        breaker_reset_at=breaker_reset_at, alert=alert_factory(config) if alert_factory else (lambda message: None),
    )
