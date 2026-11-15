from datetime import date, datetime

import pytest
import requests
from google.genai import errors, types

from ags import blockers
from ags.tools.pit_store import ET
from ags.tools.sources.news import search_news
from ags.tools.sources.grounded_news import SOURCE, GroundedNewsClient


class FakeGenai:
    """Stands in for google.genai.Client: records the call, returns a canned response."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._error:
            raise self._error
        return self._response


def grounded_response(lines, chunks, supports, usage=None):
    """lines: the answer text; chunks: [(uri, title)]; supports: [(segment_text, [chunk_index])]."""
    return types.GenerateContentResponse(
        usage_metadata=types.GenerateContentResponseUsageMetadata(**usage) if usage else None,
        candidates=[
            types.Candidate(
                content=types.Content(parts=[types.Part(text="\n".join(lines))]),
                grounding_metadata=types.GroundingMetadata(
                    web_search_queries=["corn futures news"],
                    grounding_chunks=[
                        types.GroundingChunk(web=types.GroundingChunkWeb(uri=uri, title=title))
                        for uri, title in chunks
                    ],
                    grounding_supports=[
                        types.GroundingSupport(
                            segment=types.Segment(text=text), grounding_chunk_indices=indices
                        )
                        for text, indices in supports
                    ],
                ),
            )
        ]
    )


CORN_LINE = "2026-06-01 | Corn exports jump on strong Chinese demand | Weekly sales showed China buying more."
WHEAT_LINE = "2026-06-02 | Wheat slips on rain in the Plains | Showers eased dryness concerns."


def test_search_maps_grounded_claims_to_headline_source_url_date_snippet():
    response = grounded_response(
        [CORN_LINE, WHEAT_LINE],
        chunks=[("https://reuters.com/corn-exports", "reuters.com"), ("https://agweb.com/wheat", "agweb.com")],
        supports=[(CORN_LINE, [0]), (WHEAT_LINE, [1])],
    )
    client = GroundedNewsClient(client=FakeGenai(response))

    results = client.search("corn futures news", published_before=date(2026, 6, 5))

    assert results == [
        {
            "headline": "Corn exports jump on strong Chinese demand",
            "source": "reuters.com",
            "url": "https://reuters.com/corn-exports",
            "date": "2026-06-01",
            "snippet": "Weekly sales showed China buying more.",
        },
        {
            "headline": "Wheat slips on rain in the Plains",
            "source": "agweb.com",
            "url": "https://agweb.com/wheat",
            "date": "2026-06-02",
            "snippet": "Showers eased dryness concerns.",
        },
    ]


def test_search_drops_claims_without_a_parseable_date_or_a_cited_source():
    undated = "recently | Corn rallies | No date given."
    uncited = "2026-06-03 | Soy crush hits record | Nobody cites this."
    garbled = "2026-13-45 | Sugar surges | Impossible date."
    response = grounded_response(
        [CORN_LINE, undated, uncited, garbled],
        chunks=[("https://reuters.com/corn-exports", "reuters.com")],
        supports=[(CORN_LINE, [0]), (undated, [0]), (uncited, []), (garbled, [0])],
    )
    client = GroundedNewsClient(client=FakeGenai(response))

    results = client.search("corn futures news", published_before=date(2026, 6, 5))

    assert [r["headline"] for r in results] == ["Corn exports jump on strong Chinese demand"]


def test_search_asks_google_to_stop_at_published_before_and_keeps_the_raw_response():
    response = grounded_response(
        [CORN_LINE],
        chunks=[("https://reuters.com/corn-exports", "reuters.com")],
        supports=[(CORN_LINE, [0])],
    )
    fake = FakeGenai(response)
    client = GroundedNewsClient(client=fake)

    client.search("corn futures news", published_before=date(2026, 6, 5))

    sent = fake.calls[0]
    assert "corn futures news" in sent["contents"]
    window = sent["config"].tools[0].google_search.time_range_filter
    assert window.end_time.date() == date(2026, 6, 5)
    assert window.start_time < window.end_time

    raw = client.last_raw_response
    assert raw["query"] == "corn futures news"
    assert raw["web_search_queries"] == ["corn futures news"]
    assert raw["text"] == CORN_LINE
    assert raw["grounding_chunks"] == [{"uri": "https://reuters.com/corn-exports", "title": "reuters.com"}]


def _api_error(code, status):
    return errors.ClientError(code, {"error": {"code": code, "status": status, "message": status}})


@pytest.mark.parametrize("code,status", [(401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")])
def test_search_records_a_blocking_error_and_reraises(code, status):
    blockers.clear()
    client = GroundedNewsClient(client=FakeGenai(error=_api_error(code, status)))

    with pytest.raises(errors.ClientError):
        client.search("corn futures news", published_before=date(2026, 6, 5))

    assert SOURCE in blockers.seen()
    blockers.clear()


def test_search_does_not_record_a_rate_limit_as_blocking():
    blockers.clear()
    client = GroundedNewsClient(client=FakeGenai(error=_api_error(429, "RESOURCE_EXHAUSTED")))

    with pytest.raises(errors.ClientError):
        client.search("corn futures news", published_before=date(2026, 6, 5))

    assert blockers.seen() == {}


def test_search_news_drops_a_future_dated_claim_the_grounded_client_let_through(tmp_path):
    # Google's time window is a request, not a guarantee: search_news's own PIT filter still applies.
    leak = "2026-06-09 | USDA to cut corn yield forecast | Dated after as_of."
    response = grounded_response(
        [CORN_LINE, leak],
        chunks=[("https://reuters.com/corn-exports", "reuters.com"), ("https://bloomberg.com/leak", "bloomberg.com")],
        supports=[(CORN_LINE, [0]), (leak, [1])],
    )

    results = search_news(
        tmp_path,
        commodity="corn",
        query="corn futures news",
        as_of=datetime(2026, 6, 5, 16, 0, tzinfo=ET),
        client=GroundedNewsClient(client=FakeGenai(response)),
    )

    assert [r["headline"] for r in results] == ["Corn exports jump on strong Chinese demand"]


def test_search_keeps_a_pipe_inside_the_headline():
    line = "2026-11-10 | Report Reaction | USDA cuts corn yield, lifts soybean peg | Corn traded up to 13 cents higher."
    response = grounded_response(
        [line], chunks=[("https://profarmer.com/report", "profarmer.com")], supports=[(line, [0])]
    )
    client = GroundedNewsClient(client=FakeGenai(response))

    results = client.search("corn futures news", published_before=date(2026, 11, 13))

    assert results[0]["headline"] == "Report Reaction | USDA cuts corn yield, lifts soybean peg"
    assert results[0]["snippet"] == "Corn traded up to 13 cents higher."


REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH"


def _redirected_response():
    return grounded_response(
        [CORN_LINE, WHEAT_LINE],
        chunks=[(REDIRECT, "barchart.com"), ("https://agweb.com/wheat", "agweb.com")],
        supports=[(CORN_LINE, [0]), (WHEAT_LINE, [1])],
    )


def test_search_resolves_grounding_redirects_to_the_publisher_url():
    seen = []

    def resolve(url):
        seen.append(url)
        return "https://www.barchart.com/story/corn-firm"

    client = GroundedNewsClient(client=FakeGenai(_redirected_response()), resolve_url=resolve)

    results = client.search("corn futures news", published_before=date(2026, 6, 5))

    assert [r["url"] for r in results] == ["https://www.barchart.com/story/corn-firm", "https://agweb.com/wheat"]
    assert seen == [REDIRECT]  # a publisher URL is never sent through the resolver


def test_search_keeps_the_redirect_url_when_it_cannot_be_resolved():
    def resolve(url):
        raise requests.ConnectionError("down")

    client = GroundedNewsClient(client=FakeGenai(_redirected_response()), resolve_url=resolve)

    results = client.search("corn futures news", published_before=date(2026, 6, 5))

    assert results[0]["url"] == REDIRECT


def test_search_keeps_token_usage_and_the_search_count_for_pricing_later():
    response = grounded_response(
        [CORN_LINE],
        chunks=[("https://reuters.com/corn-exports", "reuters.com")],
        supports=[(CORN_LINE, [0])],
        usage={
            "prompt_token_count": 40,
            "candidates_token_count": 60,
            "thoughts_token_count": 300,
            "tool_use_prompt_token_count": 2500,
            "total_token_count": 2900,
        },
    )
    client = GroundedNewsClient(client=FakeGenai(response))

    client.search("corn futures news", published_before=date(2026, 6, 5))

    assert client.last_raw_response["usage"] == {
        "prompt_tokens": 40,
        "output_tokens": 60,
        "thought_tokens": 300,
        "tool_use_tokens": 2500,
        "total_tokens": 2900,
        "search_queries": 1,
    }


def test_search_records_an_exhausted_quota_as_blocking():
    # The message the API returned on a key with no grounding quota (free tier): no retry fixes it.
    quota = errors.ClientError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                   "message": "You exceeded your current quota, please check your plan and billing details."}},
    )
    blockers.clear()
    client = GroundedNewsClient(client=FakeGenai(error=quota))

    with pytest.raises(errors.ClientError):
        client.search("corn futures news", published_before=date(2026, 6, 5))

    assert SOURCE in blockers.seen()
    blockers.clear()


def test_an_add_on_client_never_records_a_blocker_so_it_cannot_hold_a_cycle():
    blockers.clear()
    client = GroundedNewsClient(client=FakeGenai(error=_api_error(401, "UNAUTHENTICATED")), blocking=False)

    with pytest.raises(errors.ClientError):
        client.search("corn futures news", published_before=date(2026, 6, 5))

    assert blockers.seen() == {}
