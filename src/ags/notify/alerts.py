import json

from ags.portfolio.stores import PAPER_MANAGER
from ags.storage import Storage


def _recent_cycles(storage: Storage, book: str, count: int) -> list[dict]:
    keys = storage.list(f"portfolio/{book}/cycles/")[-count:]
    return [json.loads(storage.get(k)) for k in keys]


def consecutive_failures(storage: Storage, *, cycles: int = 2, manager_store: str = PAPER_MANAGER) -> list[str]:
    """Commodities whose pipeline failed in each of the last `cycles` scheduled cycles."""
    recent = _recent_cycles(storage, manager_store, cycles)
    if len(recent) < cycles:
        return []
    return sorted(set.intersection(*(set(c["failed_commodities"]) for c in recent)))


def breaker_newly_tripped(storage: Storage, manager_store: str = PAPER_MANAGER) -> bool:
    recent = _recent_cycles(storage, manager_store, 2)
    if not recent or not recent[-1]["breaker_active"]:
        return False
    return len(recent) == 1 or not recent[0]["breaker_active"]
