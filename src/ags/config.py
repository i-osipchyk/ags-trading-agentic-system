"""Environment loading and the typed Config built from it.

Locally: values come from .env (gitignored; see .env.example).
On Lambda: SSM parameters under SECRETS_PATH (/ags/<env>) and SHARED_SECRETS_PATH
(/platform/shared) are copied into os.environ, kebab-case name -> UPPER_SNAKE, e.g.
/ags/live/deepseek-api-key -> DEEPSEEK_API_KEY (platform docs/conventions.md § Secrets).

The cTrader client id/secret and tokens are the account's one shared pair under
/platform/shared/ctrader-*, rotated only by the platform's token refresher; this project never
refreshes or keeps its own copy. Only CTRADER_ACCOUNT_ID is per project. Locally, any CTRADER_*
key not in .env is read from the same shared parameters with your AWS credentials.

AWS_REGION is always set afterwards: from the environment / .env (Lambda sets it itself), else
DEFAULT_AWS_REGION. AWS_DEFAULT_REGION mirrors it, so every boto3 client agrees.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_AWS_REGION = "eu-west-1"
DEFAULT_SHARED_SECRETS_PATH = "/platform/shared"
SHARED_CTRADER_KEYS = ("CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN")

logger = logging.getLogger(__name__)


def load_env(dotenv_path: str | Path | None = None, *, refresh: bool = False) -> None:
    """Load .env locally, or the SSM parameters on Lambda.

    `refresh=True` re-reads SSM and overwrites what an earlier load put in os.environ: a warm
    container must see a cTrader token the platform rotated after its cold start.
    """
    on_lambda = bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))
    if not on_lambda:
        load_dotenv(dotenv_path)
    os.environ.setdefault("AWS_REGION", DEFAULT_AWS_REGION)
    os.environ.setdefault("AWS_DEFAULT_REGION", os.environ["AWS_REGION"])
    if on_lambda:
        _load_ssm(overwrite=refresh)
    elif any(not os.getenv(key) for key in SHARED_CTRADER_KEYS):
        _load_shared_ctrader()


def _load_ssm(*, overwrite: bool) -> None:
    import boto3

    ssm = boto3.client("ssm")
    values: dict[str, str] = {}
    # Shared last, so it wins: a leftover /ags/<env>/ctrader-* copy is stale by definition.
    for path in filter(None, (os.getenv("SECRETS_PATH"), os.getenv("SHARED_SECRETS_PATH"))):
        values.update(read_parameters(ssm, path))
    for key, value in values.items():
        if overwrite:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _load_shared_ctrader() -> None:
    """Fill the CTRADER_* keys .env leaves unset from /platform/shared, with local AWS credentials."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    path = os.getenv("SHARED_SECRETS_PATH") or DEFAULT_SHARED_SECRETS_PATH
    try:
        shared = read_parameters(boto3.client("ssm"), path)
    except (BotoCoreError, ClientError) as exc:
        # Config then names the missing keys; scripts that don't need cTrader carry on.
        logger.warning("could not read cTrader credentials from SSM %s: %s", path, exc)
        return
    for key in SHARED_CTRADER_KEYS:
        if key in shared and not os.getenv(key):  # a blank `KEY=` line in .env counts as unset
            os.environ[key] = shared[key]


def read_parameters(ssm, path: str) -> dict[str, str]:
    """Every parameter directly under `path`, decrypted, keyed by its UPPER_SNAKE env var name."""
    values = {}
    for page in ssm.get_paginator("get_parameters_by_path").paginate(
        Path=path, Recursive=False, WithDecryption=True
    ):
        for p in page["Parameters"]:
            values[env_name(p["Name"].rsplit("/", 1)[-1])] = p["Value"]
    return values


def env_name(parameter: str) -> str:
    return parameter.replace("-", "_").upper()


def parameter_name(env_key: str) -> str:
    return env_key.lower().replace("_", "-")


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
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    ctrader_trading_account_id: int = 0
    ctrader_demo: bool = False  # the account is a demo account: market data comes from the demo host too

    @classmethod
    def from_env(cls, *, refresh: bool = False) -> "Config":
        load_env(refresh=refresh)
        return cls.from_mapping(os.environ)

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
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or "",
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or "",
            ctrader_demo=(env.get("CTRADER_DEMO") or "").strip().lower() in ("1", "true", "yes"),
            # The demo account the manager trades; defaults to the project's one cTrader account.
            ctrader_trading_account_id=int(env.get("CTRADER_TRADING_ACCOUNT_ID") or env["CTRADER_ACCOUNT_ID"]),
        )
