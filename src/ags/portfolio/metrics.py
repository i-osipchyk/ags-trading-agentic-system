import json

from ags.portfolio.stores import PAPER_MANAGER, SHADOW
from ags.storage import Storage


def _cycles(storage: Storage, store: str) -> list[dict]:
    return [json.loads(storage.get(k)) for k in storage.list(f"portfolio/{store}/cycles/")]


def equity_curve(storage: Storage, store: str) -> list[tuple[str, float]]:
    """(as_of, closing equity) for every cycle file of a store, oldest first."""
    return [(c["as_of"], c["equity"]) for c in _cycles(storage, store)]


def manager_vs_shadow(storage: Storage, manager_store: str = PAPER_MANAGER) -> list[dict]:
    """Per cycle, how much more the manager made than the shadow since the manager's first cycle.

    Both are measured as equity change from that first cycle's opening equity, so a manager that started later
    (or from its own capital, like the demo one) is compared with the shadow over the same stretch.
    """
    shadow = {c["cycle_id"]: c for c in _cycles(storage, SHADOW)}
    rows, base = [], None
    for m in _cycles(storage, manager_store):
        s = shadow.get(m["cycle_id"])
        if s is None:
            continue
        base = base or (m["opening_equity"], s["opening_equity"])
        gain, shadow_gain = m["equity"] - base[0], s["equity"] - base[1]
        rows.append({
            "cycle_id": m["cycle_id"], "as_of": m["as_of"], "manager": m["equity"], "shadow": s["equity"],
            "manager_gain": gain, "shadow_gain": shadow_gain, "diff": gain - shadow_gain,
        })
    return rows
