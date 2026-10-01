import json
from datetime import date

from ags.tools.sources.news import fetch_document, search_news


class FakeNewsClient:
    def __init__(self, results=None, documents=None):
        self._results = results or []
        self._documents = documents or {}
        self.search_calls = []
        self.extract_calls = []

    def search(self, query, *, published_before):
        self.search_calls.append((query, published_before))
        return self._results

    def extract(self, url):
        self.extract_calls.append(url)
        return self._documents[url]


CORN_RESULTS = [
    {
        "headline": "Corn exports jump on strong Chinese demand",
        "source": "reuters.com",
        "url": "https://reuters.com/corn-exports",
        "date": "2026-06-01",
        "snippet": "Weekly export sales data showed corn demand from China rising.",
    },
    {
        "headline": "Midwest weather turns favorable for corn planting",
        "source": "agweb.com",
        "url": "https://agweb.com/corn-weather",
        "date": "2026-06-02",
        "snippet": "Dry conditions gave way to timely rain across the Corn Belt.",
    },
]


def test_search_news_fetches_through_client_and_writes_pit_store(tmp_path):
    client = FakeNewsClient(CORN_RESULTS)

    result = search_news(
        tmp_path,
        commodity="corn",
        query="corn futures news",
        as_of=date(2026, 6, 5),
        client=client,
    )

    assert result == CORN_RESULTS
    assert len(client.search_calls) == 1
    assert client.search_calls[0] == ("corn futures news", date(2026, 6, 5))

    stored_files = list((tmp_path / "tavily" / "search_news").glob("*/*.json"))
    assert len(stored_files) == 1
    stored = json.loads(stored_files[0].read_text())
    assert stored["query"] == "corn futures news"
    assert stored["results"] == CORN_RESULTS


def test_search_news_second_call_for_same_commodity_query_and_as_of_is_a_noop(tmp_path):
    client = FakeNewsClient(CORN_RESULTS)

    first = search_news(tmp_path, commodity="corn", query="corn futures news", as_of=date(2026, 6, 5), client=client)
    second = search_news(tmp_path, commodity="corn", query="corn futures news", as_of=date(2026, 6, 5), client=client)

    assert second == first
    assert len(client.search_calls) == 1


def test_search_news_drops_results_dated_after_as_of(tmp_path):
    # Defensive PIT filter: the client's own date-scoping is a request, not a
    # guarantee, so a leaking client must not be able to leak through here.
    future_dated = {
        "headline": "USDA to cut corn yield forecast, sources say",
        "source": "bloomberg.com",
        "url": "https://bloomberg.com/corn-yield-leak",
        "date": "2026-06-10",
        "snippet": "Sources familiar with the matter say next week's WASDE will cut yield.",
    }
    undated = {
        "headline": "Corn market commentary",
        "source": "example.com",
        "url": "https://example.com/corn-commentary",
        "date": None,
        "snippet": "No publish date attached to this result.",
    }
    client = FakeNewsClient(CORN_RESULTS + [future_dated, undated])

    result = search_news(tmp_path, commodity="corn", query="corn futures news", as_of=date(2026, 6, 5), client=client)

    assert result == CORN_RESULTS


CORN_ARTICLE_URL = "https://reuters.com/corn-exports"
CORN_ARTICLE = {"url": CORN_ARTICLE_URL, "text": "Full article text about record corn export sales to China."}


def test_fetch_document_fetches_through_client_and_writes_pit_store(tmp_path):
    client = FakeNewsClient(documents={CORN_ARTICLE_URL: CORN_ARTICLE})

    result = fetch_document(tmp_path, url=CORN_ARTICLE_URL, as_of=date(2026, 6, 5), client=client)

    assert result == CORN_ARTICLE
    assert client.extract_calls == [CORN_ARTICLE_URL]

    stored_files = list((tmp_path / "tavily" / "documents").glob("*/*.json"))
    assert len(stored_files) == 1
    assert json.loads(stored_files[0].read_text()) == CORN_ARTICLE


def test_fetch_document_second_call_for_same_url_is_a_noop(tmp_path):
    client = FakeNewsClient(documents={CORN_ARTICLE_URL: CORN_ARTICLE})

    first = fetch_document(tmp_path, url=CORN_ARTICLE_URL, as_of=date(2026, 6, 5), client=client)
    second = fetch_document(tmp_path, url=CORN_ARTICLE_URL, as_of=date(2026, 6, 6), client=client)

    assert second == first
    assert client.extract_calls == [CORN_ARTICLE_URL]
