from datetime import datetime, timezone

import pytest

from ags.portfolio.book import is_market_open
from ags.portfolio.demo_book import LABEL, DemoBook, TradingError

SPEC = {
    "symbol_id": 7, "lot_size": 100_000, "min_volume": 10_000, "step_volume": 10_000, "max_volume": 10**9,
    "margin_rate": 0.1,
}
SPECS = {"corn": SPEC}
QUOTES = {"corn": {"bid": 4.99, "ask": 5.01}}
SINCE = datetime(2026, 1, 6, 11, tzinfo=timezone.utc)


def position(pid, side="BUY", volume=20_000, price=5.00, label=LABEL, symbol_id=7, swap=0.0):
    return {
        "position_id": pid, "symbol_id": symbol_id, "label": label, "trade_side": side, "volume": volume,
        "price": price, "open_timestamp": "t", "swap": swap, "commission": 0.0,
    }


class FakeGateway:
    """An in-memory hedging account shared with other strategies: fills at the quote, $3 commission per deal."""

    def __init__(self, balance=123_456.0, positions=None, fail=None, orders=None, deals=None):
        self.balance = balance  # the whole account's, nothing to do with the manager's capital
        self.positions = positions or []
        self.fail = fail or {}  # "place" / "close" -> TradingError message
        self.orders = orders or []
        self.deals = deals or []
        self.calls = []
        self._next = 100

    def account(self):
        return {"balance": self.balance, "positions": [dict(p) for p in self.positions]}

    def place_market(self, *, symbol_id, side, volume, client_order_id):
        self.calls.append(("place", side, volume, client_order_id))
        if "place" in self.fail:
            raise TradingError(self.fail["place"])
        price = 5.01 if side == "BUY" else 4.99
        self._next += 1
        self.positions.append({**position(self._next, side, volume, price, symbol_id=symbol_id), "commission": -3.0})
        return {"order_id": self._next, "position_id": self._next, "price": price, "commission": -3.0,
                "gross_profit": 0.0, "swap": 0.0}

    def close_position(self, *, position_id, volume):
        self.calls.append(("close", position_id, volume))
        if "close" in self.fail:
            raise TradingError(self.fail["close"])
        pos = next(p for p in self.positions if p["position_id"] == position_id)
        sign = 1 if pos["trade_side"] == "BUY" else -1
        price = 4.99 if sign == 1 else 5.01
        gross = (price - pos["price"]) * volume / 100 * sign
        pos["volume"] -= volume
        if not pos["volume"]:
            self.positions.remove(pos)
        self._next += 1
        return {"order_id": self._next, "position_id": position_id, "price": price, "commission": -3.0,
                "gross_profit": gross, "swap": -0.5}

    def orders_since(self, since):
        return self.orders

    def deals_since(self, since):
        return self.deals


def book(gateway, balance=10_000.0, **kw):
    return DemoBook(gateway, SPECS, cycle_id="cycle_X", balance=balance, **kw)


def test_opening_orders_a_market_buy_labelled_as_the_managers_and_books_the_commission():
    gw = FakeGateway()
    b = book(gw)

    fills = b.apply_fills({"corn": 0.5}, QUOTES, SPECS, "ts")

    assert gw.calls == [("place", "BUY", 50_000, "cycle_X:corn")]
    assert fills[0]["status"] == "filled" and fills[0]["commission"] == -3.0 and fills[0]["price"] == 5.01
    assert b.state()["positions"][0]["volume"] == 50_000
    assert b.state()["balance"] == 9_997.0  # the ledger, not the account's 123,456


def test_equity_is_the_managers_capital_plus_its_own_pnl_whatever_the_account_holds():
    gw = FakeGateway(balance=999_999.0, positions=[
        position(1, volume=20_000, price=4.90, swap=-1.0),  # the manager's: +0.09 * 200 - 1.0 = +17.0
        position(2, volume=500_000, price=1.0, label="other-bot"),  # another strategy's: must not count
        position(3, volume=500_000, price=1.0, label=""),
    ])

    snap = book(gw, balance=10_000.0).snapshot(QUOTES, SPECS)

    assert snap["equity"] == pytest.approx(10_000.0 + (4.99 - 4.90) * 200 - 1.0)


def test_other_strategies_positions_are_never_read_or_closed():
    gw = FakeGateway(positions=[position(2, side="SELL", volume=40_000, label="other-bot")])
    b = book(gw)

    assert b.state()["positions"] == []
    b.apply_fills({"corn": 0.5}, QUOTES, SPECS, "ts")

    assert [c[0] for c in gw.calls] == ["place"]  # no close of the other bot's short
    assert [p["position_id"] for p in gw.positions if p["label"] == "other-bot"] == [2]


def test_below_minimum_volume_sends_nothing():
    gw = FakeGateway()
    assert book(gw).apply_fills({"corn": 0.05}, QUOTES, SPECS, "ts") == []
    assert gw.calls == []


def test_a_reversal_closes_the_position_then_opens_the_remainder_and_books_every_deal_in_the_ledger():
    gw = FakeGateway(positions=[position(1, volume=20_000, price=5.00)])
    b = book(gw, balance=10_000.0)

    fills = b.apply_fills({"corn": -0.5}, QUOTES, SPECS, "ts")

    assert [c[0] for c in gw.calls] == ["close", "place"]
    assert gw.calls[0][2] == 20_000 and gw.calls[1][2] == 30_000
    closing, opening = fills
    price_pnl = (4.99 - 5.00) * 200
    assert closing["realized_pnl"] == pytest.approx(price_pnl - 0.5) and closing["swap"] == -0.5
    assert opening["realized_pnl"] == 0.0
    # capital + realized price pnl + realized swap + both commissions (close and open)
    assert b.state()["balance"] == pytest.approx(10_000.0 + price_pnl - 0.5 - 3.0 - 3.0)
    assert [(p["trade_side"], p["volume"]) for p in b.state()["positions"]] == [("SELL", 30_000)]


def test_several_same_side_positions_net_into_one_with_a_weighted_price():
    gw = FakeGateway(positions=[position(1, volume=10_000, price=5.0, swap=-1.0), position(2, volume=30_000, price=6.0, swap=-2.0)])
    (net,) = book(gw).state()["positions"]
    assert net["volume"] == 40_000 and net["price"] == pytest.approx(5.75) and net["swap"] == -3.0
    assert net["symbol_name"] == "corn" and net["position_ids"] == [1, 2]


def test_a_rejected_order_is_recorded_and_the_cycle_carries_on():
    gw = FakeGateway(fail={"place": "MARKET_CLOSED"})
    b = book(gw)

    fills = b.apply_fills({"corn": 0.5}, QUOTES, SPECS, "ts")

    assert fills[0]["status"] == "failed" and fills[0]["error"] == "MARKET_CLOSED"
    assert b.state()["positions"] == [] and b.state()["balance"] == 10_000.0


def test_peak_equity_starts_at_the_capital_and_follows_snapshots():
    b = book(FakeGateway(), balance=10_000.0)
    assert b.state()["peak_equity"] == 10_000.0
    b.rebase_peak(9_000.0)
    assert b.state()["peak_equity"] == 9_000.0
    assert b.snapshot(QUOTES, SPECS)["equity"] == 10_000.0
    assert b.state()["peak_equity"] == 10_000.0


def test_replay_leaves_stop_outs_to_the_broker():
    assert book(FakeGateway()).replay({}, SPECS, None, None) == []


# --- running alongside other strategies ---


def test_other_strategies_orders_do_not_look_like_a_repeated_cycle():
    gw = FakeGateway(orders=[
        {"client_order_id": "other_1", "position_id": 50, "closing": False, "is_stop_out": False},
        {"client_order_id": "", "position_id": 51, "closing": True, "is_stop_out": False},
    ])
    assert book(gw).already_traded(SINCE) is False


def test_the_managers_own_orders_this_cycle_do_look_like_a_repeat():
    opened = FakeGateway(orders=[{"client_order_id": "cycle_X:corn", "position_id": 9, "closing": False, "is_stop_out": False}])
    closed = FakeGateway(
        positions=[position(1)], orders=[{"client_order_id": "", "position_id": 1, "closing": True, "is_stop_out": False}]
    )
    stopped_out = FakeGateway(
        positions=[position(1)], orders=[{"client_order_id": "", "position_id": 1, "closing": True, "is_stop_out": True}]
    )

    assert book(opened).already_traded(SINCE) and book(closed).already_traded(SINCE)
    assert not book(stopped_out).already_traded(SINCE)  # a broker stop-out is not this cycle trading


def test_a_position_closed_without_the_manager_is_booked_from_the_deal_history():
    deals = [
        {"deal_id": 1, "position_id": 11, "closing": True, "commission": -3.0, "gross_profit": -40.0, "swap": -2.0},
        {"deal_id": 2, "position_id": 99, "closing": True, "commission": -3.0, "gross_profit": 500.0, "swap": 0.0},  # other bot
        {"deal_id": 3, "position_id": 11, "closing": False, "commission": -3.0, "gross_profit": 0.0, "swap": 0.0},  # its opening
    ]
    gw = FakeGateway(positions=[position(12)], deals=deals)
    b = book(gw, balance=10_000.0, known_position_ids=[11, 12])  # last cycle held 11 and 12; 11 is gone

    closes = b.reconcile(SINCE)

    assert [d["deal_id"] for d in closes] == [1]
    assert b.state()["balance"] == pytest.approx(10_000.0 - 40.0 - 2.0 - 3.0)


def test_reconcile_does_nothing_when_every_known_position_is_still_open():
    gw = FakeGateway(positions=[position(12)], deals=[{"deal_id": 1, "position_id": 12, "closing": True,
                                                        "commission": -3.0, "gross_profit": 9.0, "swap": 0.0}])
    b = book(gw, known_position_ids=[12])
    assert b.reconcile(SINCE) == [] and b.state()["balance"] == 10_000.0


# --- trading hours ---

# Pepperstone sugar, summer: 07:30-16:59 UTC Mon-Fri, as cTrader sends it (seconds from Sunday 00:00 UTC).
SUGAR = {"schedule_timezone": "UTC", "schedule": [[d * 86_400 + 7 * 3600 + 1800, d * 86_400 + 16 * 3600 + 59 * 60] for d in range(1, 6)]}
TUE = datetime(2026, 10, 27, tzinfo=timezone.utc)


def test_market_open_follows_the_weekly_schedule_in_its_own_time_zone():
    assert is_market_open(SUGAR, TUE.replace(hour=10))
    assert not is_market_open(SUGAR, TUE.replace(hour=7, minute=15))
    assert not is_market_open(SUGAR, TUE.replace(hour=17))
    assert not is_market_open(SUGAR, datetime(2026, 10, 31, 10, tzinfo=timezone.utc))  # Saturday
    assert is_market_open({}, TUE.replace(hour=3))  # no schedule known: let the broker decide


def test_a_closed_market_sends_no_order_and_is_recorded_as_market_closed():
    gw = FakeGateway()
    closed = {"corn": {**SPEC, **SUGAR}}
    b = DemoBook(gw, closed, cycle_id="cycle_X", balance=10_000.0, clock=lambda: TUE.replace(hour=20))

    fills = b.apply_fills({"corn": 0.5}, QUOTES, closed, "ts")

    assert gw.calls == [] and [(f["status"], f["error"]) for f in fills] == [("market_closed", "market closed")]
