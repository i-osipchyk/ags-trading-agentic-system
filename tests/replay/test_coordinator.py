import json

import pytest

from ags.logging.run_log import RunLog
from ags.replay import ReplaySpec, replay_coordinator
from ags.storage import LocalStorage, Prefixed
from tests.replay.test_spec import SPEC

SPEC_OBJ = ReplaySpec.from_mapping(SPEC)  # forks at 2026-10-20T08:00Z

BEFORE_FORK = "corn_2026-10-19T20-00-00Z"
AFTER_FORK = "corn_2026-10-20T08-00-00Z"
NEXT = "corn_2026-10-20T20-00-00Z"


def _call(direction, thesis):
    return {
        "call": direction, "conviction": 3, "supporting_analysts": ["technical"], "dissenting_analysts": [],
        "thesis": thesis, "key_drivers": [{"driver": "d", "source": "technical"}], "invalidation_conditions": ["x"],
        "price_at_call": {"price": 4.5, "date": "2026-10-19"},
    }


def _official_run(store, run_id, coordinator_call):
    with RunLog(Prefixed(store, "logs"), run_id) as log:
        for agent in ("technical", "news", "weather", "supply_demand"):
            log.append(agent=agent, event_type="output", payload={"marker": f"{agent}-of-{run_id}"})
        log.append(agent="coordinator", event_type="output", payload=coordinator_call)


def _audit(store, run_id, graded_at):
    audit = {
        "run_id": run_id, "checkpoint_days": 1, "direction_correct": True, "driver_verdicts": [],
        "calibration_note": "n", "thesis_vs_outcome": "t", "graded_at": graded_at,
    }
    store.put(f"audits/{run_id}/1d.json", json.dumps(audit).encode())


class ScriptedCoordinator:
    """Answers with a valid call; with `read_record`, first asks for the track record. Records every request."""

    def __init__(self, direction="bearish", thesis="replayed", read_record=False):
        self.reply = json.dumps({k: v for k, v in _call(direction, thesis).items() if k != "price_at_call"})
        self.read_record = read_record
        self.requests = []

    def complete(self, messages, tools, *, model):
        self.requests.append(list(messages))
        if self.read_record and len(self.requests) == 1:
            call = {"id": "t1", "type": "function", "function": {"name": "read_track_record", "arguments": '{"lookback_weeks": 4}'}}
            return {"role": "assistant", "content": None, "tool_calls": [call]}
        return {"role": "assistant", "content": self.reply, "tool_calls": None}

    def track_record_seen(self):
        tool_message = next(m for m in self.requests[1] if m["role"] == "tool")
        return json.loads(tool_message["content"])["runs"]


def test_the_replayed_coordinator_gets_the_original_analyst_outputs_and_logs_only_under_the_replay(tmp_path):
    store = LocalStorage(tmp_path)
    _official_run(store, AFTER_FORK, _call("bullish", "original"))
    coordinator = ScriptedCoordinator()

    call = replay_coordinator(store, SPEC_OBJ, AFTER_FORK, coordinator)

    prompt = json.dumps(coordinator.requests[0])
    for agent in ("technical", "news", "weather", "supply_demand"):
        assert f"{agent}-of-{AFTER_FORK}" in prompt
    assert (call["call"], call["thesis"]) == ("bearish", "replayed")
    assert call["price_at_call"] == {"price": 4.5, "date": "2026-10-19"}  # the original's, not refetched
    assert store.list("logs/") == [f"logs/{AFTER_FORK}.jsonl"]  # the official log is untouched
    replay_log = [json.loads(line) for line in store.get(f"replays/coord-v2/logs/{AFTER_FORK}.jsonl").decode().splitlines()]
    assert [e["agent"] for e in replay_log if e["event_type"] == "output"] == [
        "technical", "news", "weather", "supply_demand", "coordinator",
    ]


def test_the_track_record_is_the_original_before_the_fork_and_the_replay_after_it(tmp_path):
    store = LocalStorage(tmp_path)
    _official_run(store, BEFORE_FORK, _call("bullish", "original before fork"))
    _official_run(store, AFTER_FORK, _call("bullish", "original after fork"))
    _official_run(store, NEXT, _call("bullish", "original next"))
    _audit(store, BEFORE_FORK, "2026-10-20T18:00:00+00:00")
    _audit(store, AFTER_FORK, "2026-10-20T19:00:00+00:00")  # grades the original call, not the replayed one
    replay_coordinator(store, SPEC_OBJ, AFTER_FORK, ScriptedCoordinator(thesis="replayed after fork"))
    coordinator = ScriptedCoordinator(read_record=True)

    replay_coordinator(store, SPEC_OBJ, NEXT, coordinator)

    seen = {run["run_id"]: run for run in coordinator.track_record_seen()}
    assert seen[BEFORE_FORK]["thesis"] == "original before fork"
    assert seen[AFTER_FORK]["thesis"] == "replayed after fork"
    assert NEXT not in seen
    assert len(seen[BEFORE_FORK]["audits"]) == 1
    assert seen[AFTER_FORK]["audits"] == []


def test_replaying_a_run_again_returns_the_logged_call_without_asking_the_model(tmp_path):
    store = LocalStorage(tmp_path)
    _official_run(store, AFTER_FORK, _call("bullish", "original"))
    first = replay_coordinator(store, SPEC_OBJ, AFTER_FORK, ScriptedCoordinator(thesis="first"))
    again = ScriptedCoordinator(thesis="second")

    call = replay_coordinator(store, SPEC_OBJ, AFTER_FORK, again)

    assert call == first and again.requests == []


class Unreachable:
    def complete(self, messages, tools, *, model):
        raise ConnectionError("402 Insufficient Balance")


def test_a_model_that_fails_fails_the_run_so_a_resume_asks_again_instead_of_keeping_a_degraded_call(tmp_path):
    store = LocalStorage(tmp_path)
    _official_run(store, AFTER_FORK, _call("bullish", "original"))

    with pytest.raises(ConnectionError):
        replay_coordinator(store, SPEC_OBJ, AFTER_FORK, Unreachable())
    retry = ScriptedCoordinator(thesis="after the outage")
    call = replay_coordinator(store, SPEC_OBJ, AFTER_FORK, retry)

    assert call["thesis"] == "after the outage" and len(retry.requests) == 1
    events = [json.loads(line) for line in store.get(f"replays/coord-v2/logs/{AFTER_FORK}.jsonl").decode().splitlines()]
    assert [e["payload"]["error"] for e in events if e["event_type"] == "error"] == ["ConnectionError: 402 Insufficient Balance"]
    assert [e["payload"]["thesis"] for e in events if e["agent"] == "coordinator" and e["event_type"] == "output"] == [
        "after the outage"
    ]
