import json
from datetime import date, datetime, timedelta, timezone

import pytest

from ags.agents.auditor import find_due_checkpoints, grade_checkpoint, run_auditor

NOW = datetime(2026, 6, 30, 18, 30, tzinfo=timezone.utc)


def _write_run(log_dir, run_id, call="bullish"):
    output = {"call": call, "conviction": 4, "thesis": "t", "key_drivers": [], "price_at_call": {"price": 450.0, "date": "2026-05-29"}}
    events = [{"seq": 0, "agent": "coordinator", "event_type": "output", "payload": output}]
    (log_dir / f"{run_id}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def _write_audit(audit_dir, run_id, weeks):
    (audit_dir / run_id).mkdir(parents=True, exist_ok=True)
    (audit_dir / run_id / f"{weeks}w.json").write_text(json.dumps({"checkpoint_weeks": weeks}))


def _dirs(tmp_path):
    log_dir, audit_dir = tmp_path / "logs", tmp_path / "audits"
    log_dir.mkdir()
    audit_dir.mkdir()
    return log_dir, audit_dir


def test_only_checkpoints_whose_lag_has_elapsed_are_due(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    # Triggered 3 weeks and 2 days before NOW: 1w and 2w elapsed, 4w and 8w not.
    _write_run(log_dir, "corn_2026-06-08T18-30-00Z")

    due = find_due_checkpoints(log_dir, audit_dir, now=NOW)

    assert due == [("corn_2026-06-08T18-30-00Z", 1), ("corn_2026-06-08T18-30-00Z", 2)]


def test_already_graded_checkpoints_are_not_due_again(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    _write_run(log_dir, "corn_2026-05-01T18-30-00Z")  # 8w elapsed: all four due
    _write_audit(audit_dir, "corn_2026-05-01T18-30-00Z", 1)
    _write_audit(audit_dir, "corn_2026-05-01T18-30-00Z", 4)

    due = find_due_checkpoints(log_dir, audit_dir, now=NOW)

    assert due == [("corn_2026-05-01T18-30-00Z", 2), ("corn_2026-05-01T18-30-00Z", 8)]


def test_a_checkpoint_falling_exactly_at_now_is_due(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    _write_run(log_dir, "corn_2026-06-16T18-30-00Z")  # +2w == NOW exactly

    due = find_due_checkpoints(log_dir, audit_dir, now=NOW)

    assert due == [("corn_2026-06-16T18-30-00Z", 1), ("corn_2026-06-16T18-30-00Z", 2)]


def test_runs_without_a_gradable_call_and_stray_files_are_ignored(tmp_path):
    log_dir, audit_dir = _dirs(tmp_path)
    _write_run(log_dir, "corn_2026-05-01T18-30-00Z", call=None)  # degraded coordinator
    (log_dir / "wheat_2026-05-01T18-30-00Z.jsonl").write_text(
        json.dumps({"seq": 0, "agent": "technical", "event_type": "output", "payload": {}}) + "\n"
    )  # run that never reached a coordinator output
    _write_run(log_dir, "corn._2026-05-01T18-30-00Z")  # malformed name from an early typo
    (log_dir / "notes.jsonl").write_text("")

    assert find_due_checkpoints(log_dir, audit_dir, now=NOW) == []


# --- grade_checkpoint ---------------------------------------------------

CALL_DATE = date(2026, 6, 5)
RUN_ID = "corn_2026-06-05T18-30-00Z"


class FakeChatClient:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        content = self._reply if isinstance(self._reply, str) else json.dumps(self._reply)
        return {"role": "assistant", "content": content, "tool_calls": None}


class FakePricesClient:
    def __init__(self, bars):
        self._bars = bars

    def get_daily_bars(self, symbol_name, start, end):
        return [b for b in self._bars if start <= date.fromisoformat(b["date"]) <= end]


def _bar(day, close):
    return {"date": day.isoformat(), "open": close, "high": close, "low": close, "close": close, "volume": 1}


def _bars(post_call_closes):
    # 200 flat days then a 60-day rally ending at 160 on CALL_DATE (medium trend: up),
    # then the given closes on the days after the call.
    values = [100.0] * 200 + [100.0 + i for i in range(1, 61)]
    start = CALL_DATE - timedelta(days=len(values) - 1)
    bars = [_bar(start + timedelta(days=i), v) for i, v in enumerate(values)]
    bars += [_bar(CALL_DATE + timedelta(days=i), c) for i, c in enumerate(post_call_closes, start=1)]
    return bars


def _write_full_run(log_dir, call="bullish"):
    events = [
        {"seq": 0, "agent": "weather", "event_type": "output", "payload": {"current_conditions": "WX-MARKER dry"}},
        {
            "seq": 1,
            "agent": "coordinator",
            "event_type": "output",
            "payload": {
                "call": call,
                "conviction": 4,
                "thesis": "THESIS-MARKER drought lifts corn",
                "key_drivers": [{"driver": "Iowa drought", "source": "weather"}],
                "supporting_analysts": ["weather"],
                "dissenting_analysts": [],
                "price_at_call": {"price": 160.0, "date": CALL_DATE.isoformat()},
            },
        },
    ]
    (log_dir / f"{RUN_ID}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


VERDICTS = {
    "driver_verdicts": [{"driver": "Iowa drought", "played_out": "no", "evidence": "rain arrived midweek"}],
    "calibration_note": "Conviction 4 was followed by a sharp reversal.",
    "thesis_vs_outcome": "Price fell because the drought eased, the opposite of the thesis.",
}


def _grade(tmp_path, post_call_closes, reply=VERDICTS, call="bullish", weeks=1):
    log_dir, _ = _dirs(tmp_path)
    _write_full_run(log_dir, call=call)
    client = FakeChatClient(reply)
    audit = grade_checkpoint(
        client,
        log_dir=log_dir,
        data_dir=tmp_path / "data",
        run_id=RUN_ID,
        weeks=weeks,
        model="stub-model",
        prices_client=FakePricesClient(_bars(post_call_closes)),
        now=NOW,
    )
    return audit, client


def test_price_outcome_is_computed_by_code_and_the_verdicts_come_from_the_model(tmp_path):
    # One week after the call, corn closed at 144 (-10%); a trend-follower
    # (trend was up at the call) would have been long and lost the same 10%.
    # A later bar must not be visible to the grader.
    audit, client = _grade(tmp_path, [150.0, 148.0, 146.0, 145.0, 144.0, 143.0, 144.0, 90.0])

    assert audit["run_id"] == RUN_ID
    assert audit["checkpoint_weeks"] == 1
    assert audit["price_outcome"]["start_price"] == 160.0
    assert audit["price_outcome"]["end_price"] == 144.0
    assert audit["price_outcome"]["return"] == pytest.approx(-0.10)
    assert audit["price_outcome"]["trend_benchmark"] == {"position": "long", "return": pytest.approx(-0.10)}
    assert audit["direction_correct"] is False
    assert audit["driver_verdicts"] == VERDICTS["driver_verdicts"]
    assert audit["calibration_note"] == VERDICTS["calibration_note"]
    assert audit["thesis_vs_outcome"] == VERDICTS["thesis_vs_outcome"]
    assert audit["graded_at"] == NOW.isoformat()

    prompt = "".join(m["content"] for m in client.calls[0]["messages"])
    assert "THESIS-MARKER" in prompt and "WX-MARKER" in prompt and "-10" in prompt
    assert "90.0" not in prompt  # beyond the checkpoint date
    assert client.calls[0]["tools"] is None


def test_neutral_calls_are_excluded_from_direction_scoring_but_still_graded(tmp_path):
    audit, _ = _grade(tmp_path, [150.0, 148.0, 146.0, 145.0, 144.0, 143.0, 144.0], call="neutral")

    assert audit["direction_correct"] is None
    assert audit["price_outcome"]["return"] == pytest.approx(-0.10)
    assert audit["thesis_vs_outcome"] == VERDICTS["thesis_vs_outcome"]


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        {"calibration_note": "x", "thesis_vs_outcome": "y"},  # missing driver_verdicts
        {**VERDICTS, "driver_verdicts": [{"driver": "d", "played_out": "maybe", "evidence": "e"}]},
    ],
)
def test_a_malformed_model_reply_raises_instead_of_inventing_a_grade(tmp_path, reply):
    with pytest.raises(ValueError):
        _grade(tmp_path, [150.0, 148.0, 146.0, 145.0, 144.0, 143.0, 144.0], reply=reply)


# --- run_auditor --------------------------------------------------------

RUN_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)  # 1w and 2w after the call, not 4w


def _run(tmp_path, reply=VERDICTS, now=RUN_NOW):
    log_dir, audit_dir = tmp_path / "logs", tmp_path / "audits"
    log_dir.mkdir(exist_ok=True)
    if not (log_dir / f"{RUN_ID}.jsonl").exists():
        _write_full_run(log_dir)
    client = FakeChatClient(reply)
    bars = _bars([150.0, 148.0, 146.0, 145.0, 144.0, 143.0, 144.0, 140.0, 139.0, 138.0, 137.0, 136.0, 135.0, 134.0, 133.0])
    result = run_auditor(
        client,
        log_dir=log_dir,
        audit_dir=audit_dir,
        data_dir=tmp_path / "data",
        model="stub-model",
        now=now,
        prices_client=FakePricesClient(bars),
    )
    return result, client, audit_dir


def test_writes_one_audit_file_per_due_checkpoint_and_is_idempotent(tmp_path):
    result, client, audit_dir = _run(tmp_path)

    assert result == {"graded": [(RUN_ID, 1), (RUN_ID, 2)], "failed": []}
    assert sorted(p.name for p in (audit_dir / RUN_ID).iterdir()) == ["1w.json", "2w.json"]
    one_week = json.loads((audit_dir / RUN_ID / "1w.json").read_text())
    assert one_week["checkpoint_weeks"] == 1 and one_week["run_id"] == RUN_ID
    assert len(client.calls) == 2  # a separate evaluator invocation per pair

    again, client2, _ = _run(tmp_path)

    assert again == {"graded": [], "failed": []}
    assert client2.calls == []


def test_a_failing_checkpoint_is_reported_and_left_due_without_blocking_the_others(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_full_run(log_dir)
    # price_at_call missing: ungradable
    (log_dir / "wheat_2026-06-05T18-30-00Z.jsonl").write_text(
        json.dumps({"seq": 0, "agent": "coordinator", "event_type": "output", "payload": {"call": "bullish", "price_at_call": None}}) + "\n"
    )

    result, _, audit_dir = _run(tmp_path)

    assert result["graded"] == [(RUN_ID, 1), (RUN_ID, 2)]
    assert [pair for pair, _ in result["failed"]] == [("wheat_2026-06-05T18-30-00Z", 1), ("wheat_2026-06-05T18-30-00Z", 2)]
    assert not (audit_dir / "wheat_2026-06-05T18-30-00Z").exists()  # nothing written, so it self-heals next scan


def test_written_audits_are_readable_by_the_coordinators_track_record(tmp_path):
    from ags.tools.track_record import read_track_record

    _, _, audit_dir = _run(tmp_path)

    record = read_track_record(
        tmp_path / "logs", audit_dir, commodity="corn", lookback_weeks=8, as_of=datetime(2026, 6, 25, tzinfo=timezone.utc)
    )

    assert [a["checkpoint_weeks"] for a in record[0]["audits"]] == [1, 2]
    assert record[0]["audits"][0]["direction_correct"] is False
