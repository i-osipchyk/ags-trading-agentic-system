from datetime import date

from ags.tools.sources.usda import get_balance_sheet_revisions, get_usda_report


class FakeUsdaClient:
    """Serves the latest WASDE release at or before as_of from a canned archive."""

    def __init__(self, releases_by_symbol=None):
        self._releases = releases_by_symbol or {}
        self.calls = []

    def get_report(self, report, symbol, *, as_of):
        self.calls.append((report, symbol, as_of))
        eligible = [r for r in self._releases.get(symbol, []) if date.fromisoformat(r["release_date"]) <= as_of]
        return max(eligible, key=lambda r: r["release_date"]) if eligible else None


CORN_SEP = {
    "release_date": "2026-09-11",
    "marketing_year": "2026/27",
    "ending_stocks": 2100.0,
    "stocks_to_use": 13.1,
}


def test_get_usda_report_fetches_through_client_and_writes_pit_store(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_SEP]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=date(2026, 10, 1), client=client)

    assert result["latest"] == CORN_SEP
    assert client.calls[0] == ("wasde", "corn", date(2026, 10, 1))
    stored = list((tmp_path / "usda_esmis" / "wasde" / "corn").glob("*.json"))
    assert [f.stem for f in stored] == ["2026-09-11"]


class LeakyUsdaClient:
    """Ignores as_of and returns a release dated after it."""

    def get_report(self, report, symbol, *, as_of):
        return {**CORN_SEP, "release_date": "2026-10-09"}


def test_get_usda_report_rejects_future_dated_release_from_client_and_does_not_store_it(tmp_path):
    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=date(2026, 10, 1), client=LeakyUsdaClient())

    assert result["latest"] is None
    assert not list(tmp_path.rglob("*.json"))


def test_get_usda_report_second_call_for_same_as_of_is_a_cache_noop(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_SEP]})

    first = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=date(2026, 10, 1), client=client)
    second = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=date(2026, 10, 1), client=client)

    assert second == first
    assert [c for c in client.calls if c[2] == date(2026, 10, 1)] == [("wasde", "corn", date(2026, 10, 1))]


CORN_AUG = {
    "release_date": "2026-08-12",
    "marketing_year": "2026/27",
    "ending_stocks": 1900.0,
    "stocks_to_use": 12.0,
}


def test_get_usda_report_includes_prior_release_and_change_from_it(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_AUG, CORN_SEP]})

    result = get_usda_report(tmp_path, report="wasde", symbol="corn", as_of=date(2026, 10, 1), client=client)

    assert result["latest"] == CORN_SEP
    assert result["prior"] == CORN_AUG
    assert result["change"] == {"ending_stocks": 200.0, "stocks_to_use": 1.1}
    assert result["revision_direction"] == "up"


def test_get_balance_sheet_revisions_reports_loosening_when_stocks_to_use_rises(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_AUG, CORN_SEP]})

    result = get_balance_sheet_revisions(tmp_path, symbol="corn", as_of=date(2026, 10, 1), client=client)

    assert result == {
        "symbol": "corn",
        "ending_stocks_change": 200.0,
        "stocks_to_use_change": 1.1,
        "stocks_to_use_trend": "loosening",
    }


def test_get_balance_sheet_revisions_reports_tightening_when_stocks_to_use_falls(tmp_path):
    shrinking = {**CORN_SEP, "ending_stocks": 1700.0, "stocks_to_use": 10.9}
    client = FakeUsdaClient({"corn": [CORN_AUG, shrinking]})

    result = get_balance_sheet_revisions(tmp_path, symbol="corn", as_of=date(2026, 10, 1), client=client)

    assert result["stocks_to_use_trend"] == "tightening"


def test_get_balance_sheet_revisions_has_no_trend_without_a_prior_release(tmp_path):
    client = FakeUsdaClient({"corn": [CORN_SEP]})

    result = get_balance_sheet_revisions(tmp_path, symbol="corn", as_of=date(2026, 10, 1), client=client)

    assert result == {"symbol": "corn", "ending_stocks_change": None, "stocks_to_use_change": None, "stocks_to_use_trend": None}
