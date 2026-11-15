"""Frozen inputs: re-run the analysts on exactly what an official cycle saw, with fetching disabled.

Most of what an analyst reads is reproducible from the point-in-time store at the cycle's `as_of` (prices, the
drought map, WASDE, the CPC outlook, archived forecasts). Four things are not, and a rerun that fetched them again
would see different data than the cycle did, which is what made two runs of the same cycle disagree:

- the news search: the model picks its own queries, and Tavily can't reproduce a past search;
- the intraday reference quote the cycle captured when it started;
- the CFTC positioning, which is refetched on every call;
- (the Open-Meteo forecast, which is live-only; it is archived per cycle and read from the archive instead).

`frozen_from_events` takes the first three from the official run's log, and `NoFetch` stands in for every source
client so that anything not stored fails loudly instead of quietly fetching what is public today.
"""

import json
import re
from dataclasses import dataclass

from ags.storage import Storage

_REFERENCE_FIELDS = ("kind", "bid", "ask", "price", "quote_timestamp", "captured_at")


class FrozenInputError(Exception):
    """A frozen run asked a source for something that was not stored at the time."""


SEARCH_RESULT_LIMIT = 10      # what one live Tavily search returns (tools/sources/news.py)


def _words(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def frozen_search(results: list[dict], query: str, served: set[str], *, limit: int = SEARCH_RESULT_LIMIT) -> list[dict]:
    """What one search returns in a frozen run: up to `limit` articles it has not shown yet, best match to `query` first.

    A live search returns about ten articles; handing back all of the cycle's articles on every query would send the
    model several times what it saw and cost several times as much. A rerun's queries are not the cycle's, so the
    articles are ranked by words shared with the query (headline counted double), and each is served once: asking more
    searches walks through the stored articles, and once they are all shown a search returns nothing new.
    `served` (URLs) is updated in place.
    """
    wanted = _words(query)
    fresh = [r for r in results if r.get("url") not in served]
    ranked = sorted(
        enumerate(fresh),
        key=lambda pair: (-(2 * len(wanted & _words(pair[1].get("headline"))) + len(wanted & _words(pair[1].get("snippet")))), pair[0]),
    )
    chosen = [article for _, article in ranked[:limit]]
    served.update(article.get("url") for article in chosen)
    return chosen


class NoFetch:
    """A source client that refuses every call, so a frozen run can only read what is already stored."""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)

        def refuse(*args, **kwargs):
            raise FrozenInputError(f"{name}: fetching is disabled in a frozen run; only stored data can be read")

        return refuse


@dataclass(frozen=True)
class FrozenInputs:
    source_run_id: str
    news_results: list[dict]
    reference_quote: dict | None
    positioning: dict | None
    # Whether the cycle searched the news itself (not carried it forward). The schedule changed during the first
    # weeks (news was searched in the evening, then at 06:00), so the rerun does what that cycle did, not what the
    # current rule would. None: the log has no news output, and the current rule decides.
    news_fetched: bool | None = None


def _last_output(events: list[dict], agent: str) -> dict:
    outputs = [e["payload"] for e in events if e["agent"] == agent and e["event_type"] == "output"]
    return outputs[-1] if outputs else {}


def frozen_from_events(run_id: str, events: list[dict]) -> FrozenInputs:
    """The non-reproducible inputs of an official run, read back from its log.

    News: every article any `search_news` call returned (deduplicated by URL, in the order first seen), whichever
    query asked for it, since a rerun's queries will differ. Quote: the intraday reference quote, when the cycle had
    one. Positioning: what the technical analyst was given.
    """
    names = {
        e["payload"]["id"]: e["payload"]["name"]
        for e in events if e["agent"] == "news" and e["event_type"] == "tool_call"
    }
    results, seen_urls = [], set()
    for event in events:
        if event["agent"] != "news" or event["event_type"] != "tool_result":
            continue
        if names.get(event["payload"]["tool_call_id"]) != "search_news":
            continue
        for item in json.loads(event["payload"]["content"]).get("results", []):
            if item.get("url") not in seen_urls:
                seen_urls.add(item.get("url"))
                results.append(item)

    price = _last_output(events, "coordinator").get("price_at_call") or {}
    reference = {k: price[k] for k in _REFERENCE_FIELDS if k in price} if price.get("kind") == "intraday_quote" else None
    news = _last_output(events, "news")
    return FrozenInputs(
        run_id, results, reference, _last_output(events, "technical").get("positioning"),
        news_fetched=None if not news else not news.get("carried_forward"),
    )


class LayeredStorage:
    """Reads see `upper` first and then `lower`; every write goes to `upper`, so `lower` is never touched.

    `hide` keys are invisible in `lower`: a rerun of an official run writes under the official run's own key and
    must not find, and append to, the official log.
    """

    def __init__(self, lower: Storage, upper: Storage, *, hide: set[str] = frozenset()) -> None:
        self._lower, self._upper, self._hide = lower, upper, set(hide)

    def get(self, key: str) -> bytes | None:
        found = self._upper.get(key)
        if found is not None or key in self._hide:
            return found
        return self._lower.get(key)

    def put(self, key: str, data: bytes) -> None:
        self._upper.put(key, data)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        return self._upper.put_if_absent(key, data) if self.get(key) is None else False

    def list(self, prefix: str) -> list[str]:
        return sorted({*self._upper.list(prefix), *(k for k in self._lower.list(prefix) if k not in self._hide)})
