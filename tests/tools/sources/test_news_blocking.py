import pytest
from tavily.errors import ForbiddenError, UsageLimitExceededError

from ags import blockers
from ags.tools.sources.news import TavilyNewsClient


def _client(raise_exc):
    client = TavilyNewsClient.__new__(TavilyNewsClient)

    class Inner:
        def search(self, *args, **kwargs):
            raise raise_exc

        extract = search

    client._client = Inner()
    return client


def test_an_exhausted_tavily_plan_is_recorded_as_blocking_and_still_raises():
    from datetime import date

    with pytest.raises(ForbiddenError):
        _client(ForbiddenError("This request exceeds your plan's set usage limit")).search("q", published_before=date(2026, 1, 1))

    assert blockers.seen() == {"tavily": "ForbiddenError: This request exceeds your plan's set usage limit"}


def test_a_rate_limit_is_not_blocking():
    with pytest.raises(UsageLimitExceededError):
        _client(UsageLimitExceededError("slow down")).extract("https://x")

    assert blockers.seen() == {}
