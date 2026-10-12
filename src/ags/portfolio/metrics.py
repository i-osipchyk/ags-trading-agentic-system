import json

from ags.storage import Storage


def _cycles(storage: Storage, book: str) -> list[dict]:
    return [json.loads(storage.get(k)) for k in storage.list(f"portfolio/{book}/cycles/")]


def equity_curve(storage: Storage, book: str) -> list[tuple[str, float]]:
    """(as_of, closing equity) for every cycle file of a book, oldest first."""
    return [(c["as_of"], c["equity"]) for c in _cycles(storage, book)]


def manager_vs_shadow(storage: Storage) -> list[dict]:
    """Per cycle, manager equity minus shadow equity: the value of the LLM's sizing."""
    shadow = {c["cycle_id"]: c for c in _cycles(storage, "shadow")}
    return [
        {
            "cycle_id": m["cycle_id"], "as_of": m["as_of"], "manager": m["equity"],
            "shadow": shadow[m["cycle_id"]]["equity"], "diff": m["equity"] - shadow[m["cycle_id"]]["equity"],
        }
        for m in _cycles(storage, "manager")
        if m["cycle_id"] in shadow
    ]
