from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlparse

import requests

from google import genai
from google.genai import errors, types

from ags import blockers

SOURCE = "google_grounding"

_MODEL = "gemini-3.8-flash"  # 2.5-flash is closed to new keys; model names go stale, check this first on a 404
_WINDOW_DAYS = 7

# Errors no retry fixes: a rejected key (401) or one without access (403), and a 429 that says the quota or
# plan is exhausted. The API words a per-minute rate limit differently, which is not this. Only the quota
# wording was seen live, so it is matched on the message.
_BLOCKING_CODES = (401, 403)
_QUOTA_MESSAGE = "billing details"

# Grounding chunks point at a Google redirect, not the publisher; the domain survives only in the chunk title.
_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_REDIRECT_TIMEOUT_S = 10


def _follow_redirect(url: str) -> str:
    # The redirect's Location header is the publisher URL; the page itself is never fetched.
    response = requests.get(url, allow_redirects=False, stream=True, timeout=_REDIRECT_TIMEOUT_S)
    response.close()
    return response.headers.get("Location") or url

_PROMPT = (
    "Search the web for recent news headlines about: {query}\n"
    "Answer with one line per article, in exactly this format and nothing else:\n"
    "YYYY-MM-DD | headline copied from the article | one-sentence factual snippet"
)


class GroundedNewsClient:
    """Gemini with Google Search grounding, behind the same `search` seam as TavilyNewsClient.

    Each answer line is `DATE | HEADLINE | SNIPPET`; its URL comes from the grounding chunk the
    line is cited to. A line with no date or no cited source is dropped.
    """

    def __init__(
        self, *, api_key: str | None = None, client=None, model: str = _MODEL, resolve_url=_follow_redirect,
        blocking: bool = True,
    ):
        self._client = client or genai.Client(api_key=api_key)
        self._model = model
        self._resolve_url = resolve_url
        # False when grounding is an add-on: its auth or quota failure is then not allowed to hold a cycle.
        self._blocking = blocking
        self.last_raw_response: dict | None = None

    def search(self, query: str, *, published_before: date) -> list[dict]:
        # Exclusive end: the day itself is left out, the safe side for point-in-time.
        end = datetime.combine(published_before, time.min, tzinfo=timezone.utc)
        window = types.Interval(start_time=end - timedelta(days=_WINDOW_DAYS), end_time=end)
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=_PROMPT.format(query=query),
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch(time_range_filter=window))]
                ),
            )
        except errors.ClientError as exc:
            if self._blocking and (exc.code in _BLOCKING_CODES or (exc.code == 429 and _QUOTA_MESSAGE in str(exc.message))):
                blockers.record(SOURCE, f"{type(exc).__name__}: {exc}")
            raise
        candidate = response.candidates[0]
        metadata = candidate.grounding_metadata
        # Google cannot reproduce a past search, so what it answered is kept untouched (see search_news).
        self.last_raw_response = {
            "query": query,
            "text": "".join(part.text or "" for part in candidate.content.parts),
            "web_search_queries": list(metadata.web_search_queries or []),
            "usage": self._usage(response, metadata),
            "grounding_chunks": [
                {"uri": c.web.uri, "title": c.web.title} for c in metadata.grounding_chunks or []
            ],
            "grounding_supports": [
                {"text": s.segment.text, "chunk_indices": list(s.grounding_chunk_indices or [])}
                for s in metadata.grounding_supports or []
            ],
        }
        chunks = metadata.grounding_chunks or []
        results = []
        for support in metadata.grounding_supports or []:
            # A headline can hold a `|` itself, so the date is cut at the first pipe and the snippet at the last.
            day, _, rest = support.segment.text.partition("|")
            headline, _, snippet = rest.rpartition("|")
            day, headline, snippet = day.strip(), headline.strip(), snippet.strip()
            if not headline or not support.grounding_chunk_indices:
                continue
            try:
                date.fromisoformat(day)
            except ValueError:
                continue
            web = chunks[support.grounding_chunk_indices[0]].web
            results.append(
                {"headline": headline, "source": web.title, "url": self._publisher_url(web.uri), "date": day,
                 "snippet": snippet}
            )
        return results

    def _publisher_url(self, uri: str) -> str:
        if urlparse(uri).netloc != _REDIRECT_HOST:
            return uri
        try:
            return self._resolve_url(uri)
        except requests.RequestException:
            return uri

    @staticmethod
    def _usage(response, metadata) -> dict:
        # The raw inputs to a price, not a price: the per-search fee is on top of the tokens (see ROADMAP § 5).
        usage = response.usage_metadata
        return {
            "prompt_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "thought_tokens": getattr(usage, "thoughts_token_count", None),
            "tool_use_tokens": getattr(usage, "tool_use_prompt_token_count", None),
            "total_tokens": getattr(usage, "total_token_count", None),
            "search_queries": len(metadata.web_search_queries or []),
        }
