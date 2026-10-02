import json
from datetime import date, datetime, timezone

from ags.ui.runs import build_grid, format_run_id

RUN = "corn_2026-10-01T18-30-00Z"  # a Thursday


def _write_run(log_dir, run_id, call="bullish"):
    output = {"call": call, "conviction": 4, "thesis": "t", "key_drivers": []}
    events = [{"seq": 0, "agent": "coordinator", "event_type": "output", "payload": output}]
    (log_dir / f"{run_id}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def _write_audit(audit_dir, run_id, weeks, direction_correct=True):
    (audit_dir / run_id).mkdir(parents=True, exist_ok=True)
    (audit_dir / run_id / f"{weeks}w.json").write_text(
        json.dumps({"checkpoint_weeks": weeks, "direction_correct": direction_correct})
    )


def _dirs(tmp_path):
    log_dir, audit_dir = tmp_path / "logs", tmp_path / "audits"
    log_dir.mkdir()
    audit_dir.mkdir()
    return log_dir, audit_dir


def _states(row):
    return [row["cells"][weeks]["state"] for weeks in (1, 2, 4, 8)]


def test_a_fresh_run_has_every_checkpoint_pending(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    _write_run(log_dir, RUN)

    [row] = build_grid(log_dir, audit_dir, now=datetime(2026, 10, 5, tzinfo=timezone.utc))

    assert row["run_id"] == RUN
    assert row["commodity"] == "corn"
    assert _states(row) == ["pending"] * 4


def test_a_checkpoint_turns_due_once_its_lag_elapses_and_stays_due_until_graded(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    _write_run(log_dir, RUN)

    before = build_grid(log_dir, audit_dir, now=datetime(2026, 10, 8, 18, 29, tzinfo=timezone.utc))
    at = build_grid(log_dir, audit_dir, now=datetime(2026, 10, 8, 18, 30, tzinfo=timezone.utc))

    assert _states(before[0]) == ["pending"] * 4
    assert _states(at[0]) == ["due", "pending", "pending", "pending"]


def test_a_graded_checkpoint_is_graded_with_its_direction_result_and_the_rest_are_unchanged(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    _write_run(log_dir, RUN)
    _write_audit(audit_dir, RUN, 1, direction_correct=False)
    now = datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc)  # 1w and 2w elapsed

    [row] = build_grid(log_dir, audit_dir, now=now)

    assert _states(row) == ["graded", "due", "pending", "pending"]
    assert row["cells"][1]["direction_correct"] is False


def test_a_run_without_a_call_has_no_call_cells_even_when_checkpoints_have_elapsed(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    errored = "wheat_2026-08-07T18-30-00Z"
    event = {"seq": 0, "agent": "coordinator", "event_type": "output", "payload": {"call": None, "error": "boom"}}
    (log_dir / f"{errored}.jsonl").write_text(json.dumps(event) + "\n")
    _write_run(log_dir, RUN, call="bearish")

    rows = {r["run_id"]: r for r in build_grid(log_dir, audit_dir, now=datetime(2026, 10, 20, tzinfo=timezone.utc))}

    assert _states(rows[errored]) == ["no_call"] * 4
    assert rows[errored]["call"] is None and rows[errored]["error"] == "boom"
    assert rows[RUN]["call"] == "bearish"


def test_rows_filter_by_commodity_and_inclusive_trigger_date_range(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    for run_id in ("corn_2026-09-25T18-30-00Z", "corn_2026-10-01T18-30-00Z", "wheat_2026-10-01T18-30-00Z", "wheat_2026-10-09T18-30-00Z"):
        _write_run(log_dir, run_id)
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)

    def ids(**filters):
        return [r["run_id"] for r in build_grid(log_dir, audit_dir, now=now, **filters)]

    assert ids(commodities=["wheat"]) == ["wheat_2026-10-09T18-30-00Z", "wheat_2026-10-01T18-30-00Z"]
    assert ids(start=date(2026, 10, 1), end=date(2026, 10, 1)) == [
        "corn_2026-10-01T18-30-00Z",
        "wheat_2026-10-01T18-30-00Z",
    ]
    assert len(ids()) == 4


def test_rows_are_flagged_off_cadence_unless_triggered_on_a_friday_in_new_york(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    friday = "corn_2026-10-02T18-30-00Z"
    _write_run(log_dir, RUN)  # Thursday
    _write_run(log_dir, friday)

    rows = {r["run_id"]: r for r in build_grid(log_dir, audit_dir, now=datetime(2026, 10, 5, tzinfo=timezone.utc))}

    assert rows[RUN]["off_cadence"] is True
    assert rows[friday]["off_cadence"] is False


def test_run_ids_are_displayed_as_a_capitalised_commodity_and_a_utc_timestamp():
    assert format_run_id("corn_2026-10-01T18-30-00Z") == "Corn 2026-10-01 18:30 UTC"
    assert format_run_id("soybeans_2026-09-08T15-00-00Z") == "Soybeans 2026-09-08 15:00 UTC"


def test_an_unparseable_run_id_is_shown_as_is():
    assert format_run_id("notes") == "notes"
