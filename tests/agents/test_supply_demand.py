import json
from datetime import date

from ags.agents.supply_demand import run_supply_demand_analyst


class FakeChatClient:
    """Stub model client: returns scripted messages in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return self._responses.pop(0)


class FakeUsdaClient:
    def __init__(self, releases_by_symbol=None):
        self._releases = releases_by_symbol or {}
        self.calls = []

    def get_report(self, report, symbol, *, as_of):
        self.calls.append((report, symbol, as_of))
        eligible = [r for r in self._releases.get(symbol, []) if date.fromisoformat(r["release_date"]) <= as_of]
        return max(eligible, key=lambda r: r["release_date"]) if eligible else None


class FailingUsdaClient:
    def get_report(self, report, symbol, *, as_of):
        raise ConnectionError("ESMIS request failed")


CORN_AUG = {"release_date": "2026-08-12", "marketing_year": "2026/27", "ending_stocks": 1900.0, "stocks_to_use": 12.0}
CORN_SEP = {"release_date": "2026-09-11", "marketing_year": "2026/27", "ending_stocks": 2100.0, "stocks_to_use": 13.1}

ANALYST_JSON = {
    "latest_report": {"name": "WASDE", "date": "2026-09-11"},
    "key_figures": [
        {"figure": "ending_stocks", "value": 2100.0, "prior": 1900.0, "revision_direction": "up"},
    ],
    "stocks_to_use": {"value": 13.1, "trend": "loosening"},
}


def _tool_call_message(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}],
    }


def test_run_supply_demand_analyst_returns_schema_conformant_output_for_one_symbol(tmp_path):
    as_of = date(2026, 10, 1)
    usda_client = FakeUsdaClient({"corn": [CORN_AUG, CORN_SEP]})
    chat_client = FakeChatClient(
        responses=[
            _tool_call_message("get_usda_report", {"report": "wasde"}),
            {"role": "assistant", "content": json.dumps(ANALYST_JSON), "tool_calls": None},
        ]
    )

    result = run_supply_demand_analyst(
        tmp_path, chat_client, commodity="corn", as_of=as_of, model="deepseek-chat", usda_client=usda_client
    )

    assert set(result.keys()) == {"latest_report", "key_figures", "stocks_to_use", "degraded"}
    assert result["latest_report"] == ANALYST_JSON["latest_report"]
    assert result["key_figures"] == ANALYST_JSON["key_figures"]
    assert result["stocks_to_use"] == ANALYST_JSON["stocks_to_use"]
    assert result["degraded"] is False
    assert usda_client.calls[0] == ("wasde", "corn", as_of)


def _scripted(content):
    return FakeChatClient(
        responses=[
            _tool_call_message("get_usda_report", {"report": "wasde"}),
            {"role": "assistant", "content": content, "tool_calls": None},
        ]
    )


def test_run_supply_demand_analyst_surfaces_degraded_true_on_a_source_failure_instead_of_aborting(tmp_path):
    result = run_supply_demand_analyst(
        tmp_path,
        _scripted(json.dumps({"latest_report": None, "key_figures": [], "stocks_to_use": None})),
        commodity="corn",
        as_of=date(2026, 10, 1),
        model="deepseek-chat",
        usda_client=FailingUsdaClient(),
    )

    assert result["degraded"] is True


def test_run_supply_demand_analyst_surfaces_degraded_true_when_no_release_exists_as_of_date(tmp_path):
    result = run_supply_demand_analyst(
        tmp_path,
        _scripted(json.dumps({"latest_report": None, "key_figures": [], "stocks_to_use": None})),
        commodity="corn",
        as_of=date(2026, 10, 1),
        model="deepseek-chat",
        usda_client=FakeUsdaClient({"corn": [{**CORN_SEP, "release_date": "2026-10-09"}]}),
    )

    assert result["degraded"] is True


def test_run_supply_demand_analyst_surfaces_degraded_true_when_the_model_returns_unparseable_content(tmp_path):
    result = run_supply_demand_analyst(
        tmp_path,
        FakeChatClient(responses=[{"role": "assistant", "content": "not valid json", "tool_calls": None}] * 2),
        commodity="corn",
        as_of=date(2026, 10, 1),
        model="deepseek-chat",
        usda_client=FakeUsdaClient({"corn": [CORN_SEP]}),
    )

    assert result == {"latest_report": None, "key_figures": [], "stocks_to_use": None, "degraded": True}


COFFEE_JUN = {
    "release_date": "2025-06-25",
    "marketing_year": "2025/26",
    "ending_stocks": 22819.0,
    "stocks_to_use": 13.47,
}


def test_run_supply_demand_analyst_for_coffee_reads_the_coffee_report_even_if_the_model_asks_for_wasde(tmp_path):
    as_of = date(2025, 7, 15)
    usda_client = FakeUsdaClient({"coffee": [COFFEE_JUN]})
    chat_client = FakeChatClient(
        responses=[
            _tool_call_message("get_usda_report", {"report": "wasde"}),
            {"role": "assistant", "content": json.dumps(ANALYST_JSON), "tool_calls": None},
        ]
    )

    result = run_supply_demand_analyst(
        tmp_path, chat_client, commodity="coffee", as_of=as_of, model="deepseek-chat", usda_client=usda_client
    )

    assert usda_client.calls[0] == ("coffee_world_markets", "coffee", as_of)
    assert result["degraded"] is False


def test_run_supply_demand_analyst_retries_once_when_the_first_final_reply_is_not_json(tmp_path):
    usda_client = FakeUsdaClient({"corn": [CORN_AUG, CORN_SEP]})
    chat_client = FakeChatClient(
        responses=[
            _tool_call_message("get_usda_report", {"report": "wasde"}),
            {"role": "assistant", "content": "Sure! Here is the analysis: stocks are tight.", "tool_calls": None},
            {"role": "assistant", "content": json.dumps(ANALYST_JSON), "tool_calls": None},
        ]
    )

    result = run_supply_demand_analyst(
        tmp_path, chat_client, commodity="corn", as_of=date(2026, 10, 1), model="deepseek-chat", usda_client=usda_client
    )

    assert result["latest_report"] == ANALYST_JSON["latest_report"]
    assert result["degraded"] is False
    # The retry continues the same conversation, with no tools on offer.
    assert chat_client.calls[2]["tools"] is None
    assert chat_client.calls[2]["messages"][-2]["content"] == "Sure! Here is the analysis: stocks are tight."


def test_run_supply_demand_analyst_degrades_after_a_single_retry_when_the_reply_is_still_not_json(tmp_path):
    chat_client = FakeChatClient(
        responses=[
            {"role": "assistant", "content": "not json", "tool_calls": None},
            {"role": "assistant", "content": "still not json", "tool_calls": None},
        ]
    )

    result = run_supply_demand_analyst(
        tmp_path, chat_client, commodity="corn", as_of=date(2026, 10, 1), model="deepseek-chat",
        usda_client=FakeUsdaClient(),
    )

    assert result["degraded"] is True
    assert len(chat_client.calls) == 2
