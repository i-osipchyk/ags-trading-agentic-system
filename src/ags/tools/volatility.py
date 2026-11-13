import numpy as np
import pandas as pd

_TRADING_DAYS_PER_YEAR = 252
_CURRENT_WINDOW = 20
_TRAILING_WINDOW = 100


def _realized_vol(log_returns: pd.Series, window: int) -> float:
    return float(log_returns.tail(window).std(ddof=1) * np.sqrt(_TRADING_DAYS_PER_YEAR))


def get_volatility(prices: pd.Series) -> dict:
    """Annualized realized volatility of daily log returns, as a fraction (0.20 = 20% a year).

    `realized_100d` includes the last 20 sessions, so `ratio` (20-day over 100-day) says how far the last
    month's volatility sits from the longer norm; above 1 is more volatile than usual.
    """
    log_returns = np.log(prices / prices.shift(1)).dropna()
    short, long = _realized_vol(log_returns, _CURRENT_WINDOW), _realized_vol(log_returns, _TRAILING_WINDOW)
    return {
        "realized_20d": round(short, 4),
        "realized_100d": round(long, 4),
        "ratio": round(short / long, 2) if long else None,
    }
