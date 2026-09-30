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


def test_get_volatility_reports_20d_current_vs_100d_trailing_realized_vol():
    prices = _alternating_log_return_prices([(0.01, 80), (0.05, 20)])

    result = get_volatility(prices)

    assert result["current"] == pytest.approx(0.8143450710459553)
    assert result["trailing_average"] == pytest.approx(0.384234776713878)
