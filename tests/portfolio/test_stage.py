import json
from datetime import date, datetime, timedelta, timezone

import pytest

from ags.portfolio.guardrails import GuardrailConfig
from ags.portfolio.stage import PortfolioConfig, run_portfolio
from ags.storage import LocalStorage
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES

COMMODITIES = list(CTRADER_SYMBOL_NAMES)
NAMES = {v: k for k, v in CTRADER_SYMBOL_NAMES.items()}
# Looser caps than production so one position can be levered enough to stop out.
CONFIG = PortfolioConfig(
    guardrails=GuardrailConfig(
        per_commodity_cap=5.0, grain_bucket_cap=10.0, gross_leverage_cap=20.0, margin_buffer=1.0, min_resize_fraction=0.20
    ),
    starting_equity=10_000.0,
    stop_out_level=0.5,
    shadow_risk_budget=0.01,
    drawdown_breaker=0.8,
)
SPEC = {
    "lot_size": 100_000, "min_volume": 10_000, "step_volume": 10_000, "max_volume": 100_000_000,
    "swap_long": 0.0, "swap_short": 0.0, "swap_type": "PIPS", "triple_swap_day": "WEDNESDAY",
    "pip_position": 2, "margin_rate": 0.1,
}

CYCLE1 = datetime(2026, 1, 6, 16, 0, tzinfo=timezone(timedelta(hours=-5)))  # Tue 16:00 ET
CYCLE2 = datetime(2026, 1, 7, 4, 0, tzinfo=timezone(timedelta(hours=-5)))  # Wed 04:00 ET
CYCLE3 = datetime(2026, 1, 7, 16, 0, tzinfo=timezone(timedelta(hours=-5)))


class FakeMarket:
    """Flat $5.00 market with a $0.10 daily range (ATR 0.10 -> $100 per lot), tweakable per test."""

    def __init__(self):
        self.bid, self.ask = {}, {}
        self.h1 = {}
        for i, name in enumerate(CTRADER_SYMBOL_NAMES.values()):
            self.bid[name], self.ask[name] = 4.99, 5.01

    def get_daily_bars(self, symbol_name, start, end):
        out, day = [], start
        while day <= end:
            if day.weekday() < 5:
                out.append({"date": day.isoformat(), "open": 5.0, "high": 5.05, "low": 4.95, "close": 5.0, "volume": 1})
            day += timedelta(days=1)
        return out

    def get_symbol_specs(self, symbol_name):
        return {"symbol_id": hash(symbol_name) % 1000, **SPEC}

    def get_quote(self, symbol_name):
        return {"symbol_id": 1, "bid": self.bid[symbol_name], "ask": self.ask[symbol_name], "timestamp": "x"}

    def get_h1_bars(self, symbol_name, start, end):
        return [b for b in self.h1.get(symbol_name, []) if start <= datetime.fromisoformat(b["timestamp"]) < end]


class StubManager:
    """A chat client that answers with whatever proposal the test set, and records prompts."""

    def __init__(self, deltas=None):
        self.deltas = deltas or {}
        self.prompts = []

    def complete(self, messages, tools, *, model):
        self.prompts.append(messages)
        legs = [{"symbol": c, "delta_lots": self.deltas.get(c, 0.0), "rationale": "stub"} for c in COMMODITIES]
        return {"role": "assistant", "content": json.dumps({"legs": legs, "portfolio_note": "stub"}), "tool_calls": None}


def call(direction, conviction=5):
    return {"call": direction, "conviction": conviction, "thesis": "t", "key_drivers": []}


def calls(**by_commodity):
    return {c: by_commodity.get(c, call("neutral", 3)) for c in COMMODITIES}


def cycle_id(when):
    return "cycle_" + when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def run(storage, market, when, call_map, manager, *, off_schedule=False, reset=None, lag=timedelta(0)):
    # lag: how long after its scheduled time the stage runs. On AWS it is always minutes (the seven
    # pipelines run first), so `as_of` (the scheduled time) is always in the past by then.
    return run_portfolio(
        cycle_id(when), call_map, storage, as_of=when, now=when + lag, chat_client=manager, model="stub",
        market_client=market, config=CONFIG, breaker_reset_at=lambda: reset, off_schedule=off_schedule,
    )


def read_cycle(storage, book, when):
    return json.loads(storage.get(f"portfolio/{book}/cycles/{cycle_id(when)}.json"))


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path)


def test_first_cycle_fills_both_books_and_holds_the_failed_commodity(storage):
    manager = StubManager({"corn": 8.0, "soybeans": -2.0, "cocoa": 3.0})
    c = calls(corn=call("bullish"), soybeans=call("bearish")) | {"cocoa": None}

    run(storage, FakeMarket(), CYCLE1, c, manager)

    mgr, shadow = read_cycle(storage, "manager", CYCLE1), read_cycle(storage, "shadow", CYCLE1)
    assert mgr["applied_deltas"]["corn"] == pytest.approx(8.0)
    assert mgr["applied_deltas"]["soybeans"] == pytest.approx(-2.0)
    assert mgr["applied_deltas"]["cocoa"] == 0.0  # failed commodity held
    assert "missing_input" in [v["rule"] for v in mgr["violations"]]
    assert [(f["symbol"], f["trade_side"], f["volume"]) for f in mgr["fills"]] == [
        ("corn", "BUY", 800_000), ("soybeans", "SELL", 200_000),
    ]
    # shadow: 1% x $10,000 / $100 per lot = 1 lot at conviction 5
    assert shadow["applied_deltas"]["corn"] == pytest.approx(1.0)
    assert shadow["applied_deltas"]["soybeans"] == pytest.approx(-1.0)
    assert {p["symbol_name"] for p in mgr["closing_state"]["positions"]} == {"corn", "soybeans"}


def _first_cycle(storage, market):
    manager = StubManager({"corn": 8.0, "soybeans": -2.0})
    run(storage, market, CYCLE1, calls(corn=call("bullish"), soybeans=call("bearish")), manager)


def _crash(market):
    market.h1["Corn"] = [
        {"timestamp": "2026-01-07T02:00:00+00:00", "open": 5.0, "high": 4.9, "low": 3.5, "close": 3.6, "volume": 1}
    ]
    market.bid["Corn"], market.ask["Corn"] = 3.60, 3.62


def test_next_cycle_resumes_from_the_last_closing_state_and_replays_a_stop_out(storage):
    market = FakeMarket()
    _first_cycle(storage, market)
    _crash(market)

    run(storage, market, CYCLE2, calls(corn=call("bullish"), soybeans=call("bearish")), StubManager())

    mgr, shadow = read_cycle(storage, "manager", CYCLE2), read_cycle(storage, "shadow", CYCLE2)
    assert {p["symbol_name"]: p["volume"] for p in mgr["opening_state"]["positions"]} == {"corn": 800_000, "soybeans": 200_000}
    [stop] = mgr["stop_outs"]
    assert (stop["symbol"], stop["trade_side"], stop["volume"], stop["reason"]) == ("corn", "SELL", 800_000, "stop_out")
    assert 3.5 < stop["price"] < 4.5
    assert "corn" not in {p["symbol_name"] for p in mgr["closing_state"]["positions"]}
    assert mgr["equity"] < 8_000  # the crash was realized, not shrugged off
    # the shadow book's 1-lot corn position survives the same crash
    assert shadow["stop_outs"] == []
    assert "corn" in {p["symbol_name"] for p in shadow["closing_state"]["positions"]}



def test_a_stage_running_minutes_after_its_scheduled_time_fills_at_a_fresh_quote_and_replays(storage):
    # The scheduled cycle on AWS: as_of = scheduled_time, the stage starts ~5 minutes later.
    # Quotes and H1 bars must still be captured live (at the execution instant), or the fills
    # have no price and the stop-out replay sees no bars.
    lag = timedelta(minutes=5)
    market = FakeMarket()
    run(storage, market, CYCLE1, calls(corn=call("bullish"), soybeans=call("bearish")),
        StubManager({"corn": 8.0, "soybeans": -2.0}), lag=lag)
    _crash(market)

    run(storage, market, CYCLE2, calls(corn=call("bullish"), soybeans=call("bearish")), StubManager(), lag=lag)

    first, second = read_cycle(storage, "manager", CYCLE1), read_cycle(storage, "manager", CYCLE2)
    assert [(f["symbol"], f["price"]) for f in first["fills"]] == [("corn", 5.01), ("soybeans", 4.99)]
    assert first["quoted_at"] == (CYCLE1 + lag).isoformat()
    [stop] = second["stop_outs"]  # bars up to the scheduled time were replayed
    assert (stop["symbol"], stop["reason"]) == ("corn", "stop_out")


def test_the_replay_never_uses_bars_after_the_scheduled_time(storage):
    lag = timedelta(minutes=5)
    market = FakeMarket()
    run(storage, market, CYCLE1, calls(corn=call("bullish")), StubManager({"corn": 8.0}), lag=lag)
    # A crash that only starts after CYCLE2's scheduled time, inside the stage's lag.
    corn = CTRADER_SYMBOL_NAMES["corn"]
    market.h1[corn] = [{"timestamp": CYCLE2.astimezone(timezone.utc).isoformat(), "open": 5.0, "high": 5.0,
                        "low": 1.0, "close": 1.0, "volume": 1}]

    run(storage, market, CYCLE2, calls(corn=call("bullish")), StubManager(), lag=lag)

    assert read_cycle(storage, "manager", CYCLE2)["stop_outs"] == []

def _into_drawdown(storage):
    market = FakeMarket()
    _first_cycle(storage, market)
    _crash(market)
    run(storage, market, CYCLE2, calls(corn=call("bullish"), soybeans=call("bearish")), StubManager())
    return market


def test_the_breaker_trips_on_a_20_percent_drawdown_and_blocks_increases_until_reset(storage):
    market = _into_drawdown(storage)
    assert read_cycle(storage, "manager", CYCLE2)["breaker_active"] is True
    assert read_cycle(storage, "shadow", CYCLE2)["breaker_active"] is False

    run(storage, market, CYCLE3, calls(corn=call("bullish")), StubManager({"corn": 2.0}))

    mgr = read_cycle(storage, "manager", CYCLE3)
    assert mgr["applied_deltas"]["corn"] == 0.0
    assert "drawdown_breaker" in [v["rule"] for v in mgr["violations"]]
    assert mgr["breaker_active"] is True  # sticky, even though nothing new happened


def test_a_manual_reset_clears_the_breaker_and_rebases_the_peak_so_it_does_not_retrip(storage):
    market = _into_drawdown(storage)
    reset = datetime(2026, 1, 7, 12, 0, tzinfo=timezone.utc)

    run(storage, market, CYCLE3, calls(corn=call("bullish")), StubManager({"corn": 2.0}), reset=reset)

    mgr = read_cycle(storage, "manager", CYCLE3)
    assert mgr["breaker_active"] is False
    assert mgr["breaker_reset_applied"] == reset.isoformat()
    assert mgr["applied_deltas"]["corn"] > 0
    assert mgr["peak_equity"] == pytest.approx(mgr["opening_equity"])


def test_an_off_schedule_cycle_logs_everything_but_writes_neither_book(storage):
    manager = StubManager({"corn": 8.0})

    result = run(storage, FakeMarket(), CYCLE1, calls(corn=call("bullish")), manager, off_schedule=True)

    assert storage.list("portfolio/manager/cycles/") == []
    assert storage.list("portfolio/shadow/cycles/") == []
    assert result["manager"]["fills"]  # would-be fills are still reported
    assert storage.get(f"portfolio/logs/{cycle_id(CYCLE1)}.jsonl") is not None


def test_the_portfolio_log_records_raw_and_applied_proposals_violations_and_fills_per_book(storage):
    run(storage, FakeMarket(), CYCLE1, calls(corn=call("bullish")) | {"cocoa": None}, StubManager({"corn": 8.0, "cocoa": 1.0}))

    events = [json.loads(line) for line in storage.get(f"portfolio/logs/{cycle_id(CYCLE1)}.jsonl").decode().splitlines()]
    seen = {(e["agent"], e["event_type"]) for e in events}

    for book in ("manager", "shadow"):
        for event_type in ("proposal_raw", "proposal_applied", "violations", "fills"):
            assert (book, event_type) in seen
    assert ("manager", "prompt") in seen and ("manager", "response") in seen
    applied = next(e for e in events if e["agent"] == "manager" and e["event_type"] == "proposal_applied")
    assert applied["payload"]["corn"] == pytest.approx(8.0)


def test_cycle_file_records_the_calls_given_and_which_commodities_failed(storage):
    c = calls(corn=call("bullish")) | {"cocoa": None}

    run(storage, FakeMarket(), CYCLE1, c, StubManager())

    for book in ("manager", "shadow"):
        record = read_cycle(storage, book, CYCLE1)
        assert record["failed_commodities"] == ["cocoa"]
        assert record["calls"]["corn"]["call"] == "bullish"
        assert record["calls"]["cocoa"] is None


def test_cycle_file_records_closing_lots_per_symbol(storage):
    run(storage, FakeMarket(), CYCLE1, calls(corn=call("bullish"), soybeans=call("bearish")), StubManager({"corn": 8.0, "soybeans": -2.0}))

    lots = read_cycle(storage, "manager", CYCLE1)["closing_lots"]

    assert lots["corn"] == pytest.approx(8.0)
    assert lots["soybeans"] == pytest.approx(-2.0)
    assert lots["cocoa"] == 0.0
