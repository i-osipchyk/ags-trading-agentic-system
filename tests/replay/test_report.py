import json

import pytest

from ags.replay import report
from ags.storage import LocalStorage

FORK = "cycle_2026-10-19T20-00-00Z"  # the official book the replay was seeded from
C1, C2 = "cycle_2026-10-20T08-00-00Z", "cycle_2026-10-20T20-00-00Z"


def _calls(**directions):
    return {c: ({"call": d} if d else None) for c, d in directions.items()}


def _book(store, prefix, book, cycle, equity, calls=None):
    record = {"cycle_id": cycle, "equity": equity, "calls": calls or {}}
    if prefix.startswith("replays") and cycle == FORK:
        record["seeded_from"] = "manager"
    store.put(f"{prefix}/{book}/cycles/{cycle}.json", json.dumps(record).encode())


def test_the_report_gives_each_lines_pnl_since_the_fork_and_how_many_calls_changed(tmp_path):
    store = LocalStorage(tmp_path)
    official_c1 = _calls(corn="bullish", wheat="neutral", cocoa=None)
    official_c2 = _calls(corn="bullish", wheat="neutral", cocoa="bearish")
    for cycle, mgr, shd, c in ((FORK, 10_000, 9_000, {}), (C1, 10_100, 9_050, official_c1), (C2, 9_900, 9_200, official_c2)):
        _book(store, "portfolio", "manager", cycle, mgr, c)
        _book(store, "portfolio", "shadow", cycle, shd, c)
    replayed_c1 = _calls(corn="bearish", wheat="neutral", cocoa="bullish")  # corn flipped, cocoa newly called
    replayed_c2 = _calls(corn="bullish", wheat="neutral", cocoa="bearish")  # same as official
    for cycle, mgr, shd, c in ((FORK, 10_000, 10_000, {}), (C1, 10_300, 9_950, replayed_c1), (C2, 10_500, 10_020, replayed_c2)):
        _book(store, "replays/r1/portfolio", "manager", cycle, mgr, c)
        _book(store, "replays/r1/portfolio", "shadow", cycle, shd, c)

    rows = report(store, "r1")

    assert [r["cycle_id"] for r in rows] == [C1, C2]
    assert rows[0]["pnl"] == pytest.approx(
        {"official_manager": 100, "official_shadow": 50, "replay_manager": 300, "replay_shadow": -50}
    )
    assert rows[1]["pnl"] == pytest.approx(
        {"official_manager": -100, "official_shadow": 200, "replay_manager": 500, "replay_shadow": 20}
    )
    assert [r["calls_changed"] for r in rows] == [2, 0]
