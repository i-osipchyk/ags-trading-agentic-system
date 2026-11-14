import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from ags import scoring


def _bars(data_dir, commodity, start, closes):
    folder = data_dir / "ctrader" / "daily_bars" / commodity
    folder.mkdir(parents=True, exist_ok=True)
    day = start
    for close in closes:
        # Stored under cTrader's stamp, the evening before the session `day` it covers.
        stamp = day - timedelta(days=1)
        (folder / f"{stamp}.json").write_text(json.dumps({"date": str(stamp), "close": close}))
        day += timedelta(days=1)


def _log(log_dir, commodity, triggered, call, price, version=None):
    run_id = f"{commodity}_{triggered:%Y-%m-%dT%H-%M-%SZ}"
    events = []
    if version:
        events.append({"agent": "pipeline", "event_type": "version", "payload": version})
    events.append({"agent": "coordinator", "event_type": "output",
                   "payload": {"call": call, "conviction": 3, "price_at_call": {"price": price}}})
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{run_id}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


CALL_TIME = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)  # 06:00 ET on 2026-10-20


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "logs", tmp_path / "data"


def _falling_history(data_dir, commodity="corn", end_price=90.0):
    # 160 daily closes drifting down to the call day, then 5 further closes.
    start = date(2026, 10, 20) - timedelta(days=160)
    closes = [200 - i * 0.5 for i in range(160)] + [100.0, 98.0, 96.0, 94.0, 92.0, end_price]
    _bars(data_dir, commodity, start, closes)


def test_a_call_is_entered_at_its_own_price_and_exited_at_the_nth_close_from_its_day(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    _log(log_dir, "corn", CALL_TIME, "bearish", 100.0)

    one_day = scoring.build_rows(log_dir, data_dir, horizon_days=1)
    five_days = scoring.build_rows(log_dir, data_dir, horizon_days=5)

    # The call day's own close (100.0) is the first close; the fifth is 92.0.
    assert one_day["ret"].iloc[0] == pytest.approx(0.0)
    assert five_days["ret"].iloc[0] == pytest.approx(-0.08)
    assert five_days["raw_coordinator"].iloc[0] == pytest.approx(0.08)   # short, price fell
    assert five_days["raw_always_long"].iloc[0] == pytest.approx(-0.08)
    assert five_days["raw_always_short"].iloc[0] == pytest.approx(0.08)
    assert five_days["pos_trend"].iloc[0] == -1                          # a long downtrend before the call


def test_a_call_without_its_exit_close_yet_is_left_out(dirs):
    log_dir, data_dir = dirs
    _bars(data_dir, "corn", date(2026, 10, 1), [100.0 + i for i in range(20)])  # ends 2026-10-20
    _log(log_dir, "corn", CALL_TIME, "bullish", 119.0)

    assert scoring.build_rows(log_dir, data_dir, horizon_days=5).empty
    assert len(scoring.build_rows(log_dir, data_dir, horizon_days=1)) == 1


def test_since_drops_earlier_calls_and_runs_without_a_call_or_price_are_ignored(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    _log(log_dir, "corn", CALL_TIME, "bearish", 100.0)
    _log(log_dir, "corn", CALL_TIME - timedelta(days=3), "bearish", 100.0)
    (log_dir / "corn_2026-10-21T10-00-00Z.jsonl").write_text(
        json.dumps({"agent": "coordinator", "event_type": "output", "payload": {"call": None, "degraded": True}}) + "\n"
    )

    rows = scoring.build_rows(log_dir, data_dir, horizon_days=5, since=date(2026, 10, 20))

    assert list(rows["run_id"]) == ["corn_2026-10-20T10-00-00Z"]


def test_market_neutral_subtracts_the_cycles_basket_and_needs_enough_calls_for_one(dirs):
    log_dir, data_dir = dirs
    for i, commodity in enumerate(scoring.COMMODITIES):
        # Every commodity's exit return is -(i+1)%: the basket mean is the average of those.
        _bars(data_dir, commodity, date(2026, 10, 1), [100.0] * 19 + [100.0, *[100.0 - (i + 1)] * 6])
        _log(log_dir, commodity, CALL_TIME, "bearish", 100.0)

    rows = scoring.build_rows(log_dir, data_dir, horizon_days=2)

    assert rows["basket"].iloc[0] == pytest.approx(-0.04)
    corn = rows[rows["commodity"] == "corn"].iloc[0]
    assert corn["neutral_always_long"] == pytest.approx(corn["ret"] - corn["basket"])
    assert rows["neutral_always_long"].mean() == pytest.approx(0.0)

    # Two commodities are too few to call their mean "the market".
    sparse = scoring.build_rows(log_dir, data_dir, horizon_days=2).iloc[:2].copy()
    assert scoring.summarize(sparse.assign(basket=float("nan")), neutral=True).empty


def test_summary_weights_overlapping_calls_and_counts_neutral_calls_as_zero_return(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    _log(log_dir, "corn", CALL_TIME, "bearish", 100.0)
    _log(log_dir, "corn", CALL_TIME + timedelta(hours=6), "neutral", 100.0)

    rows = scoring.build_rows(log_dir, data_dir, horizon_days=5)
    summary = scoring.summarize(rows, neutral=False).set_index("strategy")

    assert summary.loc["coordinator", "calls"] == 2
    assert summary.loc["coordinator", "effective"] == pytest.approx(0.2)   # two of the week's ten scheduled calls
    assert summary.loc["coordinator", "directional"] == 1
    assert summary.loc["coordinator", "hit_rate"] == pytest.approx(1.0)
    assert summary.loc["coordinator", "mean_return"] == pytest.approx(0.04)  # half of +8%, half of 0
    assert summary.loc["always_short", "directional"] == 2


def test_a_thin_week_is_worth_its_share_not_a_full_week(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    _log(log_dir, "corn", CALL_TIME, "bearish", 100.0)

    rows = scoring.build_rows(log_dir, data_dir, horizon_days=5)

    assert rows["weight"].iloc[0] == pytest.approx(1 / scoring.EXPECTED_CALLS_PER_WEEK)


def test_a_full_week_of_scheduled_calls_is_worth_one(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    for day in range(5):                                   # Mon-Fri of the same ISO week
        for hour in (10, 16):
            _log(log_dir, "corn", datetime(2026, 10, 19 + day, hour, 0, tzinfo=timezone.utc), "bearish", 100.0)

    rows = scoring.build_rows(log_dir, data_dir, horizon_days=1)

    assert len(rows) == 10 and rows["weight"].sum() == pytest.approx(1.0)


def test_calls_before_the_review_start_are_left_out_by_default(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    _log(log_dir, "corn", datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc), "bearish", 100.0)
    _log(log_dir, "corn", CALL_TIME, "bearish", 100.0)

    default = scoring.build_rows(log_dir, data_dir, horizon_days=1)
    everything = scoring.build_rows(log_dir, data_dir, horizon_days=1, since=None)

    assert len(default) == 1 and len(everything) == 2


def test_breakdowns_group_by_week_commodity_version_and_direction(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    _falling_history(data_dir, "wheat")
    _log(log_dir, "corn", CALL_TIME, "bearish", 100.0, {"code_version": "abcdef123", "prompt_hashes": {"all": "111111222222"}})
    _log(log_dir, "wheat", CALL_TIME, "bullish", 100.0)
    rows = scoring.build_rows(log_dir, data_dir, horizon_days=5)

    assert set(scoring.breakdown(rows, "commodity", neutral=False)["commodity"]) == {"corn", "wheat"}
    assert set(rows["version"]) == {"abcdef1/111111", "unversioned"}
    assert set(scoring.breakdown(rows, "week", neutral=False)["week"]) == {"2026-W43"}
    direction = scoring.by_direction(rows, neutral=False).set_index("call")
    assert direction.loc["bearish", "hit_rate"] == 1.0 and direction.loc["bullish", "hit_rate"] == 0.0


def test_conviction_cut_splits_directional_calls_by_stated_conviction(dirs):
    log_dir, data_dir = dirs
    _falling_history(data_dir)
    _log(log_dir, "corn", CALL_TIME, "bearish", 100.0)
    _log(log_dir, "corn", CALL_TIME + timedelta(hours=6), "neutral", 100.0)
    rows = scoring.build_rows(log_dir, data_dir, horizon_days=5)
    rows.loc[rows["call"] == "bearish", "conviction"] = 5

    cut = scoring.by_conviction(rows, neutral=False)

    assert list(cut["conviction"]) == [5]          # the neutral call is not directional
    assert cut["calls"].iloc[0] == 1 and cut["hit_rate"].iloc[0] == 1.0


def test_bootstrap_interval_is_deterministic_and_brackets_the_mean():
    frame = pd.DataFrame({
        "commodity": ["a", "a", "b", "b", "c", "c"], "week": ["w1", "w2", "w1", "w2", "w1", "w2"],
        "weight": [1.0] * 6,
    })
    values = pd.Series([0.01, 0.02, 0.03, 0.02, 0.01, 0.03])

    low, high = scoring.bootstrap_interval(frame, values)

    assert (low, high) == scoring.bootstrap_interval(frame, values)
    assert low <= values.mean() <= high


def _scored(weeks, *, coordinator, trend, basket_move=0.01):
    """`weeks` weeks of 7 commodities, each one call, with fixed neutral returns per strategy."""
    rows = []
    for week in range(weeks):
        for commodity in scoring.COMMODITIES:
            rows.append({
                "commodity": commodity, "week": f"2026-W{week:02d}", "weight": 1.0, "ret": basket_move,
                "basket": basket_move, "neutral_coordinator": coordinator, "neutral_trend": trend,
            })
    return pd.DataFrame(rows)


def test_the_bar_is_not_testable_until_enough_effective_weeks_and_a_non_falling_stretch():
    short = scoring.evaluate_pass_bar(_scored(3, coordinator=0.02, trend=0.0, basket_move=-0.01))

    assert short["verdict"] == "not_yet"
    assert "3.0 of 8 effective weeks" in short["reasons"][0]
    assert short["non_falling_weeks"] == 0 and any("did not fall" in r for r in short["reasons"])


def test_the_bar_passes_only_when_the_interval_over_the_trend_rule_is_above_zero():
    noisy = _scored(10, coordinator=0.0, trend=0.0)
    noisy.loc[::2, "neutral_coordinator"] = 0.01
    noisy.loc[1::2, "neutral_coordinator"] = -0.01
    wins = _scored(10, coordinator=0.02, trend=0.0)
    wins["neutral_coordinator"] += pd.Series(([0.001, -0.001] * 35))

    assert scoring.evaluate_pass_bar(wins)["verdict"] == "pass"
    assert scoring.evaluate_pass_bar(noisy)["verdict"] == "fail"


def test_price_data_through_is_the_oldest_commodity_newest_bar(dirs):
    _, data_dir = dirs
    _bars(data_dir, "corn", date(2026, 10, 1), [1.0] * 10)    # to 10-10
    _bars(data_dir, "wheat", date(2026, 10, 1), [1.0] * 5)    # to 10-05

    assert scoring.price_data_through(data_dir) == date(2026, 10, 5)


def test_a_bar_belongs_to_the_session_after_its_stamp(dirs):
    _, data_dir = dirs
    folder = data_dir / "ctrader" / "daily_bars" / "corn"
    folder.mkdir(parents=True)
    (folder / "2026-11-10.json").write_text('{"date": "2026-11-10", "close": 4.586}')   # Wednesday's session

    assert scoring.load_closes(data_dir, "corn").index[0] == pd.Timestamp("2026-11-11")


def test_a_no_trading_marker_is_not_a_bar(dirs):
    _, data_dir = dirs
    _bars(data_dir, "corn", date(2026, 10, 1), [1.0, 2.0])
    (data_dir / "ctrader" / "daily_bars" / "corn" / "2026-10-02.json").write_text('{"date": "2026-10-02", "no_trading": true}')

    assert list(scoring.load_closes(data_dir, "corn")) == [1.0, 2.0]
