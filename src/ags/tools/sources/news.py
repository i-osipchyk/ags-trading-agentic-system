import hashlib
from datetime import date
from typing import Protocol
from urllib.parse import urlparse

from dateutil import parser as dateutil_parser
from tavily import TavilyClient

from ags.config import Config
from ags.tools.pit_store import read_pit, write_pit

SOURCE = "tavily"
REPORT_SEARCH = "search_news"
REPORT_DOCUMENTS = "documents"

_MAX_SEARCH_RESULTS = 10


class NewsClient(Protocol):
    def search(self, query: str, *, published_before: date) -> list[dict]: ...
    def extract(self, url: str) -> dict: ...


class NewsExtractError(Exception):
    pass


def _query_symbol(commodity: str, query: str) -> str:
    query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
    return f"{commodity}_{query_hash}"


def _url_symbol(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _normalize_date(raw: str | None) -> str | None:
    # Tavily proxies whatever date format the underlying publisher used
    # (often RFC 822, not ISO) — parsed leniently and normalized to an ISO
    # date so search_news's PIT filter can compare it; an unparseable date
    # is treated as absent (dropped) rather than risking a bad comparison.
    if not raw:
        return None
    try:
        return dateutil_parser.parse(raw).date().isoformat()
    except (ValueError, OverflowError):
        return None


class TavilyNewsClient:
    def __init__(self, config: Config):
        self._client = TavilyClient(api_key=config.tavily_api_key)

    def search(self, query: str, *, published_before: date) -> list[dict]:
        response = self._client.search(
            query,
            topic="news",
            end_date=published_before.isoformat(),
            max_results=_MAX_SEARCH_RESULTS,
        )
        return [
            {
                "headline": item.get("title"),
                "source": urlparse(item.get("url") or "").netloc,
                "url": item.get("url"),
                "date": _normalize_date(item.get("published_date")),
                "snippet": item.get("content"),
            }
            for item in response.get("results", [])
        ]

    def extract(self, url: str) -> dict:
        response = self._client.extract(url, extract_depth="advanced", format="text")
        results = response.get("results", [])
        if not results:
            raise NewsExtractError(f"Tavily could not extract content for {url}")
        return {"url": results[0].get("url", url), "text": results[0].get("raw_content")}


def search_news(
    data_dir,
    *,
    commodity: str,
    query: str,
    as_of: date,
    client: NewsClient | None = None,
) -> list[dict]:
    symbol = _query_symbol(commodity, query)

    cached = read_pit(data_dir, source=SOURCE, report=REPORT_SEARCH, symbol=symbol, as_of=as_of)
    if cached is not None and cached["as_of"] == as_of.isoformat():
        return cached["results"]

    if client is None:
        client = TavilyNewsClient(Config.from_env())

    raw_results = client.search(query, published_before=as_of)
    # Defensive PIT filter: the client's own date-scoping is a request, not a
    # guarantee, and a result with no publish date can't be proven safe —
    # dropped rather than assumed safe, per CLAUDE.md's PIT hard constraint.
    results = [
        result
        for result in raw_results
        if result.get("date") is not None and date.fromisoformat(result["date"]) <= as_of
    ]

    write_pit(
        data_dir,
        source=SOURCE,
        report=REPORT_SEARCH,
        symbol=symbol,
        release_date=as_of,
        content={"as_of": as_of.isoformat(), "query": query, "results": results},
    )

    return results


def fetch_document(
    data_dir,
    *,
    url: str,
    as_of: date,
    client: NewsClient | None = None,
) -> dict:
    symbol = _url_symbol(url)

    # A document's content doesn't change by as_of the way a dated report
    # does, so unlike search_news, a cache hit from an earlier as_of is
    # still the right answer — read_pit's "latest qualifying release"
    # match is exactly what's wanted here, not an exact-date check.
    cached = read_pit(data_dir, source=SOURCE, report=REPORT_DOCUMENTS, symbol=symbol, as_of=as_of)
    if cached is not None:
        return cached

    if client is None:
        client = TavilyNewsClient(Config.from_env())

    document = client.extract(url)

    write_pit(data_dir, source=SOURCE, report=REPORT_DOCUMENTS, symbol=symbol, release_date=as_of, content=document)

    return document
