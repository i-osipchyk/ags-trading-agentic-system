import numpy as np
import pandas as pd

_TRADING_DAYS_PER_YEAR = 252
_CURRENT_WINDOW = 20
_TRAILING_WINDOW = 100


def _realized_vol(log_returns: pd.Series, window: int) -> float:
    return log_returns.tail(window).std(ddof=1) * np.sqrt(_TRADING_DAYS_PER_YEAR)


def get_volatility(prices: pd.Series) -> dict:
    log_returns = np.log(prices / prices.shift(1)).dropna()
    return {
        "current": _realized_vol(log_returns, _CURRENT_WINDOW),
        "trailing_average": _realized_vol(log_returns, _TRAILING_WINDOW),
    }
