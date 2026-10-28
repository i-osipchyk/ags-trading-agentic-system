import json

from ags.portfolio.stores import SHADOW, active_manager_store
from ags.storage import Prefixed, Storage

LINES = {
    "official_manager": ("portfolio", "manager"),  # the store in use, see `record`
    "official_shadow": ("portfolio", SHADOW),
    "replay_manager": ("replay", "manager"),
    "replay_shadow": ("replay", "shadow"),
}


def _direction(call: dict | None) -> str | None:
    return (call or {}).get("call")


def report(store: Storage, replay_id: str) -> list[dict]:
    """Per replayed cycle: each line's P&L since the fork, and how many commodity calls differ from the official ones.

    Each line is measured from its own book at the fork (the official books' file for the seed
    cycle; both replayed books start from the official manager's), so the lines are comparable
    even though the official manager and shadow had different equity there.
    """
    roots = {"portfolio": store, "replay": Prefixed(store, f"replays/{replay_id}")}

    def record(line: str, cycle_id: str) -> dict:
        root, book = LINES[line]
        if line == "official_manager":
            book = active_manager_store(store)
        return json.loads(roots[root].get(f"portfolio/{book}/cycles/{cycle_id}.json"))

    replayed = [k.rsplit("/", 1)[1].removesuffix(".json") for k in roots["replay"].list("portfolio/manager/cycles/")]
    seeds = [c for c in replayed if "seeded_from" in record("replay_manager", c)]
    if not seeds:
        raise ValueError(f"replay {replay_id} has no seeded fork cycle to measure from")
    baseline = {line: record(line, seeds[0])["equity"] for line in LINES}

    rows = []
    for cycle_id in replayed:
        if cycle_id in seeds:
            continue
        official_calls = record("official_manager", cycle_id)["calls"]
        replay_calls = record("replay_manager", cycle_id)["calls"]
        rows.append({
            "cycle_id": cycle_id,
            "pnl": {line: record(line, cycle_id)["equity"] - baseline[line] for line in LINES},
            "calls_changed": sum(
                _direction(official_calls.get(c)) != _direction(replay_calls.get(c))
                for c in {*official_calls, *replay_calls}
            ),
        })
    return rows
