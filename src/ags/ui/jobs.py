import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES

_ET = ZoneInfo("America/New_York")
_ROOT = Path(__file__).resolve().parents[3]

COMMODITIES = tuple(CTRADER_SYMBOL_NAMES)


def cadence(when: datetime) -> str:
    weekday = when.astimezone(_ET).weekday()
    if weekday == 4:
        return "friday"
    return "weekend" if weekday >= 5 else "off_cadence"


_BULK_ACTIONS = {"run_all", "audit_all"}


def needs_confirmation(action: str, when: datetime) -> bool:
    if action in _BULK_ACTIONS:
        return True
    # Audits grade past runs against stored prices, so the weekday doesn't matter to them.
    return action == "run_one" and cadence(when) == "weekend"


class JobRunning(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def launch(commands: list[list[str]], output_path: Path, exit_path: Path) -> int:
    """Run commands one after another in a detached shell; a failure doesn't skip the rest."""
    script = "rc=0; " + "".join(f"{shlex.join(c)} || rc=1; " for c in commands) + f"echo $rc > {shlex.quote(str(exit_path))}"
    with output_path.open("wb") as out:
        process = subprocess.Popen(["sh", "-c", script], stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    return process.pid


def start_job(
    jobs_dir: Path,
    commands: list[list[str]],
    *,
    label: str,
    now: datetime,
    launcher: Callable[[list[list[str]], Path, Path], int] = launch,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> None:
    jobs_dir = Path(jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    existing = current_job(jobs_dir, pid_alive=pid_alive)
    if existing is not None and existing["status"] == "running":
        raise JobRunning(existing["label"])
    output_path, exit_path = jobs_dir / "output.log", jobs_dir / "exit_code"
    exit_path.unlink(missing_ok=True)
    pid = launcher(commands, output_path, exit_path)
    (jobs_dir / "current.json").write_text(json.dumps({"pid": pid, "label": label, "started_at": now.isoformat()}))


def current_job(
    jobs_dir: Path, *, pid_alive: Callable[[int], bool] = _pid_alive, tail_lines: int = 20
) -> dict | None:
    jobs_dir = Path(jobs_dir)
    record_path = jobs_dir / "current.json"
    if not record_path.exists():
        return None
    record = json.loads(record_path.read_text())
    exit_path, output_path = jobs_dir / "exit_code", jobs_dir / "output.log"
    if pid_alive(record["pid"]) and not exit_path.exists():
        status = "running"
    else:
        status = "done" if exit_path.exists() and exit_path.read_text().strip() == "0" else "failed"
    lines = output_path.read_text(errors="replace").splitlines() if output_path.exists() else []
    return {**record, "status": status, "output_tail": "\n".join(lines[-tail_lines:])}


def pipeline_commands(commodities: list[str]) -> list[list[str]]:
    # No --as-of: UI runs are always "today", so backtests stay a deliberate CLI-only act.
    return [[sys.executable, str(_ROOT / "run_pipeline.py"), "--commodity", c] for c in commodities]


def audit_commands(*, run_id: str | None = None, weeks: int | None = None) -> list[list[str]]:
    command = [sys.executable, str(_ROOT / "run_auditor.py")]
    if run_id is not None:
        command += ["--run-id", run_id, "--weeks", str(weeks)]
    return [command]


def produced_run_ids(output: str) -> list[str]:
    """Run ids a pipeline job reported, from the `run_id: ...` lines run_pipeline.py prints."""
    return [line.split(":", 1)[1].strip() for line in output.splitlines() if line.startswith("run_id:")]
