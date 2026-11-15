import json
from datetime import datetime

import pytest

from ags.frozen import FrozenInputError, LayeredStorage, NoFetch, frozen_from_events
from ags.storage import LocalStorage


def _event(agent, event_type, payload):
    return {"agent": agent, "event_type": event_type, "payload": payload}


def _search(call_id, query, results):
    return [
        _event("news", "tool_call", {"id": call_id, "name": "search_news", "arguments": {"query": query}}),
        _event("news", "tool_result", {"tool_call_id": call_id, "content": json.dumps({"results": results})}),
    ]


def _article(n):
    return {"headline": f"H{n}", "source": "s", "url": f"https://x/{n}", "date": "2026-11-12", "snippet": ""}


QUOTE = {"kind": "intraday_quote", "bid": 4.5, "ask": 4.6, "price": 4.55, "quote_timestamp": "t1", "captured_at": "t2"}


def test_the_news_the_cycle_saw_is_the_union_of_every_search_whatever_the_query():
    events = [
        *_search("c1", "corn futures news", [_article(1), _article(2)]),
        *_search("c2", "corn exports", [_article(2), _article(3)]),
        _event("news", "tool_call", {"id": "c3", "name": "fetch_document", "arguments": {"url": "u"}}),
        _event("news", "tool_result", {"tool_call_id": "c3", "content": json.dumps({"url": "u", "text": "t"})}),
        _event("news", "tool_call", {"id": "c4", "name": "search_news", "arguments": {"query": "q"}}),
        _event("news", "tool_result", {"tool_call_id": "c4", "content": json.dumps({"error": "tool_call_cap_hit"})}),
    ]

    frozen = frozen_from_events("corn_x", events)

    assert [a["headline"] for a in frozen.news_results] == ["H1", "H2", "H3"]     # deduplicated, first seen first


def test_the_reference_quote_and_positioning_come_from_what_the_cycle_logged():
    events = [
        _event("technical", "output", {"positioning": {"net_pct_oi": 0.46}}),
        _event("coordinator", "output", {"price_at_call": {**QUOTE, "date": "2026-11-13", "last_close": {"price": 1}}}),
    ]

    frozen = frozen_from_events("corn_x", events)

    assert frozen.reference_quote == QUOTE                    # without the fields the coordinator stamped on later
    assert frozen.positioning == {"net_pct_oi": 0.46}
    assert frozen.source_run_id == "corn_x"


def test_a_cycle_without_a_live_quote_has_no_reference_quote():
    events = [_event("coordinator", "output", {"price_at_call": {"kind": "daily_close", "price": 4.5, "date": "d"}})]

    assert frozen_from_events("corn_x", events).reference_quote is None
    assert frozen_from_events("corn_x", []).news_results == []


def test_a_no_fetch_client_refuses_any_call():
    client = NoFetch()

    with pytest.raises(FrozenInputError, match="get_daily_bars"):
        client.get_daily_bars("Corn", 1, 2)
    with pytest.raises(FrozenInputError):
        client.search("q", published_before=None)


def test_layered_storage_reads_through_but_writes_only_to_the_upper_layer(tmp_path):
    lower, upper = LocalStorage(tmp_path / "lower"), LocalStorage(tmp_path / "upper")
    lower.put("logs/a.jsonl", b"official")
    store = LayeredStorage(lower, upper)

    assert store.get("logs/a.jsonl") == b"official"
    store.put("logs/b.jsonl", b"new")
    assert lower.get("logs/b.jsonl") is None and upper.get("logs/b.jsonl") == b"new"
    assert store.list("logs/") == ["logs/a.jsonl", "logs/b.jsonl"]
    assert store.put_if_absent("logs/a.jsonl", b"x") is False


def test_a_hidden_key_hides_the_official_log_so_a_rerun_cannot_append_to_it(tmp_path):
    lower, upper = LocalStorage(tmp_path / "lower"), LocalStorage(tmp_path / "upper")
    lower.put("corn_1.jsonl", b"official")
    lower.put("corn_0.jsonl", b"earlier")
    store = LayeredStorage(lower, upper, hide={"corn_1.jsonl"})

    assert store.get("corn_1.jsonl") is None
    assert store.list("corn_") == ["corn_0.jsonl"]
    store.put("corn_1.jsonl", b"rerun")
    assert store.get("corn_1.jsonl") == b"rerun" and lower.get("corn_1.jsonl") == b"official"


def test_a_rerun_does_what_the_cycle_did_about_the_news():
    searched = [_event("news", "output", {"headlines": []})]
    carried = [_event("news", "output", {"headlines": [], "carried_forward": True})]

    assert frozen_from_events("r", searched).news_fetched is True
    assert frozen_from_events("r", carried).news_fetched is False
    assert frozen_from_events("r", []).news_fetched is None          # no news output: the current rule decides


def _stored(n, headline, snippet=""):
    return {"headline": headline, "url": f"https://x/{n}", "snippet": snippet}


def test_a_frozen_search_returns_the_best_matches_to_the_query_not_all_the_articles():
    from ags.frozen import frozen_search

    articles = [_stored(i, f"unrelated story {i}") for i in range(8)] + [
        _stored(100, "Corn exports jump"), _stored(101, "Wheat falls", "corn too"), _stored(102, "Corn export sales strong"),
    ]

    first = frozen_search(articles, "corn export sales", set(), limit=2)

    assert [a["url"] for a in first] == ["https://x/102", "https://x/100"]     # most shared words first, headline counts double


def test_every_article_is_served_once_so_later_searches_walk_through_the_rest_and_then_return_nothing():
    from ags.frozen import frozen_search

    articles = [_stored(i, f"story {i}") for i in range(5)]
    served = set()

    batches = [frozen_search(articles, "story", served, limit=2) for _ in range(4)]

    assert [len(b) for b in batches] == [2, 2, 1, 0]
    assert len({a["url"] for b in batches for a in b}) == 5


def test_a_frozen_news_analyst_sends_the_model_ten_articles_a_search_not_all_of_them(tmp_path):
    from ags.agents.news import run_news_analyst

    articles = [{"headline": f"Corn story {i}", "source": "s", "url": f"https://x/{i}", "date": "2026-06-03", "snippet": ""}
                for i in range(30)]
    search = {"id": "c1", "function": {"name": "search_news", "arguments": json.dumps({"query": "corn"})}}

    class Chat:
        def __init__(self):
            self.replies = [{"role": "assistant", "content": None, "tool_calls": [search]},
                            {"role": "assistant", "content": json.dumps({"headlines": []}), "tool_calls": None}]
            self.calls = []

        def complete(self, messages, tools, *, model):
            self.calls.append(messages)
            return self.replies.pop(0)

    chat = Chat()
    run_news_analyst(tmp_path, chat, commodity="corn", as_of=datetime(2026, 6, 5, 6, 0).astimezone(), model="m",
                     frozen_results=articles)

    tool_message = next(m for m in chat.calls[1] if m["role"] == "tool")
    assert len(json.loads(tool_message["content"])["results"]) == 10
