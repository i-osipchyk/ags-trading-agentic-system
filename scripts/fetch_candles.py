"""CLI: fill the local daily-candle cache up to the last closed session, for every commodity.

The S3 mirror only holds the bars the scheduled cycles asked for, so the newest sessions can be missing from
it. This asks cTrader (market data only, never trading) for the gap and caches it under `data/` like any other
price pull (`get_prices`, closed sessions only, each day cached once). It writes only the local mirror, never S3.
Needs cTrader access: the shared token pair is read from SSM with your AWS credentials unless `.env` sets one.
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ags.config import Config
from ags.tools.pit_store import ET
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES, CTraderPricesClient, get_prices

_ROOT = Path(__file__).resolve().parents[1]  # repo root
_LOOKBACK_DAYS = 14


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description="Fetch the daily candles missing from the local cache.").parse_args(argv)
    config = Config.from_env()
    now = datetime.now(timezone.utc)
    today = now.astimezone(ET).date()
    client = CTraderPricesClient(config)
    try:
        for commodity in CTRADER_SYMBOL_NAMES:
            bars = get_prices(
                _ROOT / "data", commodity=commodity, start=today - timedelta(days=_LOOKBACK_DAYS), end=today,
                as_of=now, client=client, now=now,
            )
            print(f"{commodity}: newest close {bars[-1]['date'] if bars else 'none'}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
