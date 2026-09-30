import pytest

from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient
from ags.llm.loop import ToolSpec, run_loop


@pytest.mark.integration
def test_run_loop_against_real_deepseek_with_one_trivial_tool():
    config = Config.from_env()
    client = DeepSeekChatClient(config)

    add_calls = []

    def add(a, b):
        add_calls.append((a, b))
        return {"sum": a + b}

    add_tool = ToolSpec(
        name="add",
        description="Add two numbers and return their sum.",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
        function=add,
    )

    result = run_loop(
        client,
        model=config.deepseek_model,
        system_prompt="You are a careful assistant. Always use the add tool to perform addition.",
        user_prompt="What is 17 + 25? Use the add tool, then state the final sum in your answer.",
        tools=[add_tool],
    )

    assert add_calls, "expected the model to call the add tool at least once"
    assert result.content
    assert "42" in result.content
    assert result.tool_call_cap_hit is False
