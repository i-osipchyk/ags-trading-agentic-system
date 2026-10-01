from datetime import date, timedelta

import pytest

from ags.config import Config
from ags.tools.sources import news
from ags.tools.sources.news import TavilyNewsClient, fetch_document, search_news


@pytest.mark.integration
def test_search_news_pulls_corn_headlines_from_real_tavily_and_second_call_is_a_noop(tmp_path):
    config = Config.from_env()
    client = TavilyNewsClient(config)

    calls = []
    real_search = client.search

    def counted_search(query, *, published_before):
        calls.append((query, published_before))
        return real_search(query, published_before=published_before)

    client.search = counted_search

    as_of = date.today() - timedelta(days=1)

    first = search_news(tmp_path, commodity="corn", query="corn futures news", as_of=as_of, client=client)

    assert isinstance(first, list)
    stored_files = list((tmp_path / news.SOURCE / news.REPORT_SEARCH).glob("*/*.json"))
    assert stored_files, "fetch-through-cache should have written at least one PIT file"
    assert len(calls) == 1

    second = search_news(tmp_path, commodity="corn", query="corn futures news", as_of=as_of, client=client)

    assert second == first
    assert len(calls) == 1, "second call for the same commodity/query/as_of must be a cache no-op"

    if first:
        url = first[0]["url"]
        document = fetch_document(tmp_path, url=url, as_of=as_of, client=client)
        assert document["text"]
