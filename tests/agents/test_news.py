import json
from datetime import date, datetime

from ags.agents.news import run_news_analyst
from ags.tools.pit_store import ET


class FakeChatClient:
    """Stub model client: returns scripted messages in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return self._responses.pop(0)


class FakeNewsClient:
    def __init__(self, results=None, documents=None):
        self._results = results or []
        self._documents = documents or {}
        self.search_calls = []

    def search(self, query, *, published_before):
        self.search_calls.append((query, published_before))
        return self._results

    def extract(self, url):
        return self._documents[url]


class FailingNewsClient:
    def search(self, query, *, published_before):
        raise ConnectionError("Tavily request failed")

    def extract(self, url):
        raise ConnectionError("Tavily request failed")


CORN_RESULTS = [
    {
        "headline": "Corn exports jump on strong Chinese demand",
        "source": "reuters.com",
        "url": "https://reuters.com/corn-exports",
        "date": "2026-06-01",
        "snippet": "Weekly export sales data showed corn demand from China rising.",
    },
]


def _tool_call_message(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def test_run_news_analyst_returns_schema_conformant_output_for_one_symbol(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)
    news_client = FakeNewsClient(results=CORN_RESULTS)

    chat_client = FakeChatClient(
        responses=[
            _tool_call_message("search_news", {"query": "corn futures news"}),
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "headlines": [
                            {
                                "headline": "Corn exports jump on strong Chinese demand",
                                "source": "reuters.com",
                                "date": "2026-06-01",
                                "direction": "bullish",
                                "reason": "Rising Chinese demand tightens the export balance.",
                            }
                        ]
                    }
                ),
                "tool_calls": None,
            },
        ]
    )

    result = run_news_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        news_client=news_client,
    )

    assert set(result.keys()) == {"headlines", "degraded"}
    assert result["headlines"] == [
        {
            "headline": "Corn exports jump on strong Chinese demand",
            "source": "reuters.com",
            "date": "2026-06-01",
            "direction": "bullish",
            "reason": "Rising Chinese demand tightens the export balance.",
        }
    ]
    assert result["degraded"] is False
    assert news_client.search_calls == [("corn futures news", as_of.date())]


def test_run_news_analyst_surfaces_degraded_true_on_a_source_failure_instead_of_aborting(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)

    chat_client = FakeChatClient(
        responses=[
            _tool_call_message("search_news", {"query": "corn futures news"}),
            {
                "role": "assistant",
                "content": json.dumps({"headlines": []}),
                "tool_calls": None,
            },
        ]
    )

    result = run_news_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        news_client=FailingNewsClient(),
    )

    assert result["degraded"] is True
    assert result["headlines"] == []


def test_run_news_analyst_surfaces_degraded_true_when_the_model_returns_unparseable_content(tmp_path):
    # Observed against the real DeepSeek endpoint: once the tool-call cap
    # forces a tools-disabled final call, the model can still emit its own
    # pseudo-tool-call markup as plain content instead of complying with the
    # "JSON only" instruction. That must degrade the run, not crash it.
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)

    chat_client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="search_news">',
                "tool_calls": None,
            }
        ]
        * 2  # the agent asks once more for valid JSON before degrading
    )

    result = run_news_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        news_client=FakeNewsClient(results=CORN_RESULTS),
    )

    assert result == {"headlines": [], "degraded": True}


def _reply(content: str) -> dict:
    return {"role": "assistant", "content": content, "tool_calls": None}


def _news(tmp_path, *responses):
    return run_news_analyst(
        tmp_path, FakeChatClient(list(responses)), commodity="corn",
        as_of=datetime(2026, 6, 5, 16, 0, tzinfo=ET), model="deepseek-chat", news_client=FakeNewsClient(CORN_RESULTS),
    )


def test_run_news_analyst_flags_a_reply_under_the_wrong_key_instead_of_reporting_no_news(tmp_path):
    result = _news(tmp_path, _reply(json.dumps({"news": [{"headline": "h"}]})))

    assert result == {"headlines": [], "degraded": True}


def test_run_news_analyst_degrades_instead_of_raising_on_a_json_list_reply(tmp_path):
    assert _news(tmp_path, _reply("[]"))["degraded"] is True


def test_run_news_analyst_degrades_when_the_model_calls_a_tool_that_does_not_exist(tmp_path):
    bad_call = _tool_call_message("get_prices", {"start": "2026-06-01"})
    result = _news(tmp_path, bad_call, _reply(json.dumps({"headlines": []})))

    assert result == {"headlines": [], "degraded": True}


def test_run_news_analyst_reports_genuinely_empty_news_as_not_degraded(tmp_path):
    assert _news(tmp_path, _reply(json.dumps({"headlines": []}))) == {"headlines": [], "degraded": False}
