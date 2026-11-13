_RANGE_SESSIONS = 20
_PIVOT_LOOKBACK = 60
# A swing is a session whose high (low) is beyond the sessions either side of it. One bar each side, because in a
# steady trend the highs only step down and wider swings find nothing near price.
_PIVOT_WIDTH = 1
# Levels closer than this share of the reference price are one level.
_MIN_GAP = 0.0025
_MAX_PER_SIDE = 3


def _swings(bars: list[dict], field: str, pick, width: int) -> list[float]:
    """Prices where `field` is the extreme (by `pick`: max or min) of the `width` bars on each side too.

    A swing needs `width` later bars to confirm it, so the newest sessions are never swings; the recent
    range covers them.
    """
    values = [bar[field] for bar in bars]
    return [
        values[i]
        for i in range(width, len(values) - width)
        if values[i] == pick(values[i - width : i + width + 1])
    ]


def _nearest(levels: list[float], reference_price: float, limit: int) -> list[float]:
    """The `limit` levels nearest the reference price, treating levels within `_MIN_GAP` of it as one."""
    chosen: list[float] = []
    for level in sorted(levels, key=lambda level: abs(level - reference_price)):
        if all(abs(level - kept) > _MIN_GAP * reference_price for kept in chosen):
            chosen.append(level)
        if len(chosen) == limit:
            break
    return sorted(chosen, key=lambda level: abs(level - reference_price))


def get_key_levels(
    bars: list[dict],
    reference_price: float,
    *,
    reference: str = "last_close",
    range_sessions: int = _RANGE_SESSIONS,
    pivot_lookback: int = _PIVOT_LOOKBACK,
    pivot_width: int = _PIVOT_WIDTH,
) -> dict | None:
    """Price levels from the daily bars: the recent range and the nearest swing highs above and swing lows
    below `reference_price` (the live quote when there is one, else the last close).

    Computed by code so the analyst comments on levels instead of inventing them. None when there are too
    few sessions to confirm a swing.
    """
    trading = sorted((bar for bar in bars if not bar.get("no_trading")), key=lambda bar: bar["date"])
    if len(trading) < 2 * pivot_width + 1:
        return None
    recent = trading[-range_sessions:]
    window = trading[-pivot_lookback:]
    highs = [level for level in _swings(window, "high", max, pivot_width) if level > reference_price]
    lows = [level for level in _swings(window, "low", min, pivot_width) if level < reference_price]
    return {
        "reference_price": reference_price,
        "reference": reference,
        "recent_range": [float(min(bar["low"] for bar in recent)), float(max(bar["high"] for bar in recent))],
        "resistance": [float(level) for level in _nearest(highs, reference_price, _MAX_PER_SIDE)],
        "support": [float(level) for level in _nearest(lows, reference_price, _MAX_PER_SIDE)],
    }
