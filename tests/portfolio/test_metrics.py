import json

from ags.portfolio.metrics import equity_curve, manager_vs_shadow
from ags.storage import LocalStorage


def _write(storage, book, cycle_id, as_of, equity):
    storage.put(
        f"portfolio/{book}/cycles/{cycle_id}.json",
        json.dumps({"cycle_id": cycle_id, "as_of": as_of, "equity": equity}).encode(),
    )


def _seed(storage):
    for cycle_id, as_of, m, s in [
        ("cycle_2026-01-06T21-00-00Z", "2026-01-06T16:00:00-05:00", 10_000.0, 10_000.0),
        ("cycle_2026-01-07T09-00-00Z", "2026-01-07T04:00:00-05:00", 10_250.0, 10_100.0),
        ("cycle_2026-01-07T21-00-00Z", "2026-01-07T16:00:00-05:00", 9_900.0, 10_150.0),
    ]:
        _write(storage, "manager", cycle_id, as_of, m)
        _write(storage, "shadow", cycle_id, as_of, s)


def test_equity_curve_is_the_closing_equity_of_each_cycle_in_order(tmp_path):
    storage = LocalStorage(tmp_path)
    _seed(storage)

    assert equity_curve(storage, "manager") == [
        ("2026-01-06T16:00:00-05:00", 10_000.0),
        ("2026-01-07T04:00:00-05:00", 10_250.0),
        ("2026-01-07T16:00:00-05:00", 9_900.0),
    ]


def test_manager_vs_shadow_is_the_per_cycle_equity_difference(tmp_path):
    storage = LocalStorage(tmp_path)
    _seed(storage)

    rows = manager_vs_shadow(storage)

    assert [r["diff"] for r in rows] == [0.0, 150.0, -250.0]
    assert rows[-1] == {
        "cycle_id": "cycle_2026-01-07T21-00-00Z", "as_of": "2026-01-07T16:00:00-05:00",
        "manager": 9_900.0, "shadow": 10_150.0, "diff": -250.0,
    }


def test_a_cycle_present_in_only_one_book_is_left_out_of_the_comparison(tmp_path):
    storage = LocalStorage(tmp_path)
    _seed(storage)
    _write(storage, "manager", "cycle_2026-01-08T09-00-00Z", "2026-01-08T04:00:00-05:00", 9_000.0)

    assert len(manager_vs_shadow(storage)) == 3
