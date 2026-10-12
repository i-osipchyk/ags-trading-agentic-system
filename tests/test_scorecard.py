from datetime import datetime, timedelta, timezone

import pytest

from ags.scorecard import effective_count, effective_weights

T0 = datetime(2026, 6, 1, 16, 0, tzinfo=timezone.utc)


def call(commodity, offset):
    return {"commodity": commodity, "triggered_at": T0 + offset}


def test_ten_calls_inside_one_week_share_a_single_effective_observation():
    calls = [call("corn", timedelta(hours=12 * i)) for i in range(10)]  # all within 4.5 days

    weights = effective_weights(calls)

    assert weights == pytest.approx([0.1] * 10)
    assert effective_count(calls) == pytest.approx(1.0)


def test_an_isolated_call_counts_in_full():
    calls = [call("corn", timedelta(hours=12 * i)) for i in range(10)] + [call("corn", timedelta(weeks=3))]

    assert effective_weights(calls)[-1] == 1.0
    assert effective_count(calls) == pytest.approx(2.0)


def test_calls_for_different_commodities_never_overlap_each_other():
    calls = [call("corn", timedelta(0)), call("wheat", timedelta(0))]

    assert effective_weights(calls) == [1.0, 1.0]


def test_windows_are_one_week_half_open_so_calls_exactly_a_week_apart_do_not_overlap():
    calls = [call("corn", timedelta(0)), call("corn", timedelta(weeks=1)), call("corn", timedelta(days=6, hours=23))]

    # 0 overlaps day-6.96 (<1w apart); the week-1 call overlaps day-6.96 only.
    assert effective_weights(calls) == pytest.approx([1 / 2, 1 / 2, 1 / 3])


def test_weights_follow_the_input_order_not_chronology():
    calls = [call("corn", timedelta(weeks=3)), call("corn", timedelta(0)), call("corn", timedelta(hours=1))]

    assert effective_weights(calls) == pytest.approx([1.0, 0.5, 0.5])
