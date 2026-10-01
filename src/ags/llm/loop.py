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
        if cap_hit:
            # Observed against the real model: stripping the tools param
            # alone isn't enough — a model that still wants to keep
            # searching can ignore "JSON only" and emit its own
            # pseudo-tool-call markup as content instead. Telling it plainly
            # that no more tool calls are available fixes that.
            messages.append(
                {
                    "role": "user",
                    "content": "No more tool calls are available. Respond now with the "
                    "required JSON object, using only the tool results already gathered above.",
                }
            )
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
            # A single response can request several tool calls at once
            # (parallel tool calling) — the cap must stop execution mid-batch,
            # not just between turns, or a model that batches more calls than
            # the cap blows straight past it in one turn. Every tool_call_id
            # still gets a matching result message (the API requires one per
            # call), even the ones capped out of actually running.
            if tool_call_count >= max_tool_calls:
                result = {"error": "tool_call_cap_hit: no further tool calls executed this run"}
            else:
                tool = tool_by_name[tool_call["function"]["name"]]
                arguments = json.loads(tool_call["function"]["arguments"])
                result = tool.function(**arguments)
                tool_call_count += 1
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result),
                }
            )
