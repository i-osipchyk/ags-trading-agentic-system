import json

from ags.llm.loop import ToolSpec, run_loop


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
