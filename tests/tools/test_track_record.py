import json
from datetime import datetime, timezone

from ags.tools.track_record import read_track_record

AS_OF = datetime(2026, 6, 5, 18, 30, tzinfo=timezone.utc)


def _write_run(log_dir, run_id, **overrides):
    output = {
        "call": "bullish",
        "conviction": 4,
        "thesis": f"thesis for {run_id}",
        "key_drivers": [{"driver": "dryness", "source": "weather"}],
        "supporting_analysts": ["weather", "technical", "news"],
        "dissenting_analysts": ["supply_demand"],
        "invalidation_conditions": ["rain"],
        "price_at_call": {"price": 450.0, "date": "2026-05-29"},
        **overrides,
    }
    events = [
        {"seq": 0, "agent": "technical", "event_type": "output", "payload": {"chart_description": "x"}},
        {"seq": 1, "agent": "coordinator", "event_type": "prompt", "payload": {"messages": []}},
        {"seq": 2, "agent": "coordinator", "event_type": "output", "payload": output},
    ]
    (log_dir / f"{run_id}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def _write_audit(audit_dir, run_id, weeks, **overrides):
    (audit_dir / run_id).mkdir(parents=True, exist_ok=True)
    audit = {
        "checkpoint_weeks": weeks,
        "direction_correct": True,
        "driver_verdicts": [{"driver": "dryness", "verdict": "played_out"}],
        "calibration_note": "ok",
        "thesis_vs_outcome": "matched",
        "graded_at": "2026-06-01T00:00:00+00:00",
        **overrides,
    }
    (audit_dir / run_id / f"{weeks}w.json").write_text(json.dumps(audit))


def _read(tmp_path, **kwargs):
    log_dir, audit_dir = tmp_path / "logs", tmp_path / "audits"
    log_dir.mkdir(exist_ok=True)
    audit_dir.mkdir(exist_ok=True)
    return log_dir, audit_dir


def test_returns_past_call_fields_for_a_run_without_audits(tmp_path):
    log_dir, audit_dir = _read(tmp_path)
    _write_run(log_dir, "corn_2026-05-29T18-30-00Z")

    record = read_track_record(log_dir, audit_dir, commodity="corn", lookback_weeks=8, as_of=AS_OF)

    assert record == [
        {
            "run_id": "corn_2026-05-29T18-30-00Z",
            "call": "bullish",
            "conviction": 4,
            "thesis": "thesis for corn_2026-05-29T18-30-00Z",
            "key_drivers": [{"driver": "dryness", "source": "weather"}],
            "supporting_analysts": ["weather", "technical", "news"],
            "dissenting_analysts": ["supply_demand"],
            "audits": [],
        }
    ]


def test_includes_only_the_audit_checkpoints_that_exist(tmp_path):
    log_dir, audit_dir = _read(tmp_path)
    _write_run(log_dir, "corn_2026-05-01T18-30-00Z")
    _write_audit(audit_dir, "corn_2026-05-01T18-30-00Z", 1)
    _write_audit(audit_dir, "corn_2026-05-01T18-30-00Z", 4, direction_correct=False)

    [run] = read_track_record(log_dir, audit_dir, commodity="corn", lookback_weeks=8, as_of=AS_OF)

    assert [a["checkpoint_weeks"] for a in run["audits"]] == [1, 4]
    assert run["audits"][1] == {
        "checkpoint_weeks": 4,
        "direction_correct": False,
        "driver_verdicts": [{"driver": "dryness", "verdict": "played_out"}],
        "calibration_note": "ok",
        "thesis_vs_outcome": "matched",
    }


def test_excludes_runs_outside_the_window_the_future_and_other_commodities(tmp_path):
    log_dir, audit_dir = _read(tmp_path)
    _write_run(log_dir, "corn_2026-03-27T18-30-00Z")  # 10 weeks back: outside 8w lookback
    _write_run(log_dir, "corn_2026-05-08T18-30-00Z")  # inside
    _write_run(log_dir, "corn_2026-06-05T18-30-00Z")  # the current run itself (== as_of)
    _write_run(log_dir, "corn_2026-06-12T18-30-00Z")  # after as_of
    _write_run(log_dir, "wheat_2026-05-08T18-30-00Z")  # other commodity

    record = read_track_record(log_dir, audit_dir, commodity="corn", lookback_weeks=8, as_of=AS_OF)

    assert [r["run_id"] for r in record] == ["corn_2026-05-08T18-30-00Z"]


def test_excludes_audits_graded_after_as_of(tmp_path):
    log_dir, audit_dir = _read(tmp_path)
    _write_run(log_dir, "corn_2026-05-01T18-30-00Z")
    _write_audit(audit_dir, "corn_2026-05-01T18-30-00Z", 1, graded_at="2026-06-05T17:00:00+00:00")
    _write_audit(audit_dir, "corn_2026-05-01T18-30-00Z", 2, graded_at="2026-06-06T00:00:00+00:00")

    [run] = read_track_record(log_dir, audit_dir, commodity="corn", lookback_weeks=8, as_of=AS_OF)

    assert [a["checkpoint_weeks"] for a in run["audits"]] == [1]
