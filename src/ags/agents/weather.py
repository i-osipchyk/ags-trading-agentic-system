from datetime import datetime

from ags.agents.output import list_of, of, parse_reply
from ags.llm.loop import ChatClient, ToolSpec, run_json_loop
from ags.tools.growing_calendar import get_growing_calendar as compute_growing_calendar
from ags.tools.sources.weather import WeatherClient, regions_for
from ags.tools.sources.weather import get_weather as fetch_weather

SYSTEM_PROMPT_TEMPLATE = """You are the Weather/season analyst for {commodity} futures.

Regional conditions matter relative to crop growth stage — a dry spell
before planting is noise, the same dry spell during flowering is a real
threat. Use the get_weather tool to check conditions for the growing
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


_REPLY_FIELDS = {"regions_covered": list_of(str), "current_conditions": of(dict), "risk_assessment": of(dict)}


def run_weather_analyst(
    data_dir,
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: datetime,
    model: str,
    weather_client: WeatherClient | None = None,
) -> dict:
    degraded = False
    stage_of_crop_cycle = compute_growing_calendar(commodity, as_of)["stage"]

    # Raises on an unsupported region or a source failure: the loop reports it and asks the
    # model to retry. A region with no data at all is a gap the model can't fix.
    def _get_weather_tool(region: str) -> dict:
        nonlocal degraded
        if region not in regions_for(commodity):
            raise ValueError(f"region {region!r} is not one of {commodity}'s regions: {', '.join(regions_for(commodity))}")
        result = fetch_weather(data_dir, region=region, as_of=as_of, client=weather_client)
        if all(result.get(part) is None for part in ("drought", "outlook", "forecast")):
            degraded = True
        return result

    tools = [
        ToolSpec(
            name="get_weather",
            description="Drought monitor and CPC outlook for a US state, or recent and forecast precipitation and temperature for a non-US growing region.",
            parameters={
                "type": "object",
                "properties": {"region": {"type": "string", "description": "A supported region code, e.g. 'IA' or 'BR-MG'"}},
                "required": ["region"],
            },
            function=_get_weather_tool,
        ),
    ]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        commodity=commodity,
        as_of=as_of.isoformat(),
        stage_of_crop_cycle=stage_of_crop_cycle,
        supported_regions=", ".join(regions_for(commodity)),
    )
    user_prompt = f"Assess weather/season conditions for {commodity} as of {as_of.isoformat()}."

    loop_result = run_json_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )

    parsed, complete, unparsed = parse_reply(loop_result.content, _REPLY_FIELDS)

    return {
        "regions_covered": parsed.get("regions_covered", []),
        "current_conditions": parsed.get("current_conditions", {}),
        "stage_of_crop_cycle": stage_of_crop_cycle,
        "risk_assessment": parsed.get("risk_assessment", {}),
        **unparsed,
        "degraded": degraded or not complete or loop_result.tool_errors > 0,
    }
