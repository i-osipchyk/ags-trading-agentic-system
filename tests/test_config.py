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


class FakeSecrets:
    def __init__(self, secret_string):
        self._secret_string = secret_string
        self.calls = []

    def get_secret_value(self, SecretId):
        self.calls.append(SecretId)
        return {"SecretString": self._secret_string}


_SECRET = {
    "DEEPSEEK_API_KEY": "ds",
    "CTRADER_CLIENT_ID": "id",
    "CTRADER_CLIENT_SECRET": "secret",
    "CTRADER_ACCESS_TOKEN": "access",
    "CTRADER_ACCOUNT_ID": "123",
    "TAVILY_API_KEY": "tv",
    "CTRADER_REFRESH_TOKEN": "refresh",
    "TELEGRAM_BOT_TOKEN": "bot",
    "TELEGRAM_CHAT_ID": "chat",
}


def test_from_secret_builds_config_from_secrets_manager_json():
    import json

    secrets = FakeSecrets(json.dumps(_SECRET))

    config = Config.from_secret(secrets, "ags/prod")

    assert secrets.calls == ["ags/prod"]
    assert config.deepseek_api_key == "ds"
    assert config.ctrader_account_id == 123
    assert config.ctrader_refresh_token == "refresh"
    assert config.telegram_bot_token == "bot"
    assert config.telegram_chat_id == "chat"


def test_from_secret_raises_on_missing_required_key():
    import json

    secret = {k: v for k, v in _SECRET.items() if k != "TAVILY_API_KEY"}

    with pytest.raises(ConfigError, match="TAVILY_API_KEY"):
        Config.from_secret(FakeSecrets(json.dumps(secret)), "ags/prod")
