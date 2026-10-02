import json
from datetime import date

from ags.llm.loop import ChatClient, ToolSpec, run_json_loop
from ags.tools.sources.usda import EsmisClient, UsdaClient
from ags.tools.sources.usda import get_balance_sheet_revisions as fetch_revisions
from ags.tools.sources.usda import get_usda_report as fetch_usda_report
from ags.tools.sources.usda import report_for_symbol

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


def run_supply_demand_analyst(
    data_dir,
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: date,
    model: str,
    usda_client: UsdaClient | None = None,
) -> dict:
    degraded = False
    if usda_client is None:
        usda_client = EsmisClient()

    def _get_usda_report_tool(report: str) -> dict:
        nonlocal degraded
        try:
            result = fetch_usda_report(data_dir, report=report_for_symbol(commodity), symbol=commodity, as_of=as_of, client=usda_client)
            if result["latest"] is None:
                degraded = True
            return result
        except Exception as exc:
            degraded = True
            return {"error": str(exc)}

    def _get_revisions_tool() -> dict:
        nonlocal degraded
        try:
            return fetch_revisions(data_dir, symbol=commodity, as_of=as_of, client=usda_client)
        except Exception as exc:
            degraded = True
            return {"error": str(exc)}

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

    try:
        parsed = json.loads(loop_result.content)
    except json.JSONDecodeError:
        return {"latest_report": None, "key_figures": [], "stocks_to_use": None, "degraded": True}

    return {
        "latest_report": parsed.get("latest_report"),
        "key_figures": parsed.get("key_figures", []),
        "stocks_to_use": parsed.get("stocks_to_use"),
        "degraded": degraded,
    }
