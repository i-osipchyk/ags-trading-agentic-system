from datetime import date

import pytest

from ags.tools.sources.combined_news import CombinedNewsClient, is_article_url

DAY = date(2026, 11, 13)


class FakeTavily:
    def __init__(self, results):
        self.results, self.last_raw_response = results, None

    def search(self, query, *, published_before):
        self.last_raw_response = {"results": self.results}
        return self.results

    def extract(self, url):
        return {"url": url, "text": "body"}


class FakeGrounded:
    def __init__(self, results=None, error=None):
        self.results, self.error, self.last_raw_response = results or [], error, {"stale": True}

    def search(self, query, *, published_before):
        if self.error:
            raise self.error
        self.last_raw_response = {"query": query}
        return self.results


def _item(url, **extra):
    return {"headline": "h", "source": "s", "url": url, "date": "2026-11-12", "snippet": "x", **extra}


@pytest.mark.parametrize("url, expected", [
    ("https://www.barchart.com/story/news/123/corn-rallies", True),
    ("https://www.youtube.com/watch?v=abc", False),
    ("https://youtu.be/abc", False),
    ("https://www.tradingview.com/symbols/CBOT-ZC1!/", False),
    ("https://www.barchart.com/futures/quotes/ZCZ26", False),
    (None, False),
])
def test_only_article_shaped_urls_pass(url, expected):
    assert is_article_url(url) is expected


def test_results_are_tagged_by_provider_and_deduped_by_url():
    client = CombinedNewsClient(
        FakeTavily([_item("https://a.com/1")]),
        FakeGrounded([_item("https://a.com/1"), _item("https://b.com/2"), _item("https://youtu.be/x")]),
    )

    results = client.search("corn", published_before=DAY)

    assert [(r["url"], r["provider"]) for r in results] == [
        ("https://a.com/1", "tavily"), ("https://b.com/2", "google_grounding")]
    assert client.last_raw_response == {"tavily": {"results": [_item("https://a.com/1")]},
                                        "google_grounding": {"query": "corn"}}


def test_a_grounding_failure_still_returns_tavilys_results_and_no_stale_raw_answer():
    client = CombinedNewsClient(FakeTavily([_item("https://a.com/1")]), FakeGrounded(error=RuntimeError("boom")))

    results = client.search("corn", published_before=DAY)

    assert [r["provider"] for r in results] == ["tavily"]
    assert client.last_raw_response["google_grounding"] is None


def test_extract_goes_to_tavily():
    assert CombinedNewsClient(FakeTavily([]), FakeGrounded()).extract("https://a.com/1")["text"] == "body"


def test_grounding_alone_tags_filters_and_keeps_the_raw_answer_in_the_same_layout():
    from ags.tools.sources.combined_news import GroundedNewsOnlyClient

    grounded = FakeGrounded([_item("https://b.com/2"), _item("https://youtu.be/x")])
    client = GroundedNewsOnlyClient(grounded)

    results = client.search("corn", published_before=DAY)

    assert [(r["url"], r["provider"]) for r in results] == [("https://b.com/2", "google_grounding")]
    assert client.last_raw_response == {"google_grounding": {"query": "corn"}}


def test_grounding_alone_keeps_no_stale_raw_answer_when_the_call_raises():
    from ags.tools.sources.combined_news import GroundedNewsOnlyClient

    client = GroundedNewsOnlyClient(FakeGrounded(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        client.search("corn", published_before=DAY)

    assert client.last_raw_response == {"google_grounding": None}
