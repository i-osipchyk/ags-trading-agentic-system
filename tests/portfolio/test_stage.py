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


def run(storage, market, when, call_map, manager, *, reset=None, lag=timedelta(0)):
    # lag: how long after its scheduled time the stage runs. On AWS it is always minutes (the seven
    # pipelines run first), so `as_of` (the scheduled time) is always in the past by then.
    return run_portfolio(
        cycle_id(when), call_map, storage, as_of=when, now=when + lag, chat_client=manager, model="stub",
        market_client=market, config=CONFIG, breaker_reset_at=lambda: reset,
    )


def read_cycle(storage, book, when):
    return json.loads(storage.get(f"portfolio/{book}/cycles/{cycle_id(when)}.json"))


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path)


class FailingManager:
    def complete(self, messages, tools, *, model):
        raise ConnectionError("provider down")


def test_a_manager_call_that_raises_holds_the_manager_book_and_the_shadow_book_still_trades(storage):
    run(storage, FakeMarket(), CYCLE1, calls(corn=call("bullish")), FailingManager())

    mgr, shadow = read_cycle(storage, "manager", CYCLE1), read_cycle(storage, "shadow", CYCLE1)
    assert mgr["raw_proposal"] is None
    assert set(mgr["applied_deltas"].values()) == {0.0}
    assert [v["rule"] for v in mgr["violations"]] == ["schema"]
    assert "ConnectionError: provider down" in mgr["manager_error"]
    assert shadow["applied_deltas"]["corn"] == pytest.approx(1.0)


class FlakyManager(StubManager):
    """Raises on the first call, then answers like StubManager."""

    def complete(self, messages, tools, *, model):
        if not self.prompts:
            self.prompts.append(messages)
            raise ConnectionError("provider blip")
        return super().complete(messages, tools, model=model)


def test_a_manager_call_that_raises_once_is_retried_and_its_proposal_used(storage):
    run(storage, FakeMarket(), CYCLE1, calls(corn=call("bullish")), FlakyManager({"corn": 2.0}))

    mgr = read_cycle(storage, "manager", CYCLE1)
    assert mgr["manager_error"] is None
    assert mgr["applied_deltas"]["corn"] == pytest.approx(2.0)
    errors = [json.loads(line) for line in storage.get(f"portfolio/logs/{cycle_id(CYCLE1)}.jsonl").decode().splitlines()]
    assert [e["payload"]["attempt"] for e in errors if e["event_type"] == "error"] == [1]


class ShortHistoryMarket(FakeMarket):
    """Too few daily bars for corn's ATR."""

    def get_daily_bars(self, symbol_name, start, end):
        bars = super().get_daily_bars(symbol_name, start, end)
        return bars[-3:] if symbol_name == NAMES_TO_SYMBOL["corn"] else bars


NAMES_TO_SYMBOL = dict(CTRADER_SYMBOL_NAMES)


def test_a_symbol_without_risk_facts_is_held_and_the_cycle_still_runs(storage):
    manager = StubManager({"corn": 2.0, "cocoa": 1.0})

    run(storage, ShortHistoryMarket(), CYCLE1, calls(corn=call("bullish"), cocoa=call("bullish")), manager)

    mgr, shadow = read_cycle(storage, "manager", CYCLE1), read_cycle(storage, "shadow", CYCLE1)
    assert mgr["applied_deltas"]["corn"] == 0.0 and shadow["applied_deltas"]["corn"] == 0.0
    assert "missing_input" in [v["rule"] for v in mgr["violations"] if v["symbol"] == "corn"]
    assert "ValueError" in mgr["risk_errors"]["corn"]
    assert mgr["applied_deltas"]["cocoa"] == pytest.approx(1.0)
    # The manager is told corn couldn't be measured rather than given made-up numbers.
    prompt = json.loads(manager.prompts[0][1]["content"])
    assert prompt["risk"]["corn"] == {"atr": None, "atr_usd_per_lot": None}


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


def test_h1_bars_are_captured_for_every_symbol_not_only_the_ones_held(storage):
    market = FakeMarket()
    _first_cycle(storage, market)  # holds corn and soybeans only
    market.h1["Cocoa"] = [
        {"timestamp": "2026-01-07T02:00:00+00:00", "open": 5.0, "high": 5.1, "low": 4.9, "close": 5.0, "volume": 1}
    ]

    run(storage, market, CYCLE2, calls(), StubManager())

    assert storage.list("data/ctrader/h1_bars/cocoa/")


def test_the_drawdown_breaker_defaults_to_a_40_percent_drawdown():
    assert PortfolioConfig.from_mapping({}).drawdown_breaker == pytest.approx(0.60)


# --- replay mode: market data read back from what the official cycle captured, never fetched or written


class NoMarket:
    def __getattr__(self, name):
        raise AssertionError(f"a replay must not call the market ({name})")


class ReadOnlyData:
    def __init__(self, inner):
        self._inner = inner

    def get(self, key):
        return self._inner.get(key)

    def list(self, prefix):
        return self._inner.list(prefix)

    def put(self, key, data):
        raise AssertionError(f"a replay must not write market data ({key})")

    put_if_absent = put


LATE = timedelta(minutes=7)  # a live cycle reads the market minutes after its scheduled time
LATER = timedelta(days=30)


def _official(storage, market, when, call_map, manager):
    run_portfolio(
        cycle_id(when), call_map, storage, as_of=when, now=when + LATE, chat_client=manager, model="stub",
        market_client=market, config=CONFIG,
    )
    return read_cycle(storage, "manager", when)


def _replayed(storage, when, call_map, manager, quoted_at):
    from ags.storage import Prefixed

    return run_portfolio(
        cycle_id(when), call_map, Prefixed(storage, "replays/r1"), as_of=when, now=when + LATER, chat_client=manager,
        model="stub", market_client=NoMarket(), config=CONFIG, data=ReadOnlyData(Prefixed(storage, "data")),
        quoted_at=datetime.fromisoformat(quoted_at),
    )


def test_a_replayed_cycle_fills_at_the_quotes_the_official_cycle_captured(storage):
    market = FakeMarket()
    official = _official(storage, market, CYCLE1, calls(corn=call("bullish")), StubManager())
    market.bid["Corn"], market.ask["Corn"] = 9.99, 10.01  # the market moved on since

    replayed = _replayed(storage, CYCLE1, calls(corn=call("bullish")), StubManager({"corn": 8.0}), official["quoted_at"])

    assert [(f["symbol"], f["price"]) for f in replayed["manager"]["fills"]] == [("corn", 5.01)]
    assert storage.list("replays/r1/portfolio/manager/cycles/") == [f"replays/r1/portfolio/manager/cycles/{cycle_id(CYCLE1)}.json"]


def test_a_replayed_cycle_reproduces_the_official_stop_out_from_stored_h1_bars(storage):
    market = FakeMarket()
    c = calls(corn=call("bullish"), soybeans=call("bearish"))
    first = _official(storage, market, CYCLE1, c, StubManager({"corn": 8.0, "soybeans": -2.0}))
    _crash(market)
    second = _official(storage, market, CYCLE2, c, StubManager())

    _replayed(storage, CYCLE1, c, StubManager({"corn": 8.0, "soybeans": -2.0}), first["quoted_at"])
    replayed = _replayed(storage, CYCLE2, c, StubManager(), second["quoted_at"])

    assert second["stop_outs"]  # the scenario does stop out
    assert replayed["manager"]["stop_outs"] == second["stop_outs"]
    assert replayed["manager"]["equity"] == pytest.approx(second["equity"])


# --- manager on the demo account ---

from ags.portfolio.stage import fill_costs  # noqa: E402
from tests.portfolio.test_demo_book import FakeGateway, position  # noqa: E402


def _demo_run(tmp_path, gateway, *, deltas=None, as_of=CYCLE1, now=None, cycle_id="cycle_demo", **extra):
    specs_market = FakeMarket()
    ids = {name: i for i, name in enumerate(CTRADER_SYMBOL_NAMES.values(), start=1)}
    specs_market.get_symbol_specs = lambda symbol_name: {**SPEC, "symbol_id": ids[symbol_name]}
    config = PortfolioConfig(**{**CONFIG.__dict__, "manager_book": "demo"})
    return run_portfolio(
        cycle_id, calls(corn=call("bullish", 8)), LocalStorage(tmp_path), as_of=as_of,
        now=now or as_of, chat_client=StubManager({"corn": 0.5}), model="m", market_client=specs_market, config=config,
        trading_gateway=lambda: gateway, **extra,
    )


def test_the_manager_trades_the_demo_account_and_the_shadow_stays_on_paper(tmp_path):
    gateway = FakeGateway(balance=777_000.0)  # the account's balance is other strategies' business

    cycle = _demo_run(tmp_path, gateway)

    manager = cycle["manager"]
    assert manager["book_kind"] == "demo" and cycle["shadow"]["book_kind"] == "paper"
    assert [c[:3] for c in gateway.calls] == [("place", "BUY", 50_000)]
    assert [(p["symbol_name"], p["volume"]) for p in manager["closing_state"]["positions"]] == [("corn", 50_000)]
    # Equity is the 10k starting capital plus the manager's own result, not the account's balance.
    assert manager["opening_equity"] == 10_000.0
    assert manager["closing_state"]["balance"] == 10_000.0 - 3.0
    assert 9_000 < manager["equity"] < 11_000
    assert manager["costs"]["commission"] == 3.0 and cycle["shadow"]["costs"]["commission"] == 0.0
    assert (tmp_path / "portfolio/demo/manager/cycles/cycle_demo.json").exists()  # its own store
    assert (tmp_path / "portfolio/shadow/cycles/cycle_demo.json").exists()
    assert not (tmp_path / "portfolio/manager").exists()  # the paper manager store is untouched


def test_a_second_attempt_at_a_cycle_the_manager_already_traded_refuses_to_trade(tmp_path):
    gateway = FakeGateway(orders=[{"client_order_id": "cycle_demo:corn", "position_id": 9, "closing": False, "is_stop_out": False}])

    with pytest.raises(RuntimeError, match="already sent orders"):
        _demo_run(tmp_path, gateway)
    assert gateway.calls == []


def test_other_strategies_orders_on_the_account_do_not_stop_the_manager_trading(tmp_path):
    gateway = FakeGateway(orders=[{"client_order_id": "other-bot", "position_id": 5, "closing": False, "is_stop_out": False}])

    _demo_run(tmp_path, gateway)

    assert [c[0] for c in gateway.calls] == ["place"]


def test_the_next_demo_cycle_continues_from_the_managers_own_balance(tmp_path):
    gateway = FakeGateway(balance=1.0)
    first = _demo_run(tmp_path, gateway)
    gateway.calls.clear()

    second = _demo_run(tmp_path, gateway, cycle_id="cycle_demo2", as_of=CYCLE2, now=CYCLE2)

    assert second["manager"]["opening_state"]["balance"] == first["manager"]["closing_state"]["balance"]
    assert [p["volume"] for p in second["manager"]["opening_state"]["positions"]] == [50_000]


def test_the_demo_manager_starts_from_scratch_whatever_the_paper_manager_has_recorded(tmp_path):
    storage = LocalStorage(tmp_path)
    paper = run_portfolio(
        "cycle_1_paper", calls(corn=call("bullish", 8)), storage, as_of=CYCLE1, now=CYCLE1,
        chat_client=StubManager({"corn": 0.5}), model="m", market_client=FakeMarket(), config=CONFIG,
    )["manager"]
    assert paper["closing_state"]["positions"]  # long corn on paper
    gateway = FakeGateway()

    cycle = _demo_run(tmp_path, gateway, cycle_id="cycle_2_demo", as_of=CYCLE2, now=CYCLE2)

    manager = cycle["manager"]
    assert manager["opening_state"]["positions"] == [] and manager["opening_state"]["balance"] == 10_000.0
    assert manager["opening_equity"] == 10_000.0 and manager["opening_state"]["peak_equity"] == 10_000.0
    assert paper["book_kind"] == "paper" and (tmp_path / "portfolio/manager/cycles/cycle_1_paper.json").exists()
    # the paper chain did not move; the shadow carried on from its own record
    assert not list((tmp_path / "portfolio/manager/cycles").glob("cycle_2*"))
    assert cycle["shadow"]["opening_state"]["positions"]


def test_the_first_demo_cycle_refuses_to_start_when_ags_positions_are_already_open(tmp_path):
    gateway = FakeGateway(positions=[position(5)])

    with pytest.raises(RuntimeError, match="must start flat"):
        _demo_run(tmp_path, gateway)
    assert gateway.calls == []


def test_fill_costs_measure_spread_against_the_midpoint_and_flip_the_brokers_signs():
    quotes = {"corn": {"bid": 4.99, "ask": 5.01}}
    fills = [
        {"symbol": "corn", "trade_side": "BUY", "volume": 100_000, "price": 5.01, "commission": -3.0, "swap": -0.5},
        {"symbol": "corn", "trade_side": "SELL", "volume": 100_000, "price": 4.99, "status": "failed"},
    ]
    costs = fill_costs(fills, quotes)
    assert costs == {"spread": pytest.approx(10.0), "drift": 0.0, "commission": 3.0, "swap": 0.5, "failed_orders": 1}


# --- the price moves between the analysts' reference quote and execution -----------------------

from ags.portfolio.guardrails import apply_guardrails  # noqa: E402
from ags.portfolio.stage import drift_facts, reference_prices  # noqa: E402


def _intraday(price):
    return {"kind": "intraday_quote", "price": price, "bid": price - 0.01, "ask": price + 0.01}


def _bull_corn(reference):
    c = call("bullish", 8)
    return {**c, "price_at_call": _intraday(reference)}


def test_reference_prices_come_only_from_intraday_stamped_calls():
    cs = calls(corn=_bull_corn(5.0), wheat={**call("bullish"), "price_at_call": {"kind": "daily_close", "price": 4.0}})
    ref = reference_prices(cs)
    assert ref["corn"] == 5.0 and ref["wheat"] is None and ref["cocoa"] is None


def test_drift_is_the_execution_move_from_the_reference_in_percent_and_atrs():
    quotes = {c: {"bid": 5.09, "ask": 5.11} for c in COMMODITIES}  # mid 5.10
    drift = drift_facts({**dict.fromkeys(COMMODITIES), "corn": 5.0}, quotes, {c: {"atr": 0.1} for c in COMMODITIES})
    assert drift["corn"] == {"pct": pytest.approx(0.02), "atr": pytest.approx(1.0)}
    assert drift["wheat"] is None


SPECS_ONE = {c: {"step_volume": 10_000, "lot_size": 100_000, "min_volume": 10_000, "margin_rate": 0.1} for c in COMMODITIES}
RISK_ONE = {c: {"notional_per_lot": 5_000.0} for c in COMMODITIES}


def _guard(proposed, current, drift):
    proposal = {"legs": [{"symbol": c, "delta_lots": proposed.get(c, 0.0)} for c in COMMODITIES]}
    book = {"equity": 100_000.0, "lots": {c: current.get(c, 0.0) for c in COMMODITIES}}
    return apply_guardrails(
        proposal, book, calls(corn=call("bullish"), wheat=call("bearish")), SPECS_ONE, RISK_ONE,
        GuardrailConfig(10.0, 10.0, 20.0, 1.0, 0.2, drift_limit_atr=0.5), False, drift=drift,
    )


def test_a_symbol_that_drifted_past_the_limit_takes_no_new_exposure():
    applied, violations = _guard({"corn": 1.0}, {}, {"corn": 0.8})
    assert applied["corn"] == 0.0 and [v["rule"] for v in violations] == ["drift"]


def test_drift_never_blocks_a_reduction_or_a_symbol_within_the_limit():
    applied, violations = _guard({"corn": -0.5, "wheat": -1.0}, {"corn": 1.0}, {"corn": 2.0, "wheat": 0.4})
    assert applied["corn"] == -0.5 and applied["wheat"] == -1.0 and violations == []


class _MovingMarket(FakeMarket):
    """The price jumps $0.20 while the manager's model call is running."""

    def __init__(self):
        super().__init__()
        self.calls_seen = 0

    def jump(self):
        for name in self.bid:
            self.bid[name] += 0.20
            self.ask[name] += 0.20


class _JumpingManager(StubManager):
    def __init__(self, market, deltas):
        super().__init__(deltas)
        self.market = market

    def complete(self, messages, tools, *, model):
        self.market.jump()
        return super().complete(messages, tools, model=model)


def test_both_books_execute_on_quotes_taken_after_the_manager_call_and_record_the_drift(tmp_path):
    market = _MovingMarket()
    reference = 5.0  # mid before the jump; execution mid is 5.20 = 2 ATR (ATR 0.10)
    config = PortfolioConfig(**{**CONFIG.__dict__, "guardrails": GuardrailConfig(
        5.0, 10.0, 20.0, 1.0, 0.20, drift_limit_atr=3.0)})

    cycle = run_portfolio(
        "cycle_drift", calls(corn=_bull_corn(reference)), LocalStorage(tmp_path), as_of=CYCLE1, now=CYCLE1,
        chat_client=_JumpingManager(market, {"corn": 0.5}), model="m", market_client=market, config=config,
    )

    for book in ("manager", "shadow"):
        record = cycle[book]
        assert record["reference_prices"]["corn"] == 5.0
        assert record["drift"]["corn"]["atr"] == pytest.approx(2.0)
        buys = [f for f in record["fills"] if f["symbol"] == "corn"]
        assert buys and all(f["price"] == pytest.approx(5.21) for f in buys)  # the post-jump ask
        assert record["costs"]["drift"] > 0
    assert cycle["manager"]["exec_quoted_at"]


def test_the_drift_limit_holds_back_a_stale_call_in_both_books(tmp_path):
    market = _MovingMarket()
    cycle = run_portfolio(
        "cycle_stale", calls(corn=_bull_corn(5.0)), LocalStorage(tmp_path), as_of=CYCLE1, now=CYCLE1,
        chat_client=_JumpingManager(market, {"corn": 0.5}), model="m", market_client=market, config=CONFIG,
    )  # CONFIG limit is the default 0.5 ATR; the jump is 2 ATR

    for book in ("manager", "shadow"):
        assert cycle[book]["fills"] == []
        assert "drift" in {v["rule"] for v in cycle[book]["violations"]}


# --- demo trading alongside the held-slot and replay machinery ---


def test_a_held_slot_sends_no_orders_to_the_demo_account_and_is_not_recorded(tmp_path):
    gateway = FakeGateway()

    cycle = _demo_run(tmp_path, gateway, hold_books=lambda: True)

    assert cycle["held"] is True and gateway.calls == []
    assert not (tmp_path / "portfolio/demo/manager").exists() and not (tmp_path / "portfolio/shadow").exists()


def test_the_retry_of_a_held_slot_then_trades_and_records_it(tmp_path):
    gateway = FakeGateway()
    _demo_run(tmp_path, gateway, hold_books=lambda: True)

    cycle = _demo_run(tmp_path, gateway)

    assert cycle["held"] is False and [c[0] for c in gateway.calls] == ["place"]
    assert (tmp_path / "portfolio/demo/manager/cycles/cycle_demo.json").exists()


def test_a_replay_never_trades_even_when_the_config_says_demo(storage):
    market = FakeMarket()
    official = _official(storage, market, CYCLE1, calls(corn=call("bullish")), StubManager())
    gateway = FakeGateway()
    from ags.storage import Prefixed

    replayed = run_portfolio(
        cycle_id(CYCLE1), calls(corn=call("bullish")), Prefixed(storage, "replays/r1"), as_of=CYCLE1,
        now=CYCLE1 + LATER, chat_client=StubManager({"corn": 8.0}), model="stub", market_client=NoMarket(),
        config=PortfolioConfig(**{**CONFIG.__dict__, "manager_book": "demo"}),
        data=ReadOnlyData(Prefixed(storage, "data")), quoted_at=datetime.fromisoformat(official["quoted_at"]),
        trading_gateway=lambda: gateway,
    )

    assert gateway.calls == [] and replayed["manager"]["book_kind"] == "paper"
    assert storage.list("replays/r1/portfolio/manager/cycles/")  # the replayed paper manager's own store


def test_a_replay_trades_on_the_execution_quotes_the_official_cycle_recorded(storage):
    market = _MovingMarket()
    official = run_portfolio(
        cycle_id(CYCLE1), calls(corn=call("bullish")), storage, as_of=CYCLE1, now=CYCLE1 + LATE,
        chat_client=_JumpingManager(market, {"corn": 0.5}), model="stub", market_client=market, config=CONFIG,
    )["manager"]
    from datetime import timezone as _tz

    from ags.storage import Prefixed

    replayed = run_portfolio(
        cycle_id(CYCLE1), calls(corn=call("bullish")), Prefixed(storage, "replays/r1"), as_of=CYCLE1,
        now=datetime.now(_tz.utc) + timedelta(days=1), chat_client=StubManager({"corn": 0.5}), model="stub",
        market_client=NoMarket(), config=CONFIG, data=ReadOnlyData(Prefixed(storage, "data")),
        quoted_at=datetime.fromisoformat(official["quoted_at"]),
        execution_quoted_at=datetime.fromisoformat(official["exec_quoted_at"]),
    )["manager"]

    assert official["fills"][0]["price"] == pytest.approx(5.21)  # the post-jump ask, not the 5.01 decision quote
    assert replayed["fills"][0]["price"] == pytest.approx(5.21)


def test_drift_is_unknown_for_a_symbol_whose_atr_could_not_be_measured():
    quotes = {c: {"bid": 5.09, "ask": 5.11} for c in COMMODITIES}
    risk = {c: {"atr": 0.1} for c in COMMODITIES} | {"corn": {"atr": None, "error": "too few bars"}}

    drift = drift_facts(dict.fromkeys(COMMODITIES, 5.0), quotes, risk)

    assert drift["corn"]["atr"] is None and drift["corn"]["pct"] == pytest.approx(0.02)
    assert drift["wheat"]["atr"] == pytest.approx(1.0)
