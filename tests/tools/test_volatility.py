import numpy as np
import pandas as pd
import pytest

from ags.tools.volatility import get_volatility


def _alternating_log_return_prices(amplitudes_and_counts: list[tuple[float, int]]) -> pd.Series:
    log_returns = np.concatenate(
        [np.array([1, -1] * (count // 2)) * amplitude for amplitude, count in amplitudes_and_counts]
    )
    prices = 100.0 * np.exp(np.concatenate([[0], np.cumsum(log_returns)]))
    index = pd.date_range("2020-01-01", periods=len(prices), freq="D")
    return pd.Series(prices, index=index)


def test_get_volatility_reports_annualized_20d_and_100d_realized_vol_and_their_ratio():
    prices = _alternating_log_return_prices([(0.01, 80), (0.05, 20)])

    result = get_volatility(prices)

    assert set(result) == {"realized_20d", "realized_100d", "ratio"}
    assert result["realized_20d"] == pytest.approx(0.8143, abs=1e-4)
    assert result["realized_100d"] == pytest.approx(0.3842, abs=1e-4)
    assert result["ratio"] == pytest.approx(2.12, abs=0.01)


def test_get_volatility_returns_plain_floats_not_numpy_scalars():
    result = get_volatility(_alternating_log_return_prices([(0.01, 120)]))

    assert all(type(value) is float for value in result.values())


def test_get_volatility_has_no_ratio_when_the_longer_window_is_flat():
    flat = pd.Series([100.0] * 120, index=pd.date_range("2020-01-01", periods=120, freq="D"))

    assert get_volatility(flat) == {"realized_20d": 0.0, "realized_100d": 0.0, "ratio": None}
