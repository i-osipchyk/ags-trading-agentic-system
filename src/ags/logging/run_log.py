import json
from datetime import datetime, timezone
from pathlib import Path


class RunLog:
    def __init__(self, log_dir: Path, run_id: str) -> None:
        self._path = Path(log_dir) / f"{run_id}.jsonl"
        self._next_seq = len(self.read()) if self._path.exists() else 0

    def append(self, *, agent: str, event_type: str, payload: dict) -> None:
        event = {
            "seq": self._next_seq,
            "agent": agent,
            "event_type": event_type,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._path.open("a") as f:
            f.write(json.dumps(event) + "\n")
        self._next_seq += 1

    def read(self) -> list[dict]:
        with self._path.open("r") as f:
            return [json.loads(line) for line in f]
