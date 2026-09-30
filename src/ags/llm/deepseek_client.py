from openai import OpenAI

from ags.config import Config


class DeepSeekChatClient:
    """Thin wrapper over the OpenAI-compatible chat completions API.

    Implements the `ChatClient` protocol `run_loop` depends on — the
    hand-rolled tool-calling loop owns all agentic logic (looping, cap
    enforcement, tool dispatch); this class only shapes one request/response
    pair.
    """

    def __init__(self, config: Config):
        self._client = OpenAI(api_key=config.deepseek_api_key, base_url=config.deepseek_base_url)

    def complete(self, messages: list[dict], tools: list[dict] | None, *, model: str) -> dict:
        kwargs = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools

        response = self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message

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

        return {"role": message.role, "content": message.content, "tool_calls": tool_calls}
