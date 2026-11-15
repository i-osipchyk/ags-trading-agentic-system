from datetime import date

import pytest

from ags import alignment
from ags.alignment import (
    PRICE_SCALE,
    WEATHER_SCALE,
    adjust_bias,
    make_envelope,
    news_bias,
    supply_demand_bias,
    technical_bias,
    weather_bias,
)

AS_OF = date(2026, 11, 13)


def _trend(short, medium, long):
    return {k: {"state": s, "age": 3} for k, s in zip(("short", "medium", "long"), (short, medium, long))}


def test_the_envelope_carries_its_scale_and_what_each_value_means():
    envelope = make_envelope(
        bias=-1, scale=WEATHER_SCALE, confidence=3, horizon="weeks", drivers=[], invalidation=[],
        bias_source="code", basis="x",
    )

    assert envelope["scale"]["min"] == -1 and envelope["scale"]["max"] == 1
    assert "bullish" in envelope["scale"]["meaning"]["1"] and "bearish" in envelope["scale"]["meaning"]["-1"]
    assert envelope["code_bias"] == -1


@pytest.mark.parametrize("override", [{"bias": 3}, {"confidence": 6}, {"horizon": "year"}, {"bias_source": "model"}])
def test_the_envelope_refuses_values_outside_its_vocabulary(override):
    fields = dict(bias=0, scale=PRICE_SCALE, confidence=3, horizon="1 week", drivers=[], invalidation=[],
                  bias_source="code", basis="x")

    with pytest.raises(ValueError):
        make_envelope(**{**fields, **override})


def test_weather_bias_cannot_leave_minus_one_to_plus_one():
    with pytest.raises(ValueError):
        make_envelope(bias=2, scale=WEATHER_SCALE, confidence=3, horizon="weeks", drivers=[], invalidation=[],
                      bias_source="code", basis="x")


@pytest.mark.parametrize("states,expected", [
    (("up", "up", "up"), (2, 4)), (("down", "down", "down"), (-2, 4)), (("up", "up", "sideways"), (1, 3)),
    (("down", "down", "up"), (-1, 3)), (("up", "down", "sideways"), (0, 2)), (("sideways",) * 3, (0, 4)),
])
def test_technical_base_bias_from_the_three_trend_states(states, expected):
    assert technical_bias(_trend(*states)) == expected


def test_technical_bias_without_a_trend_is_no_view():
    assert technical_bias(None) == (0, 1)


def test_the_model_moves_a_code_bias_one_step_and_only_with_a_reason():
    assert adjust_bias(-2, 1, "slowing, reversal candle", PRICE_SCALE) == (-1, "slowing, reversal candle")
    assert adjust_bias(-2, 1, "  ", PRICE_SCALE) == (-2, None)
    assert adjust_bias(-2, 1, None, PRICE_SCALE) == (-2, None)
    assert adjust_bias(-2, 2, "too far", PRICE_SCALE) == (-2, None)
    assert adjust_bias(-2, -1, "pushes past the scale", PRICE_SCALE) == (-2, None)
    assert adjust_bias(0, True, "a bool is not a step", PRICE_SCALE) == (0, None)


def _sd(value, prior, date_="2026-11-10", name="WASDE"):
    return {"latest_report": {"name": name, "date": date_}, "degraded": False, "demand_indicators": None,
            "key_figures": [{"figure": "ending_stocks (million bushels)", "value": value, "prior": prior}]}


def test_ending_stocks_revised_down_is_bullish_and_up_is_bearish_strong_from_three_percent():
    bias, confidence, basis = supply_demand_bias(_sd(1758, 1849), as_of=AS_OF)       # -4.9%
    assert (bias, confidence) == (2, 4) and "-4.9%" in basis
    assert supply_demand_bias(_sd(1860, 1849), as_of=AS_OF)[0] == -1                  # +0.6%
    assert supply_demand_bias(_sd(2000, 1849), as_of=AS_OF)[0] == -2
    assert supply_demand_bias(_sd(1849, 1849), as_of=AS_OF)[0] == 0


def test_an_old_release_lowers_supply_demand_confidence_and_no_revision_is_no_view():
    assert supply_demand_bias(_sd(1758, 1849, "2026-09-01"), as_of=AS_OF)[1] == 2
    assert supply_demand_bias(_sd(1758, None), as_of=AS_OF)[:2] == (0, 1)
    assert supply_demand_bias({"latest_report": None, "degraded": True}, as_of=AS_OF)[:2] == (0, 1)


def test_cocoa_demand_is_the_average_year_on_year_grind():
    output = {"latest_report": {"name": "grinds", "date": "2026-10-20"}, "degraded": False,
              "demand_indicators": {"europe": {"yoy_pct": 10.0}, "asia": {"yoy_pct": 7.0}}, "key_figures": []}

    assert supply_demand_bias(output, as_of=AS_OF)[0] == 2
    output["demand_indicators"] = {"europe": {"yoy_pct": -4.0}, "asia": None}
    assert supply_demand_bias(output, as_of=AS_OF)[0] == -1


def _news(directions_and_dates, carried=False):
    return {"headlines": [{"direction": d, "date": dt} for d, dt in directions_and_dates], "carried_forward": carried}


def test_recent_one_sided_news_is_strong_and_a_lone_old_headline_is_weak():
    strong, confidence, _ = news_bias(_news([("bearish", "2026-11-13")] * 4), as_of=AS_OF)
    assert (strong, confidence) == (-2, 4)
    old = news_bias(_news([("bullish", "2026-10-01")]), as_of=AS_OF)
    assert old[0] == 0 and old[1] == 1


def test_newer_headlines_outweigh_older_ones_and_carrying_forward_lowers_confidence():
    mixed = _news([("bullish", "2026-11-13"), ("bullish", "2026-11-13"), ("bearish", "2026-11-01")] * 2)
    fresh = news_bias(mixed, as_of=AS_OF)
    carried = news_bias({**mixed, "carried_forward": True}, as_of=AS_OF)

    assert fresh[0] > 0
    assert carried[1] == fresh[1] - 1


def test_news_without_dated_headlines_is_no_view():
    assert news_bias({"headlines": [{"direction": "bullish", "date": None}]}, as_of=AS_OF)[:2] == (0, 1)
    assert news_bias({"headlines": []}, as_of=AS_OF)[:2] == (0, 1)


def _weather(*labels):
    return {"risk_assessment": {f"R{i}": label for i, label in enumerate(labels)}, "degraded": False}


def test_a_threat_in_a_critical_stage_is_bullish_and_favourable_conditions_bearish():
    assert weather_bias(_weather("watch", "watch", "watch"), stage="flowering")[0] == 1
    assert weather_bias(_weather("severe", "benign", "benign"), stage="flowering")[0] == 0     # mean 0.33
    assert weather_bias(_weather("favourable", "favourable", "benign"), stage="grain_fill")[0] == -1
    assert weather_bias(_weather("benign", "benign"), stage="flowering")[0] == 0


def test_the_same_labels_count_for_less_in_a_relevant_stage_and_nothing_off_season():
    labels = _weather("watch", "watch", "watch")

    assert weather_bias(labels, stage="harvest")[0] == 0           # 0.5 x 0.5 = 0.25
    assert weather_bias(_weather("severe", "severe"), stage="harvest")[0] == 1     # 1.0 x 0.5 = 0.5
    assert weather_bias(_weather("severe", "severe"), stage="dormant")[0] == 0


def test_weather_says_which_stage_it_is_in_and_that_the_stage_is_already_in_the_bias():
    bias, confidence, basis, context = weather_bias(_weather("severe"), stage="dormant")

    assert context == {"stage_of_crop_cycle": "dormant", "stage_sensitivity": "off_season", "stage_weight": 0.0,
                       "stage_already_applied": True}
    assert "already included" in basis and "dormant" in basis
    assert confidence == 2


def test_weather_without_a_usable_assessment_is_no_view_and_a_degraded_one_is_less_confident():
    assert weather_bias({"risk_assessment": {"IA": "unknown"}}, stage="flowering")[:2] == (0, 1)
    assert weather_bias({**_weather("watch", "watch", "watch"), "degraded": True}, stage="flowering")[1] == 2


def test_every_calendar_stage_has_a_sensitivity():
    from ags.tools.growing_calendar import _CALENDARS

    for stages in _CALENDARS.values():
        for stage in stages.values():
            assert alignment.stage_sensitivity(stage) in alignment.STAGE_WEIGHT
