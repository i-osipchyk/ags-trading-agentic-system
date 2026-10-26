import json

import pytest

from ags.logging.run_log import RunLog
from ags.replay import ReplaySpec, pending_cycles, replay_coordinator, replay_portfolio
from ags.storage import LocalStorage, Prefixed
from tests.portfolio.test_stage import (
    COMMODITIES,
    CONFIG,
    CYCLE1,
    CYCLE2,
    FakeMarket,
    StubManager,
    _crash,
    _official,
    call,
    calls,
    cycle_id,
)

SPEC = ReplaySpec.from_mapping({
    "replay_id": "r1",
    "fork_at": CYCLE2.isoformat(),
    "coordinator": {"variant": "default", "model": "stub"},
    "manager": {"variant": "default", "model": "stub"},
})


class Coordinator:
    def __init__(self, direction):
        self.direction = direction

    def complete(self, messages, tools, *, model):
        reply = {
            "call": self.direction, "conviction": 5, "supporting_analysts": ["technical", "news", "weather", "supply_demand"],
            "dissenting_analysts": [], "thesis": "replayed", "key_drivers": [{"driver": "d", "source": "technical"}],
            "invalidation_conditions": ["x"],
        }
        return {"role": "assistant", "content": json.dumps(reply), "tool_calls": None}


def _replay_calls(store, when, directions):
    """Official commodity logs for the cycle, then the replayed coordinator on each (neutral unless given)."""
    stamp = cycle_id(when).removeprefix("cycle_")
    for commodity in COMMODITIES:
        run_id = f"{commodity}_{stamp}"
        with RunLog(Prefixed(store, "logs"), run_id) as log:
            log.append(agent="coordinator", event_type="output", payload={"price_at_call": {"price": 5.0, "date": "d"}})
        replay_coordinator(store, SPEC, run_id, Coordinator(directions.get(commodity, "neutral")))


def _keys_outside_replays(tmp_path):
    return sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file() and not str(p.relative_to(tmp_path)).startswith("replays"))


@pytest.fixture
def store(tmp_path):
    return LocalStorage(tmp_path)


def test_both_books_open_from_the_official_managers_book_at_the_fork_and_trade_on_the_replayed_calls(store, tmp_path):
    market = FakeMarket()
    _official(store, market, CYCLE1, calls(corn=call("bullish")), StubManager({"corn": 3.0}))  # shadow buys 1 lot
    _official(store, market, CYCLE2, calls(corn=call("bullish")), StubManager())
    _replay_calls(store, CYCLE2, {"corn": "bullish", "soybeans": "bearish"})
    before = _keys_outside_replays(tmp_path)

    result = replay_portfolio(store, SPEC, cycle_id(CYCLE2), StubManager(), CONFIG)

    for book in ("manager", "shadow"):
        assert {p["symbol_name"]: p["volume"] for p in result[book]["opening_state"]["positions"]} == {"corn": 300_000}
        assert result[book]["calls"]["soybeans"]["call"] == "bearish"
    assert result["shadow"]["applied_deltas"]["soybeans"] < 0
    assert _keys_outside_replays(tmp_path) == before


def test_a_breaker_tripped_before_the_fork_stays_tripped_in_the_replay(store):
    market = FakeMarket()
    c = calls(corn=call("bullish"), soybeans=call("bearish"))
    _official(store, market, CYCLE1, c, StubManager({"corn": 8.0, "soybeans": -2.0}))
    _crash(market)
    tripped = _official(store, market, CYCLE2, c, StubManager())
    assert tripped["breaker_active"] is True
    spec = ReplaySpec.from_mapping({**SPEC.to_mapping(), "fork_at": "2026-01-07T12:00:00+00:00"})  # between CYCLE2 and CYCLE3
    from tests.portfolio.test_stage import CYCLE3

    _official(store, market, CYCLE3, c, StubManager())
    stamp = cycle_id(CYCLE3).removeprefix("cycle_")
    for commodity in COMMODITIES:
        with RunLog(Prefixed(store, "logs"), f"{commodity}_{stamp}") as log:
            log.append(agent="coordinator", event_type="output", payload={})
        replay_coordinator(store, spec, f"{commodity}_{stamp}", Coordinator("bullish"))

    result = replay_portfolio(store, spec, cycle_id(CYCLE3), StubManager({"corn": 2.0}), CONFIG)

    assert result["manager"]["breaker_active"] is True
    assert result["manager"]["applied_deltas"]["corn"] == 0.0


class Unreachable:
    def complete(self, messages, tools, *, model):
        raise ConnectionError("402 Insufficient Balance")


def test_a_manager_that_fails_fails_the_step_so_a_resume_runs_the_cycle_again(store):
    market = FakeMarket()
    _official(store, market, CYCLE1, calls(corn=call("bullish")), StubManager({"corn": 3.0}))
    _official(store, market, CYCLE2, calls(corn=call("bullish")), StubManager())
    _replay_calls(store, CYCLE2, {"corn": "bullish"})

    with pytest.raises(ConnectionError):
        replay_portfolio(store, SPEC, cycle_id(CYCLE2), Unreachable(), CONFIG)
    assert pending_cycles(store, SPEC) == [cycle_id(CYCLE2)]  # nothing recorded for the failed cycle

    result = replay_portfolio(store, SPEC, cycle_id(CYCLE2), StubManager({"corn": 1.0}), CONFIG)

    assert result["manager"]["manager_error"] is None
    assert result["manager"]["applied_deltas"]["corn"] == pytest.approx(1.0)
