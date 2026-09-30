import os
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

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        missing = [key for key in _REQUIRED_KEYS if not os.environ.get(key)]
        if missing:
            raise ConfigError(f"Missing required env key(s): {', '.join(missing)}")

        return cls(
            deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL") or _DEFAULT_DEEPSEEK_BASE_URL,
            deepseek_model=os.environ.get("DEEPSEEK_MODEL") or _DEFAULT_DEEPSEEK_MODEL,
            ctrader_client_id=os.environ["CTRADER_CLIENT_ID"],
            ctrader_client_secret=os.environ["CTRADER_CLIENT_SECRET"],
            ctrader_access_token=os.environ["CTRADER_ACCESS_TOKEN"],
            ctrader_account_id=int(os.environ["CTRADER_ACCOUNT_ID"]),
            tavily_api_key=os.environ["TAVILY_API_KEY"],
            max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS") or _DEFAULT_MAX_TOOL_CALLS),
            chart_lookback_candles=int(
                os.environ.get("CHART_LOOKBACK_CANDLES") or _DEFAULT_CHART_LOOKBACK_CANDLES
            ),
        )
