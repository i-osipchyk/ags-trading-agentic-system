import inspect
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
    # Failed tool calls (unknown name, bad arguments, or the tool raised) that no later call to
    # the same tool made good. Callers fold this into `degraded`: the run went on, but on less
    # evidence than it asked for. A failure the model fixed on retry isn't counted.
    tool_errors: int = 0


def _tool_schema(tool: ToolSpec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


# Sent with every failed call, so the model knows whether to fix its arguments or just call again.
_ARGUMENTS_RETRY = "The call was not run because its arguments were wrong. Correct them and call {name} again."
_UNKNOWN_TOOL_RETRY = "No tool has that name. Call one of the available tools instead."
_RAISED_RETRY = (
    "{name} failed while running. If the error points at your arguments, correct them; "
    "either way, call {name} again."
)


def _call_error(tool_by_name: dict[str, ToolSpec], tool_call: dict) -> tuple[ToolSpec, dict] | str:
    """The tool and its arguments, or why the model's call can't be run as asked."""
    name = tool_call["function"]["name"]
    tool = tool_by_name.get(name)
    if tool is None:
        return f"unknown tool {name!r}; available: {sorted(tool_by_name)}"
    try:
        arguments = json.loads(tool_call["function"]["arguments"] or "{}")
    except json.JSONDecodeError as exc:
        return f"arguments are not valid JSON: {exc}"
    if not isinstance(arguments, dict):
        return "arguments must be a JSON object"
    try:
        inspect.signature(tool.function).bind(**arguments)
    except TypeError as exc:
        return f"arguments don't match {name}: {exc}"
    return tool, arguments


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
    # Failed calls per tool name (None: a name that doesn't exist), cleared by a later success.
    unresolved: dict[str | None, int] = {}

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
        # "usage" is bookkeeping about the reply (logged by the caller's client wrapper),
        # not conversation: it must not be sent back to the provider.
        messages.append({k: v for k, v in response_message.items() if k != "usage"})

        tool_calls = response_message.get("tool_calls")
        if not tool_calls:
            return LoopResult(
                content=response_message.get("content") or "",
                tool_call_cap_hit=cap_hit,
                messages=messages,
                tool_errors=sum(unresolved.values()),
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
                # A bad call still counts toward the cap, so a model stuck repeating it can't loop forever.
                tool_call_count += 1
                name = tool_call["function"]["name"]
                resolved = _call_error(tool_by_name, tool_call)
                if isinstance(resolved, str):
                    known = name if name in tool_by_name else None
                    unresolved[known] = unresolved.get(known, 0) + 1
                    retry = _ARGUMENTS_RETRY.format(name=name) if known else _UNKNOWN_TOOL_RETRY
                    result = {"error": resolved, "retry": retry}
                else:
                    tool, arguments = resolved
                    # A tool that fails (a source outage, bad stored data, arguments it can't
                    # use) is reported back too, not raised: the model can retry or answer without it.
                    try:
                        result = tool.function(**arguments)
                    except Exception as exc:
                        unresolved[name] = unresolved.get(name, 0) + 1
                        result = {"error": f"{type(exc).__name__}: {exc}", "retry": _RAISED_RETRY.format(name=name)}
                    else:
                        unresolved.pop(name, None)
                        # A model that called a made-up name and then a real tool has recovered.
                        unresolved.pop(None, None)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result),
                }
            )


_JSON_RETRY_NUDGE = (
    "Your previous reply was not valid JSON. Respond again with only the required JSON object — no other text."
)
_INVALID_RETRY_NUDGE = (
    "Your previous reply did not meet the required format:\n{problems}\n"
    "Respond again with only the corrected JSON object — no other text."
)


def _retry_nudge(content: str, validate: Callable[[object], list[str]] | None) -> str | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return _JSON_RETRY_NUDGE
    problems = validate(parsed) if validate else []
    return _INVALID_RETRY_NUDGE.format(problems="\n".join(f"- {p}" for p in problems)) if problems else None


def run_json_loop(
    client: ChatClient,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tools: list[ToolSpec],
    max_tool_calls: int = 5,
    validate: Callable[[object], list[str]] | None = None,
) -> LoopResult:
    """`run_loop` for agents whose final reply must be JSON.

    If the final reply isn't valid JSON, or `validate` (given the parsed reply) lists problems
    with it, asks once more in the same conversation, naming what was wrong, with no tools on
    offer. The second reply is returned whether or not it passes — callers still handle a bad one.
    """
    result = run_loop(
        client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools=tools,
        max_tool_calls=max_tool_calls,
    )
    nudge = _retry_nudge(result.content, validate)
    if nudge is None:
        return result

    messages = [*result.messages, {"role": "user", "content": nudge}]
    retry = client.complete(messages, None, model=model)
    messages.append(retry)
    return LoopResult(
        content=retry.get("content") or "",
        tool_call_cap_hit=result.tool_call_cap_hit,
        messages=messages,
        tool_errors=result.tool_errors,
    )
