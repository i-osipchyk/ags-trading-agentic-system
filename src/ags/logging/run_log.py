import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ags.storage import Storage, as_storage


class RunLog:
    def __init__(self, log_dir: Storage | Path, run_id: str, *, buffered: bool = False, fresh: bool = False) -> None:
        self._store = as_storage(log_dir)
        # Buffered: events go to a local temp file and are uploaded once, by
        # close() — for backends (S3) where a rewrite per event is a PUT per event.
        self._buffer = tempfile.NamedTemporaryFile("w+", prefix="runlog-", suffix=".jsonl") if buffered else None
        self._key = f"{run_id}.jsonl"
        # Fresh: start empty and replace what's there (a retry, after attempts.archive kept it).
        existing = None if fresh else self._store.get(self._key)
        self._lines = existing.decode().splitlines() if existing is not None else []
        self._next_seq = len(self._lines)
        if self._buffer is not None and self._lines:
            self._buffer.write("\n".join(self._lines) + "\n")

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
        self._next_seq += 1
        if self._buffer is not None:
            self._buffer.write(self._lines[-1] + "\n")
            self._buffer.flush()
            return
        self._store.put(self._key, ("\n".join(self._lines) + "\n").encode())

    def close(self) -> None:
        if self._buffer is None:
            return
        self._buffer.seek(0)
        data = self._buffer.read().encode()
        self._buffer.close()
        self._buffer = None
        if data:
            self._store.put(self._key, data)

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def read(self) -> list[dict]:
        data = self._store.get(self._key)
        if data is None:
            raise FileNotFoundError(self._key)
        return [json.loads(line) for line in data.decode().splitlines()]
