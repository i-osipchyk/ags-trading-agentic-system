"""CLI: one full local cycle — the seven commodity pipelines, then the portfolio stage.

Writes into the unofficial `local/` namespace only (official runs come from the
scheduled AWS cycle). `--off-schedule` logs the portfolio stage but writes neither book.
A breaker reset is read from `local/breaker_reset_at` (an ISO timestamp), if present.
"""

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient
from ags.pipeline import run_pipeline
from ags.portfolio.guardrails import GuardrailConfig
from ags.portfolio.stage import COMMODITIES, PortfolioConfig, run_portfolio
from ags.storage import LocalStorage, Prefixed
from ags.tools.sources.prices import CTraderPricesClient

_ROOT = Path(__file__).parent
_LOCAL = _ROOT / "local"


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key) or default)


def _portfolio_config() -> PortfolioConfig:
    return PortfolioConfig(
        guardrails=GuardrailConfig(
            per_commodity_cap=_env_float("PER_COMMODITY_CAP", 1.0),
            grain_bucket_cap=_env_float("GRAIN_BUCKET_CAP", 1.5),
            gross_leverage_cap=_env_float("GROSS_LEVERAGE_CAP", 3.0),
            margin_buffer=_env_float("MARGIN_BUFFER", 0.40),
            min_resize_fraction=_env_float("MIN_RESIZE_FRACTION", 0.20),
        ),
        starting_equity=_env_float("STARTING_EQUITY_USD", 10_000),
        stop_out_level=_env_float("STOP_OUT_MARGIN_LEVEL", 0.50),
        shadow_risk_budget=_env_float("SHADOW_RISK_BUDGET", 0.01),
        drawdown_breaker=_env_float("DRAWDOWN_BREAKER", 0.80),
    )


def _breaker_reset_at() -> datetime | None:
    path = _LOCAL / "breaker_reset_at"
    return datetime.fromisoformat(path.read_text().strip()) if path.is_file() else None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run all seven pipelines and the portfolio stage into local/.")
    parser.add_argument("--off-schedule", action="store_true", help="log the portfolio stage but write no cycle files")
    args = parser.parse_args(argv)

    config = Config.from_env()
    now = datetime.now(timezone.utc)
    storage = LocalStorage(_LOCAL)
    chat_client = DeepSeekChatClient(config)
    market = CTraderPricesClient(config)
    try:
        calls = {}
        for commodity in COMMODITIES:
            result = run_pipeline(
                commodity=commodity, as_of=now, trigger_timestamp=now, chat_client=chat_client,
                model=config.deepseek_model, log_dir=Prefixed(storage, "logs"), data_dir=Prefixed(storage, "data"),
                audit_dir=Prefixed(storage, "audits"), prices_client=market,
            )
            calls[commodity] = result["call"]
            print(f"{commodity}: {result['call']['call']} ({result['call']['conviction']})")

        cycle = run_portfolio(
            "cycle_" + now.strftime("%Y-%m-%dT%H-%M-%SZ"), calls, storage, as_of=now, now=now,
            chat_client=chat_client, model=config.deepseek_model, market_client=market,
            config=_portfolio_config(), breaker_reset_at=_breaker_reset_at, off_schedule=args.off_schedule,
        )
    finally:
        market.close()

    for book in ("manager", "shadow"):
        record = cycle[book]
        print(f"\n== {book} == equity {record['equity']:.2f}  free margin {record['free_margin']:.2f}")
        for fill in record["fills"]:
            print(f"  {fill['trade_side']} {fill['symbol']} {fill['volume']} @ {fill['price']}")
        for violation in record["violations"]:
            print(f"  violation: {violation['rule']} {violation['symbol']}")


if __name__ == "__main__":
    main()
