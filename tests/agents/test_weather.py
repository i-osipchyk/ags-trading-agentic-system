import json
from datetime import date

from ags.agents.weather import run_weather_analyst


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
    as_of = date(2026, 10, 1)
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
    as_of = date(2026, 10, 1)

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
    as_of = date(2026, 10, 1)

    chat_client = FakeChatClient(
        responses=[{"role": "assistant", "content": "not valid json", "tool_calls": None}]
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
        "degraded": True,
    }
