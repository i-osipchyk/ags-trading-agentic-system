from datetime import datetime

from ags.tools.grounding_compare import compare, fetch_cached, pit_filter
from ags.tools.pit_store import ET

AS_OF = datetime(2026, 11, 13, 6, 0, tzinfo=ET)


def item(day, url, source="barchart.com", headline="h"):
    return {"headline": headline, "source": source, "url": url, "date": day, "snippet": "s"}


def test_compare_counts_what_grounding_returned_leaked_and_kept():
    grounded = [
        item("2026-11-13", "https://a.com/1"),  # dated the cycle day: not public at 06:00 ET
        item("2026-11-12", "https://a.com/2"),
        item("2026-11-10", "https://a.com/3"),
    ]

    report = compare(tavily=[], grounded=grounded, as_of=AS_OF)

    assert report["grounded"]["returned"] == 3
    assert report["grounded"]["leaked"] == 1
    assert report["grounded"]["kept"] == 2


def test_compare_reports_the_age_of_what_each_side_would_hand_the_analyst():
    tavily = [item("2026-11-12", "https://t.com/1"), item("2026-11-10", "https://t.com/2"),
              item("2026-09-14", "https://t.com/3")]
    grounded = [item("2026-11-13", "https://a.com/leak"), item("2026-11-12", "https://a.com/1"),
                item("2026-11-10", "https://a.com/2")]

    report = compare(tavily=tavily, grounded=grounded, as_of=AS_OF)

    assert report["tavily"] == {"kept": 3, "median_age_days": 3, "oldest_age_days": 60}
    assert report["grounded"]["median_age_days"] == 2  # the leaked item is not counted
    assert report["grounded"]["oldest_age_days"] == 3


def test_compare_reports_no_age_for_an_empty_side():
    report = compare(tavily=[], grounded=[], as_of=AS_OF)

    assert report["tavily"] == {"kept": 0, "median_age_days": None, "oldest_age_days": None}


def test_compare_overlaps_on_url_and_on_domain_ignoring_www_and_leaked_items():
    tavily = [
        item("2026-11-12", "https://www.barchart.com/story/1", source="www.barchart.com"),
        item("2026-11-11", "https://farmprogress.com/a", source="www.farmprogress.com"),
    ]
    grounded = [
        item("2026-11-12", "https://www.barchart.com/story/1", source="barchart.com"),  # same article
        item("2026-11-12", "https://barchart.com/story/2", source="barchart.com"),  # same domain, other article
        item("2026-11-13", "https://farmprogress.com/a", source="farmprogress.com"),  # leaked: not counted
    ]

    report = compare(tavily=tavily, grounded=grounded, as_of=AS_OF)

    assert report["overlap"] == {"shared_urls": 1, "shared_domains": ["barchart.com"]}


class FakeGrounding:
    def __init__(self, fail_first=False):
        self.calls = 0
        self._fail = fail_first
        self.last_raw_response = None

    def search(self, query, *, published_before):
        self.calls += 1
        if self._fail and self.calls == 1:
            raise RuntimeError("boom")
        self.last_raw_response = {"query": query, "usage": {"search_queries": 1}}
        return [item("2026-11-12", "https://a.com/1")]


def test_fetch_cached_bills_once_per_as_of_commodity_and_query(tmp_path):
    client = FakeGrounding()

    first = fetch_cached(tmp_path, client=client, as_of=AS_OF, commodity="corn", query="corn futures news")
    second = fetch_cached(tmp_path, client=client, as_of=AS_OF, commodity="corn", query="corn futures news")

    assert client.calls == 1
    assert second["grounded"] == first["grounded"] and second["raw"] == first["raw"]
    assert second["cached"] is True and first["cached"] is False
    assert second["raw"]["usage"] == {"search_queries": 1}

    fetch_cached(tmp_path, client=client, as_of=AS_OF, commodity="corn", query="corn exports")  # another query
    fetch_cached(tmp_path, client=client, as_of=AS_OF.replace(day=12), commodity="corn", query="corn futures news")
    assert client.calls == 3


def test_fetch_cached_does_not_cache_a_failed_call(tmp_path):
    client = FakeGrounding(fail_first=True)

    try:
        fetch_cached(tmp_path, client=client, as_of=AS_OF, commodity="corn", query="q")
    except RuntimeError:
        pass
    retried = fetch_cached(tmp_path, client=client, as_of=AS_OF, commodity="corn", query="q")

    assert client.calls == 2
    assert retried["grounded"] == [item("2026-11-12", "https://a.com/1")]


def test_pit_filter_drops_undated_and_not_yet_released_items():
    items = [
        item("2026-11-12", "https://a.com/ok"),
        item("2026-11-13", "https://a.com/same-day"),  # end-of-day release: not public at 06:00 ET
        item(None, "https://a.com/undated"),
    ]

    kept = pit_filter(items, as_of=AS_OF, source="tavily")

    assert [i["url"] for i in kept] == ["https://a.com/ok"]
