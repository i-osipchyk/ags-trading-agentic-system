import csv
import json
from datetime import timezone

import pytest

from ags.logging.run_log import RunLog
from ags.replay import ReplaySpec, run_replay
from ags.storage import LocalStorage, Prefixed
from tests.portfolio.test_stage import (
    COMMODITIES,
    CONFIG,
    CYCLE1,
    CYCLE2,
    CYCLE3,
    FakeMarket,
    StubManager,
    _official,
    call,
    calls,
    cycle_id,
)

DELTAS = {CYCLE1: {"corn": 3.0}, CYCLE2: {"corn": 1.0, "soybeans": -1.0}, CYCLE3: {"corn": -2.0}}
DIRECTIONS = {"corn": "bullish", "soybeans": "bearish"}
SPEC = ReplaySpec.from_mapping({
    "replay_id": "identity", "fork_at": CYCLE2.isoformat(),
    "coordinator": {"variant": "default", "model": "stub"}, "manager": {"variant": "default", "model": "stub"},
})
NOW = CYCLE3 + (CYCLE3 - CYCLE1)


class Agents:
    """Answers as the coordinator or the manager — the same answers the official cycles got."""

    def __init__(self, fail_coordinator_at=None):
        self.fail_coordinator_at = fail_coordinator_at
        self.coordinator_calls = 0

    def complete(self, messages, tools, *, model):
        system = messages[0]["content"]
        if "You are the Coordinator for " in system:
            if self.fail_coordinator_at and self.fail_coordinator_at.astimezone(timezone.utc).isoformat() in system:
                raise ConnectionError("402 Insufficient Balance")
            self.coordinator_calls += 1
            commodity = system.split("You are the Coordinator for ")[1].split(" ")[0]
            reply = {
                "call": DIRECTIONS.get(commodity, "neutral"), "conviction": 5, "dissenting_analysts": [],
                "supporting_analysts": ["technical", "news", "weather", "supply_demand"], "thesis": "t",
                "key_drivers": [{"driver": "d", "source": "technical"}], "invalidation_conditions": ["x"],
            }
            return {"role": "assistant", "content": json.dumps(reply), "tool_calls": None}
        when = next(w for w in DELTAS if w.isoformat() in system)
        return StubManager(DELTAS[when]).complete(messages, tools, model=model)


def _official_world(store):
    market = FakeMarket()
    official = {}
    for when in (CYCLE1, CYCLE2, CYCLE3):
        stamp = cycle_id(when).removeprefix("cycle_")
        for commodity in COMMODITIES:
            with RunLog(Prefixed(store, "logs"), f"{commodity}_{stamp}") as log:
                for agent in ("technical", "news", "weather", "supply_demand"):
                    log.append(agent=agent, event_type="output", payload={"signal": agent})
        official_calls = calls(**{c: call(d) for c, d in DIRECTIONS.items()})
        official[when] = _official(store, market, when, official_calls, StubManager(DELTAS[when]))
    return official


@pytest.fixture
def store(tmp_path):
    return LocalStorage(tmp_path / "bucket")


def test_an_identity_replay_reproduces_the_official_manager_book_and_keeps_the_results_locally(store, tmp_path):
    official = _official_world(store)
    local = tmp_path / "local"

    rows = run_replay(store, SPEC, Agents(), CONFIG, local_dir=local, now=NOW, code_version="abc1234")

    assert [r["cycle_id"] for r in rows] == [cycle_id(CYCLE2), cycle_id(CYCLE3)]
    for row in rows:
        assert row["pnl"]["replay_manager"] == pytest.approx(row["pnl"]["official_manager"])
    assert official[CYCLE3]["equity"] != official[CYCLE1]["equity"]  # the books did move
    with open(local / "report.csv") as f:
        assert [r["cycle_id"] for r in csv.DictReader(f)] == [cycle_id(CYCLE2), cycle_id(CYCLE3)]
    assert json.loads((local / "spec.json").read_text())["replay_id"] == "identity"
    assert (local / "portfolio" / "manager" / "cycles" / f"{cycle_id(CYCLE3)}.json").is_file()
    differences = (local / "differences.md").read_text()
    assert "# Replay `identity`" in differences and "abc1234" in differences


def test_a_failed_model_stops_the_run_keeps_what_was_done_locally_and_a_second_run_resumes(store, tmp_path):
    _official_world(store)
    local = tmp_path / "local"

    with pytest.raises(ConnectionError):
        run_replay(store, SPEC, Agents(fail_coordinator_at=CYCLE3), CONFIG, local_dir=local, now=NOW)
    assert (local / "portfolio" / "manager" / "cycles" / f"{cycle_id(CYCLE2)}.json").is_file()
    assert not (local / "portfolio" / "manager" / "cycles" / f"{cycle_id(CYCLE3)}.json").exists()

    agents = Agents()
    rows = run_replay(store, SPEC, agents, CONFIG, local_dir=local, now=NOW)

    assert [r["cycle_id"] for r in rows] == [cycle_id(CYCLE2), cycle_id(CYCLE3)]
    assert agents.coordinator_calls <= len(COMMODITIES)  # only the failed cycle's coordinators ran again
    assert (local / "portfolio" / "manager" / "cycles" / f"{cycle_id(CYCLE3)}.json").is_file()
