"""CLI: replay only the shadow book over the logged calls, from a flat account of a chosen size.

Local and read-only on the official record: the calls, quotes, specs and bars come from the local
mirror (`scripts/sync_local.sh`), and everything written goes to replays/{replay_id}/. No model
call, no order, no AWS write; the only network use is the M5 market-data fetch below. The manager stage is stubbed to hold everything, so the manager book
in the replay is meaningless; read `replays/{replay_id}/portfolio/shadow/cycles/`.

Fills are priced from M5 bars, not the stored quotes (some were captured with another symbol's tick).
For each cycle's decision and execution instants the quote is the open of the M5 bar containing that
instant (so nothing after it is used), as the bid, plus the symbol's median spread from its good stored
quotes. The bars are fetched once from cTrader (market data only, no orders) and cached under
replays/{replay_id}/m5/.

Usage: uv run python scripts/run_shadow_replay.py [--equity 100000] [--replay-id shadow-100k]
"""

import argparse
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ags.config import load_env
from ags.portfolio.stage import COMMODITIES, run_portfolio
from ags.replay.guards import NoMarket, ReadOnly
from ags.replay.shadow_prices import (
    MAX_STALE, SyntheticQuotes, fetch_m5, hold_all_manager, live_config, median_spreads,
)
from ags.storage import LocalStorage, Prefixed

ROOT = Path(__file__).parent.parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Replay the shadow book from a flat account.")
    parser.add_argument("--equity", type=float, default=100_000)
    parser.add_argument("--replay-id", default="shadow-100k")
    args = parser.parse_args(argv)
    load_env(ROOT / ".env")

    official = LocalStorage(ROOT)
    out_dir = ROOT / "replays" / args.replay_id
    replay = LocalStorage(out_dir)
    config = replace(live_config(), starting_equity=args.equity)
    cycles = sorted(
        k.rsplit("/", 1)[1].removesuffix(".json")
        for k in official.list("portfolio/shadow/cycles/") if k.endswith(".json")
    )
    print(f"{len(cycles)} cycles, starting equity {args.equity:,.0f}")
    records = {c: json.loads(official.get(f"portfolio/shadow/cycles/{c}.json")) for c in cycles}
    instants = sorted({
        datetime.fromisoformat(r[k]) for r in records.values() for k in ("quoted_at", "exec_quoted_at") if r.get(k)
    })
    bars = fetch_m5(out_dir / "m5", instants[0] - MAX_STALE - timedelta(days=1), instants[-1] + timedelta(minutes=10))
    data = SyntheticQuotes(
        ReadOnly(Prefixed(official, "data")), bars, median_spreads(official), instants
    )
    print(f"{len(data.closed_market)} of {len(instants) * len(COMMODITIES)} quotes priced at a closed market's last bar close")
    # A day ahead, so a cycle from today still reads the stored specs instead of treating itself as live.
    now = datetime.now(timezone.utc) + timedelta(days=1)
    for cycle_id in cycles:
        if replay.get(f"portfolio/shadow/cycles/{cycle_id}.json"):
            continue  # resume
        rec = records[cycle_id]
        exec_at = rec.get("exec_quoted_at")
        run_portfolio(
            cycle_id, rec["calls"], replay, as_of=datetime.fromisoformat(rec["as_of"]), chat_client=None,
            now=now, model="none", market_client=NoMarket(), config=config, data=data,
            quoted_at=datetime.fromisoformat(rec["quoted_at"]),
            execution_quoted_at=datetime.fromisoformat(exec_at) if exec_at else None,
            manager=hold_all_manager, hold_on_manager_error=False,
        )
    last = sorted(replay.list("portfolio/shadow/cycles/"))[-1]
    final = json.loads(replay.get(last))
    print(f"closing equity {final['equity']:,.2f}; lots {final['closing_lots']}")
    print(f"replay in {out_dir}")


if __name__ == "__main__":
    main()
