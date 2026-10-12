import pytest

from ags.portfolio.guardrails import GuardrailConfig, apply_guardrails
from ags.portfolio.shadow import shadow_proposal

SYMBOLS = ["corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa"]
# equity $10,000 x 1% budget = $100 of risk; $50 ATR per lot -> 2 lots at full conviction
RISK = {s: {"atr_usd_per_lot": 50.0} for s in SYMBOLS}


def calls(**by_symbol):
    return {s: by_symbol.get(s, {"call": "neutral", "conviction": 3}) for s in SYMBOLS}


def book(**lots):
    return {"equity": 10_000.0, "lots": {s: lots.get(s, 0.0) for s in SYMBOLS}}


def deltas(proposal):
    return {leg["symbol"]: leg["delta_lots"] for leg in proposal["legs"]}


def test_bullish_sizes_to_conviction_over_five_of_the_risk_budget_per_atr_dollar():
    p = shadow_proposal(calls(corn={"call": "bullish", "conviction": 5}, wheat={"call": "bullish", "conviction": 3}), book(), RISK, 0.01)

    assert deltas(p)["corn"] == pytest.approx(2.0)
    assert deltas(p)["wheat"] == pytest.approx(1.2)


def test_bearish_targets_a_short():
    p = shadow_proposal(calls(sugar={"call": "bearish", "conviction": 5}), book(), RISK, 0.01)

    assert deltas(p)["sugar"] == pytest.approx(-2.0)


def test_delta_is_target_minus_current():
    p = shadow_proposal(calls(corn={"call": "bullish", "conviction": 5}), book(corn=0.5), RISK, 0.01)

    assert deltas(p)["corn"] == pytest.approx(1.5)


def test_a_neutral_leg_is_held_whatever_its_position():
    p = shadow_proposal(calls(), book(coffee=3.0), RISK, 0.01)

    assert deltas(p)["coffee"] == 0.0


def test_a_failed_commodity_is_held():
    c = calls() | {"cotton": None}

    p = shadow_proposal(c, book(cotton=-1.0), RISK, 0.01)

    assert deltas(p)["cotton"] == 0.0


def test_sizing_scales_with_equity():
    b = book() | {"equity": 20_000.0}

    p = shadow_proposal(calls(corn={"call": "bullish", "conviction": 5}), b, RISK, 0.01)

    assert deltas(p)["corn"] == pytest.approx(4.0)


def test_the_proposal_has_all_seven_legs_and_passes_the_guardrail_schema_check():
    p = shadow_proposal(calls(corn={"call": "bullish", "conviction": 5}), book(), RISK, 0.01)
    specs = {s: {"lot_size": 100_000, "min_volume": 10_000, "step_volume": 10_000, "margin_rate": 0.1} for s in SYMBOLS}
    risk = {s: {"notional_per_lot": 2_000.0} for s in SYMBOLS}
    config = GuardrailConfig(1.0, 1.5, 3.0, 0.40, 0.20)
    c = calls(corn={"call": "bullish", "conviction": 5})

    applied, violations = apply_guardrails(p, book(), c, specs, risk, config, False)

    assert [leg["symbol"] for leg in p["legs"]] == SYMBOLS
    assert applied["corn"] == pytest.approx(2.0)
    assert violations == []
