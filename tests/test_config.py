import pytest

from ags.config import Config, ConfigError


def test_from_env_raises_on_missing_required_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CTRADER_CLIENT_ID", "id")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")

    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        Config.from_env()
