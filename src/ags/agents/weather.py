import json
from datetime import date

from ags.llm.loop import ChatClient, ToolSpec, run_json_loop
from ags.tools.growing_calendar import get_growing_calendar as compute_growing_calendar
from ags.tools.sources.weather import SUPPORTED_REGIONS, WeatherClient
from ags.tools.sources.weather import get_weather as fetch_weather

SYSTEM_PROMPT_TEMPLATE = """You are the Weather/season analyst for {commodity} futures.

Regional conditions matter relative to crop growth stage — a dry spell
before planting is noise, the same dry spell during flowering is a real
threat. Use the get_weather tool to check conditions for the US growing
regions you judge most relevant to {commodity} (supported regions:
{supported_regions}).

Known fact as of {as_of} (computed, not your judgment call):
  stage_of_crop_cycle: {stage_of_crop_cycle}

Respond with a JSON object containing exactly these fields:
  "regions_covered": list of region codes you assessed
  "current_conditions": object mapping each region to a short description
    of observed conditions
  "risk_assessment": object mapping each region to "benign" | "watch" | "severe"
No other text — JSON only."""


def run_weather_analyst(
    data_dir,
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: date,
    model: str,
    weather_client: WeatherClient | None = None,
) -> dict:
    degraded = False
    stage_of_crop_cycle = compute_growing_calendar(commodity, as_of)["stage"]

    def _get_weather_tool(region: str) -> dict:
        nonlocal degraded
        try:
            result = fetch_weather(data_dir, region=region, as_of=as_of, client=weather_client)
            if result["drought"] is None and result["outlook"] is None:
                degraded = True
            return result
        except Exception as exc:
            degraded = True
            return {"error": str(exc)}

    tools = [
        ToolSpec(
            name="get_weather",
            description="Drought monitor conditions and CPC temp/precip outlook for a US region.",
            parameters={
                "type": "object",
                "properties": {"region": {"type": "string", "description": "Two-letter US state code, e.g. 'IA'"}},
                "required": ["region"],
            },
            function=_get_weather_tool,
        ),
    ]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        commodity=commodity,
        as_of=as_of.isoformat(),
        stage_of_crop_cycle=stage_of_crop_cycle,
        supported_regions=", ".join(SUPPORTED_REGIONS),
    )
    user_prompt = f"Assess weather/season conditions for {commodity} as of {as_of.isoformat()}."

    loop_result = run_json_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )

    try:
        parsed = json.loads(loop_result.content)
    except json.JSONDecodeError:
        return {
            "regions_covered": [],
            "current_conditions": {},
            "stage_of_crop_cycle": stage_of_crop_cycle,
            "risk_assessment": {},
            "degraded": True,
        }

    return {
        "regions_covered": parsed.get("regions_covered", []),
        "current_conditions": parsed.get("current_conditions", {}),
        "stage_of_crop_cycle": stage_of_crop_cycle,
        "risk_assessment": parsed.get("risk_assessment", {}),
        "degraded": degraded,
    }
