import json
from datetime import datetime

import pytest

from ags.agents.aligned import align_outputs
from ags.agents.news import ALIGNED_SYSTEM_PROMPT_TEMPLATE as NEWS_ALIGNED
from ags.agents.news import SYSTEM_PROMPT_TEMPLATE as NEWS_LIVE
from ags.agents.news import run_news_analyst
from ags.agents.technical import ALIGNED_SYSTEM_PROMPT_TEMPLATE as TECH_ALIGNED
from ags.agents.technical import SYSTEM_PROMPT_TEMPLATE as TECH_LIVE
from ags.agents.weather import ALIGNED_SYSTEM_PROMPT_TEMPLATE as WEATHER_ALIGNED
from ags.agents.weather import SYSTEM_PROMPT_TEMPLATE as WEATHER_LIVE
from ags.agents.weather import run_weather_analyst
from ags.tools.pit_store import ET

AS_OF = datetime(2026, 11, 13, 6, 0, tzinfo=ET)


def _trend(short="down", medium="down", long="down"):
    return {k: {"state": s, "age": 5} for k, s in zip(("short", "medium", "long"), (short, medium, long))}


def _outputs(**overrides):
    base = {
        "technical": {"trend": _trend(), "seasonality_alignment": "typical", "chart_description": "x",
                      "degraded": False},
        "news": {"headlines": [{"headline": "Corn slips", "date": "2026-11-12", "direction": "bearish",
                                "reason": "exports weak"}] * 3, "degraded": False},
        "weather": {"regions_covered": ["IA"], "current_conditions": {"IA": "dry"},
                    "risk_assessment": {"IA": "watch"}, "stage_of_crop_cycle": "flowering", "degraded": False},
        "supply_demand": {"latest_report": {"name": "WASDE", "date": "2026-11-10"}, "degraded": False,
                          "demand_indicators": None,
                          "key_figures": [{"figure": "ending_stocks (million bushels)", "value": 1758, "prior": 1849}]},
    }
    return {**base, **overrides}


def test_every_analyst_gets_the_common_envelope_and_keeps_its_evidence():
    outputs = _outputs()

    aligned = align_outputs(outputs, as_of=AS_OF)

    for name, output in aligned.items():
        assert {"bias", "code_bias", "scale", "confidence", "horizon", "drivers", "invalidation", "basis"} <= set(output)
        assert outputs[name].items() <= output.items()            # nothing the analyst said is lost
    assert [aligned[a]["bias"] for a in ("technical", "news", "weather", "supply_demand")] == [-2, -2, 1, 2]
    assert aligned["weather"]["scale"]["min"] == -1
    assert aligned["technical"]["scale"]["max"] == 2
    assert "bias" not in outputs["technical"]                                    # the input is not changed


def test_weather_says_its_stage_and_that_it_is_already_in_the_bias():
    weather = align_outputs(_outputs(), as_of=AS_OF)["weather"]

    assert weather["context"]["stage_of_crop_cycle"] == "flowering"
    assert weather["context"]["stage_already_applied"] is True
    assert "already included" in weather["basis"]


def test_the_technical_model_moves_the_code_bias_one_step_and_both_are_kept():
    technical = {**_outputs()["technical"], "bias_adjustment": 1, "adjustment_reason": "slowing, reversal candle",
                 "invalidation": ["close below the 4.48 low"]}

    result = align_outputs(_outputs(technical=technical), as_of=AS_OF)["technical"]

    assert (result["code_bias"], result["bias"], result["bias_source"]) == (-2, -1, "code+model")
    assert result["adjustment_reason"] == "slowing, reversal candle"
    assert result["invalidation"] == ["close below the 4.48 low"]
    assert any("analyst adjustment" in d["driver"] for d in result["drivers"])


def test_an_adjustment_without_a_reason_is_ignored():
    technical = {**_outputs()["technical"], "bias_adjustment": 1, "adjustment_reason": ""}

    result = align_outputs(_outputs(technical=technical), as_of=AS_OF)["technical"]

    assert (result["code_bias"], result["bias"], result["bias_source"]) == (-2, -2, "code")


def test_a_crashed_analyst_gets_no_envelope_and_a_failing_alignment_keeps_the_evidence():
    crashed = {"degraded": True, "error": "ValueError: boom"}
    broken = {"degraded": False}      # a weather output with no stage cannot be aligned

    aligned = align_outputs(_outputs(technical=crashed, weather=broken), as_of=AS_OF)

    assert aligned["technical"] == crashed
    assert "alignment_error" in aligned["weather"] and aligned["weather"]["degraded"] is False
    assert "bias" in aligned["news"]                                  # the others are unaffected


def test_a_carried_forward_news_output_is_aligned_with_todays_recency():
    carried = {**_outputs()["news"], "carried_forward": True, "source_run_id": "corn_x"}

    fresh = align_outputs(_outputs(), as_of=AS_OF)["news"]
    result = align_outputs(_outputs(news=carried), as_of=AS_OF)["news"]

    assert result["confidence"] == fresh["confidence"] - 1


def test_the_live_prompts_are_unchanged_and_the_aligned_ones_are_separate():
    for live, aligned in ((TECH_LIVE, TECH_ALIGNED), (NEWS_LIVE, NEWS_ALIGNED), (WEATHER_LIVE, WEATHER_ALIGNED)):
        assert "bias" not in live.lower().replace("biased", "")
        assert aligned != live
    assert "favourable" in WEATHER_ALIGNED and "favourable" not in WEATHER_LIVE
    assert "do not soften a label because of the stage" in WEATHER_ALIGNED
    assert "{code_bias}" in TECH_ALIGNED


class _Chat:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append(messages)
        return self._responses.pop(0)


def _reply(payload):
    return {"role": "assistant", "content": json.dumps(payload), "tool_calls": None}


def test_the_aligned_weather_analyst_asks_for_the_new_labels_and_keeps_its_invalidation(tmp_path):
    chat = _Chat([_reply({"regions_covered": [], "current_conditions": {}, "risk_assessment": {"IA": "favourable"},
                          "invalidation": ["a hard frost"]})])

    result = run_weather_analyst(tmp_path, chat, commodity="corn", as_of=AS_OF, model="m", aligned=True)

    assert "favourable" in chat.calls[0][0]["content"]
    assert result["invalidation"] == ["a hard frost"] and result["risk_assessment"] == {"IA": "favourable"}
    assert result["degraded"] is False


def test_the_live_weather_analyst_does_not_ask_for_them(tmp_path):
    chat = _Chat([_reply({"regions_covered": [], "current_conditions": {}, "risk_assessment": {}})])

    result = run_weather_analyst(tmp_path, chat, commodity="corn", as_of=AS_OF, model="m")

    assert "favourable" not in chat.calls[0][0]["content"] and "invalidation" not in result


def test_a_missing_aligned_field_degrades_the_run_instead_of_hiding_the_gap(tmp_path):
    chat = _Chat([_reply({"headlines": []})])        # no "invalidation"

    class Empty:
        def search(self, query, *, published_before):
            return []

    result = run_news_analyst(tmp_path, chat, commodity="corn", as_of=AS_OF, model="m", news_client=Empty(),
                              aligned=True)

    assert result["degraded"] is True


def test_the_aligned_news_analyst_returns_its_invalidation(tmp_path):
    chat = _Chat([_reply({"headlines": [], "invalidation": ["a surprise export sale"]})])

    class Empty:
        def search(self, query, *, published_before):
            return []

    result = run_news_analyst(tmp_path, chat, commodity="corn", as_of=AS_OF, model="m", news_client=Empty(),
                              aligned=True)

    assert result["invalidation"] == ["a surprise export sale"] and result["degraded"] is False
