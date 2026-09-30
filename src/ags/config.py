import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


_REQUIRED_KEYS = (
    "DEEPSEEK_API_KEY",
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "TAVILY_API_KEY",
)

_DEFAULT_MAX_TOOL_CALLS = 5


@dataclass(frozen=True)
class Config:
    deepseek_api_key: str
    ctrader_client_id: str
    ctrader_client_secret: str
    tavily_api_key: str
    max_tool_calls: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        missing = [key for key in _REQUIRED_KEYS if not os.environ.get(key)]
        if missing:
            raise ConfigError(f"Missing required env key(s): {', '.join(missing)}")

        return cls(
            deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
            ctrader_client_id=os.environ["CTRADER_CLIENT_ID"],
            ctrader_client_secret=os.environ["CTRADER_CLIENT_SECRET"],
            tavily_api_key=os.environ["TAVILY_API_KEY"],
            max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS") or _DEFAULT_MAX_TOOL_CALLS),
        )
