import pandas as pd

from ags.tools.trend import EMA_SPANS


def get_chart_snapshot(bars: list[dict], lookback: int) -> list[dict]:
    """The latest `lookback` daily candles, each enriched with the EMA_SPANS
    values as of that date — computed over the full `bars` history passed
    in, not just the slice returned, so the EMAs are properly warmed up
    rather than starting cold at the window edge."""
    trading_bars = sorted(
        (bar for bar in bars if not bar.get("no_trading")),
        key=lambda bar: bar["date"],
    )

    close_series = pd.Series({pd.Timestamp(bar["date"]): bar["close"] for bar in trading_bars})
    emas = {span: close_series.ewm(span=span, adjust=False).mean() for span in EMA_SPANS}

    candles = []
    for bar in trading_bars[-lookback:]:
        ts = pd.Timestamp(bar["date"])
        candles.append(
            {
                "date": bar["date"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
                **{f"ema_{span}": emas[span].loc[ts] for span in EMA_SPANS},
            }
        )
    return candles
