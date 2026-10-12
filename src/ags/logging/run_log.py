import json
from datetime import datetime, timezone
from pathlib import Path

from ags.storage import Storage, as_storage


class RunLog:
    def __init__(self, log_dir: Storage | Path, run_id: str) -> None:
        self._store = as_storage(log_dir)
        self._key = f"{run_id}.jsonl"
        existing = self._store.get(self._key)
        self._lines = existing.decode().splitlines() if existing is not None else []
        self._next_seq = len(self._lines)

    def append(self, *, agent: str, event_type: str, payload: dict) -> None:
        event = {
            "seq": self._next_seq,
            "agent": agent,
            "event_type": event_type,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # The Storage protocol has no append, so each event rewrites the log;
        # persisting per event keeps a partial log if the run dies mid-way.
        self._lines.append(json.dumps(event))
        self._store.put(self._key, ("\n".join(self._lines) + "\n").encode())
        self._next_seq += 1

    def read(self) -> list[dict]:
        data = self._store.get(self._key)
        if data is None:
            raise FileNotFoundError(self._key)
        return [json.loads(line) for line in data.decode().splitlines()]
