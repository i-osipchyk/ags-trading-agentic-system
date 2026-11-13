import pytest

from ags.tools.levels import get_key_levels


def _bars(highs: list[float], lows: list[float]) -> list[dict]:
    return [
        {"date": f"2026-01-{i + 1:02d}", "open": h, "high": h, "low": l, "close": l, "volume": 1}
        for i, (h, l) in enumerate(zip(highs, lows))
    ]


# With three bars each side confirming a swing: swing highs of 15 (day 4) and 14 (day 10); a swing low of 9 (day 7).
_HIGHS = [10, 11, 12, 15, 12, 11, 10, 11, 12, 14, 12, 11, 10, 9, 8]
_LOWS = [h - 1 for h in _HIGHS]


def test_key_levels_gives_the_recent_range_and_the_nearest_swing_levels_around_the_reference_price():
    result = get_key_levels(_bars(_HIGHS, _LOWS), 12.5, pivot_width=3)

    assert result == {
        "reference_price": 12.5, "reference": "last_close", "recent_range": [7.0, 15.0],
        "resistance": [14.0, 15.0], "support": [9.0],
    }


def test_key_levels_names_the_reference_when_it_is_a_live_quote():
    assert get_key_levels(_bars(_HIGHS, _LOWS), 12.5, reference="intraday_quote", pivot_width=3)["reference"] == "intraday_quote"


def test_key_levels_ignores_the_last_sessions_as_swings_until_later_bars_confirm_them():
    # The final high of 20 has no bars after it, so it is in the range but not a resistance level.
    highs = [*_HIGHS, 14, 20]
    result = get_key_levels(_bars(highs, [h - 1 for h in highs]), 12.5, pivot_width=3)

    assert result["recent_range"][1] == 20.0
    assert 20.0 not in result["resistance"]


def test_key_levels_counts_levels_within_a_quarter_percent_as_one_keeping_the_nearer():
    highs = [10, 11, 12, 15, 12, 11, 10, 11, 12, 15.02, 12, 11, 10, 9, 8]
    result = get_key_levels(_bars(highs, [h - 1 for h in highs]), 12.5, pivot_width=3)

    assert result["resistance"] == [15.0]


def test_key_levels_limits_each_side_to_the_nearest_three():
    highs = [10, 11, 12, 11, 20, 11, 10, 11, 30, 11, 10, 11, 40, 11, 10, 11, 50, 11, 10, 11, 12, 11, 10]
    result = get_key_levels(_bars(highs, [h - 1 for h in highs]), 12.5, pivot_width=3)

    assert result["resistance"] == [20.0, 30.0, 40.0]


def test_key_levels_only_considers_the_recent_window_for_the_range():
    result = get_key_levels(_bars(_HIGHS, _LOWS), 12.5, range_sessions=5, pivot_width=3)

    assert result["recent_range"] == [7.0, 12.0]


def test_key_levels_skips_non_trading_days():
    bars = _bars(_HIGHS, _LOWS)
    bars.insert(5, {"date": "2026-01-05", "no_trading": True})

    assert get_key_levels(bars, 12.5, pivot_width=3)["recent_range"] == [7.0, 15.0]


def test_key_levels_is_none_without_enough_history_to_confirm_any_swing():
    assert get_key_levels(_bars([10, 11], [9, 10]), 11.0) is None


def test_key_levels_finds_the_lower_highs_of_a_steady_downtrend_near_price_by_default():
    # The highs step down, so no swing is wider than one bar, yet 4.74 and 4.64 are the peaks of two bounces,
    # where price was turned back on the way down.
    highs = [4.90, 4.80, 4.745, 4.70, 4.74, 4.66, 4.62, 4.64, 4.57, 4.56]
    result = get_key_levels(_bars(highs, [h - 0.05 for h in highs]), 4.58)

    assert result["resistance"][:2] == [4.64, 4.74]
