import pandas as pd
import pytest

from ags.tools.chart import get_chart_snapshot
from ags.tools.trend import EMA_SPANS


def _flat_bars(days: int, price: float = 100.0) -> list[dict]:
    dates = pd.date_range("2020-01-01", periods=days, freq="D")
    return [
        {"date": d.date().isoformat(), "open": price, "high": price, "low": price, "close": price, "volume": 100}
        for d in dates
    ]


def test_get_chart_snapshot_returns_latest_n_candles_with_emas_for_a_flat_series():
    bars = _flat_bars(days=200)

    result = get_chart_snapshot(bars, lookback=30)

    assert len(result) == 30
    assert [c["date"] for c in result] == [b["date"] for b in bars[-30:]]
    for candle in result:
        assert candle["open"] == candle["high"] == candle["low"] == candle["close"] == 100.0
        # EMA of a constant series is that same constant, at any span —
        # an independently reasoned fact, not a re-derivation of the code.
        for span in EMA_SPANS:
            assert candle[f"ema_{span}"] == pytest.approx(100.0)


def test_get_chart_snapshot_passes_through_distinct_ohlcv_fields_per_candle():
    bars = [
        {"date": "2026-01-05", "open": 4.50, "high": 4.58, "low": 4.47, "close": 4.52, "volume": 1000},
        {"date": "2026-01-06", "open": 4.52, "high": 4.60, "low": 4.51, "close": 4.58, "volume": 1100},
    ]

    result = get_chart_snapshot(bars, lookback=2)

    assert result[0] == {
        "date": "2026-01-05",
        "open": 4.50,
        "high": 4.58,
        "low": 4.47,
        "close": 4.52,
        "volume": 1000,
        # EMA at the first data point equals that point's close (no prior
        # history to blend in yet), regardless of span.
        **{f"ema_{span}": pytest.approx(4.52) for span in EMA_SPANS},
    }
    assert result[1]["close"] == 4.58
    assert result[1]["volume"] == 1100


def test_get_chart_snapshot_returns_all_bars_when_fewer_than_lookback_are_available():
    bars = _flat_bars(days=5)

    result = get_chart_snapshot(bars, lookback=30)

    assert len(result) == 5
