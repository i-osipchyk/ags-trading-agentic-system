import pytest

from ags.portfolio.risk import risk_facts

SPECS = {"lot_size": 100_000}  # cTrader volume units (1/100 of a unit) -> 1,000 units per lot


def _bar(high, low, close):
    return {"date": "x", "open": close, "high": high, "low": low, "close": close, "volume": 1}


# True ranges: 3 (high-low), 5 (gap up: high - prev close), 5 (low vs prev close)
BARS = [_bar(101, 99, 100), _bar(103, 100, 102), _bar(107, 104, 106), _bar(106, 101, 102)]


def test_atr_is_the_mean_true_range_including_gaps_over_the_period():
    facts = risk_facts(BARS, SPECS, atr_period=3)

    assert facts["atr"] == pytest.approx(13 / 3)


def test_dollar_atr_and_notional_per_lot_use_units_per_lot_and_last_close():
    facts = risk_facts(BARS, SPECS, atr_period=3)

    assert facts["atr_usd_per_lot"] == pytest.approx(13 / 3 * 1000)
    assert facts["notional_per_lot"] == pytest.approx(102 * 1000)


def test_default_period_is_14_and_needs_15_bars():
    flat = [_bar(102, 100, 101)] * 15

    assert risk_facts(flat, SPECS)["atr"] == pytest.approx(2.0)
    with pytest.raises(ValueError, match="15"):
        risk_facts(flat[:14], SPECS)


def test_only_the_most_recent_period_counts():
    old_wild = [_bar(200, 50, 100)] * 5
    calm = [_bar(102, 100, 101)] * 15

    assert risk_facts(old_wild + calm, SPECS)["atr"] == pytest.approx(2.0)
