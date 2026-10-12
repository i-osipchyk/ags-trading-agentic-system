from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from llmkit import LLM

from ags.llm.client import LLMClient, bind, trace
from ags.llm.loop import run_loop


class ListSink:
    def __init__(self):
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def flush(self):
        pass


def _usage():
    return SimpleNamespace(
        prompt_tokens=1200, completion_tokens=300, total_tokens=1500,
        prompt_cache_hit_tokens=1000, prompt_cache_miss_tokens=200,
    )


class FakeOpenAI:
    """Replies with each queued message in turn; records every request."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        message = self.messages.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=_usage(), model=kwargs["model"])


def _message(content="hello", tool_calls=None):
    return SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls)


def _client(fake):
    sink = ListSink()
    llm = LLM(project="ags", env="test", sinks=[sink], clients={"deepseek": fake})
    return LLMClient(llm), sink


def test_a_bound_call_is_recorded_under_its_component_with_metadata_and_cost():
    fake = FakeOpenAI(_message())
    client, sink = _client(fake)

    result = client.bind("news", commodity="corn", run_id="corn_x").complete(
        [{"role": "user", "content": "hi"}], None, model="deepseek-flash"
    )

    assert result["content"] == "hello" and result["tool_calls"] is None
    [record] = sink.records
    assert (record.project, record.env, record.component, record.provider) == ("ags", "test", "news", "deepseek")
    assert record.metadata == {"commodity": "corn", "run_id": "corn_x"}
    assert record.cache_read_tokens == 1000 and record.input_tokens == 200 and record.output_tokens == 300
    # The run log keeps DeepSeek's own usage fields plus llmkit's price for the call.
    assert result["usage"] == {
        "prompt_tokens": 1200, "completion_tokens": 300, "prompt_cache_hit_tokens": 1000,
        "prompt_cache_miss_tokens": 200, "cost_usd": record.cost_usd,
    }
    assert record.cost_usd and record.cost_usd > 0
    assert "tools" not in fake.requests[0]


def test_an_unpriced_model_is_logged_with_a_null_cost_not_a_guess():
    client, sink = _client(FakeOpenAI(_message()))

    result = client.bind("news").complete([{"role": "user", "content": "hi"}], None, model="deepseek-unknown")

    assert result["usage"]["cost_usd"] is None
    assert sink.records[0].cost_usd is None


def test_tool_calls_are_passed_through_in_the_loop_shape():
    tool_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="add", arguments='{"a": 1}'))
    fake = FakeOpenAI(_message(content=None, tool_calls=[tool_call]))
    client, _ = _client(fake)
    tools = [{"type": "function", "function": {"name": "add", "parameters": {}}}]

    result = client.bind("technical").complete([{"role": "user", "content": "x"}], tools, model="deepseek-flash")

    assert fake.requests[0]["tools"] == tools
    assert result["tool_calls"] == [{"id": "c1", "type": "function", "function": {"name": "add", "arguments": '{"a": 1}'}}]


def test_the_root_client_cannot_call_a_model_without_a_component():
    client, _ = _client(FakeOpenAI(_message()))

    with pytest.raises(AttributeError):
        run_loop(client, model="deepseek-flash", system_prompt="s", user_prompt="u", tools=[])


def test_rebinding_keeps_metadata_and_calls_in_a_trace_share_its_id():
    client, sink = _client(FakeOpenAI(_message(), _message()))
    pipeline = client.bind("coordinator", commodity="corn")

    with trace(client, "corn_2026-06-10T08-00-00Z"):
        pipeline.complete([{"role": "user", "content": "a"}], None, model="deepseek-flash")
        bind(pipeline, "news", run_id="r").complete([{"role": "user", "content": "b"}], None, model="deepseek-flash")

    first, second = sink.records
    assert first.trace_id == second.trace_id and first.trace_name == "corn_2026-06-10T08-00-00Z"
    assert second.component == "news" and second.metadata == {"commodity": "corn", "run_id": "r"}
    assert datetime.fromisoformat(first.started_at).tzinfo == timezone.utc


def test_bind_and_trace_leave_plain_test_fakes_alone():
    fake = object()

    assert bind(fake, "news") is fake
    with trace(fake, "x"):
        pass
