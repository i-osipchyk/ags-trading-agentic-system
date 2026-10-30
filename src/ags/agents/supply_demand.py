import json
from datetime import datetime

from ags.agents.output import list_of, of, parse_reply
from ags.llm.loop import ChatClient, ToolSpec, run_json_loop
from ags.tools.sources.grinds import GrindsClient, get_grinds
from ags.tools.sources.usda import EsmisClient, UsdaClient, report_for_symbol
from ags.tools.sources.usda import get_balance_sheet_revisions as fetch_revisions
from ags.tools.sources.usda import get_usda_report as fetch_usda_report

SYSTEM_PROMPT_TEMPLATE = """You are the Supply/demand analyst for {commodity} futures.

You read structured USDA releases only — no news, no commentary. Use the
get_usda_report tool for the latest release (WASDE; for coffee, the FAS
"Coffee: World Markets and Trade" report) and its change from the prior
release, and get_balance_sheet_revisions for the month-over-month change in
ending stocks and stocks-to-use. The as-of date ({as_of}) is fixed; you
cannot see anything released after it.

Respond with a JSON object containing exactly these fields:
  "latest_report": {{"name": report name, "date": release date (YYYY-MM-DD)}}
  "key_figures": list of {{"figure", "value", "prior", "revision_direction"}}
    where revision_direction is "up" | "down" | "unchanged"
  "stocks_to_use": {{"value": current stocks-to-use, "trend": "tightening" | "loosening"}}
No other text — JSON only."""


# The USDA publishes no cocoa balance sheet; its demand side is the regional quarterly grind releases.
COCOA_PROMPT_TEMPLATE = """You are the Supply/demand analyst for cocoa futures.

You read structured official releases only — no news, no commentary. The USDA publishes no cocoa balance
sheet, so your evidence is the quarterly grind (bean processing) release of each of the two regional
associations: Europe (ECA) and Asia (CAA). Grinding is the demand side of the balance:
a year-on-year fall in grinds signals weak demand for beans. The as-of date ({as_of}) is fixed; you cannot
see anything released after it.

Known facts (the latest release of each region, computed by code; yoy_pct is the change against the same
quarter a year earlier, tonnes is null where the release gives no tonnage, a null region is unavailable):
{grinds}

Respond with a JSON object containing exactly these fields:
  "latest_report": {{"name": what the releases are, "date": newest publication date (YYYY-MM-DD)}}, or null
  "key_figures": list of {{"figure", "value", "prior", "revision_direction"}}, one per available region, where
    value is the region's grind in tonnes (its yoy_pct when tonnes is null), prior is the same quarter a year
    earlier if known else null, and revision_direction is "up" | "down" | "unchanged" for the year-on-year change
  "stocks_to_use": null (there is no balance sheet)
No other text — JSON only."""


# Null is a legitimate answer for the report and stocks-to-use when nothing has been released yet.
_REPLY_FIELDS = {
    "latest_report": of(dict, type(None)),
    "key_figures": list_of(dict),
    "stocks_to_use": of(dict, type(None)),
}


def run_supply_demand_analyst(
    data_dir,
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: datetime,
    model: str,
    usda_client: UsdaClient | None = None,
    grinds_client: GrindsClient | None = None,
) -> dict:
    degraded = False
    demand_indicators = None
    if commodity == "cocoa":
        if grinds_client is None:
            degraded = True
        else:
            demand_indicators = get_grinds(data_dir, as_of=as_of, client=grinds_client)
            degraded = any(release is None for release in demand_indicators.values())
        system_prompt = COCOA_PROMPT_TEMPLATE.format(as_of=as_of.isoformat(), grinds=json.dumps(demand_indicators))
        loop_result = run_json_loop(
            chat_client, model=model, system_prompt=system_prompt, user_prompt=f"Assess supply/demand for cocoa as of {as_of.isoformat()}.", tools=[]
        )
        parsed, complete, unparsed = parse_reply(loop_result.content, _REPLY_FIELDS)
        return {
            "latest_report": parsed.get("latest_report"),
            "key_figures": parsed.get("key_figures", []),
            "stocks_to_use": parsed.get("stocks_to_use"),
            "demand_indicators": demand_indicators,
            **unparsed,
            "degraded": degraded or not complete or loop_result.tool_errors > 0,
        }

    if usda_client is None:
        usda_client = EsmisClient()

    # Both raise on a source failure: the loop reports it and asks the model to retry. No
    # release as of the date is a gap the model can't fix.
    def _get_usda_report_tool(report: str) -> dict:
        nonlocal degraded
        result = fetch_usda_report(data_dir, report=report_for_symbol(commodity), symbol=commodity, as_of=as_of, client=usda_client)
        if result["latest"] is None:
            degraded = True
        return result

    def _get_revisions_tool() -> dict:
        return fetch_revisions(data_dir, symbol=commodity, as_of=as_of, client=usda_client)

    tools = [
        ToolSpec(
            name="get_usda_report",
            description="Latest USDA report as released at the as-of date, plus change from the prior release.",
            parameters={
                "type": "object",
                "properties": {"report": {"type": "string", "description": "Report name, e.g. 'wasde'"}},
                "required": ["report"],
            },
            function=_get_usda_report_tool,
        ),
        ToolSpec(
            name="get_balance_sheet_revisions",
            description="Month-over-month change in ending stocks and stocks-to-use for this commodity.",
            parameters={"type": "object", "properties": {}},
            function=_get_revisions_tool,
        ),
    ]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(commodity=commodity, as_of=as_of.isoformat())
    user_prompt = f"Assess supply/demand for {commodity} as of {as_of.isoformat()}."

    loop_result = run_json_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )

    parsed, complete, unparsed = parse_reply(loop_result.content, _REPLY_FIELDS)

    return {
        "latest_report": parsed.get("latest_report"),
        "key_figures": parsed.get("key_figures", []),
        "stocks_to_use": parsed.get("stocks_to_use"),
        "demand_indicators": demand_indicators,
        **unparsed,
        "degraded": degraded or not complete or loop_result.tool_errors > 0,
    }
