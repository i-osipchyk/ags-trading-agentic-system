import json

import pytest

from ags.llm.loop import ToolSpec, run_json_loop, run_loop


class FakeChatClient:
    """Stub model client: returns scripted messages in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return self._responses.pop(0)


def test_run_loop_returns_direct_answer_with_no_tool_calls():
    client = FakeChatClient(
        responses=[
            {"role": "assistant", "content": "corn is in an uptrend", "tool_calls": None},
        ]
    )

    result = run_loop(
        client,
        model="deepseek-chat",
        system_prompt="You are a technical analyst.",
        user_prompt="What's the trend for corn?",
        tools=[],
    )

    assert result.content == "corn is in an uptrend"
    assert result.tool_call_cap_hit is False
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "deepseek-chat"


def test_run_loop_executes_a_requested_tool_and_feeds_result_back_to_model():
    tool_calls_received = []

    def get_trend(symbol):
        tool_calls_received.append(symbol)
        return {"trend": "up", "age_days": 12}

    trend_tool = ToolSpec(
        name="get_trend",
        description="Get the current trend state for a symbol.",
        parameters={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
        function=get_trend,
    )

    client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "get_trend", "arguments": json.dumps({"symbol": "corn"})},
                    }
                ],
            },
            {"role": "assistant", "content": "corn is up", "tool_calls": None},
        ]
    )

    result = run_loop(
        client,
        model="deepseek-chat",
        system_prompt="You are a technical analyst.",
        user_prompt="What's the trend for corn?",
        tools=[trend_tool],
    )

    assert result.content == "corn is up"
    assert result.tool_call_cap_hit is False
    assert tool_calls_received == ["corn"]
    assert len(client.calls) == 2

    tool_result_message = result.messages[-2]
    assert tool_result_message["role"] == "tool"
    assert tool_result_message["tool_call_id"] == "call_1"
    assert json.loads(tool_result_message["content"]) == {"trend": "up", "age_days": 12}

    assert client.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_trend",
                "description": "Get the current trend state for a symbol.",
                "parameters": trend_tool.parameters,
            },
        }
    ]


def _tool_call_response(call_id, symbol):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "function": {"name": "get_trend", "arguments": json.dumps({"symbol": symbol})}}
        ],
    }


def test_run_loop_forces_tools_disabled_final_call_on_the_6th_tool_call_attempt():
    tool_calls_received = []

    def get_trend(symbol):
        tool_calls_received.append(symbol)
        return {"trend": "up", "age_days": 1}

    trend_tool = ToolSpec(
        name="get_trend",
        description="Get the current trend state for a symbol.",
        parameters={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
        function=get_trend,
    )

    # 5 responses that each request the tool, then a 6th response returned
    # once the loop stops offering tools and forces a final answer.
    client = FakeChatClient(
        responses=[
            _tool_call_response(f"call_{i}", "corn")
            for i in range(1, 6)
        ]
        + [{"role": "assistant", "content": "final answer after cap", "tool_calls": None}]
    )

    result = run_loop(
        client,
        model="deepseek-chat",
        system_prompt="You are a technical analyst.",
        user_prompt="What's the trend for corn?",
        tools=[trend_tool],
        max_tool_calls=5,
    )

    assert result.content == "final answer after cap"
    assert result.tool_call_cap_hit is True
    assert tool_calls_received == ["corn"] * 5
    assert len(client.calls) == 6
    assert all(call["tools"] is not None for call in client.calls[:5])
    assert client.calls[5]["tools"] is None

    # The forced final call must tell the model plainly that no more tool
    # calls are available — observed against the real model: without this,
    # it sometimes ignores "JSON only" and emits its own pseudo-tool-call
    # markup as content instead of answering, when it still wants to search
    # more but tools have been withdrawn.
    forced_call_messages = client.calls[5]["messages"]
    assert forced_call_messages[-1]["role"] == "user"
    assert "no more tool calls" in forced_call_messages[-1]["content"].lower()


def test_run_loop_caps_tool_execution_within_a_single_batch_that_exceeds_the_cap():
    # A model can request several tool calls in one response (parallel tool
    # calling). The cap must stop execution mid-batch, not just between
    # turns — otherwise a model that batches 7 calls at once blows straight
    # past a cap of 5 in a single turn.
    tool_calls_received = []

    def get_trend(symbol):
        tool_calls_received.append(symbol)
        return {"trend": "up", "age_days": 1}

    trend_tool = ToolSpec(
        name="get_trend",
        description="Get the current trend state for a symbol.",
        parameters={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
        function=get_trend,
    )

    batch_of_seven = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"call_{i}", "function": {"name": "get_trend", "arguments": json.dumps({"symbol": "corn"})}}
            for i in range(1, 8)
        ],
    }

    client = FakeChatClient(
        responses=[batch_of_seven, {"role": "assistant", "content": "final answer after cap", "tool_calls": None}]
    )

    result = run_loop(
        client,
        model="deepseek-chat",
        system_prompt="You are a technical analyst.",
        user_prompt="What's the trend for corn?",
        tools=[trend_tool],
        max_tool_calls=5,
    )

    assert result.content == "final answer after cap"
    assert result.tool_call_cap_hit is True
    # Only the first 5 of the 7 requested calls actually ran the tool function.
    assert tool_calls_received == ["corn"] * 5
    assert len(client.calls) == 2
    assert client.calls[1]["tools"] is None

    # Every tool_call_id from the batch still gets a matching tool-result
    # message (the API requires one per call), even the two that were capped.
    tool_result_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_result_messages) == 7
    assert json.loads(tool_result_messages[5]["content"]) == {
        "error": "tool_call_cap_hit: no further tool calls executed this run"
    }
    assert json.loads(tool_result_messages[6]["content"]) == {
        "error": "tool_call_cap_hit: no further tool calls executed this run"
    }


def test_run_loop_does_not_echo_a_replys_usage_back_to_the_model():
    # Usage is bookkeeping about a reply, not part of the conversation: the
    # provider would reject (or bill for) an unknown field in the next request.
    echo = ToolSpec(
        name="echo",
        description="Echo.",
        parameters={"type": "object", "properties": {}},
        function=lambda: {"ok": True},
    )
    client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            {"role": "assistant", "content": "done", "tool_calls": None, "usage": {"prompt_tokens": 20}},
        ]
    )

    result = run_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[echo])

    second_request = client.calls[1]["messages"]
    assert all("usage" not in message for message in second_request)
    assert all("usage" not in message for message in result.messages)


def test_run_loop_feeds_a_failing_tool_back_to_the_model_as_an_error():
    def broken(lookback_weeks):
        raise KeyError("conviction")

    tool = ToolSpec(
        name="read_track_record",
        description="Past calls.",
        parameters={"type": "object", "properties": {"lookback_weeks": {"type": "integer"}}},
        function=broken,
    )
    client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "read_track_record", "arguments": '{"lookback_weeks": 12}'}},
                    {"id": "call_2", "function": {"name": "no_such_tool", "arguments": "{}"}},
                    {"id": "call_3", "function": {"name": "read_track_record", "arguments": "not json"}},
                ],
            },
            {"role": "assistant", "content": '{"call": "neutral"}', "tool_calls": None},
        ]
    )

    result = run_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[tool])

    assert result.content == '{"call": "neutral"}'
    errors = [json.loads(m["content"])["error"] for m in result.messages if m["role"] == "tool"]
    assert errors[0] == "KeyError: 'conviction'"
    assert errors[1].startswith("unknown tool")
    assert errors[2].startswith("arguments are not valid JSON")
    assert result.tool_errors == 3


def _tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("get_rainfall", '{"symbol": "ZC"}'),  # a tool that doesn't exist
        ("get_trend", '{"symbol": "ZC"'),  # truncated JSON
        ("get_trend", '["ZC"]'),  # arguments that aren't an object
        ("get_trend", '{"ticker": "ZC"}'),  # wrong argument name
    ],
)
def test_a_bad_tool_call_is_answered_with_an_error_and_counted_instead_of_crashing(name, arguments):
    def get_trend(symbol):
        raise AssertionError("must not run")

    tool = ToolSpec(name="get_trend", description="d", parameters={"type": "object"}, function=get_trend)
    client = FakeChatClient(
        responses=[
            {"role": "assistant", "content": None, "tool_calls": [_tool_call(name, arguments)]},
            {"role": "assistant", "content": "{}", "tool_calls": None},
        ]
    )

    result = run_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[tool])

    assert result.content == "{}"
    assert result.tool_errors == 1
    tool_message = client.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "error" in json.loads(tool_message["content"])


def test_a_clean_run_has_no_tool_errors():
    client = FakeChatClient(responses=[{"role": "assistant", "content": "{}", "tool_calls": None}])

    assert run_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[]).tool_errors == 0


def _trend_tool(function):
    return ToolSpec(name="get_trend", description="d", parameters={"type": "object"}, function=function)


def _calls_then_answer(*tool_calls):
    return [{"role": "assistant", "content": None, "tool_calls": [call]} for call in tool_calls] + [
        {"role": "assistant", "content": "{}", "tool_calls": None}
    ]


def test_a_failed_call_tells_the_model_to_retry_and_a_good_retry_clears_the_error():
    client = FakeChatClient(
        _calls_then_answer(
            _tool_call("get_trend", '{"ticker": "ZC"}', "c1"),  # wrong argument name
            _tool_call("get_trend", '{"symbol": "ZC"}', "c2"),
        )
    )

    result = run_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[_trend_tool(lambda symbol: {"trend": "up"})])

    first = json.loads(client.calls[1]["messages"][-1]["content"])
    assert "arguments were wrong" in first["retry"] and "get_trend" in first["retry"]
    assert result.tool_errors == 0


def test_a_tool_that_raises_is_retried_and_counted_only_if_the_retry_also_fails():
    attempts = []

    def flaky(symbol):
        attempts.append(symbol)
        if len(attempts) == 1:
            raise ConnectionError("source down")
        return {"trend": "up"}

    recovered = run_loop(
        FakeChatClient(_calls_then_answer(_tool_call("get_trend", '{"symbol": "ZC"}', "c1"), _tool_call("get_trend", '{"symbol": "ZC"}', "c2"))),
        model="m", system_prompt="s", user_prompt="u", tools=[_trend_tool(flaky)],
    )

    def down(symbol):
        raise ConnectionError("source down")

    client = FakeChatClient(_calls_then_answer(_tool_call("get_trend", '{"symbol": "ZC"}', "c1"), _tool_call("get_trend", '{"symbol": "ZC"}', "c2")))
    still_down = run_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[_trend_tool(down)])

    assert recovered.tool_errors == 0
    assert still_down.tool_errors == 2
    error = json.loads(client.calls[1]["messages"][-1]["content"])
    assert error["error"] == "ConnectionError: source down"
    assert "failed while running" in error["retry"]


def test_an_unknown_tool_followed_by_a_real_one_has_recovered():
    client = FakeChatClient(
        _calls_then_answer(_tool_call("get_rainfall", "{}", "c1"), _tool_call("get_trend", '{"symbol": "ZC"}', "c2"))
    )

    result = run_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[_trend_tool(lambda symbol: {})])

    assert "available tools" in json.loads(client.calls[1]["messages"][-1]["content"])["retry"]
    assert result.tool_errors == 0


def test_run_json_loop_sends_validation_problems_back_once():
    client = FakeChatClient(
        [
            {"role": "assistant", "content": '{"call": "up"}', "tool_calls": None},
            {"role": "assistant", "content": '{"call": "bullish"}', "tool_calls": None},
        ]
    )

    result = run_json_loop(
        client, model="m", system_prompt="s", user_prompt="u", tools=[],
        validate=lambda parsed: [] if parsed["call"] == "bullish" else ["call: must be bullish"],
    )

    assert result.content == '{"call": "bullish"}'
    assert "- call: must be bullish" in client.calls[1]["messages"][-1]["content"]
    assert client.calls[1]["tools"] is None


def test_run_json_loop_does_not_retry_a_valid_reply():
    client = FakeChatClient([{"role": "assistant", "content": "{}", "tool_calls": None}])

    run_json_loop(client, model="m", system_prompt="s", user_prompt="u", tools=[], validate=lambda parsed: [])

    assert len(client.calls) == 1
