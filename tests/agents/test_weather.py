import json
from datetime import date, datetime

import pytest

from ags.agents.weather import run_weather_analyst
from ags.tools.pit_store import ET


class FakeChatClient:
    """Stub model client: returns scripted messages in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return self._responses.pop(0)


class FakeWeatherClient:
    def __init__(self, by_region=None):
        self._by_region = by_region or {}
        self.drought_calls = []
        self.outlook_calls = []

    def get_drought_conditions(self, region, *, as_of):
        self.drought_calls.append((region, as_of))
        return self._by_region.get(region, {}).get("drought")

    def get_outlook(self, region, *, as_of):
        self.outlook_calls.append((region, as_of))
        return self._by_region.get(region, {}).get("outlook")


class FailingWeatherClient:
    def get_drought_conditions(self, region, *, as_of):
        raise ConnectionError("USDM request failed")

    def get_outlook(self, region, *, as_of):
        raise ConnectionError("CPC request failed")


IOWA = {
    "drought": {
        "map_date": "2026-09-29",
        "none_sq_mi": 51520.52,
        "d0_sq_mi": 4790.98,
        "d1_sq_mi": 21.07,
        "d2_sq_mi": 0.0,
        "d3_sq_mi": 0.0,
        "d4_sq_mi": 0.0,
    },
    "outlook": {
        "issued": "2026-09-30",
        "valid_period": "Oct 2026",
        "temp": {"category": "Above", "probability": 40.0},
        "precip": {"category": "EC", "probability": 33.0},
    },
}


def _tool_call_message(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}],
    }


def test_run_weather_analyst_returns_schema_conformant_output_for_one_symbol(tmp_path):
    as_of = datetime(2026, 10, 1, 16, 0, tzinfo=ET)
    weather_client = FakeWeatherClient(by_region={"IA": IOWA})

    chat_client = FakeChatClient(
        responses=[
            _tool_call_message("get_weather", {"region": "IA"}),
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "regions_covered": ["IA"],
                        "current_conditions": {"IA": "Mostly drought-free with minor abnormal dryness in the west."},
                        "risk_assessment": {"IA": "benign"},
                    }
                ),
                "tool_calls": None,
            },
        ]
    )

    result = run_weather_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        weather_client=weather_client,
    )

    assert set(result.keys()) == {
        "regions_covered",
        "current_conditions",
        "stage_of_crop_cycle",
        "risk_assessment",
        "degraded",
    }
    assert result["regions_covered"] == ["IA"]
    assert result["current_conditions"] == {"IA": "Mostly drought-free with minor abnormal dryness in the west."}
    # October is harvest for corn per the growing calendar — code-computed,
    # not something the LLM was asked for.
    assert result["stage_of_crop_cycle"] == "harvest"
    assert result["risk_assessment"] == {"IA": "benign"}
    assert result["degraded"] is False
    assert weather_client.drought_calls == [("IA", as_of)]


def test_run_weather_analyst_surfaces_degraded_true_on_a_source_failure_instead_of_aborting(tmp_path):
    as_of = datetime(2026, 10, 1, 16, 0, tzinfo=ET)

    chat_client = FakeChatClient(
        responses=[
            _tool_call_message("get_weather", {"region": "IA"}),
            {
                "role": "assistant",
                "content": json.dumps({"regions_covered": [], "current_conditions": {}, "risk_assessment": {}}),
                "tool_calls": None,
            },
        ]
    )

    result = run_weather_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        weather_client=FailingWeatherClient(),
    )

    assert result["degraded"] is True
    # The growing-calendar fact is code-computed and independent of any
    # source failure, so it must still surface even when weather degrades.
    assert result["stage_of_crop_cycle"] == "harvest"


def test_run_weather_analyst_surfaces_degraded_true_when_the_model_returns_unparseable_content(tmp_path):
    as_of = datetime(2026, 10, 1, 16, 0, tzinfo=ET)

    chat_client = FakeChatClient(
        responses=[{"role": "assistant", "content": "not valid json", "tool_calls": None}] * 2
    )

    result = run_weather_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        weather_client=FakeWeatherClient(by_region={"IA": IOWA}),
    )

    assert result == {
        "regions_covered": [],
        "current_conditions": {},
        "stage_of_crop_cycle": "harvest",
        "risk_assessment": {},
        "unparsed_reply": "not valid json",  # handed to the coordinator as is
        "degraded": True,
    }


def _final_reply() -> dict:
    return {
        "role": "assistant",
        "content": json.dumps({"regions_covered": [], "current_conditions": {}, "risk_assessment": {}}),
        "tool_calls": None,
    }


def test_run_weather_analyst_offers_coffee_only_its_growing_regions(tmp_path):
    chat_client = FakeChatClient(responses=[_final_reply()])

    run_weather_analyst(
        tmp_path,
        chat_client,
        commodity="coffee",
        as_of=datetime(2026, 10, 30, 10, 0, tzinfo=ET),
        model="deepseek-chat",
        weather_client=FakeWeatherClient(),
    )

    system_prompt = chat_client.calls[0]["messages"][0]["content"]
    # Brazil (Minas Gerais, Sao Paulo, Espirito Santo), Vietnam (Dak Lak), Colombia (Huila): no US states.
    assert "BR-ES, BR-MG, BR-SP, CO-HU, VN-DL" in system_prompt
    assert "IA" not in system_prompt.split("supported regions:")[1].split(")")[0]


def test_run_weather_analyst_rejects_a_region_outside_the_commodity_s_list_without_fetching(tmp_path):
    weather_client = FakeWeatherClient(by_region={"IA": IOWA})
    chat_client = FakeChatClient(responses=[_tool_call_message("get_weather", {"region": "IA"}), _final_reply()])

    result = run_weather_analyst(
        tmp_path,
        chat_client,
        commodity="coffee",
        as_of=datetime(2026, 10, 30, 10, 0, tzinfo=ET),
        model="deepseek-chat",
        weather_client=weather_client,
    )

    assert weather_client.drought_calls == []
    assert result["degraded"] is True  # the model's tool call failed


@pytest.mark.parametrize(
    ("commodity", "expected"),
    [
        # Brazil Center-South cane, India (Maharashtra, Karnataka), Thailand.
        ("sugar", "BR-CS, IN-KA, IN-MH, TH"),
        # Ivory Coast and Ghana.
        ("cocoa", "CI, GH"),
        # The US cotton belt plus Brazil (Mato Grosso), China (Xinjiang) and India (Gujarat).
        ("cotton", "AL, AR, BR-MT, CN-XJ, GA, IN-GJ, LA, MO, MS, NC, SC, TN, TX"),
    ],
)
def test_run_weather_analyst_offers_each_commodity_its_own_growing_regions(tmp_path, commodity, expected):
    chat_client = FakeChatClient(responses=[_final_reply()])

    run_weather_analyst(
        tmp_path,
        chat_client,
        commodity=commodity,
        as_of=datetime(2026, 10, 30, 10, 0, tzinfo=ET),
        model="deepseek-chat",
        weather_client=FakeWeatherClient(),
    )

    assert f"supported regions:\n{expected})" in chat_client.calls[0]["messages"][0]["content"]
