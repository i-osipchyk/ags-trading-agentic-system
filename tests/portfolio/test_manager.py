import json
from datetime import datetime

from ags.portfolio.manager import run_portfolio_manager
from ags.tools.pit_store import ET

SYMBOLS = ["corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa"]
AS_OF = datetime(2026, 6, 5, 16, 0, tzinfo=ET)

CALLS = {
    s: {
        "call": "bullish", "conviction": 4, "thesis": f"THESIS-{s}",
        "key_drivers": [{"driver": "d", "source": "weather"}],
        # coordinator extras the manager must NOT be shown:
        "supporting_analysts": ["technical", "news", "weather"], "dissenting_analysts": ["supply_demand"],
        "invalidation_conditions": ["INVALIDATION-MARKER"], "price_at_call": {"close": 1}, "rubric_violations": [],
        "horizon": "1 week", "degraded": False,
    }
    for s in SYMBOLS
} | {"cocoa": None}
LOTS = {s: 0.0 for s in SYMBOLS} | {"corn": 1.5}
RISK = {s: {"atr": 0.1, "atr_usd_per_lot": 50.0, "notional_per_lot": 5000.0} for s in SYMBOLS}

VALID = {
    "legs": [{"symbol": s, "delta_lots": 0.5, "rationale": "r"} for s in SYMBOLS],
    "portfolio_note": "grains treated as one bucket",
}


class FakeChatClient:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        content = self._reply if isinstance(self._reply, str) else json.dumps(self._reply)
        return {"role": "assistant", "content": content, "tool_calls": None}


def _run(reply):
    client = FakeChatClient(reply)
    result = run_portfolio_manager(
        client, as_of=AS_OF, model="stub", calls=CALLS, lots=LOTS, risk=RISK, equity=10_000.0, free_margin=8_500.0
    )
    return result, client


def test_a_valid_proposal_is_returned_as_parsed():
    result, _ = _run(VALID)

    assert result["proposal"] == VALID
    assert result["degraded"] is False


def test_unparseable_output_yields_no_proposal_so_guardrail_one_holds_everything():
    result, _ = _run("I think we should buy corn")

    assert result["proposal"] is None
    assert result["degraded"] is True
    assert result["raw"] == "I think we should buy corn"


def test_a_structurally_bad_proposal_is_passed_through_for_the_guardrail_schema_check():
    bad = {"legs": [{"symbol": "corn", "delta_lots": "lots", "rationale": "r"}], "portfolio_note": "n"}

    result, _ = _run(bad)

    assert result["proposal"] == bad


def test_the_prompt_has_the_allowed_inputs_and_nothing_else():
    _, client = _run(VALID)
    prompt = "\n".join(m["content"] for m in client.calls[0]["messages"])

    for expected in ("THESIS-corn", '"corn": 1.5', "10000", "8500", "atr_usd_per_lot"):
        assert expected in prompt
    for forbidden in ("INVALIDATION-MARKER", "supporting_analysts", "price_at_call", "notional_per_lot", "track record"):
        assert forbidden not in prompt


def test_a_failed_commodity_is_shown_as_null_and_no_tools_are_offered():
    _, client = _run(VALID)
    prompt = client.calls[0]["messages"][1]["content"]

    assert '"cocoa": null' in prompt
    assert client.calls[0]["tools"] is None
