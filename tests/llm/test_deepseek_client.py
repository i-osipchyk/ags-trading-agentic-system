from types import SimpleNamespace

from ags.config import Config
from ags.llm import deepseek_client
from ags.llm.deepseek_client import DeepSeekChatClient


def _config() -> Config:
    return Config(
        deepseek_api_key="k",
        deepseek_base_url="http://localhost",
        deepseek_model="deepseek-chat",
        ctrader_client_id="",
        ctrader_client_secret="",
        ctrader_access_token="",
        ctrader_account_id=0,
        tavily_api_key="",
        max_tool_calls=5,
        chart_lookback_candles=30,
    )


def _stub_openai(monkeypatch, usage):
    message = SimpleNamespace(role="assistant", content="hello", tool_calls=None)
    reply = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: reply))

    monkeypatch.setattr(deepseek_client, "OpenAI", FakeOpenAI)


def test_complete_returns_the_token_usage_the_api_reported(monkeypatch):
    _stub_openai(
        monkeypatch,
        SimpleNamespace(
            prompt_tokens=1200,
            completion_tokens=300,
            total_tokens=1500,
            prompt_cache_hit_tokens=1000,
            prompt_cache_miss_tokens=200,
        ),
    )

    result = DeepSeekChatClient(_config()).complete([{"role": "user", "content": "hi"}], None, model="deepseek-chat")

    assert result["content"] == "hello"
    assert result["usage"] == {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "prompt_cache_hit_tokens": 1000,
        "prompt_cache_miss_tokens": 200,
    }
