import pytest

from ags.portfolio.paper_book import PaperBook

# Corn-like symbol: 1 lot = 1,000 units (lot_size is in cTrader's 1/100-unit volume),
# 0.1-lot steps, 10:1 margin.
SPECS = {
    "corn": {
        "symbol_id": 1, "lot_size": 100_000, "min_volume": 10_000, "step_volume": 10_000,
        "max_volume": 10_000_000, "swap_long": -5.0, "swap_short": 2.0, "swap_type": "PIPS",
        "triple_swap_day": "WEDNESDAY", "pip_position": 2, "margin_rate": 0.1,
    }
}
T0 = "2026-01-06T21:00:00+00:00"


def quote(bid, ask):
    return {"corn": {"bid": bid, "ask": ask}}


def test_buy_opens_a_long_at_the_ask_and_marks_to_the_bid():
    book = PaperBook(starting_equity=10_000)

    fills = book.apply_fills({"corn": 2.0}, quote(4.99, 5.01), SPECS, T0)

    assert fills == [
        {"symbol": "corn", "trade_side": "BUY", "volume": 200_000, "price": 5.01, "realized_pnl": 0.0, "timestamp": T0}
    ]
    snap = book.snapshot(quote(4.99, 5.01), SPECS)
    assert snap["equity"] == pytest.approx(10_000 - 0.02 * 2000)  # paid the 2c spread on 2,000 units
    assert snap["used_margin"] == pytest.approx(5.00 * 2000 * 0.1)  # mid price
    assert snap["free_margin"] == pytest.approx(snap["equity"] - snap["used_margin"])


def _long_two_lots():
    book = PaperBook(starting_equity=10_000)
    book.apply_fills({"corn": 2.0}, quote(4.99, 5.01), SPECS, T0)  # BUY 2 lots @ 5.01
    return book


def _position(book):
    positions = book.state()["positions"]
    return positions[0] if positions else None


# start: long 2 lots @ 5.01, then trade at bid 5.19 / ask 5.21
@pytest.mark.parametrize(
    "delta, side, volume, price, balance",
    [
        (+1.0, "BUY", 300_000, 15230 / 3000, 10_000),  # add: volume-weighted entry, nothing realized
        (-1.0, "BUY", 100_000, 5.01, 10_000 + 180),  # reduce keeps entry; (5.19-5.01) * 1000 units
        (-2.0, None, None, None, 10_000 + 360),  # close
        (-3.0, "SELL", 100_000, 5.19, 10_000 + 360),  # flip: close 2 lots, open 1 short at the bid
        (-2.95, "SELL", 90_000, 5.19, 10_000 + 360),  # delta rounds toward zero first: 2.9 lots
    ],
)
def test_fills_net_into_one_position_per_symbol(delta, side, volume, price, balance):
    book = _long_two_lots()

    book.apply_fills({"corn": delta}, quote(5.19, 5.21), SPECS, T0)

    position = _position(book)
    if side is None:
        assert position is None
    else:
        assert (position["trade_side"], position["volume"]) == (side, volume)
        assert position["price"] == pytest.approx(price)
    assert book.state()["balance"] == pytest.approx(balance)


def test_volume_rounds_toward_zero_to_the_step_and_below_min_is_not_filled():
    book = PaperBook(starting_equity=10_000)

    short = book.apply_fills({"corn": -0.234}, quote(4.99, 5.01), SPECS, T0)
    nothing = book.apply_fills({"corn": 0.05}, quote(4.99, 5.01), SPECS, T0)

    assert [(f["trade_side"], f["volume"]) for f in short] == [("SELL", 20_000)]
    assert nothing == []
    assert _position(book)["volume"] == 20_000


def test_closing_fill_reports_realized_pnl_and_flip_reports_two_fills():
    book = _long_two_lots()

    fills = book.apply_fills({"corn": -3.0}, quote(5.19, 5.21), SPECS, T0)

    assert [(f["trade_side"], f["volume"], f["price"], round(f["realized_pnl"], 6)) for f in fills] == [
        ("SELL", 200_000, 5.19, 360.0),
        ("SELL", 100_000, 5.19, 0.0),
    ]


from datetime import date

# 2026-01-05 is a Monday
MON, TUE, WED, THU, FRI, SAT, SUN, NEXT_MON = (date(2026, 1, 5 + i) for i in range(8))


def _swap(book):
    return book.state()["positions"][0]["swap"]


@pytest.mark.parametrize(
    "day, expected",
    [
        (TUE, -100.0),  # -5 pips * 0.01 * 2,000 units
        (WED, -300.0),  # triple-swap day
        (SAT, 0.0),  # no rollover at the weekend
    ],
)
def test_long_pays_swap_at_each_rollover_and_triple_on_the_triple_swap_day(day, expected):
    book = _long_two_lots()

    book.accrue_swap(day, SPECS)

    assert _swap(book) == pytest.approx(expected)


def test_a_position_held_over_a_weekend_accrues_only_on_weekday_rollovers():
    book = _long_two_lots()

    for day in (THU, FRI, SAT, SUN, NEXT_MON):
        book.accrue_swap(day, SPECS)

    assert _swap(book) == pytest.approx(-300.0)


def test_short_uses_the_short_swap_rate_and_a_credit_raises_equity():
    book = PaperBook(starting_equity=10_000)
    book.apply_fills({"corn": -2.0}, quote(4.99, 5.01), SPECS, T0)  # SELL @ 4.99

    book.accrue_swap(TUE, SPECS)

    assert _swap(book) == pytest.approx(40.0)  # +2 pips * 0.01 * 2,000 units
    assert book.snapshot(quote(4.99, 5.01), SPECS)["equity"] == pytest.approx(10_000 - 0.02 * 2000 + 40)


def test_percentage_swap_is_an_annual_rate_on_notional_per_day():
    specs = {"corn": {**SPECS["corn"], "swap_type": "PERCENTAGE", "swap_long": -3.0}}
    book = PaperBook(starting_equity=10_000)
    book.apply_fills({"corn": 2.0}, quote(4.99, 5.01), specs, T0)

    book.accrue_swap(TUE, specs, prices={"corn": 5.00})

    assert _swap(book) == pytest.approx(-3.0 / 100 / 365 * 5.00 * 2000)


def test_swap_realizes_into_balance_when_the_position_closes():
    book = _long_two_lots()
    book.accrue_swap(TUE, SPECS)

    book.apply_fills({"corn": -2.0}, quote(5.01, 5.03), SPECS, T0)

    assert book.state()["balance"] == pytest.approx(10_000 + 0.0 - 100.0)


# ---- stop-out replay ---------------------------------------------------------
from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ags.portfolio.book import Position

ET = ZoneInfo("America/New_York")
WHEAT_SPEC = {**SPECS["corn"], "symbol_id": 2}
TWO_SPECS = {"corn": SPECS["corn"], "wheat": WHEAT_SPEC}
SINCE = datetime(2026, 1, 6, 12, tzinfo=timezone.utc)  # Tuesday
UNTIL = datetime(2026, 1, 6, 16, tzinfo=timezone.utc)


def h1(symbol_hour, high, low, close=None):
    close = close if close is not None else (high + low) / 2
    ts = datetime(2026, 1, 6, symbol_hour, tzinfo=timezone.utc).isoformat()
    return {"timestamp": ts, "open": close, "high": high, "low": low, "close": close, "volume": 1}


def book_with(positions, balance, marks):
    state = {
        "balance": balance, "peak_equity": balance, "next_position_id": 10,
        "positions": [asdict(Position(i + 1, s, TWO_SPECS[s]["symbol_id"], side, vol, px, T0)) for i, (s, side, vol, px) in enumerate(positions)],
        "marks": marks,
    }
    return PaperBook(state, stop_out_level=0.5)


def test_drawdown_through_the_stop_out_level_closes_the_position_at_the_breach_price():
    # 10 lots long @5.01. Equity(p) = 10000 + 10000(p-5.01); margin(p) = 1000p;
    # equity = 50% of margin at p = 40100 / 9500.
    book = book_with([("corn", "BUY", 1_000_000, 5.01)], 10_000, {"corn": 5.01})

    events = book.replay({"corn": [h1(12, high=5.05, low=4.00, close=4.10)]}, TWO_SPECS, SINCE, UNTIL)

    breach = 40100 / 9500
    assert [(e["symbol"], e["volume"], e["reason"]) for e in events] == [("corn", 1_000_000, "stop_out")]
    assert events[0]["price"] == pytest.approx(breach)
    assert book.state()["positions"] == []
    assert book.state()["balance"] == pytest.approx(10_000 + (breach - 5.01) * 10_000)


def test_short_is_stopped_out_on_the_bars_high():
    # equity(p) = 10000 + 10000(4.99-p); margin(p) = 1000p; breach at p = 59900 / 10500
    book = book_with([("corn", "SELL", 1_000_000, 4.99)], 10_000, {"corn": 4.99})

    events = book.replay({"corn": [h1(12, high=6.00, low=4.90, close=5.00)]}, TWO_SPECS, SINCE, UNTIL)

    assert events[0]["price"] == pytest.approx(59900 / 10500)


def test_a_bar_that_stays_above_the_stop_out_level_only_moves_the_mark():
    book = book_with([("corn", "BUY", 1_000_000, 5.01)], 10_000, {"corn": 5.01})

    events = book.replay({"corn": [h1(12, high=5.05, low=4.90, close=4.95)]}, TWO_SPECS, SINCE, UNTIL)

    assert events == []
    assert book.state()["marks"]["corn"] == 4.95
    assert len(book.state()["positions"]) == 1


def test_the_largest_loss_position_is_closed_first_even_if_it_is_not_the_one_whose_bar_triggered():
    # wheat: 10 lots long @6.00 marked 5.40 (-6000). corn: 2 lots long @5.01 falls in this bar.
    # Breach when corn hits 4720/1900; by then corn has lost less than wheat, so wheat is closed, at its mark.
    book = book_with(
        [("wheat", "BUY", 1_000_000, 6.00), ("corn", "BUY", 200_000, 5.01)], 14_000, {"wheat": 5.40, "corn": 5.01}
    )

    events = book.replay({"corn": [h1(12, high=5.05, low=2.00, close=2.10)]}, TWO_SPECS, SINCE, UNTIL)

    assert [(e["symbol"], e["price"], round(e["realized_pnl"], 6)) for e in events] == [("wheat", 5.40, -6000.0)]
    assert [p["symbol_name"] for p in book.state()["positions"]] == ["corn"]
    assert book.state()["balance"] == pytest.approx(8_000)


def test_replay_accrues_swap_for_each_17_00_et_rollover_it_crosses():
    # Tue 12:00Z -> Thu 03:00Z crosses Tue 17:00 ET (1x) and Wed 17:00 ET (3x, triple-swap day).
    book = book_with([("corn", "BUY", 200_000, 5.01)], 10_000, {"corn": 5.01})
    bars = [h1(h, 5.02, 5.00, 5.01) for h in range(12, 16)]

    book.replay({"corn": bars}, TWO_SPECS, datetime(2026, 1, 6, 12, tzinfo=timezone.utc), datetime(2026, 1, 8, 3, tzinfo=timezone.utc))

    assert book.state()["positions"][0]["swap"] == pytest.approx(-100.0 * (1 + 3))


def test_replaying_the_saved_cycle_states_reproduces_the_final_equity_exactly():
    import json

    def run(book, cycles):
        for when, deltas, q in cycles:
            book.apply_fills(deltas, q, SPECS, when)
            book.accrue_swap(date(2026, 1, 6), SPECS)
        return book.snapshot(cycles[-1][2], SPECS)["equity"]

    cycles = [
        ("2026-01-06T21:00:00+00:00", {"corn": 3.3}, quote(4.987, 5.013)),
        ("2026-01-07T09:00:00+00:00", {"corn": -1.1}, quote(5.1234, 5.1456)),
        ("2026-01-07T21:00:00+00:00", {"corn": -4.4}, quote(4.9, 4.93)),
    ]
    continuous = run(PaperBook(starting_equity=10_000), cycles)

    book = PaperBook(starting_equity=10_000)
    for cycle in cycles:  # persist to a cycle file (JSON) and reload between every cycle
        book = PaperBook(json.loads(json.dumps(book.state())))
        last = run(book, [cycle])

    assert last == continuous


def test_peak_equity_ratchets_up_with_snapshots_and_never_falls():
    book = book_with([("corn", "BUY", 200_000, 5.00)], 10_000, {})

    up = book.snapshot(quote(5.50, 5.51), TWO_SPECS)
    book.snapshot(quote(4.00, 4.01), TWO_SPECS)

    assert up["peak_equity"] == pytest.approx(10_000 + 0.50 * 2000)
    assert book.state()["peak_equity"] == pytest.approx(11_000)
    assert book.snapshot(quote(4.00, 4.01), TWO_SPECS)["equity"] == pytest.approx(8_000)
