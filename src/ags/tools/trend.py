import pandas as pd

_PAIRS = {"short": (8, 32), "medium": (16, 64), "long": (32, 128)}
_BAND = 0.005


def _classify(prices: pd.Series, fast_n: int, slow_n: int) -> dict:
    fast = prices.ewm(span=fast_n, adjust=False).mean()
    slow = prices.ewm(span=slow_n, adjust=False).mean()
    diff_ratio = (fast - slow) / slow

    def state_at(ratio: float) -> str:
        if ratio > _BAND:
            return "up"
        if ratio < -_BAND:
            return "down"
        return "sideways"

    states = diff_ratio.apply(state_at)
    current_state = states.iloc[-1]

    age = 0
    for state in states.iloc[::-1]:
        if state != current_state:
            break
        age += 1

    return {"state": current_state, "age": age}


def get_trend_state(prices: pd.Series) -> dict:
    return {name: _classify(prices, fast_n, slow_n) for name, (fast_n, slow_n) in _PAIRS.items()}
