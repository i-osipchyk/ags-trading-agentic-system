import json
from datetime import datetime, timezone

from ags.portfolio.stage import COMMODITIES, PortfolioConfig, run_portfolio
from ags.replay.coordinator import _outputs
from ags.portfolio.stores import active_manager_store
from ags.replay.guards import NoMarket, ReadOnly
from ags.replay.plan import cycle_time, official_cycles
from ags.replay.spec import ReplaySpec
from ags.replay.variants import MANAGERS
from ags.storage import Prefixed, Storage

BOOKS = ("manager", "shadow")


def _seed(store: Storage, replay: Storage, spec: ReplaySpec) -> None:
    """Start both replayed books from the official manager's last book before the fork (if any).

    Written as that cycle's file in each replayed book, so the stage resumes from it as it would
    from any previous cycle: same positions, marks, peak equity and breaker state.
    """
    if any(replay.list(f"portfolio/{book}/cycles/") for book in BOOKS):
        return
    before = [c for c in official_cycles(store) if cycle_time(c) < spec.fork_at]
    if not before:
        return
    record = json.loads(store.get(f"portfolio/{active_manager_store(store)}/cycles/{before[-1]}.json"))
    for book in BOOKS:
        replay.put_if_absent(
            f"portfolio/{book}/cycles/{before[-1]}.json", json.dumps({**record, "book": book, "seeded_from": "manager"}).encode()
        )


def replay_portfolio(
    store: Storage, spec: ReplaySpec, cycle_id: str, chat_client, config: PortfolioConfig, *, now: datetime | None = None
) -> dict:
    """Run one official cycle's portfolio stage on the replayed calls, into `replays/{id}/` only.

    Market data is the official cycle's own (its quotes at its `quoted_at`), read-only. Manual
    breaker resets are ignored: a replayed book that trips the breaker stays tripped. A failed
    manager call raises (no book file is written), so a resumed replay runs the cycle again.
    """
    replay = Prefixed(store, f"replays/{spec.replay_id}")
    official = json.loads(store.get(f"portfolio/{active_manager_store(store)}/cycles/{cycle_id}.json"))
    _seed(store, replay, spec)
    stamp = cycle_id.removeprefix("cycle_")
    calls = {c: _outputs(Prefixed(replay, "logs"), f"{c}_{stamp}").get("coordinator") for c in COMMODITIES}
    return run_portfolio(
        cycle_id, calls, replay, as_of=datetime.fromisoformat(official["as_of"]),
        now=now or datetime.now(timezone.utc), chat_client=chat_client, model=spec.manager.model,
        market_client=NoMarket(), config=config, breaker_reset_at=lambda: None,
        data=ReadOnly(Prefixed(store, "data")), quoted_at=datetime.fromisoformat(official["quoted_at"]),
        # The execution quotes the official cycle traded on (after its manager call), read back, never fetched.
        execution_quoted_at=datetime.fromisoformat(official["exec_quoted_at"]) if official.get("exec_quoted_at") else None,
        manager=MANAGERS[spec.manager.variant], hold_on_manager_error=False,
    )
