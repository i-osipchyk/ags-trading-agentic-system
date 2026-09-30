import pytest

import ags.config
from ags.config import Config, ConfigError


def test_from_env_raises_on_missing_required_key(monkeypatch):
    # Stubbed so this test doesn't depend on whether the developer's own
    # .env happens to have DEEPSEEK_API_KEY filled in.
    monkeypatch.setattr(ags.config, "load_dotenv", lambda: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CTRADER_CLIENT_ID", "id")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "token")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "123")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")

    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        Config.from_env()
