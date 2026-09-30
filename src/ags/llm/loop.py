import json
from dataclasses import dataclass, field
from typing import Callable, Protocol


class ChatClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None, *, model: str) -> dict: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    function: Callable[..., dict]


@dataclass(frozen=True)
class LoopResult:
    content: str
    tool_call_cap_hit: bool
    messages: list[dict] = field(default_factory=list)


def _tool_schema(tool: ToolSpec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def run_loop(
    client: ChatClient,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tools: list[ToolSpec],
    max_tool_calls: int = 5,
) -> LoopResult:
    tool_by_name = {tool.name: tool for tool in tools}
    tool_schemas = [_tool_schema(tool) for tool in tools] or None

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    tool_call_count = 0

    while True:
        cap_hit = tool_call_count >= max_tool_calls
        response_message = client.complete(messages, None if cap_hit else tool_schemas, model=model)
        messages.append(response_message)

        tool_calls = response_message.get("tool_calls")
        if not tool_calls:
            return LoopResult(
                content=response_message.get("content") or "",
                tool_call_cap_hit=cap_hit,
                messages=messages,
            )

        for tool_call in tool_calls:
            tool = tool_by_name[tool_call["function"]["name"]]
            arguments = json.loads(tool_call["function"]["arguments"])
            result = tool.function(**arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result),
                }
            )
            tool_call_count += 1
