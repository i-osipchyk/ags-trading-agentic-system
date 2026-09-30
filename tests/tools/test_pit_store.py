import json
from datetime import date
from pathlib import Path

from ags.tools.pit_store import read_pit, write_pit


def _write_release(data_dir: Path, source: str, report: str, symbol: str, release_date: str, content: dict) -> None:
    dir_path = data_dir / source / report / symbol
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{release_date}.json").write_text(json.dumps(content))


def test_read_pit_returns_release_when_release_date_before_as_of(tmp_path):
    release = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    _write_release(tmp_path, "usda", "wasde", "corn", "2026-01-15", release)

    result = read_pit(tmp_path, source="usda", report="wasde", symbol="corn", as_of=date(2026, 2, 1))

    assert result == release


def test_read_pit_ignores_future_release_when_a_qualifying_release_also_exists(tmp_path):
    qualifying = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    future = {"release_date": "2026-03-01", "fetched_at": "2026-03-01T18:00:00Z", "figures": {"ending_stocks": 9999}}
    _write_release(tmp_path, "usda", "wasde", "corn", "2026-01-15", qualifying)
    _write_release(tmp_path, "usda", "wasde", "corn", "2026-03-01", future)

    result = read_pit(tmp_path, source="usda", report="wasde", symbol="corn", as_of=date(2026, 2, 1))

    assert result == qualifying


def test_read_pit_returns_latest_qualifying_release_when_a_revision_exists(tmp_path):
    earlier = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    revision = {"release_date": "2026-02-01", "fetched_at": "2026-02-01T18:00:00Z", "figures": {"ending_stocks": 1111}}
    _write_release(tmp_path, "usda", "wasde", "corn", "2026-01-15", earlier)
    _write_release(tmp_path, "usda", "wasde", "corn", "2026-02-01", revision)

    result = read_pit(tmp_path, source="usda", report="wasde", symbol="corn", as_of=date(2026, 3, 1))

    assert result == revision


def test_read_pit_returns_none_when_no_releases_exist(tmp_path):
    result = read_pit(tmp_path, source="usda", report="wasde", symbol="corn", as_of=date(2026, 2, 1))

    assert result is None


def test_read_pit_never_returns_a_future_dated_release(tmp_path):
    future_release = {"release_date": "2026-03-01", "fetched_at": "2026-03-01T18:00:00Z", "figures": {}}
    _write_release(tmp_path, "usda", "wasde", "corn", "2026-03-01", future_release)

    result = read_pit(tmp_path, source="usda", report="wasde", symbol="corn", as_of=date(2026, 2, 1))

    assert result is None


def test_read_pit_release_date_equal_to_as_of_is_inclusive(tmp_path):
    release = {"release_date": "2026-02-01", "fetched_at": "2026-02-01T18:00:00Z", "figures": {}}
    _write_release(tmp_path, "usda", "wasde", "corn", "2026-02-01", release)

    result = read_pit(tmp_path, source="usda", report="wasde", symbol="corn", as_of=date(2026, 2, 1))

    assert result == release


def test_write_pit_writes_a_new_release_and_reports_it_was_written(tmp_path):
    content = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}

    written = write_pit(tmp_path, source="usda", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content=content)

    assert written is True
    stored = json.loads((tmp_path / "usda" / "wasde" / "corn" / "2026-01-15.json").read_text())
    assert stored == content


def test_write_pit_is_a_noop_when_release_date_already_exists(tmp_path):
    original = {"release_date": "2026-01-15", "fetched_at": "2026-01-15T18:00:00Z", "figures": {"ending_stocks": 1234}}
    write_pit(tmp_path, source="usda", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content=original)

    later_fetch = {"release_date": "2026-01-15", "fetched_at": "2026-01-16T09:00:00Z", "figures": {"ending_stocks": 9999}}
    written = write_pit(tmp_path, source="usda", report="wasde", symbol="corn", release_date=date(2026, 1, 15), content=later_fetch)

    assert written is False
    stored = json.loads((tmp_path / "usda" / "wasde" / "corn" / "2026-01-15.json").read_text())
    assert stored == original
