import json
from datetime import date, datetime

import pytest

from ags.agents.coordinator import run_coordinator
from ags.tools.pit_store import ET

AS_OF = datetime(2026, 6, 5, 16, 0, tzinfo=ET)

ANALYST_OUTPUTS = {
    "technical": {"seasonality_alignment": "typical", "chart_description": "TECH-MARKER steady uptrend", "degraded": False},
    "news": {"headlines": [{"headline": "NEWS-MARKER exports jump", "direction": "bullish"}], "degraded": False},
    "weather": {"risk_assessment": {"IA": "severe"}, "current_conditions": {"IA": "WX-MARKER drought"}, "degraded": False},
    "supply_demand": {"latest_report": "SD-MARKER WASDE", "stocks_to_use": {"trend": "tightening"}, "degraded": False},
}


class FakeChatClient:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        content = self._reply if isinstance(self._reply, str) else json.dumps(self._reply)
        return {"role": "assistant", "content": content, "tool_calls": None}


def _call(reply, outputs=ANALYST_OUTPUTS, **kwargs):
    client = FakeChatClient(reply)
    result = run_coordinator(
        client, commodity="corn", as_of=AS_OF, model="stub-model", analyst_outputs=outputs, **kwargs
    )
    return result, client


FULL_AGREEMENT = {
    "call": "bullish",
    "conviction": 5,
    "supporting_analysts": ["technical", "news", "weather", "supply_demand"],
    "dissenting_analysts": [],
    "thesis": "Every analyst points the same way.",
    "key_drivers": [{"driver": "Iowa drought", "source": "weather"}],
    "invalidation_conditions": ["Rain returns to the western belt"],
}


def test_full_agreement_yields_the_fixed_call_schema():
    result, client = _call(FULL_AGREEMENT)

    assert result == {**FULL_AGREEMENT, "horizon": "1 week", "price_at_call": None, "rubric_violations": [], "degraded": False}

    system = client.calls[0]["messages"][0]["content"]
    user = client.calls[0]["messages"][1]["content"]
    prompt = system + user
    for marker in ("TECH-MARKER", "NEWS-MARKER", "WX-MARKER", "SD-MARKER", "2026-06-05"):
        assert marker in prompt
    assert client.calls[0]["tools"] is None  # coordinator has no tools yet (Stage 10 adds read_track_record)


SPLIT = {
    **FULL_AGREEMENT,
    "conviction": 3,
    "supporting_analysts": ["technical", "weather"],
    "dissenting_analysts": ["news", "supply_demand"],
}


def test_split_signals_at_conviction_3_are_rubric_consistent():
    result, _ = _call(SPLIT)

    assert result["conviction"] == 3
    assert result["rubric_violations"] == []


def test_conviction_5_with_dissent_is_flagged_and_capped_to_what_the_rubric_allows():
    result, _ = _call({**SPLIT, "conviction": 5})

    # Two supporting fails both the 5 and the 4 rung.
    assert result["conviction"] == 3
    assert result["conviction_as_proposed"] == 5
    assert len(result["rubric_violations"]) == 1
    assert "5" in result["rubric_violations"][0]
    assert result["degraded"] is False


def test_a_rubric_breach_is_sent_back_once_and_a_corrected_reply_stands():
    client = ScriptedChatClient([_reply({**SPLIT, "conviction": 5}), _reply(SPLIT)])

    result = run_coordinator(client, commodity="corn", as_of=AS_OF, model="m", analyst_outputs=ANALYST_OUTPUTS)

    assert result["conviction"] == 3
    assert result["rubric_violations"] == []
    assert "conviction_as_proposed" not in result
    assert "conviction 5 requires" in client.calls[1]["messages"][-1]["content"]


def test_mostly_neutral_backdrop_at_conviction_2_is_consistent_but_4_is_flagged():
    neutral = {**FULL_AGREEMENT, "call": "neutral", "supporting_analysts": ["weather"], "dissenting_analysts": []}

    weak, _ = _call({**neutral, "conviction": 2})
    overclaimed, _ = _call({**neutral, "conviction": 4})

    assert weak["rubric_violations"] == []
    assert len(overclaimed["rubric_violations"]) == 1
    assert "4" in overclaimed["rubric_violations"][0]


def test_non_json_reply_degrades_instead_of_crashing():
    result, _ = _call("I think corn looks bullish")

    assert result["degraded"] is True
    assert result["call"] is None
    assert result["conviction"] is None
    assert result["horizon"] == "1 week"


def test_out_of_range_conviction_or_unknown_call_is_a_degraded_null_call():
    bad_conviction, _ = _call({**FULL_AGREEMENT, "conviction": 7})
    bad_call, _ = _call({**FULL_AGREEMENT, "call": "very bullish"})
    missing_call = {k: v for k, v in FULL_AGREEMENT.items() if k != "call"}
    incomplete, _ = _call(missing_call)

    for result in (bad_conviction, bad_call, incomplete):
        assert result["degraded"] is True
        assert result["call"] is None
        assert result["invalid_fields"]


def _reply(content):
    return {"role": "assistant", "content": content if isinstance(content, str) else json.dumps(content), "tool_calls": None}


class ScriptedChatClient:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self._replies.pop(0)


def test_coordinator_can_read_its_track_record_through_a_tool():
    past = [{"run_id": "corn_2026-05-29T18-30-00Z", "call": "bullish", "audits": []}]
    asked = []

    def track_record(lookback_weeks):
        asked.append(lookback_weeks)
        return past

    tool_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "read_track_record", "arguments": '{"lookback_weeks": 6}'}}
        ],
    }
    final = {"role": "assistant", "content": json.dumps(FULL_AGREEMENT), "tool_calls": None}
    client = ScriptedChatClient([tool_call, final])

    result = run_coordinator(
        client,
        commodity="corn",
        as_of=AS_OF,
        model="stub-model",
        analyst_outputs=ANALYST_OUTPUTS,
        track_record=track_record,
    )

    assert [t["function"]["name"] for t in client.calls[0]["tools"]] == ["read_track_record"]
    assert asked == [6]
    tool_message = client.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"]) == {"runs": past}
    assert result["call"] == "bullish"


def test_coordinator_has_no_tools_without_a_track_record():
    _, client = _call(FULL_AGREEMENT)
    assert client.calls[0]["tools"] is None


def test_a_reply_that_is_not_an_object_is_a_degraded_null_call():
    result, client = _call(["not", "an", "object"])

    assert result["call"] is None
    assert result["degraded"] is True
    assert len(client.calls) == 2  # asked once more first


@pytest.mark.parametrize(
    "field, bad",
    [
        ("supporting_analysts", None),  # used to crash the rubric check outside the guard
        ("supporting_analysts", "technical, news, weather, supply_demand"),
        ("dissenting_analysts", ["macro"]),  # not one of the four
        ("thesis", {"text": "t"}),
        ("key_drivers", ["Iowa drought"]),
        ("key_drivers", [{"driver": "Iowa drought", "source": "macro"}]),
        ("invalidation_conditions", "rain"),
    ],
)
def test_a_mistyped_non_core_field_falls_back_to_empty_and_keeps_the_call(field, bad):
    result, client = _call({**FULL_AGREEMENT, field: bad})

    assert result["call"] == "bullish"
    assert result["degraded"] is True
    assert field in result["invalid_fields"]
    assert result[field] in ([], None)
    assert field in client.calls[1]["messages"][-1]["content"]  # the retry named the problem


def test_a_mistyped_field_fixed_on_retry_is_a_clean_call():
    client = ScriptedChatClient([_reply({**FULL_AGREEMENT, "thesis": {"text": "t"}}), _reply(FULL_AGREEMENT)])

    result = run_coordinator(client, commodity="corn", as_of=AS_OF, model="m", analyst_outputs=ANALYST_OUTPUTS)

    assert result == {**FULL_AGREEMENT, "horizon": "1 week", "price_at_call": None, "rubric_violations": [], "degraded": False}


def test_unusable_supporting_analysts_cap_conviction_since_agreement_cant_be_checked():
    result, _ = _call({**FULL_AGREEMENT, "supporting_analysts": None})

    assert result["supporting_analysts"] == []
    assert result["conviction"] == 3
    assert result["conviction_as_proposed"] == 5


def test_an_analyst_both_supporting_and_dissenting_is_invalid():
    result, _ = _call({**SPLIT, "dissenting_analysts": ["technical", "news"]})

    assert set(result["invalid_fields"]) == {"supporting_analysts", "dissenting_analysts"}


def test_counting_a_crashed_analyst_as_support_is_a_rubric_violation():
    outputs = {**ANALYST_OUTPUTS, "news": {"degraded": True, "error": "KeyError: 'x'"}}

    result, client = _call(FULL_AGREEMENT, outputs=outputs)

    assert result["call"] == "bullish"
    assert any("crashed" in v and "news" in v for v in result["rubric_violations"])
    assert '"error"' in client.calls[0]["messages"][0]["content"]  # the prompt says how to treat it
    # The crashed analyst is dropped from the support, which leaves three: conviction 4 at most.
    assert "news" not in result["supporting_analysts"]
    assert result["conviction"] == 4


def test_an_analysts_unparsed_reply_reaches_the_coordinator():
    outputs = {**ANALYST_OUTPUTS, "news": {"headlines": [], "unparsed_reply": {"news": ["UNPARSED-MARKER"]}, "degraded": True}}

    _, client = _call(FULL_AGREEMENT, outputs=outputs)

    assert "UNPARSED-MARKER" in client.calls[0]["messages"][1]["content"]
    assert "unparsed_reply" in client.calls[0]["messages"][0]["content"]
