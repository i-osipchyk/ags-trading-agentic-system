import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ags.ui.jobs import JobRunning, cadence, produced_run_ids, current_job, launch, needs_confirmation, start_job


@pytest.mark.parametrize(
    "when, expected",
    [
        (datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc), "friday"),
        (datetime(2026, 10, 1, 18, 30, tzinfo=timezone.utc), "off_cadence"),  # Thursday
        (datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc), "off_cadence"),  # Monday
        (datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc), "weekend"),
        (datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc), "weekend"),
        # Saturday in UTC but still Friday evening in New York.
        (datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc), "friday"),
        # Monday in UTC but still Sunday night in New York.
        (datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc), "weekend"),
    ],
)
def test_cadence_is_decided_by_the_weekday_in_new_york(when, expected):
    assert cadence(when) == expected


FRIDAY = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)
THURSDAY = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("action", ["run_all", "audit_all"])
def test_bulk_actions_always_need_confirmation(action):
    assert needs_confirmation(action, FRIDAY) is True


def test_single_runs_need_confirmation_only_on_weekends():
    assert needs_confirmation("run_one", FRIDAY) is False
    assert needs_confirmation("run_one", THURSDAY) is False
    assert needs_confirmation("run_one", SATURDAY) is True


def test_a_single_audit_never_needs_confirmation_even_on_weekends():
    assert needs_confirmation("audit_one", SATURDAY) is False



T0 = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)


def _fake_launcher(pid=4242, output="", exit_code=None):
    calls = []

    def launcher(commands, output_path, exit_path):
        calls.append(commands)
        output_path.write_text(output)
        if exit_code is not None:
            exit_path.write_text(str(exit_code))
        return pid

    launcher.calls = calls
    return launcher


def test_no_job_has_ever_run(tmp_path):
    assert current_job(tmp_path) is None


def test_a_started_job_is_running_while_its_pid_is_alive_and_blocks_a_second_start(tmp_path):
    start_job(tmp_path, [["run"]], label="corn", launcher=_fake_launcher(), now=T0, pid_alive=lambda pid: True)

    job = current_job(tmp_path, pid_alive=lambda pid: True)
    assert job["status"] == "running" and job["label"] == "corn" and job["started_at"] == T0.isoformat()

    second = _fake_launcher()
    with pytest.raises(JobRunning):
        start_job(tmp_path, [["run"]], label="wheat", launcher=second, now=T0, pid_alive=lambda pid: True)
    assert second.calls == []


def test_a_dead_pid_is_a_stale_lock_that_no_longer_blocks(tmp_path):
    start_job(tmp_path, [["run"]], label="corn", launcher=_fake_launcher(), now=T0, pid_alive=lambda pid: True)

    second = _fake_launcher()
    start_job(tmp_path, [["run"]], label="wheat", launcher=second, now=T0, pid_alive=lambda pid: False)

    assert second.calls == [[["run"]]]
    assert current_job(tmp_path, pid_alive=lambda pid: True)["label"] == "wheat"


@pytest.mark.parametrize("exit_code, status", [(0, "done"), (1, "failed"), (None, "failed")])
def test_a_finished_job_is_done_or_failed_by_its_exit_code(tmp_path, exit_code, status):
    start_job(tmp_path, [["run"]], label="corn", launcher=_fake_launcher(exit_code=exit_code), now=T0)

    assert current_job(tmp_path, pid_alive=lambda pid: False)["status"] == status


def test_current_job_returns_only_the_last_lines_of_output(tmp_path):
    output = "".join(f"line {i}\n" for i in range(50))
    start_job(tmp_path, [["run"]], label="corn", launcher=_fake_launcher(output=output), now=T0)

    tail = current_job(tmp_path, tail_lines=20)["output_tail"]

    assert tail.splitlines() == [f"line {i}" for i in range(30, 50)]


def test_the_real_launcher_runs_commands_in_sequence_and_records_output_and_exit_code(tmp_path):
    start_job(tmp_path, [["echo", "first"], ["sh", "-c", "echo second; exit 3"], ["echo", "third"]], label="x", launcher=launch, now=T0)

    for _ in range(100):
        job = current_job(tmp_path)
        if job["status"] != "running":
            break
        time.sleep(0.05)

    assert job["status"] == "failed"  # one command failed, but the later ones still ran
    assert job["output_tail"].split() == ["first", "second", "third"]


from ags.ui.jobs import COMMODITIES, audit_commands, pipeline_commands


def _args(commands):
    # Drop the interpreter and reduce the script to its file name.
    return [[Path(c[1]).name, *c[2:]] for c in commands]


def test_the_universe_is_the_seven_fixed_commodities():
    assert COMMODITIES == ("corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa")


def test_pipeline_commands_run_the_cli_once_per_commodity_with_no_as_of_override():
    commands = pipeline_commands(["corn", "wheat"])

    assert _args(commands) == [["run_pipeline.py", "--commodity", "corn"], ["run_pipeline.py", "--commodity", "wheat"]]


def test_audit_commands_grade_everything_due_or_one_checkpoint():
    assert _args(audit_commands()) == [["run_auditor.py"]]
    assert _args(audit_commands(run_id="corn_2026-10-01T18-30-00Z", weeks=1)) == [
        ["run_auditor.py", "--run-id", "corn_2026-10-01T18-30-00Z", "--weeks", "1"]
    ]


def test_produced_run_ids_are_read_from_the_pipeline_output_in_order():
    output = "run_id: corn_2026-10-02T15-40-41Z\n\n== call ==\n- **Call:** bearish\nrun_id: wheat_2026-10-02T15-50-00Z\n"

    assert produced_run_ids(output) == ["corn_2026-10-02T15-40-41Z", "wheat_2026-10-02T15-50-00Z"]


def test_output_without_run_ids_produces_none():
    assert produced_run_ids("graded corn_2026-10-02T15-40-41Z 1w\n1 graded, 0 failed") == []
