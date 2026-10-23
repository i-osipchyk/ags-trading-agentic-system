import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ags.storage import Storage, as_storage

_CALL_FIELDS = ("call", "conviction", "thesis", "key_drivers", "supporting_analysts", "dissenting_analysts")


def _coordinator_output(log_store: Storage, run_id: str) -> dict | None:
    for line in log_store.get(f"{run_id}.jsonl").decode().splitlines():
        event = json.loads(line)
        if event["agent"] == "coordinator" and event["event_type"] == "output":
            return event["payload"]
    return None


_AUDIT_FIELDS = ("direction_correct", "driver_verdicts", "calibration_note", "thesis_vs_outcome")


def _horizon_days(audit: dict) -> int:
    return audit["checkpoint_days"] if "checkpoint_days" in audit else audit["checkpoint_weeks"] * 7


def _audits(audit_store: Storage, run_id: str, as_of: datetime) -> list[dict]:
    audits = []
    for key in audit_store.list(f"{run_id}/"):
        if not key.endswith(("w.json", "d.json")):
            continue
        audit = json.loads(audit_store.get(key))
        if datetime.fromisoformat(audit["graded_at"]) > as_of:
            continue
        checkpoint = "checkpoint_days" if "checkpoint_days" in audit else "checkpoint_weeks"
        audits.append({checkpoint: audit[checkpoint], **{f: audit[f] for f in _AUDIT_FIELDS}})
    return sorted(audits, key=_horizon_days)


def read_track_record(log_dir: Storage | Path, audit_dir: Storage | Path, *, commodity: str, lookback_weeks: int, as_of: datetime) -> list[dict]:
    log_store, audit_store = as_storage(log_dir), as_storage(audit_dir)
    earliest = as_of - timedelta(weeks=lookback_weeks)
    record = []
    for key in log_store.list(f"{commodity}_"):
        if "/" in key or not key.endswith(".jsonl"):
            continue
        run_id = key.removesuffix(".jsonl")
        triggered = datetime.strptime(run_id.removeprefix(f"{commodity}_"), "%Y-%m-%dT%H-%M-%SZ").replace(
            tzinfo=timezone.utc
        )
        if not earliest <= triggered < as_of:
            continue
        output = _coordinator_output(log_store, run_id)
        # A failed coordinator logs a degraded output with no call (and possibly none of the
        # other fields); there is nothing to review, same as the auditor skips it.
        if output is None or output.get("call") is None:
            continue
        record.append({"run_id": run_id, **{f: output[f] for f in _CALL_FIELDS}, "audits": _audits(audit_store, run_id, as_of)})
    return record
