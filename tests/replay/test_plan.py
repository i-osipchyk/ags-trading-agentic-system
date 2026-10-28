import json

from ags.replay import ReplaySpec, pending_cycles
from ags.storage import LocalStorage
from tests.replay.test_spec import SPEC

SPEC_OBJ = ReplaySpec.from_mapping(SPEC)  # forks at 2026-10-20T08:00Z


def _cycle(store, cycle_id, prefix="portfolio"):
    store.put(f"{prefix}/manager/cycles/{cycle_id}.json", json.dumps({"cycle_id": cycle_id}).encode())


def test_pending_cycles_are_the_official_ones_from_the_fork_with_a_book_file_and_not_yet_replayed(tmp_path):
    store = LocalStorage(tmp_path)
    for cycle_id in (
        "cycle_2026-10-19T20-00-00Z",  # before the fork
        "cycle_2026-10-20T08-00-00Z",  # the fork cycle itself
        "cycle_2026-10-20T20-00-00Z",
        # 2026-10-21T08-00-00Z: an outage, no book file
        "cycle_2026-10-22T20-00-00Z",
    ):
        _cycle(store, cycle_id)
    _cycle(store, "cycle_2026-10-20T08-00-00Z", prefix="replays/coord-v2/portfolio")  # already replayed

    assert pending_cycles(store, SPEC_OBJ) == ["cycle_2026-10-20T20-00-00Z", "cycle_2026-10-22T20-00-00Z"]


def test_official_cycles_come_from_the_demo_managers_store_once_it_has_records(tmp_path):
    store = LocalStorage(tmp_path)
    _cycle(store, "cycle_2026-10-19T20-00-00Z")  # the paper manager's era
    assert pending_cycles(store, SPEC_OBJ) == []
    store.put("portfolio/demo/manager/cycles/cycle_2026-10-21T10-00-00Z.json", b"{}")

    assert pending_cycles(store, SPEC_OBJ) == ["cycle_2026-10-21T10-00-00Z"]
