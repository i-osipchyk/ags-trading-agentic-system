import json
from datetime import date, datetime, timedelta, timezone

from ags.tools.sources.usda import get_balance_sheet_revisions, get_usda_report
from ags.tools.pit_store import ET, is_released


class FakeUsdaClient:
    """Serves the latest WASDE release at or before as_of from a canned archive."""

    def __init__(self, releases_by_symbol=None):
        self._releases = releases_by_symbol or {}
        self.calls = []

    def get_report(self, report, symbol, *, as_of):
        self.calls.append((report, symbol, as_of))
        eligible = [r for r in self._releases.get(symbol, []) if is_released("usda_esmis", date.fromisoformat(r["release_date"]), as_of)]
        return max(eligible, key=lambda r: r["release_date"]) if eligible else None


CORN_SEP = {
    "release_date": "2026-09-11",
    "marketing_year": "2026/27",
    "ending_stocks": 2100.0,
    "stocks_to_use": 13.1,
}


def test_get_usda_report_fetches_through_client_and_writes_pit_store(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_SEP]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result["latest"] == CORN_SEP
    assert client.calls[0] == ("wasde", "corn", datetime(2026, 10, 1, 16, 0, tzinfo=ET))
    stored = list((tmp_path / "usda_esmis" / "wasde" / "corn").glob("*.json"))
    assert [f.stem for f in stored] == ["2026-09-11"]


class LeakyUsdaClient:
    """Ignores as_of and returns a release dated after it."""

    def get_report(self, report, symbol, *, as_of):
        return {**CORN_SEP, "release_date": "2026-10-09"}


def test_get_usda_report_rejects_future_dated_release_from_client_and_does_not_store_it(tmp_path):
    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=LeakyUsdaClient())

    assert result["latest"] is None
    assert not list(tmp_path.rglob("*.json"))


def test_get_usda_report_second_call_for_same_as_of_is_a_cache_noop(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_SEP]})

    first = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)
    second = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert second == first
    assert [c for c in client.calls if c[2] == datetime(2026, 10, 1, 16, 0, tzinfo=ET)] == [("wasde", "corn", datetime(2026, 10, 1, 16, 0, tzinfo=ET))]


CORN_AUG = {
    "release_date": "2026-08-12",
    "marketing_year": "2026/27",
    "ending_stocks": 1900.0,
    "stocks_to_use": 12.0,
}


def test_get_usda_report_includes_prior_release_and_change_from_it(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_AUG, CORN_SEP]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result["latest"] == CORN_SEP
    assert result["prior"] == CORN_AUG
    assert result["change"] == {"ending_stocks": 200.0, "stocks_to_use": 1.1}
    assert result["revision_direction"] == "up"


def test_get_balance_sheet_revisions_reports_loosening_when_stocks_to_use_rises(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_AUG, CORN_SEP]})

    result = get_balance_sheet_revisions(tmp_path, symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result == {
        "symbol": "corn",
        "ending_stocks_change": 200.0,
        "stocks_to_use_change": 1.1,
        "stocks_to_use_trend": "loosening",
    }


def test_get_balance_sheet_revisions_reports_tightening_when_stocks_to_use_falls(tmp_path):
    shrinking = {**CORN_SEP, "ending_stocks": 1700.0, "stocks_to_use": 10.9}
    client = FakeUsdaClient({"corn": [CORN_AUG, shrinking]})

    result = get_balance_sheet_revisions(tmp_path, symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result["stocks_to_use_trend"] == "tightening"


def test_get_balance_sheet_revisions_has_no_trend_without_a_prior_release(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_SEP]})

    result = get_balance_sheet_revisions(tmp_path, symbol="corn", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result == {"symbol": "corn", "ending_stocks_change": None, "stocks_to_use_change": None, "stocks_to_use_trend": None}


COFFEE_DEC = {
    "release_date": "2024-12-18",
    "marketing_year": "2025/26",
    "ending_stocks": 21752.0,
    "stocks_to_use": 13.06,
}
COFFEE_JUN = {
    "release_date": "2025-06-25",
    "marketing_year": "2025/26",
    "ending_stocks": 22819.0,
    "stocks_to_use": 13.47,
}


def test_get_balance_sheet_revisions_for_coffee_reads_the_coffee_report_not_wasde(tmp_path):
    client = FakeUsdaClient({"coffee": [COFFEE_DEC, COFFEE_JUN]})

    result = get_balance_sheet_revisions(tmp_path, symbol="coffee", as_of=datetime(2025, 7, 15, 16, 0, tzinfo=ET), client=client)

    assert {report for report, _, _ in client.calls} == {"coffee_world_markets"}
    assert result == {
        "symbol": "coffee",
        "ending_stocks_change": 1067.0,
        "stocks_to_use_change": 0.41,
        "stocks_to_use_trend": "loosening",
    }


COFFEE_DEC_2025 = {
    "release_date": "2025-12-18",
    "marketing_year": "2025/26",
    "ending_stocks": 20148.0,
    "stocks_to_use": 11.59,
}
COFFEE_JUL_2026 = {
    "release_date": "2026-07-22",
    "marketing_year": "2026/27",
    "ending_stocks": 26283.0,
    "stocks_to_use": 14.62,
}


def test_a_prior_release_for_another_marketing_year_is_not_a_revision(tmp_path):
    # The Jul 2026 edition opens 2026/27; the Dec 2025 edition before it is the 2025/26 balance sheet, so
    # the gap between them is a new year's forecast, not a revision.
    client = FakeUsdaClient({"coffee": [COFFEE_DEC_2025, COFFEE_JUL_2026]})
    as_of = datetime(2026, 10, 30, 10, 0, tzinfo=ET)

    report = get_usda_report(tmp_path, report="coffee_world_markets", symbol="coffee", as_of=as_of, client=client)
    revisions = get_balance_sheet_revisions(tmp_path, symbol="coffee", as_of=as_of, client=client)

    assert report["latest"] == COFFEE_JUL_2026
    assert report["prior"] is None
    assert "change" not in report
    assert revisions == {"symbol": "coffee", "ending_stocks_change": None, "stocks_to_use_change": None, "stocks_to_use_trend": None}


CORN_OCT = {"release_date": "2026-10-09", "marketing_year": "2026/27", "ending_stocks": 1849.0, "stocks_to_use": 11.34}
CORN_NOV = {"release_date": "2026-11-10", "marketing_year": "2026/27", "ending_stocks": 1758.0, "stocks_to_use": 10.82}
_AFTER_NOV_RELEASE = datetime(2026, 11, 10, 12, 30, tzinfo=ET)


def _cache_october(tmp_path, record=CORN_OCT):
    get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=datetime(2026, 10, 20, 12, 30, tzinfo=ET), client=FakeUsdaClient({"corn": [record]}), now=datetime(2026, 11, 1, tzinfo=timezone.utc))


def test_a_live_cycle_after_a_new_release_refreshes_a_stale_cached_release(tmp_path):
    _cache_october(tmp_path)
    client = FakeUsdaClient({"corn": [CORN_OCT, CORN_NOV]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=client, now=_AFTER_NOV_RELEASE + timedelta(minutes=6))

    assert result["latest"] == CORN_NOV
    assert result["prior"] == CORN_OCT
    assert result["change"]["ending_stocks"] == -91.0
    assert len(client.calls) == 1  # the prior release comes from the cache
    assert {f.stem for f in (tmp_path / "usda_esmis" / "wasde" / "corn").glob("*.json")} == {"2026-10-09", "2026-11-10"}


def test_a_live_cycle_before_the_new_release_keeps_the_cached_one(tmp_path):
    _cache_october(tmp_path)
    before = datetime(2026, 11, 10, 6, 0, tzinfo=ET)
    client = FakeUsdaClient({"corn": [CORN_OCT, CORN_NOV]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=before, client=client, now=before + timedelta(minutes=6))

    assert result["latest"] == CORN_OCT


def test_a_live_cycle_keeps_the_cache_when_the_source_has_nothing_newer_yet(tmp_path):
    _cache_october(tmp_path)

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=FakeUsdaClient({"corn": [CORN_OCT]}), now=_AFTER_NOV_RELEASE)

    assert result["latest"] == CORN_OCT


def test_a_replayed_as_of_reads_the_cache_and_never_asks_the_source(tmp_path):
    _cache_october(tmp_path)
    client = FakeUsdaClient({"corn": [CORN_OCT, CORN_NOV]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=client, now=_AFTER_NOV_RELEASE + timedelta(days=3))

    assert result["latest"] == CORN_OCT
    assert [c for c in client.calls if c[2] == _AFTER_NOV_RELEASE] == []


def test_a_recent_cached_release_is_not_refreshed(tmp_path):
    full = {**CORN_OCT, "figures": {}}
    _cache_october(tmp_path, full)
    client = FakeUsdaClient({"corn": [full]})
    as_of = datetime(2026, 10, 25, 12, 30, tzinfo=ET)  # 16 days after the release

    get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=as_of, client=client, now=as_of)

    assert [c for c in client.calls if c[2] == as_of] == []


NOV_FIGURES = {
    "yield": {"value": 179.6, "prior": 181.2},
    "ending_stocks": {"value": 1758.0, "prior": 1849.0},
    "total_use": {"value": 16255.0, "prior": 16305.0},
    "avg_farm_price": {"value": 4.6, "prior": None},
}
CORN_NOV_FULL = {**CORN_NOV, "figures": NOV_FIGURES, "prior_stocks_to_use": 11.34, "units": "million bushels"}


def test_the_report_gives_each_figures_change_from_the_prior_month_the_release_states(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_NOV_FULL]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=client, now=_AFTER_NOV_RELEASE)

    assert result["figure_changes"]["yield"] == {"value": 179.6, "prior": 181.2, "change": -1.6, "direction": "down"}
    assert result["figure_changes"]["ending_stocks"]["change"] == -91.0
    assert "avg_farm_price" not in result["figure_changes"]  # no earlier month to compare with


def test_the_revision_comes_from_the_release_itself_when_no_earlier_release_is_stored(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_NOV_FULL]})  # nothing before it to serve as the prior release

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=client, now=_AFTER_NOV_RELEASE)

    assert result["prior"] is None
    assert result["change"] == {"ending_stocks": -91.0, "stocks_to_use": round(CORN_NOV["stocks_to_use"] - 11.34, 4)}
    assert result["revision_direction"] == "down"


def _cache_nov_without_figures(tmp_path):
    """The Nov release as an older version of the parser stored it (a replayed read, so it only reads the source)."""
    get_usda_report(
        tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=FakeUsdaClient({"corn": [CORN_NOV]}),
        now=_AFTER_NOV_RELEASE + timedelta(days=3),
    )
    assert "figures" not in json.loads((tmp_path / "usda_esmis" / "wasde" / "corn" / "2026-11-10.json").read_text())


def test_a_live_cycle_stores_a_record_cached_without_figures_again_with_them(tmp_path):
    _cache_nov_without_figures(tmp_path)
    client = FakeUsdaClient({"corn": [CORN_NOV_FULL]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=client, now=_AFTER_NOV_RELEASE)

    assert result["latest"]["figures"] == NOV_FIGURES
    stored = json.loads((tmp_path / "usda_esmis" / "wasde" / "corn" / "2026-11-10.json").read_text())
    assert stored["figures"] == NOV_FIGURES


def test_a_replay_leaves_a_record_cached_without_figures_alone(tmp_path):
    _cache_nov_without_figures(tmp_path)
    client = FakeUsdaClient({"corn": [CORN_NOV_FULL]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=_AFTER_NOV_RELEASE, client=client, now=_AFTER_NOV_RELEASE + timedelta(days=3))

    assert "figures" not in result["latest"]
    assert [c for c in client.calls if c[2] == _AFTER_NOV_RELEASE] == []
