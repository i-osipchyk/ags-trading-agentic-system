from datetime import datetime, timezone

from ags.portfolio.stores import active_manager_store
from ags.replay.spec import ReplaySpec
from ags.storage import Storage

_CYCLE_FORMAT = "cycle_%Y-%m-%dT%H-%M-%SZ"


def cycle_time(cycle_id: str) -> datetime:
    return datetime.strptime(cycle_id, _CYCLE_FORMAT).replace(tzinfo=timezone.utc)


def _manager_cycles(store: Storage, prefix: str) -> list[str]:
    return [
        key.rsplit("/", 1)[1].removesuffix(".json")
        for key in store.list(f"{prefix}/cycles/")
        if key.endswith(".json")
    ]


def official_cycles(store: Storage) -> list[str]:
    """Official cycles with a manager book file (the demo manager's store once it has records).

    An outage cycle wrote none, so it never appears.
    """
    return sorted(_manager_cycles(store, f"portfolio/{active_manager_store(store)}"))


def pending_cycles(store: Storage, spec: ReplaySpec) -> list[str]:
    """Official cycles from the fork on that this replay hasn't run yet, oldest first (books chain)."""
    done = set(_manager_cycles(store, f"replays/{spec.replay_id}/portfolio/manager"))
    return [c for c in official_cycles(store) if cycle_time(c) >= spec.fork_at and c not in done]
