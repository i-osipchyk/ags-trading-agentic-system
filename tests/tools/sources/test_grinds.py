import json
from datetime import datetime
from pathlib import Path

from ags.tools.pit_store import ET
from ags.tools.sources import grinds
from ags.tools.sources.grinds import CocoaGrindsClient, get_grinds

EUROPE = {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": 348215, "yoy_pct": 3.22}
NORTH_AMERICA = {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": 109978, "yoy_pct": -2.49}
ASIA = {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": None, "yoy_pct": 29.22}


class FakeGrindsClient:
    def __init__(self, by_region):
        self._by_region = by_region
        self.calls = []

    def get_release(self, region, *, as_of):
        self.calls.append((region, as_of))
        return self._by_region.get(region)


def _client():
    return FakeGrindsClient({"europe": EUROPE, "north_america": NORTH_AMERICA, "asia": ASIA})



def test_get_grinds_returns_the_latest_quarter_of_each_region(tmp_path):
    client = _client()

    result = get_grinds(tmp_path, as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET), client=client)

    # North America (NCA) is skipped for now: its site blocks automated clients, so it is never requested.
    assert result == {"europe": EUROPE, "asia": ASIA}
    assert sorted(region for region, _ in client.calls) == ["asia", "europe"]


def test_get_grinds_drops_a_release_that_is_not_yet_public_at_as_of(tmp_path):
    # All three bodies publish on Oct 15; none counts as public during that day's 06:00 and 12:30 ET cycles.
    on_release_day = get_grinds(tmp_path, as_of=datetime(2026, 10, 15, 12, 30, tzinfo=ET), client=_client())

    assert on_release_day == {"europe": None, "asia": None}


def test_get_grinds_archives_each_release_once_per_region(tmp_path):
    q4 = {
        "europe": {"quarter": "2026-Q4", "published": "2027-01-21", "tonnes": 340000, "yoy_pct": 12.0},
        "north_america": {"quarter": "2026-Q4", "published": "2027-01-21", "tonnes": 100000, "yoy_pct": 1.0},
        "asia": {"quarter": "2026-Q4", "published": "2027-01-21", "tonnes": None, "yoy_pct": 5.0},
    }

    get_grinds(tmp_path, as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET), client=_client())
    get_grinds(tmp_path, as_of=datetime(2026, 10, 16, 12, 30, tzinfo=ET), client=_client())
    get_grinds(tmp_path, as_of=datetime(2027, 1, 22, 6, 0, tzinfo=ET), client=FakeGrindsClient(q4))

    archived = sorted((tmp_path / "cocoa_grinds" / "quarterly" / "europe").glob("*.json"))
    assert [json.loads(path.read_text()) for path in archived] == [EUROPE, q4["europe"]]
    assert len(list((tmp_path / "cocoa_grinds" / "quarterly" / "asia").glob("*.json"))) == 2


FIXTURES = Path(__file__).parents[2] / "fixtures" / "cocoa_grinds"
_NCA_UPLOADS = "https://candyusa.com/wordpress/wp-content/uploads"
_NCA_Q3 = f"{_NCA_UPLOADS}/2026/10/Q3-2026-Cocoa-Grinds-Report.pdf"
_NCA_Q2 = f"{_NCA_UPLOADS}/2026/07/Q2-2026-Cocoa-Grinds.pdf"
# The hrefs of the live page, including a revised letter and the release schedule that are not grind reports.
_NCA_LISTING = "".join(
    f'<a href="{url}">x</a>'
    for url in (
        f"{_NCA_UPLOADS}/2026/01/Q4-2025-CocoaGrinds.pdf",
        f"{_NCA_UPLOADS}/2026/01/NCA_2026-2027_report_schedule_FINAL.pdf",
        f"{_NCA_UPLOADS}/2026/06/Q1-2025-NCA-Letter-to-ICE-revised-as-of-June-22-2026.pdf",
        _NCA_Q2,
        _NCA_Q3,
    )
)


class _Response:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _serve(monkeypatch, pages: dict[str, str | Path]):
    """Serve each URL from `pages` (HTML text, or a fixture file's bytes); any other URL fails the test."""

    def fake_get(url, **kwargs):
        assert url in pages, f"unexpected request: {url}"
        page = pages[url]
        return _Response(content=page.read_bytes()) if isinstance(page, Path) else _Response(text=page)

    monkeypatch.setattr(grinds.requests, "get", fake_get)


def test_the_client_reads_north_americas_latest_grind_from_the_nca_report(monkeypatch):
    _serve(
        monkeypatch,
        {
            "https://candyusa.com/cocoa-grinds-report/": _NCA_LISTING,
            _NCA_Q3: FIXTURES / "nca_q3_2026.pdf",
        },
    )

    record = CocoaGrindsClient().get_release("north_america", as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET))

    # The Q3 2026 memo: "Cocoa Beans Ground 109,978 (2026) vs 112,784 (2025), (2,806), (2.49%)", dated Oct 15 2026.
    assert record == {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": 109978, "yoy_pct": -2.49}


def test_the_client_falls_back_to_the_previous_nca_report_until_the_new_one_is_public(monkeypatch):
    _serve(
        monkeypatch,
        {
            "https://candyusa.com/cocoa-grinds-report/": _NCA_LISTING,
            _NCA_Q3: FIXTURES / "nca_q3_2026.pdf",
            _NCA_Q2: FIXTURES / "nca_q2_2026.pdf",
        },
    )

    record = CocoaGrindsClient().get_release("north_america", as_of=datetime(2026, 10, 15, 12, 30, tzinfo=ET))

    # The Q3 memo is dated Oct 15; the Q2 memo (Jul 16): 109,659 t against 101,865 t, +7.65%.
    assert record == {"quarter": "2026-Q2", "published": "2026-07-16", "tonnes": 109659, "yoy_pct": 7.65}


_ECA_UPLOADS = "https://www.eurococoa.com/wp-content/uploads"
_ECA_Q3 = f"{_ECA_UPLOADS}/WEBSITE-REPORT-WESTERN-STATS-Q3-2026.pdf"
# Hrefs of the live page: file names vary (suffixes, an older file with no quarter in its name).
_ECA_LISTING = "".join(
    f'<a href="{url}">x</a>'
    for url in (
        f"{_ECA_UPLOADS}/1501-WEBSITE-REPORT-WESTERN-STATISTICS.pdf",
        f"{_ECA_UPLOADS}/WEBSITE-REPORT-WESTERN-STATS-Q4-2025-1.pdf",
        f"{_ECA_UPLOADS}/WEBSITE-REPORT-WESTERN-STATS-Q1-2026-2.pdf",
        f"{_ECA_UPLOADS}/WEBSITE-REPORT-WESTERN-STATS-Q2-2026.pdf",
        _ECA_Q3,
    )
)


def test_the_client_reads_europes_latest_grind_from_the_eca_table(monkeypatch):
    _serve(
        monkeypatch,
        {"https://www.eurococoa.com/grind-stats/": _ECA_LISTING, _ECA_Q3: FIXTURES / "eca_q3_2026.pdf"},
    )

    record = CocoaGrindsClient().get_release("europe", as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET))

    # "Date: October 15th 2026". 2026 row (Q4 not yet reported): 348,215 for Q3, against 337,353 in Q3 2025.
    assert record == {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": 348215, "yoy_pct": 3.22}


_CAA_FILES = "https://www.cocoaasia.org/_files/ugd"
_CAA_Q3 = f"{_CAA_FILES}/fba979_fbefe2fe86d94e21bb7aa3fe8220d843.pdf"
# The Wix page: a quarter's label is followed by its download link, and some quarters only link an image.
_CAA_LISTING = (
    '<div>2026 Q3</div><a href="' + _CAA_Q3 + '">Download</a>'
    f'<div>2026 Q2</div><a href="{_CAA_FILES}/fba979_7e8a5c99b5474b05b1b03cbd363163c8.pdf">Download</a>'
    '<div>2024 Q3</div><a href="https://static.wixstatic.com/media/CAA-Grind-Q3-2024.jpg">Download</a>'
)


def test_the_client_reads_asias_latest_grind_from_the_caa_commentary(monkeypatch):
    _serve(
        monkeypatch,
        {"https://www.cocoaasia.org/grinding-figures": _CAA_LISTING, _CAA_Q3: FIXTURES / "caa_q3_2026.pdf"},
    )

    record = CocoaGrindsClient().get_release("asia", as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET))

    # "Published: 15th October 2026. Increase of +29.22% compared to the corresponding period last year, Q3'25".
    # The tonnage only appears in chart text, which is not parsed.
    assert record == {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": None, "yoy_pct": 29.22}


class _AsiaDown(FakeGrindsClient):
    def get_release(self, region, *, as_of):
        if region == "asia":
            raise ConnectionError("cocoaasia.org unreachable")
        return super().get_release(region, as_of=as_of)


def test_get_grinds_keeps_the_other_regions_when_one_source_fails(tmp_path):
    client = _AsiaDown({"europe": EUROPE, "asia": ASIA})

    result = get_grinds(tmp_path, as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET), client=client)

    assert result == {"europe": EUROPE, "asia": None}
