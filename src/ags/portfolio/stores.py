"""Where each book's cycle files live: `portfolio/<store>/cycles/<cycle_id>.json`.

The paper manager and the shadow keep the original `manager` and `shadow` stores. The manager trading
the demo account has its own, `demo/manager`, so it starts from scratch and never reads paper records.
"""

SHADOW = "shadow"
PAPER_MANAGER = "manager"
DEMO_MANAGER = "demo/manager"


def active_manager_store(storage) -> str:
    """For tools with no config (the UI, replays): the demo manager's store once it has records, else the paper one."""
    return DEMO_MANAGER if storage.list(f"portfolio/{DEMO_MANAGER}/cycles/") else PAPER_MANAGER


def manager_store(manager_book: str) -> str:
    """The store of the manager that is active under `MANAGER_BOOK` (`paper` or `demo`)."""
    return DEMO_MANAGER if (manager_book or "paper").lower() == "demo" else PAPER_MANAGER
