"""The project's single LLM seam: every model call goes through llmkit from here.

llmkit attributes each call to project (ags) / env / component and prices it (platform
docs/llm-observability.md). The component is the agent name (technical, news, weather,
supply_demand, coordinator, manager, auditor, run_chat), the same name the run logs use, so
llmkit's numbers join the run logs one to one.

The root `LLMClient` can't complete anything by itself: a caller must `bind()` it to a component
first, so no call goes out unattributed. `run_loop` only ever sees a bound client.
"""

import os
from contextlib import AbstractContextManager, nullcontext

from llmkit import LLM

from ags.llm.loop import ChatClient

_PROVIDER = "deepseek"  # DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL, read by llmkit


class LLMClient:
    def __init__(self, llm: LLM | None = None) -> None:
        # LLMKIT_PROJECT / LLMKIT_ENV come from .env locally and from the lambda-app module on AWS.
        self._llm = llm or LLM(project=os.getenv("LLMKIT_PROJECT") or "ags")

    def bind(self, component: str, **metadata) -> "BoundClient":
        return BoundClient(self._llm, component, metadata)

    def trace(self, name: str) -> AbstractContextManager:
        """Group one run's calls (one pipeline run, one portfolio stage) under one trace."""
        return self._llm.trace(name)

    def flush(self) -> None:
        self._llm.flush()


class BoundClient:
    """A `ChatClient` whose calls are all recorded under one component."""

    def __init__(self, llm: LLM, component: str, metadata: dict) -> None:
        self._llm = llm
        self.component = component
        self.metadata = metadata

    def bind(self, component: str, **metadata) -> "BoundClient":
        return BoundClient(self._llm, component, {**self.metadata, **metadata})

    def complete(self, messages: list[dict], tools: list[dict] | None, *, model: str) -> dict:
        kwargs = {"tools": tools} if tools else {}
        result = self._llm.chat(
            model=model, messages=messages, component=self.component, provider=_PROVIDER,
            metadata=self.metadata, **kwargs,
        )
        message = result.message

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in message.tool_calls
            ]

        usage = _usage(getattr(result.raw, "usage", None))
        if usage is not None:
            # Priced by llmkit's table (the only one); None when the model has no price there.
            usage["cost_usd"] = result.record.cost_usd
        return {"role": message.role, "content": message.content, "tool_calls": tool_calls, "usage": usage}


def bind(client, component: str, **metadata) -> ChatClient:
    """`client` bound to `component`; clients without `bind` (test fakes) are returned as is."""
    binder = getattr(client, "bind", None)
    return binder(component, **metadata) if binder else client


def trace(client, name: str) -> AbstractContextManager:
    tracer = getattr(client, "trace", None)
    return tracer(name) if tracer else nullcontext()


def flush(client) -> None:
    flusher = getattr(client, "flush", None)
    if flusher:
        flusher()


def _usage(usage) -> dict | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        # DeepSeek-specific split of the prompt into cached and uncached tokens (priced differently).
        "prompt_cache_hit_tokens": getattr(usage, "prompt_cache_hit_tokens", None),
        "prompt_cache_miss_tokens": getattr(usage, "prompt_cache_miss_tokens", None),
    }
