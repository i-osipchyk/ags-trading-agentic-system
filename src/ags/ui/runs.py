import json
from datetime import date, datetime, timedelta
from pathlib import Path

from ags.ui.jobs import cadence
from ags.logging.run_log import RunLog
from ags.agents.auditor import CHECKPOINT_WEEKS, _triggered_at


def _cell(audit_dir: Path, run_id: str, triggered: datetime, weeks: int, now: datetime) -> dict:
    audit_path = Path(audit_dir) / run_id / f"{weeks}w.json"
    if audit_path.exists():
        return {"state": "graded", "direction_correct": json.loads(audit_path.read_text()).get("direction_correct")}
    return {"state": "due" if triggered + timedelta(weeks=weeks) <= now else "pending"}


def _coordinator_output(log_dir: Path, run_id: str) -> dict:
    outputs = [e["payload"] for e in RunLog(log_dir, run_id).read() if e["agent"] == "coordinator" and e["event_type"] == "output"]
    return outputs[-1] if outputs else {}


def build_grid(
    log_dir: Path,
    audit_dir: Path,
    *,
    now: datetime,
    commodities: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict]:
    rows = []
    for log_path in sorted(Path(log_dir).glob("*.jsonl")):
        run_id = log_path.stem
        triggered = _triggered_at(run_id)
        if triggered is None:
            continue
        commodity = run_id.rsplit("_", 1)[0]
        if commodities is not None and commodity not in commodities:
            continue
        if (start is not None and triggered.date() < start) or (end is not None and triggered.date() > end):
            continue
        output = _coordinator_output(log_dir, run_id)
        call = output.get("call")
        if call is None:
            cells = {weeks: {"state": "no_call"} for weeks in CHECKPOINT_WEEKS}
        else:
            cells = {weeks: _cell(audit_dir, run_id, triggered, weeks, now) for weeks in CHECKPOINT_WEEKS}
        rows.append(
            {
                "run_id": run_id,
                "commodity": commodity,
                "triggered_at": triggered,
                "off_cadence": cadence(triggered) != "friday",
                "call": call,
                "error": output.get("error"),
                "cells": cells,
            }
        )
    return sorted(rows, key=lambda r: r["triggered_at"], reverse=True)


def format_run_id(run_id: str) -> str:
    triggered = _triggered_at(run_id)
    if triggered is None:
        return run_id
    return f"{run_id.rsplit('_', 1)[0].capitalize()} {triggered:%Y-%m-%d %H:%M} UTC"
