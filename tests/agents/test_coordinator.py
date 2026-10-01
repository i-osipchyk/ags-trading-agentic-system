import json
from datetime import date

from ags.agents.coordinator import run_coordinator

AS_OF = date(2026, 6, 5)

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


def test_conviction_5_with_dissent_is_flagged_but_the_models_conviction_is_kept():
    result, _ = _call({**SPLIT, "conviction": 5})

    assert result["conviction"] == 5
    assert len(result["rubric_violations"]) == 1
    assert "5" in result["rubric_violations"][0]


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


def test_out_of_range_conviction_or_unknown_call_degrades():
    bad_conviction, _ = _call({**FULL_AGREEMENT, "conviction": 7})
    bad_call, _ = _call({**FULL_AGREEMENT, "call": "very bullish"})
    missing_field = {k: v for k, v in FULL_AGREEMENT.items() if k != "thesis"}
    incomplete, _ = _call(missing_field)

    for result in (bad_conviction, bad_call, incomplete):
        assert result["degraded"] is True
        assert result["call"] is None


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
