import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CALL_FIELDS = ("call", "conviction", "thesis", "key_drivers", "supporting_analysts", "dissenting_analysts")


def _coordinator_output(log_path: Path) -> dict | None:
    for line in log_path.read_text().splitlines():
        event = json.loads(line)
        if event["agent"] == "coordinator" and event["event_type"] == "output":
            return event["payload"]
    return None


_AUDIT_FIELDS = ("checkpoint_weeks", "direction_correct", "driver_verdicts", "calibration_note", "thesis_vs_outcome")


def _audits(audit_dir: Path, run_id: str, as_of: datetime) -> list[dict]:
    audits = []
    for path in Path(audit_dir, run_id).glob("*w.json"):
        audit = json.loads(path.read_text())
        if datetime.fromisoformat(audit["graded_at"]) > as_of:
            continue
        audits.append({f: audit[f] for f in _AUDIT_FIELDS})
    return sorted(audits, key=lambda a: a["checkpoint_weeks"])


def read_track_record(log_dir: Path, audit_dir: Path, *, commodity: str, lookback_weeks: int, as_of: datetime) -> list[dict]:
    earliest = as_of - timedelta(weeks=lookback_weeks)
    record = []
    for log_path in sorted(Path(log_dir).glob(f"{commodity}_*.jsonl")):
        triggered = datetime.strptime(log_path.stem.removeprefix(f"{commodity}_"), "%Y-%m-%dT%H-%M-%SZ").replace(
            tzinfo=timezone.utc
        )
        if not earliest <= triggered < as_of:
            continue
        output = _coordinator_output(log_path)
        if output is None:
            continue
        record.append({"run_id": log_path.stem, **{f: output[f] for f in _CALL_FIELDS}, "audits": _audits(audit_dir, log_path.stem, as_of)})
    return record
