import json
from collections import defaultdict
from datetime import datetime, timedelta

from ags.storage import Storage

# The $ come from llmkit (platform price table, priced at each call's start time), stored with the
# token usage of every logged response. There is deliberately no second price table here.


# Tavily credits per tool call, as an upper bound: a basic search is 1, an advanced extract is 2
# per 5 URLs and the agent extracts one URL per call. A call answered from the PIT cache costs
# nothing but is still counted.
_TAVILY_CREDITS = {"search_news": 1, "fetch_document": 2}


def _commodity(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    return "portfolio" if "portfolio/" in key else name.split("_", 1)[0]


def cycle_usage(storage: Storage, keys: list[str]) -> dict:
    """LLM $ and Tavily credits spent by one cycle, from its run logs (`keys`; missing ones skipped)."""
    cost, credits = 0.0, 0
    for key in keys:
        data = storage.get(key)
        for line in (data or b"").decode().splitlines():
            event = json.loads(line)
            payload = event.get("payload") or {}
            if event["event_type"] == "response":
                cost += (payload.get("usage") or {}).get("cost_usd") or 0.0
            elif event["event_type"] == "tool_call" and event["agent"] == "news":
                credits += _TAVILY_CREDITS.get(payload.get("name"), 0)
    return {"cost_usd": cost, "tavily_credits": credits}


def _empty() -> dict:
    return {"total": 0.0, "unpriced": 0, "by_agent": defaultdict(float), "by_commodity": defaultdict(float)}


def _add(window: dict, key: str, event: dict, cost: float | None) -> None:
    if cost is None:
        window["unpriced"] += 1
        return
    window["total"] += cost
    window["by_agent"][event["agent"]] += cost
    window["by_commodity"][_commodity(key)] += cost


def _plain(window: dict) -> dict:
    return {"total": window["total"], "unpriced": window["unpriced"], "by_agent": dict(window["by_agent"]),
            "by_commodity": dict(window["by_commodity"])}


def _responses(storage: Storage, key: str):
    """(event, usage) for every priced-or-unpriced model response in the log at `key`."""
    for line in (storage.get(key) or b"").decode().splitlines():
        event = json.loads(line)
        usage = (event.get("payload") or {}).get("usage") if event["event_type"] == "response" else None
        if usage:
            yield event, usage


def cycle_spend(storage: Storage, keys: list[str]) -> dict:
    """LLM $ of one cycle, by agent and commodity: its run logs (`keys`) and their retried attempts."""
    window = _empty()
    for key in keys:
        for k in [key, *storage.list(f"attempts/{key.removesuffix('.jsonl')}.attempt")]:
            for event, usage in _responses(storage, k):
                _add(window, k, event, usage.get("cost_usd"))
    return _plain(window)


def llm_spend(storage: Storage, *, now: datetime) -> dict:
    """LLM $ from the per-response usage in run logs: the last 24 h and the calendar month to date.

    A response whose model llmkit has no price for (`cost_usd` null or absent) is counted under
    `unpriced` rather than guessed.
    """
    windows = {"24h": _empty(), "mtd": _empty()}
    day_ago = now - timedelta(hours=24)
    # attempts/: the replaced attempts of retried runs, which spent money too (logging/attempts.py).
    for key in storage.list("logs/") + storage.list("portfolio/logs/") + storage.list("attempts/"):
        for event, usage in _responses(storage, key):
            at = datetime.fromisoformat(event["timestamp"])
            member = []
            if at > day_ago and at <= now:
                member.append("24h")
            if (at.year, at.month) == (now.year, now.month) and at <= now:
                member.append("mtd")
            for name in member:
                _add(windows[name], key, event, usage.get("cost_usd"))
    return {k: _plain(v) for k, v in windows.items()}
