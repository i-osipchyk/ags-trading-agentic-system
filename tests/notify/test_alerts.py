import json

from ags.notify.alerts import breaker_newly_tripped, consecutive_failures
from ags.storage import LocalStorage


def _write(storage, n, failed=(), breaker=False, book="manager"):
    cycle_id = f"cycle_2026-06-0{n}T20-00-00Z"
    storage.put(
        f"portfolio/{book}/cycles/{cycle_id}.json",
        json.dumps({"cycle_id": cycle_id, "failed_commodities": list(failed), "breaker_active": breaker}).encode(),
    )


def test_commodity_failing_in_the_last_two_cycles_is_reported(tmp_path):
    storage = LocalStorage(tmp_path)
    _write(storage, 1, failed=["corn"])
    _write(storage, 2, failed=["corn", "cocoa"])
    _write(storage, 3, failed=["corn", "cocoa"])

    assert consecutive_failures(storage) == ["cocoa", "corn"]


def test_a_recovery_in_between_resets_the_streak(tmp_path):
    storage = LocalStorage(tmp_path)
    _write(storage, 1, failed=["corn"])
    _write(storage, 2, failed=[])
    _write(storage, 3, failed=["corn"])

    assert consecutive_failures(storage) == []


def test_fewer_cycles_than_the_streak_length_reports_nothing(tmp_path):
    storage = LocalStorage(tmp_path)
    _write(storage, 1, failed=["corn"])

    assert consecutive_failures(storage) == []


def test_breaker_alert_fires_only_on_the_cycle_it_trips(tmp_path):
    storage = LocalStorage(tmp_path)
    _write(storage, 1, breaker=False)
    _write(storage, 2, breaker=True)
    assert breaker_newly_tripped(storage) is True

    _write(storage, 3, breaker=True)
    assert breaker_newly_tripped(storage) is False
