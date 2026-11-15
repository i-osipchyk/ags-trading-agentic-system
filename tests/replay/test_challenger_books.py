import json

import pytest

from ags.portfolio.stage import COMMODITIES
from ags.replay.challenger_books import book_calls, challenger_output, max_drawdown

LIVE = {c: {"call": "bullish", "conviction": 3} for c in COMMODITIES}
LIVE["cocoa"] = None


def _challenger(**calls):
    return {c: ({"call": call, "conviction": 2} if call else None) for c, call in calls.items()}


def test_the_fallback_book_uses_the_live_call_wherever_the_challenger_made_none():
    live, challenger = book_calls("fallback", LIVE, _challenger(corn="bearish", coffee=None))

    assert live == LIVE
    assert challenger["corn"]["call"] == "bearish"            # the challenger's own call
    assert challenger["coffee"] == LIVE["coffee"]             # none from the challenger: the live call
    assert challenger["cocoa"] is None                        # live had none either


def test_the_matched_books_hold_the_same_universe_and_drop_what_the_challenger_did_not_call():
    live, challenger = book_calls("matched", LIVE, _challenger(corn="bearish", coffee=None))

    assert live["corn"] == LIVE["corn"] and live["coffee"] is None          # live is held where the challenger had none
    assert challenger["corn"]["call"] == "bearish" and challenger["coffee"] is None
    assert {c for c, v in live.items() if v} == {c for c, v in challenger.items() if v} - {"cocoa"} | set()


def test_an_unknown_policy_is_refused():
    with pytest.raises(ValueError):
        book_calls("sometimes", LIVE, {})


def test_the_challengers_output_is_its_last_coordinator_output_with_a_call(tmp_path):
    def write(name, outputs):
        (tmp_path / f"{name}.jsonl").write_text("\n".join(
            json.dumps({"agent": "coordinator", "event_type": "output", "payload": o}) for o in outputs) + "\n")

    write("corn_s", [{"call": "bullish", "conviction": 3}])
    write("wheat_s", [{"call": None, "degraded": True}])

    assert challenger_output(tmp_path, "corn", "s")["call"] == "bullish"
    assert challenger_output(tmp_path, "wheat", "s") is None               # a failed coordinator is no call
    assert challenger_output(tmp_path, "soybeans", "s") is None            # never rerun


def test_max_drawdown_is_the_largest_fall_from_a_peak():
    assert max_drawdown([100, 120, 90, 110, 105]) == (30, 0.25)
    assert max_drawdown([100, 101, 102]) == (0, 0)
