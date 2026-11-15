"""The shadow book run on the challenger's calls, to compare its equity curve with the live one (ROADMAP.md Step 1).

The shadow book is the fixed mechanical sizer (portfolio/shadow.py) behind the deterministic guardrails, so two books
that differ only in their calls differ only because of the calls. Both are replayed from a flat account through the
same stage and the same M5-priced fills (shadow_prices.py), into their own folder, never the official record.

The challenger has no call for some commodity-cycles (coffee, sugar and cocoa before 2026-11-02, whose inputs cannot
be reproduced; any cycle at an ad hoc time). `policy` says what the two books do there:

- `fallback`: the challenger book uses the live call where the challenger has none, so both books trade all seven
  commodities all the time and differ only where the challenger made a call.
- `matched`: neither book trades a commodity-cycle the challenger has no call for (it is held), so the two books
  hold the same universe and any difference is the calls alone.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ags.portfolio.stage import COMMODITIES, PortfolioConfig, run_portfolio
from ags.replay.guards import NoMarket
from ags.replay.shadow_prices import hold_all_manager
from ags.storage import LocalStorage, Storage

POLICIES = ("fallback", "matched")


def challenger_output(logs: Path, commodity: str, stamp: str) -> dict | None:
    """The challenger's coordinator output for one commodity-cycle, when it made a call."""
    path = Path(logs) / f"{commodity}_{stamp}.jsonl"
    if not path.exists():
        return None
    outputs = [
        e["payload"] for line in path.read_text().splitlines() if line
        if (e := json.loads(line))["agent"] == "coordinator" and e["event_type"] == "output"
    ]
    return outputs[-1] if outputs and outputs[-1].get("call") else None


def book_calls(policy: str, official: dict, challenger: dict[str, dict | None]) -> tuple[dict, dict]:
    """(the live book's calls, the challenger book's calls) for one cycle under `policy`."""
    if policy == "fallback":
        return dict(official), {c: challenger.get(c) or official.get(c) for c in COMMODITIES}
    if policy == "matched":
        return ({c: official.get(c) if challenger.get(c) else None for c in COMMODITIES},
                {c: challenger.get(c) for c in COMMODITIES})
    raise ValueError(f"policy {policy!r} is not one of {POLICIES}")


def run_book(
    records: dict[str, dict], calls: dict[str, dict], store: Storage, *, config: PortfolioConfig, data: Storage,
) -> list[dict]:
    """Replay the shadow book over the cycles in order, from a flat account; returns [{as_of, equity, ...}] per cycle.

    The manager stage is stubbed to hold everything, so only `portfolio/shadow/cycles/` of `store` is meaningful.
    Resumes: a cycle already in the store is not run again.
    """
    # A day ahead, so a recent cycle reads the stored specs instead of treating itself as live.
    now = datetime.now(timezone.utc) + timedelta(days=1)
    series = []
    for cycle_id in sorted(records):
        key = f"portfolio/shadow/cycles/{cycle_id}.json"
        if store.get(key) is None:
            rec = records[cycle_id]
            exec_at = rec.get("exec_quoted_at")
            run_portfolio(
                cycle_id, calls[cycle_id], store, as_of=datetime.fromisoformat(rec["as_of"]), chat_client=None,
                now=now, model="none", market_client=NoMarket(), config=config, data=data,
                quoted_at=datetime.fromisoformat(rec["quoted_at"]),
                execution_quoted_at=datetime.fromisoformat(exec_at) if exec_at else None,
                manager=hold_all_manager, hold_on_manager_error=False,
            )
        record = json.loads(store.get(key))
        series.append({
            "as_of": record["as_of"], "opening_equity": record["opening_equity"], "equity": record["equity"],
            "closing_lots": record["closing_lots"], "failed": record["failed_commodities"],
        })
    return series


def max_drawdown(equity: list[float]) -> tuple[float, float]:
    """(largest peak-to-trough fall in money, as a share of that peak)."""
    peak, worst, worst_pct = equity[0], 0.0, 0.0
    for value in equity:
        peak = max(peak, value)
        worst, worst_pct = max(worst, peak - value), max(worst_pct, (peak - value) / peak)
    return worst, worst_pct


__all__ = ["POLICIES", "LocalStorage", "book_calls", "challenger_output", "max_drawdown", "run_book"]
