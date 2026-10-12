import json

import pytest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from ags.tools.pit_store import read_pit, write_pit

ET = ZoneInfo("America/New_York")


def _write_release(data_dir: Path, source: str, report: str, symbol: str, release_date: str, content: dict) -> None:
    dir_path = data_dir / source / report / symbol
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{release_date}.json").write_text(json.dumps(content))


def test_read_pit_returns_release_when_release_date_before_as_of(tmp_path):
    release = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-01-15", release)

    result = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 1, 12, 0, tzinfo=ET))

    assert result == release


def test_read_pit_ignores_future_release_when_a_qualifying_release_also_exists(tmp_path):
    qualifying = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    future = {"release_date": "2026-03-01", "fetched_at": "2026-03-01T18:00:00Z", "figures": {"ending_stocks": 9999}}
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-01-15", qualifying)
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-03-01", future)

    result = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 1, 12, 0, tzinfo=ET))

    assert result == qualifying


def test_read_pit_returns_latest_qualifying_release_when_a_revision_exists(tmp_path):
    earlier = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    revision = {"release_date": "2026-02-01", "fetched_at": "2026-02-01T18:00:00Z", "figures": {"ending_stocks": 1111}}
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-01-15", earlier)
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-02-01", revision)

    result = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 3, 1, 12, 0, tzinfo=ET))

    assert result == revision


def test_read_pit_returns_none_when_no_releases_exist(tmp_path):
    result = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 1, 12, 0, tzinfo=ET))

    assert result is None


def test_read_pit_never_returns_a_future_dated_release(tmp_path):
    future_release = {"release_date": "2026-03-01", "fetched_at": "2026-03-01T18:00:00Z", "figures": {}}
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-03-01", future_release)

    result = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 1, 12, 0, tzinfo=ET))

    assert result is None


def test_read_pit_release_timestamp_equal_to_as_of_is_inclusive(tmp_path):
    release = {"release_date": "2026-02-01", "fetched_at": "2026-02-01T18:00:00Z", "figures": {}}
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-02-01", release)

    result = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 1, 12, 0, tzinfo=ET))

    assert result == release


def test_write_pit_writes_a_new_release_and_reports_it_was_written(tmp_path):
    content = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}

    written = write_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content=content)

    assert written is True
    stored = json.loads((tmp_path / "usda_esmis" / "wasde" / "corn" / "2026-01-15.json").read_text())
    assert stored == content


def test_write_pit_is_a_noop_when_release_date_already_exists(tmp_path):
    original = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    write_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content=original)

    later_fetch = {"release_date": "2026-01-15", "fetched_at": "2026-01-16T09:00:00Z", "figures": {"ending_stocks": 9999}}
    written = write_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content=later_fetch)

    assert written is False
    stored = json.loads((tmp_path / "usda_esmis" / "wasde" / "corn" / "2026-01-15.json").read_text())
    assert stored == original


def test_read_pit_hides_a_same_day_timestamped_release_until_as_of_reaches_it(tmp_path):
    released_at = datetime(2026, 2, 3, 9, 30, tzinfo=ET)
    write_pit(tmp_path, source="quotes", report="snapshot", symbol="corn", release_date=released_at, content={"bid": 4.5})

    before = read_pit(tmp_path, source="quotes", report="snapshot", symbol="corn", as_of=datetime(2026, 2, 3, 9, 29, tzinfo=ET))
    after = read_pit(tmp_path, source="quotes", report="snapshot", symbol="corn", as_of=datetime(2026, 2, 3, 9, 31, tzinfo=ET))

    assert before is None
    assert after == {"bid": 4.5}


def test_two_timestamped_releases_on_one_day_are_stored_separately_and_the_latest_visible_wins(tmp_path):
    write_pit(tmp_path, source="news", report="search", symbol="corn", release_date=datetime(2026, 2, 3, 4, 0, tzinfo=ET), content={"n": 1})
    second_written = write_pit(tmp_path, source="news", report="search", symbol="corn", release_date=datetime(2026, 2, 3, 16, 0, tzinfo=ET), content={"n": 2})

    at_0400 = read_pit(tmp_path, source="news", report="search", symbol="corn", as_of=datetime(2026, 2, 3, 4, 0, tzinfo=ET))
    at_1600 = read_pit(tmp_path, source="news", report="search", symbol="corn", as_of=datetime(2026, 2, 3, 16, 0, tzinfo=ET))

    assert second_written is True
    assert at_0400 == {"n": 1}
    assert at_1600 == {"n": 2}


def test_date_only_release_of_a_source_with_a_configured_time_is_visible_from_that_time(tmp_path):
    # WASDE is published at 12:00 ET.
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-02-10", {"figures": {}})

    before = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 10, 11, 59, tzinfo=ET))
    after = read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 10, 12, 0, tzinfo=ET))

    assert before is None
    assert after == {"figures": {}}


def test_date_only_release_of_an_unconfigured_source_is_visible_only_from_the_next_day(tmp_path):
    _write_release(tmp_path, "mystery", "report", "corn", "2026-02-10", {"x": 1})

    late_same_day = read_pit(tmp_path, source="mystery", report="report", symbol="corn", as_of=datetime(2026, 2, 10, 23, 59, tzinfo=ET))
    next_midnight = read_pit(tmp_path, source="mystery", report="report", symbol="corn", as_of=datetime(2026, 2, 11, 0, 0, tzinfo=ET))

    assert late_same_day is None
    assert next_midnight == {"x": 1}


def test_read_pit_compares_instants_across_timezones(tmp_path):
    _write_release(tmp_path, "usda_esmis", "wasde", "corn", "2026-02-10", {"figures": {}})

    # 16:59 UTC == 11:59 ET (before noon ET); 17:00 UTC == 12:00 ET.
    utc = ZoneInfo("UTC")
    assert read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 10, 16, 59, tzinfo=utc)) is None
    assert read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 10, 17, 0, tzinfo=utc)) == {"figures": {}}


def test_read_pit_rejects_a_naive_as_of(tmp_path):
    with pytest.raises(ValueError):
        read_pit(tmp_path, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 10, 12, 0))


def test_pit_store_works_through_a_storage_and_keeps_the_on_disk_layout(tmp_path):
    from ags.storage import LocalStorage, Prefixed

    store = Prefixed(LocalStorage(tmp_path), "data")
    written = write_pit(store, source="usda_esmis", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content={"n": 1})
    again = write_pit(store, source="usda_esmis", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content={"n": 2})

    result = read_pit(store, source="usda_esmis", report="wasde", symbol="corn", as_of=datetime(2026, 2, 1, 12, 0, tzinfo=ET))

    assert (written, again) == (True, False)
    assert result == {"n": 1}
    assert json.loads((tmp_path / "data" / "usda_esmis" / "wasde" / "corn" / "2026-01-15.json").read_text()) == {"n": 1}
