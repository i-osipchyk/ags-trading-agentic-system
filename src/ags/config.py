import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


_REQUIRED_KEYS = (
    "DEEPSEEK_API_KEY",
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_ACCOUNT_ID",
    "TAVILY_API_KEY",
)

_DEFAULT_MAX_TOOL_CALLS = 5
_DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
_DEFAULT_CHART_LOOKBACK_CANDLES = 30


@dataclass(frozen=True)
class Config:
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    ctrader_client_id: str
    ctrader_client_secret: str
    ctrader_access_token: str
    ctrader_account_id: int
    tavily_api_key: str
    max_tool_calls: int
    chart_lookback_candles: int
    ctrader_refresh_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        return cls.from_mapping(os.environ)

    @classmethod
    def from_secret(cls, secrets_client, secret_id: str) -> "Config":
        """Lambda path: one Secrets Manager secret (JSON of the same keys), read per cold start."""
        secret = json.loads(secrets_client.get_secret_value(SecretId=secret_id)["SecretString"])
        return cls.from_mapping(secret)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "Config":
        missing = [key for key in _REQUIRED_KEYS if not env.get(key)]
        if missing:
            raise ConfigError(f"Missing required env key(s): {', '.join(missing)}")

        return cls(
            deepseek_api_key=env["DEEPSEEK_API_KEY"],
            deepseek_base_url=env.get("DEEPSEEK_BASE_URL") or _DEFAULT_DEEPSEEK_BASE_URL,
            deepseek_model=env.get("DEEPSEEK_MODEL") or _DEFAULT_DEEPSEEK_MODEL,
            ctrader_client_id=env["CTRADER_CLIENT_ID"],
            ctrader_client_secret=env["CTRADER_CLIENT_SECRET"],
            ctrader_access_token=env["CTRADER_ACCESS_TOKEN"],
            ctrader_account_id=int(env["CTRADER_ACCOUNT_ID"]),
            tavily_api_key=env["TAVILY_API_KEY"],
            max_tool_calls=int(env.get("MAX_TOOL_CALLS") or _DEFAULT_MAX_TOOL_CALLS),
            chart_lookback_candles=int(
                env.get("CHART_LOOKBACK_CANDLES") or _DEFAULT_CHART_LOOKBACK_CANDLES
            ),
            ctrader_refresh_token=env.get("CTRADER_REFRESH_TOKEN") or "",
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or "",
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or "",
        )
