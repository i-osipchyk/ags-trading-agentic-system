import json
from datetime import date

from ags.llm.loop import ChatClient, ToolSpec, run_loop
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


def run_news_analyst(
    data_dir,
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: date,
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

    loop_result = run_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )

    try:
        parsed = json.loads(loop_result.content)
    except json.JSONDecodeError:
        # Observed against the real model: once the tool-call cap forces a
        # tools-disabled final call, it can still emit its own pseudo-tool-call
        # markup as plain content instead of the requested JSON. A malformed
        # final answer degrades the run rather than crashing the pipeline.
        return {"headlines": [], "degraded": True}

    return {
        "headlines": parsed.get("headlines", []),
        "degraded": degraded,
    }
