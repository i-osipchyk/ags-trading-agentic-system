import json
from collections import defaultdict
from datetime import datetime, timedelta

from ags.storage import Storage

# The $ come from llmkit (platform price table, priced at each call's start time), stored with the
# token usage of every logged response. There is deliberately no second price table here.


def _commodity(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    return "portfolio" if key.startswith("portfolio/") else name.split("_", 1)[0]


def _empty() -> dict:
    return {"total": 0.0, "unpriced": 0, "by_agent": defaultdict(float), "by_commodity": defaultdict(float)}


def llm_spend(storage: Storage, *, now: datetime) -> dict:
    """LLM $ from the per-response usage in run logs: the last 24 h and the calendar month to date.

    A response whose model llmkit has no price for (`cost_usd` null or absent) is counted under
    `unpriced` rather than guessed.
    """
    windows = {"24h": _empty(), "mtd": _empty()}
    day_ago = now - timedelta(hours=24)
    for key in storage.list("logs/") + storage.list("portfolio/logs/"):
        for line in storage.get(key).decode().splitlines():
            event = json.loads(line)
            usage = (event.get("payload") or {}).get("usage") if event["event_type"] == "response" else None
            if not usage:
                continue
            at = datetime.fromisoformat(event["timestamp"])
            cost = usage.get("cost_usd")
            member = []
            if at > day_ago and at <= now:
                member.append("24h")
            if (at.year, at.month) == (now.year, now.month) and at <= now:
                member.append("mtd")
            for name in member:
                w = windows[name]
                if cost is None:
                    w["unpriced"] += 1
                    continue
                w["total"] += cost
                w["by_agent"][event["agent"]] += cost
                w["by_commodity"][_commodity(key)] += cost
    return {k: {"total": v["total"], "unpriced": v["unpriced"], "by_agent": dict(v["by_agent"]),
                "by_commodity": dict(v["by_commodity"])}
            for k, v in windows.items()}
