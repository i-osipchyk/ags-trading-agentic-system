import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ags.agents.output import list_of, parse_reply
from ags.llm.loop import ChatClient, ToolSpec, run_json_loop
from ags.storage import Storage, as_storage
from ags.tools.pit_store import ET
from ags.tools.sources.news import NewsClient
from ags.tools.sources.news import fetch_document as fetch_document_pit
from ags.tools.sources.news import search_news as fetch_search_news

SYSTEM_PROMPT_TEMPLATE = """You are the News analyst for {commodity} futures.

Report what's being said, not why it matters — no macro or technical
commentary of your own. Use the search_news tool to find recent headlines
(as of {as_of}) and fetch_document to pull full article text when a
headline alone isn't enough to judge its direction.

Respond with a JSON object containing exactly this field:
  "headlines": a deduped list of objects, each with:
    "headline": the article title
    "source": where it's from
    "date": the article's publish date (ISO format)
    "direction": "bullish" | "bearish" | "neutral"
    "reason": one-line reason for that direction call
No other text — JSON only."""

# Tavily is searched only on the evening cycle (16:00 ET); the 04:00 cycle carries that output
# forward, which halves search credits. Any run at or after noon ET counts as evening.
NEWS_FETCH_FROM_HOUR_ET = 12
# Friday 16:00 -> Monday 04:00 is 60 h; older than this, the slot is left empty and degraded.
MAX_CARRY_AGE = timedelta(days=4)


def fetches_news(as_of: datetime) -> bool:
    return as_of.astimezone(ET).hour >= NEWS_FETCH_FROM_HOUR_ET


def carry_forward_news(log_dir: Storage | Path, *, commodity: str, before: datetime) -> dict:
    """The newest fetched News output for `commodity` from a run triggered before `before`.

    `before` bounds the search so the result is point-in-time safe. Carried outputs are never
    carried again: each one points at the run that actually searched, via `source_run_id`.
    """
    log_store = as_storage(log_dir)
    for key in reversed(log_store.list(f"{commodity}_")):
        if "/" in key or not key.endswith(".jsonl"):
            continue
        run_id = key.removesuffix(".jsonl")
        triggered = datetime.strptime(run_id.removeprefix(f"{commodity}_"), "%Y-%m-%dT%H-%M-%SZ").replace(
            tzinfo=timezone.utc
        )
        if triggered >= before:
            continue
        if before - triggered > MAX_CARRY_AGE:
            break
        output = _news_output(log_store, key)
        if output is not None and not output.get("carried_forward"):
            return {
                "headlines": output.get("headlines", []),
                "degraded": bool(output.get("degraded")),
                "carried_forward": True,
                "source_run_id": run_id,
            }
    return {"headlines": [], "degraded": True, "carried_forward": True, "source_run_id": None}


def _news_output(log_store: Storage, key: str) -> dict | None:
    for line in reversed(log_store.get(key).decode().splitlines()):
        event = json.loads(line)
        if event["agent"] == "news" and event["event_type"] == "output":
            return event["payload"]
    return None


def run_news_analyst(
    data_dir,
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: datetime,
    model: str,
    news_client: NewsClient | None = None,
) -> dict:
    degraded = False

    def _search_news_tool(query: str) -> dict:
        nonlocal degraded
        try:
            results = fetch_search_news(data_dir, commodity=commodity, query=query, as_of=as_of, client=news_client)
            return {"results": results}
        except Exception as exc:
            degraded = True
            return {"error": str(exc)}

    def _fetch_document_tool(url: str) -> dict:
        nonlocal degraded
        try:
            return fetch_document_pit(data_dir, url=url, as_of=as_of, client=news_client)
        except Exception as exc:
            degraded = True
            return {"error": str(exc)}

    tools = [
        ToolSpec(
            name="search_news",
            description="Web search for recent headlines, filtered to items published on or before as_of.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query, e.g. 'corn futures news'"}},
                "required": ["query"],
            },
            function=_search_news_tool,
        ),
        ToolSpec(
            name="fetch_document",
            description="Full text of a news article, given its URL.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            function=_fetch_document_tool,
        ),
    ]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(commodity=commodity, as_of=as_of.isoformat())
    user_prompt = f"Find recent news for {commodity} as of {as_of.isoformat()}."

    loop_result = run_json_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )

    # Observed against the real model: once the tool-call cap forces a
    # tools-disabled final call, it can still emit its own pseudo-tool-call
    # markup as plain content instead of the requested JSON. A malformed
    # final answer degrades the run rather than crashing the pipeline.
    parsed, complete = parse_reply(loop_result.content, {"headlines": list_of(dict)})

    return {
        "headlines": parsed.get("headlines", []),
        "degraded": degraded or not complete or loop_result.tool_errors > 0,
    }
