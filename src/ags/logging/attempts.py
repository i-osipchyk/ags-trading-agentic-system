"""Earlier attempts of a retried run, kept beside the official record.

A retry (ARCHITECTURE.md § Retrying a held cycle) replaces a run's log at its official key, so
every reader that scans `logs/` or `portfolio/logs/` sees one attempt per run. The attempt it
replaces is copied first to `attempts/<key minus .jsonl>.attempt<n>.jsonl`, out of those scans
but still in the record: nothing logged is lost.
"""

from ags.storage import Storage

PREFIX = "attempts/"


def archive(storage: Storage, key: str) -> str | None:
    """Copy the log at `key` to the next free attempt number; None when there's nothing there."""
    data = storage.get(key)
    if data is None:
        return None
    stem = key.removesuffix(".jsonl")
    n = len(storage.list(f"{PREFIX}{stem}.attempt")) + 1
    target = f"{PREFIX}{stem}.attempt{n}.jsonl"
    storage.put_if_absent(target, data)
    return target
