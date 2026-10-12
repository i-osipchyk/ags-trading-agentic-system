import json
from datetime import datetime

from ags.notify.spend import llm_spend
from ags.storage import Storage
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _ranked(by: dict[str, float]) -> str:
    return ", ".join(f"{k} {_money(v)}" for k, v in sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))


def _latest(storage: Storage, book: str) -> dict | None:
    keys = storage.list(f"portfolio/{book}/cycles/")
    return json.loads(storage.get(keys[-1])) if keys else None


def _positions(lots: dict) -> str:
    held = [f"{c} {lots[c]:+.1f}" for c in CTRADER_SYMBOL_NAMES if lots.get(c)]
    return ", ".join(held) or "flat"


def _book_line(label: str, record: dict) -> str:
    return (f"{label}: equity {_money(record['equity'])} · free margin {_money(record['free_margin'])}"
            f" · {_positions(record['closing_lots'])}")


def _spend_section(storage: Storage, now: datetime, aws_cost) -> list[str]:
    spend = llm_spend(storage, now=now)
    day = spend["24h"]
    lines = ["<b>Spend</b>", f"LLM 24h: {_money(day['total'])} · MTD: {_money(spend['mtd']['total'])}"]
    if spend["mtd"]["unpriced"]:
        lines.append(f"Unpriced LLM calls (MTD, model missing from llmkit's price table): {spend['mtd']['unpriced']}")
    if day["by_agent"]:
        lines += [f"By agent (24h): {_ranked(day['by_agent'])}", f"By commodity (24h): {_ranked(day['by_commodity'])}"]
    try:
        label, amount = aws_cost()
        lines.append(f"AWS ({label}): {_money(amount)}")
    except Exception:
        lines.append("AWS: unavailable")
    return lines


def _book_section(storage: Storage) -> list[str]:
    manager, shadow = _latest(storage, "manager"), _latest(storage, "shadow")
    if manager is None or shadow is None:
        return ["<b>Book</b>", "No scheduled cycle yet"]
    diff = manager["equity"] - shadow["equity"]
    sign = "+" if diff >= 0 else "−"
    return [
        f"<b>Book</b> ({manager['cycle_id']})", _book_line("Manager", manager), _book_line("Shadow", shadow),
        f"Manager − shadow: {sign}{_money(abs(diff))}",
    ]


def _calls_section(storage: Storage) -> list[str]:
    manager = _latest(storage, "manager")
    if manager is None:
        return []
    lines = ["<b>Latest calls</b>"]
    for commodity in CTRADER_SYMBOL_NAMES:
        call = manager["calls"].get(commodity)
        lines.append(f"{commodity}: {call['call']} ({call['conviction']})" if call else f"{commodity}: failed")
    return lines


def compose_digest(storage: Storage, *, now: datetime, aws_cost) -> str:
    sections = [
        [f"<b>AGS digest · {now.date().isoformat()}</b>"],
        _spend_section(storage, now, aws_cost),
        _book_section(storage),
        _calls_section(storage),
    ]
    return "\n\n".join("\n".join(s) for s in sections if s)
