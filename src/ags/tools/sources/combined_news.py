import logging
from datetime import date
from urllib.parse import urlparse

from ags.tools.sources import grounded_news

logger = logging.getLogger(__name__)

# Grounding returns video and quote or index pages now and then (about 7% of a sample); neither is an article.
_NON_ARTICLE_HOSTS = ("youtube.com", "youtu.be")
_NON_ARTICLE_PATHS = ("/quote/", "/quotes/", "/symbols/")


def is_article_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    if host in _NON_ARTICLE_HOSTS or any(host.endswith("." + h) for h in _NON_ARTICLE_HOSTS):
        return False
    return not any(part in parsed.path.lower() for part in _NON_ARTICLE_PATHS)


class CombinedNewsClient:
    """Tavily plus Google grounding behind the one `NewsClient` seam, for a forward-only trial.

    The two providers share few URLs (17 of 439 on the same queries, comparison note), so grounding is added beside
    Tavily, not instead of it. Grounding can only be used live: it ignores its own time filter, so a replayed
    `as_of` would see the future. Tavily stays the base: if grounding raises, the search still returns Tavily's.
    """

    def __init__(self, tavily, grounded) -> None:
        self._tavily = tavily
        self._grounded = grounded
        self.last_raw_response: dict | None = None

    def search(self, query: str, *, published_before: date) -> list[dict]:
        results = [{**r, "provider": "tavily"} for r in self._tavily.search(query, published_before=published_before)]
        raw = {"tavily": self._tavily.last_raw_response}
        self._grounded.last_raw_response = None  # not the previous query's answer if this call raises first
        try:
            grounded = self._grounded.search(query, published_before=published_before)
        except Exception as exc:  # a grounding failure must never cost the cycle its Tavily results
            logger.warning("grounded news search failed for %r: %s: %s", query, type(exc).__name__, exc)
            grounded = []
        # Kept untouched even when the call raised after the response arrived: Google cannot replay a search.
        raw[grounded_news.SOURCE] = self._grounded.last_raw_response
        self.last_raw_response = raw
        seen = {r["url"] for r in results}
        for item in grounded:
            if item["url"] in seen or not is_article_url(item["url"]):
                continue
            seen.add(item["url"])
            results.append({**item, "provider": grounded_news.SOURCE})
        return results

    def extract(self, url: str) -> dict:
        return self._tavily.extract(url)
