import pytest

from ags.portfolio.guardrails import GuardrailConfig, apply_guardrails

SYMBOLS = ["corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa"]
GRAINS = ["corn", "soybeans", "wheat"]

# 1 lot = 1,000 units, 0.1-lot step and minimum, 10:1 margin, $2,000 notional per lot.
SPEC = {"lot_size": 100_000, "min_volume": 10_000, "step_volume": 10_000, "margin_rate": 0.1}
SPECS = {s: SPEC for s in SYMBOLS}
RISK = {s: {"notional_per_lot": 2_000.0} for s in SYMBOLS}
CONFIG = GuardrailConfig(
    per_commodity_cap=1.0, grain_bucket_cap=1.5, gross_leverage_cap=3.0,
    margin_buffer=0.40, min_resize_fraction=0.20,
)
# equity $10,000 -> per-commodity cap 5 lots, grain bucket 7.5, gross 15, margin buffer 20 lots


def proposal(**deltas):
    return {
        "legs": [{"symbol": s, "delta_lots": deltas.get(s, 0.0), "rationale": "r"} for s in SYMBOLS],
        "portfolio_note": "n",
    }


def calls(**by_symbol):
    return {s: {"call": by_symbol.get(s, "bullish"), "conviction": 3} for s in SYMBOLS}


def run(prop, *, lots=None, call_map=None, breaker=False, equity=10_000.0, config=CONFIG, risk=RISK, specs=SPECS):
    book = {"equity": equity, "lots": {s: 0.0 for s in SYMBOLS} | (lots or {})}
    applied, violations = apply_guardrails(prop, book, call_map or calls(), specs, risk, config, breaker)
    return applied, violations


def rules(violations, symbol=None):
    return [v["rule"] for v in violations if symbol is None or v["symbol"] == symbol]


# 1. schema
@pytest.mark.parametrize(
    "bad",
    [
        None,
        {"portfolio_note": "n"},
        {"legs": proposal()["legs"][:6], "portfolio_note": "n"},  # a symbol missing
        {"legs": proposal()["legs"] + [proposal()["legs"][0]], "portfolio_note": "n"},  # duplicate
        {"legs": [{"symbol": s, "delta_lots": "lots", "rationale": "r"} for s in SYMBOLS], "portfolio_note": "n"},
    ],
)
def test_a_malformed_proposal_holds_everything(bad):
    applied, violations = run(bad, lots={"corn": 2.0})

    assert applied == {s: 0.0 for s in SYMBOLS}
    assert rules(violations) == ["schema"]


def test_a_valid_proposal_within_all_limits_passes_untouched():
    applied, violations = run(proposal(corn=2.0, coffee=1.5))

    assert applied["corn"] == 2.0 and applied["coffee"] == 1.5
    assert violations == []


# 9. missing input
def test_a_commodity_whose_pipeline_failed_is_held_whatever_the_manager_said():
    call_map = calls() | {"corn": None}

    applied, violations = run(proposal(corn=2.0, coffee=1.0), lots={"corn": 3.0}, call_map=call_map)

    assert applied["corn"] == 0.0 and applied["coffee"] == 1.0
    assert rules(violations, "corn") == ["missing_input"]


# 2. direction
def test_a_neutral_call_freezes_its_leg():
    applied, violations = run(proposal(corn=-2.0, coffee=1.0), lots={"corn": 3.0}, call_map=calls(corn="neutral"))

    assert applied["corn"] == 0.0
    assert applied["coffee"] == 1.0
    assert rules(violations, "corn") == ["direction"]


def test_a_sign_flip_against_a_bullish_call_clamps_to_flat_without_touching_other_legs():
    applied, violations = run(proposal(corn=-4.0, coffee=1.0), lots={"corn": 3.0})

    assert applied["corn"] == -3.0  # delta that lands the leg exactly flat
    assert applied["coffee"] == 1.0
    assert rules(violations) == ["direction"]
    assert violations[0]["symbol"] == "corn" and violations[0]["proposed"] == -4.0


def test_an_existing_position_against_the_call_is_closed_even_if_the_manager_proposes_nothing():
    applied, violations = run(proposal(), lots={"corn": -2.0}, call_map=calls(corn="bullish"))

    assert applied["corn"] == 2.0
    assert rules(violations, "corn") == ["direction"]


def test_bearish_may_end_short_or_flat_but_not_long():
    applied, _ = run(proposal(corn=-2.0, coffee=3.0), call_map=calls(corn="bearish", coffee="bearish"))

    assert applied["corn"] == -2.0
    assert applied["coffee"] == 0.0


# 3. per-commodity cap
@pytest.mark.parametrize(
    "call, delta, expected",
    [("bullish", 7.0, 5.0), ("bearish", -7.0, -5.0)],
)
def test_a_leg_is_clamped_to_the_per_commodity_notional_cap(call, delta, expected):
    applied, violations = run(proposal(coffee=delta), call_map=calls(coffee=call))

    assert applied["coffee"] == expected
    assert rules(violations, "coffee") == ["per_commodity_cap"]


def test_the_cap_shrinks_a_neutral_frozen_leg_that_has_drifted_over_it():
    applied, violations = run(proposal(), lots={"coffee": 6.0}, call_map=calls(coffee="neutral"))

    assert applied["coffee"] == -1.0
    assert rules(violations, "coffee") == ["per_commodity_cap"]


# 7. no-churn threshold
def test_a_resize_under_20_percent_of_the_current_leg_is_dropped():
    applied, violations = run(proposal(coffee=0.5), lots={"coffee": 4.0})

    assert applied["coffee"] == 0.0
    assert rules(violations, "coffee") == ["no_churn"]


def test_a_resize_of_exactly_20_percent_goes_through():
    applied, violations = run(proposal(coffee=0.8), lots={"coffee": 4.0})

    assert applied["coffee"] == pytest.approx(0.8)
    assert violations == []


def test_a_trade_smaller_than_one_volume_step_is_dropped():
    applied, violations = run(proposal(coffee=0.05))

    assert applied["coffee"] == 0.0
    assert rules(violations, "coffee") == ["no_churn"]


def test_churn_never_cancels_a_safety_clamp():
    # 5.3 lots is 0.3 over the cap: shrinking by 0.3 is a 5.7% resize, but it must still happen.
    applied, _ = run(proposal(), lots={"coffee": 5.3}, call_map=calls(coffee="neutral"))

    assert applied["coffee"] == pytest.approx(-0.3)


# 8. drawdown breaker
def test_breaker_blocks_increases():
    applied, violations = run(proposal(coffee=1.5), lots={"coffee": 3.0}, breaker=True)

    assert applied["coffee"] == 0.0
    assert rules(violations, "coffee") == ["drawdown_breaker"]


def test_breaker_blocks_opening_a_new_position():
    applied, _ = run(proposal(coffee=2.0), breaker=True)

    assert applied["coffee"] == 0.0


def test_breaker_still_allows_reductions_and_closing():
    applied, violations = run(proposal(coffee=-1.5, corn=-3.0), lots={"coffee": 3.0, "corn": 3.0}, breaker=True)

    assert applied["coffee"] == -1.5 and applied["corn"] == -3.0
    assert violations == []


def test_breaker_off_allows_increases():
    applied, _ = run(proposal(coffee=1.5), lots={"coffee": 3.0}, breaker=False)

    assert applied["coffee"] == 1.5


# 4-6. portfolio-level scaling
def test_grain_bucket_scales_the_grain_legs_proportionally_and_leaves_others_alone():
    applied, violations = run(proposal(corn=5.0, soybeans=5.0, wheat=5.0, coffee=2.0))

    assert [applied[g] for g in GRAINS] == pytest.approx([2.5, 2.5, 2.5])  # 15 lots -> bucket cap 7.5
    assert applied["coffee"] == 2.0
    assert sorted(rules(violations)) == ["grain_bucket"] * 3


def test_grain_bucket_is_per_direction_so_a_long_short_grain_spread_is_not_scaled():
    call_map = calls(wheat="bearish")

    applied, violations = run(proposal(corn=5.0, wheat=-5.0), call_map=call_map)

    assert applied["corn"] == 5.0 and applied["wheat"] == -5.0
    assert violations == []


def test_gross_leverage_scales_every_leg_proportionally():
    applied, violations = run(proposal(sugar=5.0, coffee=5.0, cotton=5.0, cocoa=5.0))  # 20 lots > 15

    assert [applied[s] for s in ("sugar", "coffee", "cotton", "cocoa")] == pytest.approx([3.7] * 4)  # 3.75 rounded toward zero
    assert set(rules(violations)) == {"gross_leverage"}


def test_a_neutral_frozen_leg_shrinks_under_the_gross_cap_along_with_everything_else():
    applied, _ = run(
        proposal(sugar=5.0, cotton=5.0, cocoa=5.0), lots={"coffee": 5.0}, call_map=calls(coffee="neutral")
    )  # 20 lots -> x0.75

    assert applied["coffee"] == pytest.approx(-1.3)  # 5.0 -> 3.75 -> 3.7
    assert applied["sugar"] == pytest.approx(3.7)


def test_margin_buffer_scales_down_when_used_margin_exceeds_the_share_of_equity():
    config = GuardrailConfig(1.0, 1.5, 3.0, margin_buffer=0.10, min_resize_fraction=0.20)  # 1,000 of margin = 5 lots

    applied, violations = run(proposal(coffee=5.0, cotton=5.0), config=config)

    assert applied["coffee"] == pytest.approx(2.5) and applied["cotton"] == pytest.approx(2.5)
    assert set(rules(violations)) == {"margin_buffer"}


# rounding
def test_rounding_is_toward_zero_so_it_never_pushes_a_leg_back_over_its_cap():
    applied, _ = run(proposal(coffee=9.0), equity=10_500.0)  # cap = 5.25 lots

    assert applied["coffee"] == pytest.approx(5.2)


def test_a_symbol_whose_minimum_volume_exceeds_its_cap_is_reported_not_rounded_up():
    specs = SPECS | {"cocoa": SPEC | {"min_volume": 800_000}}  # 8-lot minimum vs a 5-lot cap

    applied, violations = run(proposal(cocoa=5.0), specs=specs)

    assert applied["cocoa"] == 0.0
    assert rules(violations, "cocoa")[-1] == "min_volume"
