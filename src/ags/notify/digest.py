import json
from datetime import datetime

from ags.logging.run_id import generate_run_id
from ags.notify.spend import cycle_spend, llm_spend
from ags.storage import Storage
from ags.tools.pit_store import ET
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES


# cTrader volume is in 1/100 of a unit (portfolio/book.py).
_UNITS_PER_VOLUME = 1 / 100


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _signed_money(value: float) -> str:
    """+$1.00 / −$1.00 / $0.00: a change, with the sign always shown."""
    if round(value, 2) == 0:
        return _money(0)
    return f"{'+' if value > 0 else '−'}{_money(abs(value))}"


def _recent(storage: Storage, book: str) -> tuple[dict | None, dict | None]:
    """(latest, previous) cycle records of a book; None where there aren't that many."""
    records = [json.loads(storage.get(k)) for k in storage.list(f"portfolio/{book}/cycles/")[-2:]]
    return (records[-1] if records else None), (records[0] if len(records) == 2 else None)


def _latest(storage: Storage, book: str) -> dict | None:
    return _recent(storage, book)[0]


def _change(current: float, previous: float | None) -> str:
    return "" if previous is None else f" ({_signed_money(current - previous)})"


def _values(record: dict) -> dict[str, float]:
    """USD value of each held commodity at the cycle's marks; a short counts gross."""
    state = record["closing_state"]
    return {p["symbol_name"]: p["volume"] * _UNITS_PER_VOLUME * state["marks"][p["symbol_name"]]
            for p in state["positions"]}


def _lots(value: float) -> str:
    """+2.0 / −0.5 / 0."""
    if round(value, 1) == 0:
        return "0"
    return f"{'+' if value > 0 else '−'}{abs(value):.1f}"


def _commodity_line(commodity: str, record: dict, previous: dict | None) -> str:
    lots, value = record["closing_lots"][commodity], _values(record).get(commodity, 0.0)
    line = f"- {CTRADER_SYMBOL_NAMES[commodity]} {_lots(lots)}"
    if previous is not None:
        line += f" ({_lots(lots - previous['closing_lots'][commodity])})"
    line += f" | {_money(value)}"
    if previous is not None:
        line += f" ({_signed_money(value - _values(previous).get(commodity, 0.0))})"
    return line


def _book_block(label: str, record: dict, previous: dict | None) -> list[str]:
    margin_pct = record["used_margin"] / record["equity"] * 100 if record["equity"] else 0.0
    return [
        f"{label}:",
        f"- Equity {_money(record['equity'])}{_change(record['equity'], previous and previous['equity'])}",
        f"- Positions value {_money(sum(_values(record).values()))}",
        f"- Margin {_money(record['used_margin'])} ({margin_pct:.1f}%)",
        "By commodity:",
        *(_commodity_line(c, record, previous) for c in CTRADER_SYMBOL_NAMES),
    ]


def _h2(title: str) -> str:
    return f"<b><u>{title}</u></b>"


def _items(by: dict[str, float]) -> list[str]:
    return [f"- {k} {_money(v)}" for k, v in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))]


def _cycle_log_keys(record: dict) -> list[str]:
    as_of = datetime.fromisoformat(record["as_of"])
    runs = [f"logs/{generate_run_id(c, as_of)}.jsonl" for c in CTRADER_SYMBOL_NAMES]
    return [*runs, f"portfolio/logs/{record['cycle_id']}.jsonl"]


def _spend_section(storage: Storage, now: datetime) -> list[str]:
    mtd = llm_spend(storage, now=now)["mtd"]
    manager = _latest(storage, "manager")
    lines = [_h2("Spend")]
    if manager is not None:
        cycle = cycle_spend(storage, _cycle_log_keys(manager))
        lines.append(f"This cycle: {_money(cycle['total'])}")
    lines.append(f"Month to date: {_money(mtd['total'])}")
    if mtd["unpriced"]:
        lines.append(f"Unpriced LLM calls (MTD, model missing from llmkit's price table): {mtd['unpriced']}")
    if manager is not None and cycle["by_agent"]:
        lines += ["By agent:", *_items(cycle["by_agent"]), "By commodity:", *_items(cycle["by_commodity"])]
    return lines


def _book_section(storage: Storage) -> list[str]:
    (manager, manager_prev), (shadow, shadow_prev) = _recent(storage, "manager"), _recent(storage, "shadow")
    if manager is None or shadow is None:
        return [_h2("Book"), "No scheduled cycle yet"]
    gap = manager["equity"] - shadow["equity"]
    previous_gap = manager_prev["equity"] - shadow_prev["equity"] if manager_prev and shadow_prev else None
    return [
        _h2("Book"), *_book_block("Manager", manager, manager_prev), "",
        *_book_block("Shadow", shadow, shadow_prev), "",
        f"Manager − shadow: {_signed_money(gap)}{_change(gap, previous_gap)}",
    ]


def _call(record: dict, commodity: str) -> str:
    call = record["calls"].get(commodity)
    return f"{call['call']} {call['conviction']}" if call else "failed"


def _calls_section(storage: Storage) -> list[str]:
    manager, previous = _recent(storage, "manager")
    if manager is None:
        return []
    lines = [_h2("Latest calls")]
    for commodity, name in CTRADER_SYMBOL_NAMES.items():
        was = f" (was {_call(previous, commodity)})" if previous else ""
        lines.append(f"- {name}: {_call(manager, commodity)}{was}")
    return lines


def compose_digest(storage: Storage, *, now: datetime) -> str:
    sections = [
        [f"<b>AGS Digest · {now.astimezone(ET):%Y-%m-%d %H:%M} ET</b>"],
        _spend_section(storage, now),
        _book_section(storage),
        _calls_section(storage),
    ]
    return "\n\n".join("\n".join(s) for s in sections if s)
