import pandas as pd

from ags.tools.trend import get_trend_state


def _flat_then_rising(flat_days: int, rise_days: int) -> pd.Series:
    values = [100.0] * flat_days + [100.0 + i for i in range(1, rise_days + 1)]
    index = pd.date_range("2020-01-01", periods=flat_days + rise_days, freq="D")
    return pd.Series(values, index=index)


def test_get_trend_state_reports_up_on_all_timeframes_after_a_sustained_rally():
    prices = _flat_then_rising(flat_days=400, rise_days=150)

    result = get_trend_state(prices)

    assert result == {
        "short": {"state": "up", "age": 148},
        "medium": {"state": "up", "age": 147},
        "long": {"state": "up", "age": 146},
    }


def test_get_trend_state_reports_down_on_all_timeframes_after_a_sustained_decline():
    flat_days, fall_days = 400, 150
    values = [300.0] * flat_days + [300.0 - i for i in range(1, fall_days + 1)]
    index = pd.date_range("2020-01-01", periods=flat_days + fall_days, freq="D")
    prices = pd.Series(values, index=index)

    result = get_trend_state(prices)

    assert result == {
        "short": {"state": "down", "age": 146},
        "medium": {"state": "down", "age": 144},
        "long": {"state": "down", "age": 142},
    }


def test_get_trend_state_reports_sideways_on_all_timeframes_for_a_flat_series():
    prices = pd.Series(
        [100.0] * 200, index=pd.date_range("2020-01-01", periods=200, freq="D")
    )

    result = get_trend_state(prices)

    assert result == {
        "short": {"state": "sideways", "age": 200},
        "medium": {"state": "sideways", "age": 200},
        "long": {"state": "sideways", "age": 200},
    }
