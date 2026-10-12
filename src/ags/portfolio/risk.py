_ATR_PERIOD = 14
_CTRADER_VOLUME_PER_UNIT = 100  # cTrader volumes are sent in 1/100 of a unit


def _true_range(bar: dict, prev_close: float) -> float:
    return max(bar["high"] - bar["low"], abs(bar["high"] - prev_close), abs(bar["low"] - prev_close))


def risk_facts(bars: list[dict], specs: dict, *, atr_period: int = _ATR_PERIOD) -> dict:
    """Code-computed risk inputs from completed daily bars (oldest first) and symbol specs.

    ATR is the simple mean of the last `atr_period` true ranges. Prices are
    USD-quoted, so one price unit on one unit of contract is one dollar.
    """
    if len(bars) < atr_period + 1:
        raise ValueError(f"need {atr_period + 1} daily bars for a {atr_period}-day ATR, got {len(bars)}")
    recent = bars[-(atr_period + 1) :]
    ranges = [_true_range(bar, prev["close"]) for prev, bar in zip(recent, recent[1:])]
    atr = sum(ranges) / atr_period
    units_per_lot = specs["lot_size"] / _CTRADER_VOLUME_PER_UNIT
    return {
        "atr": atr,
        "atr_usd_per_lot": atr * units_per_lot,
        "notional_per_lot": bars[-1]["close"] * units_per_lot,
    }
