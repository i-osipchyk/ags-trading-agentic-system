import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ags import challenger
from ags.challenger import run_challenger, select_runs


def _official(log_dir, commodity, when, *, call="bullish", cost=0.01, carried=False, results=()):
    run_id = f"{commodity}_{when:%Y-%m-%dT%H-%M-%SZ}"
    events = [
        {"agent": "news", "event_type": "output", "payload": {"headlines": [], "carried_forward": carried}},
        {"agent": "coordinator", "event_type": "response", "payload": {"usage": {"cost_usd": cost}}},
        {"agent": "coordinator", "event_type": "output", "payload": {"call": call, "conviction": 3}},
    ]
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{run_id}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return run_id


def _at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)    # EDT until Nov 1: ET = UTC - 4


@pytest.fixture
def official(tmp_path):
    logs = tmp_path / "logs"
    ids = {
        "corn_am": _official(logs, "corn", _at(28, 10)),       # 06:00 ET
        "corn_pm": _official(logs, "corn", _at(28, 16, 30)),   # 12:30 ET
        "corn_adhoc": _official(logs, "corn", _at(28, 9, 28)),
        "wheat_am": _official(logs, "wheat", _at(28, 10)),
        "corn_early": _official(logs, "corn", _at(12, 10)),
    }
    return logs, ids


def test_runs_are_selected_by_date_and_cycle_time_and_ordered_by_time(official):
    logs, ids = official

    chosen = select_runs(logs, since=date(2026, 10, 13), slots=("06:00", "12:30"))

    assert chosen == [ids["corn_am"], ids["wheat_am"], ids["corn_pm"]]       # not the ad hoc run, nor before since
    assert select_runs(logs, since=date(2026, 10, 13), slots=("06:00",), commodities=("wheat",)) == [ids["wheat_am"]]
    assert select_runs(logs, since=date(2026, 10, 13), until=date(2026, 10, 27)) == []


def test_the_estimate_scales_the_official_cost_and_time_by_workers(official):
    logs, ids = official

    plan = challenger.estimate(logs, [ids["corn_am"], ids["corn_pm"]], workers=2)

    assert plan["runs"] == 2
    assert plan["cost_usd"] == pytest.approx(0.02 * challenger.ALIGNED_COST_FACTOR)
    assert plan["hours"] == pytest.approx(2 * challenger.SECONDS_PER_RUN / 2 / 3600)


def _fake_run_one(calls, fail=()):
    def run_one(run_id, frozen, store):
        calls.append(run_id)
        if run_id in fail:
            raise RuntimeError("boom")
        assert frozen.source_run_id == run_id
        # What a real run does: write its log under its own key. The official one is hidden from it.
        assert store.get(f"{run_id}.jsonl") is None
        store.put(f"{run_id}.jsonl", json.dumps(
            {"agent": "coordinator", "event_type": "output", "payload": {"call": "bearish", "conviction": 2}}).encode() + b"\n")
    return run_one


def test_each_commodity_runs_in_time_order_and_logs_go_only_under_out(official, tmp_path):
    logs, ids = official
    out, calls = tmp_path / "out", []
    before = sorted(p.name for p in logs.iterdir())

    outcome = run_challenger(select_runs(logs, since=date(2026, 10, 13), slots=("06:00", "12:30")),
                             official_logs=logs, out=out, run_one=_fake_run_one(calls), workers=1, progress=lambda m: None)

    assert sorted(outcome.done) == sorted([ids["corn_am"], ids["corn_pm"], ids["wheat_am"]])
    assert calls.index(ids["corn_am"]) < calls.index(ids["corn_pm"])
    assert sorted(p.name for p in (out / "logs").iterdir()) == sorted(f"{r}.jsonl" for r in outcome.done)
    assert sorted(p.name for p in logs.iterdir()) == before                      # the official record is untouched


def test_a_rerun_skips_what_is_done_and_retries_what_failed(official, tmp_path):
    logs, ids = official
    out, first, second = tmp_path / "out", [], []
    run_ids = select_runs(logs, since=date(2026, 10, 13), slots=("06:00", "12:30"))

    one = run_challenger(run_ids, official_logs=logs, out=out, run_one=_fake_run_one(first, fail={ids["corn_pm"]}),
                         workers=2, progress=lambda m: None)
    two = run_challenger(run_ids, official_logs=logs, out=out, run_one=_fake_run_one(second), workers=2,
                         progress=lambda m: None)

    assert list(one.failed) == [ids["corn_pm"]] and "RuntimeError: boom" in one.failed[ids["corn_pm"]]
    assert json.loads((out / "failures.jsonl").read_text())["run_id"] == ids["corn_pm"]
    assert second == [ids["corn_pm"]] and sorted(two.skipped) == sorted([ids["corn_am"], ids["wheat_am"]])
    assert not two.failed


def test_a_blocking_provider_stops_the_batch(official, tmp_path):
    logs, ids = official
    calls = []

    outcome = run_challenger(select_runs(logs, since=date(2026, 10, 13), slots=("06:00", "12:30")),
                             official_logs=logs, out=tmp_path / "out", run_one=_fake_run_one(calls), workers=1,
                             blocked=lambda: {"deepseek": "402 out of credit"}, progress=lambda m: None)

    assert outcome.stopped and "deepseek" in outcome.stopped
    assert len(calls) < 3


def test_a_batch_is_pinned_to_its_prompts_and_rules_and_refuses_to_resume_under_others(tmp_path):
    from ags.challenger import BatchChanged, check_batch

    current = {"prompt_hashes": {"technical": "a", "weather": "b", "all": "c"}, "alignment_rules": "r1"}
    check_batch(tmp_path / "b", current)

    check_batch(tmp_path / "b", current)                                           # the same prompts: fine
    with pytest.raises(BatchChanged, match="technical"):
        check_batch(tmp_path / "b", {**current, "prompt_hashes": {**current["prompt_hashes"], "technical": "z"}})
    with pytest.raises(BatchChanged, match="alignment_rules"):
        check_batch(tmp_path / "b", {**current, "alignment_rules": "r2"})
    check_batch(tmp_path / "other", {**current, "alignment_rules": "r2"})          # a new batch name starts afresh


def test_the_fingerprint_covers_the_aligned_prompts_and_the_bias_rules():
    fingerprint = challenger.fingerprint()

    assert set(fingerprint["prompt_hashes"]) >= {"technical", "news", "weather", "coordinator", "all"}
    assert len(fingerprint["alignment_rules"]) == 12
