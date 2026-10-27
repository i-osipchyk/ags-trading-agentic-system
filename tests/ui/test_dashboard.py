import json
from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

from ags.storage import LocalStorage
from ags.ui.dashboard import (
    calls_frame,
    daily_totals,
    equity_frame,
    load_cycles,
    margin_frame,
    max_drawdown,
    pnl_frame,
    positions_frame,
)


def test_max_drawdown_is_the_largest_peak_to_trough_fall():
    # Peak 12,000 -> trough 9,000 is -3,000 (-25%); the later 11,000 -> 10,500 is smaller.
    curve = [10_000.0, 12_000.0, 9_000.0, 11_000.0, 10_500.0]

    dd = max_drawdown(curve)

    assert dd["max"] == pytest.approx(3_000.0)
    assert dd["max_pct"] == pytest.approx(25.0)


def test_current_drawdown_is_measured_from_the_running_peak_to_the_last_point():
    curve = [10_000.0, 12_000.0, 9_000.0, 11_000.0, 10_500.0]

    dd = max_drawdown(curve)

    assert dd["current"] == pytest.approx(1_500.0)
    assert dd["current_pct"] == pytest.approx(12.5)


def test_a_curve_that_only_rises_has_no_drawdown():
    dd = max_drawdown([10_000.0, 10_100.0, 10_200.0])

    assert dd == {"max": 0.0, "max_pct": 0.0, "current": 0.0, "current_pct": 0.0}


def test_an_empty_curve_has_no_drawdown():
    assert max_drawdown([]) == {"max": 0.0, "max_pct": 0.0, "current": 0.0, "current_pct": 0.0}


NOW = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)


def _cycle(storage, book, cycle_id, as_of, **fields):
    storage.put(f"portfolio/{book}/cycles/{cycle_id}.json", json.dumps({"cycle_id": cycle_id, "as_of": as_of, **fields}).encode())


def test_load_cycles_returns_the_books_cycles_oldest_first_with_utc_timestamps(tmp_path):
    storage = LocalStorage(tmp_path)
    _cycle(storage, "manager", "cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", equity=10_250.0)
    _cycle(storage, "manager", "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", equity=10_000.0)
    _cycle(storage, "shadow", "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", equity=9_999.0)

    frame = load_cycles(storage, "manager", now=NOW)

    assert list(frame["equity"]) == [10_000.0, 10_250.0]
    assert [t.isoformat() for t in frame["at"]] == ["2026-01-06T21:00:00+00:00", "2026-01-07T09:00:00+00:00"]


def test_load_cycles_keeps_only_the_last_n_days_when_a_range_is_given(tmp_path):
    storage = LocalStorage(tmp_path)
    _cycle(storage, "manager", "cycle_2025-12-01T21-00-00Z", "2025-12-01T16:00:00-05:00", equity=1.0)
    _cycle(storage, "manager", "cycle_2026-01-10T21-00-00Z", "2026-01-10T16:00:00-05:00", equity=2.0)
    _cycle(storage, "manager", "cycle_2026-01-19T21-00-00Z", "2026-01-19T16:00:00-05:00", equity=3.0)

    assert list(load_cycles(storage, "manager", now=NOW, days=7)["equity"]) == [3.0]
    assert list(load_cycles(storage, "manager", now=NOW, days=30)["equity"]) == [2.0, 3.0]
    assert list(load_cycles(storage, "manager", now=NOW)["equity"]) == [1.0, 2.0, 3.0]


def test_load_cycles_of_a_book_with_no_cycles_is_an_empty_frame_with_the_at_column(tmp_path):
    frame = load_cycles(LocalStorage(tmp_path), "manager", now=NOW)

    assert frame.empty
    assert "at" in frame.columns


def test_equity_frame_lines_up_both_books_per_cycle_with_the_gap(tmp_path):
    storage = LocalStorage(tmp_path)
    for cycle_id, as_of, m, s in [
        ("cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", 10_000.0, 10_000.0),
        ("cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", 10_250.0, 10_100.0),
    ]:
        _cycle(storage, "manager", cycle_id, as_of, equity=m, peak_equity=m)
        _cycle(storage, "shadow", cycle_id, as_of, equity=s, peak_equity=s)

    frame = equity_frame(storage, now=NOW)

    assert list(frame["manager"]) == [10_000.0, 10_250.0]
    assert list(frame["shadow"]) == [10_000.0, 10_100.0]
    assert list(frame["gap"]) == [0.0, 150.0]
    assert list(frame.columns) == ["at", "manager", "shadow", "gap"]


def test_equity_frame_skips_cycles_only_one_book_has(tmp_path):
    storage = LocalStorage(tmp_path)
    _cycle(storage, "manager", "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", equity=1.0, peak_equity=1.0)
    _cycle(storage, "manager", "cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", equity=2.0, peak_equity=2.0)
    _cycle(storage, "shadow", "cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", equity=3.0, peak_equity=3.0)

    assert list(equity_frame(storage, now=NOW)["manager"]) == [2.0]


def test_equity_frame_with_no_cycles_is_empty(tmp_path):
    assert equity_frame(LocalStorage(tmp_path), now=NOW).empty


COMMODITIES = ["corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa"]


def _positions_cycle(storage, book, cycle_id, as_of, lots, positions, marks):
    _cycle(
        storage, book, cycle_id, as_of,
        closing_lots={c: lots.get(c, 0.0) for c in COMMODITIES},
        closing_state={"positions": positions, "marks": marks},
    )


def _seed_positions(storage):
    # 5 lots of corn = volume 500_000 (1/100 unit) -> 5_000 units at $4.00 = $20,000 long.
    _positions_cycle(
        storage, "manager", "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00",
        {"corn": 5.0, "wheat": -2.0},
        [{"symbol_name": "corn", "trade_side": "BUY", "volume": 500_000}, {"symbol_name": "wheat", "trade_side": "SELL", "volume": 200_000}],
        {"corn": 4.0, "wheat": 5.5},
    )
    _positions_cycle(storage, "manager", "cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", {}, [], {"corn": 4.1, "wheat": 5.5})


def test_positions_frame_in_lots_has_a_signed_column_per_commodity_and_gross_exposure(tmp_path):
    storage = LocalStorage(tmp_path)
    _seed_positions(storage)

    frame = positions_frame(load_cycles(storage, "manager", now=NOW), unit="lots")

    assert list(frame["corn"]) == [5.0, 0.0]
    assert list(frame["wheat"]) == [-2.0, 0.0]
    assert list(frame["gross"]) == [7.0, 0.0]
    assert list(frame.columns) == ["at", *COMMODITIES, "gross"]


def test_positions_frame_in_usd_values_each_position_at_its_mark_with_shorts_negative(tmp_path):
    storage = LocalStorage(tmp_path)
    _seed_positions(storage)

    frame = positions_frame(load_cycles(storage, "manager", now=NOW), unit="usd")

    assert list(frame["corn"]) == pytest.approx([20_000.0, 0.0])
    assert list(frame["wheat"]) == pytest.approx([-11_000.0, 0.0])
    assert list(frame["gross"]) == pytest.approx([31_000.0, 0.0])


def test_positions_frame_of_no_cycles_is_empty_with_the_same_columns(tmp_path):
    frame = positions_frame(load_cycles(LocalStorage(tmp_path), "manager", now=NOW), unit="lots")

    assert frame.empty
    assert list(frame.columns) == ["at", *COMMODITIES, "gross"]


def test_margin_frame_is_used_margin_over_equity_in_percent_for_each_book(tmp_path):
    storage = LocalStorage(tmp_path)
    cycle_id, as_of = "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00"
    _cycle(storage, "manager", cycle_id, as_of, equity=10_000.0, used_margin=2_500.0)
    _cycle(storage, "shadow", cycle_id, as_of, equity=8_000.0, used_margin=1_000.0)

    frame = margin_frame(storage, now=NOW)

    assert list(frame["manager"]) == [25.0]
    assert list(frame["shadow"]) == [12.5]


def test_margin_frame_with_zero_equity_is_zero_not_a_division_error(tmp_path):
    storage = LocalStorage(tmp_path)
    cycle_id, as_of = "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00"
    for book in ("manager", "shadow"):
        _cycle(storage, book, cycle_id, as_of, equity=0.0, used_margin=0.0)

    assert list(margin_frame(storage, now=NOW)["manager"]) == [0.0]


def test_calls_frame_has_a_row_per_commodity_per_cycle_and_marks_failed_runs(tmp_path):
    storage = LocalStorage(tmp_path)
    calls = {c: {"call": "neutral", "conviction": 1} for c in COMMODITIES}
    calls["corn"] = {"call": "bullish", "conviction": 4}
    calls["wheat"] = None
    _cycle(storage, "manager", "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", calls=calls)

    frame = calls_frame(load_cycles(storage, "manager", now=NOW))

    assert len(frame) == len(COMMODITIES)
    corn = frame[frame["commodity"] == "corn"].iloc[0]
    assert (corn["call"], corn["conviction"]) == ("bullish", 4)
    wheat = frame[frame["commodity"] == "wheat"].iloc[0]
    assert (wheat["call"], wheat["conviction"]) == ("failed", 0)


def test_calls_frame_of_no_cycles_is_empty(tmp_path):
    assert calls_frame(load_cycles(LocalStorage(tmp_path), "manager", now=NOW)).empty


def _page(root, jobs_dir, now, pull):
    from ags.ui.dashboard_page import render_dashboard_page

    render_dashboard_page(root=root, jobs_dir=jobs_dir, now=lambda: now, pull=pull)


def _seed_book_history(root):
    storage = LocalStorage(root)
    calls = {c: {"call": "neutral", "conviction": 1} for c in COMMODITIES}
    for book, equities in (("manager", [10_000.0, 12_000.0, 9_000.0]), ("shadow", [10_000.0, 10_500.0, 10_400.0])):
        for i, equity in enumerate(equities):
            cycle_id = f"cycle_2026-01-{6 + i:02d}T21-00-00Z"
            _cycle(
                storage, book, cycle_id, f"2026-01-{6 + i:02d}T16:00:00-05:00", equity=equity, peak_equity=max(equities[: i + 1]),
                used_margin=equity / 4, calls=calls, fills=[], stop_outs=[], closing_lots={c: 0.0 for c in COMMODITIES},
                closing_state={"positions": [], "marks": {}},
            )


def _app(tmp_path, pull=lambda: None):
    return AppTest.from_function(
        _page, kwargs={"root": tmp_path, "jobs_dir": tmp_path / "jobs", "now": NOW, "pull": pull}
    ).run(timeout=30)


def _text(app):
    return " ".join(str(el.value) for el in [*app.markdown, *app.title, *app.header, *app.subheader, *app.caption, *app.metric, *app.info])


def test_dashboard_with_no_cycles_says_so_and_still_offers_the_pull_button(tmp_path):
    app = _app(tmp_path)

    assert not app.exception
    assert "no cycles" in _text(app).lower()
    assert [b.label for b in app.button] == ["Pull latest data"]


def test_dashboard_shows_the_max_and_current_drawdown_of_each_book(tmp_path):
    _seed_book_history(tmp_path)

    app = _app(tmp_path)

    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    # Manager: peak 12,000 -> 9,000 = $3,000 (25.0%), and it is still there now.
    assert metrics["Manager max drawdown"] == "$3,000.00 (25.0%)"
    assert metrics["Manager current drawdown"] == "$3,000.00 (25.0%)"
    # Shadow: peak 10,500 -> 10,400 = $100 (0.95%).
    assert metrics["Shadow max drawdown"] == "$100.00 (1.0%)"


def test_dashboard_range_limits_the_drawdown_window(tmp_path):
    _seed_book_history(tmp_path)
    app = _app(tmp_path)

    app.radio(key="range").set_value("7d").run()

    # NOW is 2026-01-20, so every cycle (Jan 6-8) is older than 7 days.
    assert not app.exception
    assert "no cycles" in _text(app).lower()


def test_pull_button_starts_the_sync(tmp_path):
    pulled = []
    app = _app(tmp_path, pull=lambda: pulled.append(True))

    app.button[0].click().run()

    assert pulled == [True]
    assert not app.exception


def test_the_dashboard_is_the_default_page_and_the_old_pages_remain():
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "run_chat.py"
    app = AppTest.from_file(str(script)).run(timeout=30)

    assert not app.exception
    assert [t.value for t in app.title] == ["Dashboard"]
    source = script.read_text()
    assert 'title="Runs"' in source and 'title="Run detail"' in source


def _pnl_cycle(storage, cycle_id, as_of, positions, marks, fills=(), stop_outs=()):
    _cycle(
        storage, "manager", cycle_id, as_of, fills=list(fills), stop_outs=list(stop_outs),
        closing_state={"positions": positions, "marks": marks},
    )


def test_pnl_frame_is_cumulative_realized_plus_unrealized_per_commodity(tmp_path):
    storage = LocalStorage(tmp_path)
    # Long 5 lots of corn at 4.00, marked 4.10, swap -10: (0.10 x 5,000 units) - 10 = 490 unrealized.
    corn = {"symbol_name": "corn", "trade_side": "BUY", "volume": 500_000, "price": 4.0, "swap": -10.0}
    # Short 2 lots of wheat at 5.50, marked 5.40: (0.10 x 2,000 units) = 200 unrealized.
    wheat = {"symbol_name": "wheat", "trade_side": "SELL", "volume": 200_000, "price": 5.5, "swap": 0.0}
    _pnl_cycle(storage, "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", [corn, wheat], {"corn": 4.1, "wheat": 5.4})
    # Next cycle: corn closed for 600 realized, wheat stopped out for -50; nothing open any more.
    _pnl_cycle(
        storage, "cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", [], {"corn": 4.2, "wheat": 5.6},
        fills=[{"symbol": "corn", "realized_pnl": 600.0}, {"symbol": "wheat", "realized_pnl": 0.0}],
        stop_outs=[{"symbol": "wheat", "realized_pnl": -50.0}],
    )

    frame = pnl_frame(load_cycles(storage, "manager", now=NOW))

    assert list(frame["corn"]) == pytest.approx([490.0, 600.0])
    assert list(frame["wheat"]) == pytest.approx([200.0, -50.0])
    assert list(frame["total"]) == pytest.approx([690.0, 550.0])
    assert list(frame.columns) == ["at", *COMMODITIES, "total"]


def test_pnl_frame_keeps_realized_pnl_from_cycles_before_the_range(tmp_path):
    storage = LocalStorage(tmp_path)
    _pnl_cycle(storage, "cycle_2025-12-01T21-00-00Z", "2025-12-01T16:00:00-05:00", [], {}, fills=[{"symbol": "corn", "realized_pnl": 100.0}])
    _pnl_cycle(storage, "cycle_2026-01-19T21-00-00Z", "2026-01-19T16:00:00-05:00", [], {}, fills=[{"symbol": "corn", "realized_pnl": 25.0}])

    frame = pnl_frame(load_cycles(storage, "manager", now=NOW), now=NOW, days=7)

    assert list(frame["corn"]) == [125.0]


def test_pnl_frame_of_no_cycles_is_empty_with_the_same_columns(tmp_path):
    frame = pnl_frame(load_cycles(LocalStorage(tmp_path), "manager", now=NOW))

    assert frame.empty
    assert list(frame.columns) == ["at", *COMMODITIES, "total"]


def test_calls_frame_gives_each_call_an_end_at_the_next_cycle_and_the_last_one_a_half_day(tmp_path):
    storage = LocalStorage(tmp_path)
    calls = {c: {"call": "neutral", "conviction": 1} for c in COMMODITIES}
    _cycle(storage, "manager", "cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", calls=calls)
    _cycle(storage, "manager", "cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", calls=calls)

    corn = calls_frame(load_cycles(storage, "manager", now=NOW)).query("commodity == 'corn'")

    assert [t.isoformat() for t in corn["until"]] == ["2026-01-07T09:00:00+00:00", "2026-01-07T21:00:00+00:00"]


def test_pnl_panel_follows_equity_and_can_switch_between_the_books(tmp_path):
    _seed_book_history(tmp_path)
    app = _app(tmp_path)

    headings = [h.value for h in app.subheader]
    assert headings.index("P&L by commodity") == headings.index("Equity") + 1
    assert app.radio(key="pnl_book").value == "manager"

    app.radio(key="pnl_book").set_value("shadow").run()

    assert not app.exception
    assert app.radio(key="pnl_book").value == "shadow"


def test_daily_totals_sum_each_day_with_a_running_total_and_skip_days_without_cost():
    rows = [
        {"day": "2026-01-08", "key": "corn", "cost": 1.5},
        {"day": "2026-01-06", "key": "corn", "cost": 1.0},
        {"day": "2026-01-06", "key": "wheat", "cost": 0.5},
        {"day": "2026-01-07", "key": "corn", "cost": 0.0},
    ]

    frame = daily_totals(rows, now=NOW)

    assert list(frame["day"]) == ["2026-01-06", "2026-01-08"]
    assert list(frame["total"]) == [1.5, 1.5]
    assert list(frame["cumulative"]) == [1.5, 3.0]


def test_daily_totals_in_a_range_start_the_running_total_at_the_first_day_in_it():
    rows = [
        {"day": "2025-12-01", "key": "corn", "cost": 10.0},
        {"day": "2026-01-19", "key": "corn", "cost": 2.0},
        {"day": "2026-01-20", "key": "corn", "cost": 3.0},
    ]

    frame = daily_totals(rows, now=NOW, days=7)

    assert list(frame["day"]) == ["2026-01-19", "2026-01-20"]
    assert list(frame["cumulative"]) == [2.0, 5.0]


def test_daily_totals_of_no_spend_is_empty_with_the_same_columns():
    frame = daily_totals([], now=NOW)

    assert frame.empty
    assert list(frame.columns) == ["day", "total", "cumulative"]
