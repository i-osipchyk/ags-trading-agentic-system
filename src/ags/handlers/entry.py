"""Lambda entry-point plumbing shared by every handler module.

A fresh Runtime per invocation: the SSM secrets are re-read (so a token refreshed while this
container was warm is picked up) and the cTrader connection is never left stale between
invocations. llmkit is flushed after every invocation.
"""

import logging
import os

from ags.config import load_env

logging.getLogger().setLevel(logging.INFO)
load_env()  # once per cold start: AWS_REGION default, SSM -> os.environ

_AUTH_ERROR_MARKERS = ("ACCESS_TOKEN", "CANT_ROUTE_REQUEST", "ACCOUNT_NOT_AUTHORIZED")


def _is_auth_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _AUTH_ERROR_MARKERS)


def invoke(handle, event: dict, *, build):
    """Build a runtime and run the handler. On a cTrader auth error, re-read the secrets once and retry."""
    try:
        return _invoke_once(handle, event, build)
    except Exception as exc:
        if not _is_auth_error(exc):
            raise
    return _invoke_once(handle, event, build)


def _invoke_once(handle, event: dict, build):
    from ags.llm.client import flush

    runtime = build()
    try:
        return handle(event, runtime)
    finally:
        flush(getattr(runtime, "chat_client", None))
        close = getattr(runtime.market_client, "close", None)
        if close:
            close()


def build_default_runtime():
    import boto3

    from ags.config import Config
    from ags.handlers.runtime import build_runtime
    from ags.llm.client import LLMClient
    from ags.tools.sources.prices import CTraderPricesClient

    return build_runtime(
        os.environ, config=Config.from_env(refresh=True), ssm_client=boto3.client("ssm"),
        chat_factory=lambda config: LLMClient(), market_factory=CTraderPricesClient,
        alert_factory=lambda config: _telegram(config).send,
    )


def _telegram(config):
    from ags.notify.telegram import TelegramClient

    return TelegramClient(config.telegram_bot_token, config.telegram_chat_id)


def _config():
    from ags.config import Config

    return Config.from_env(refresh=True)


def digest_handler(event, context):
    from datetime import datetime, timezone

    import boto3

    from ags.handlers import digest
    from ags.notify.aws_cost import prior_day_aws_cost
    from ags.storage import S3Storage

    now = datetime.now(timezone.utc)
    return digest.handle(
        event, storage=S3Storage(os.environ["AGS_BUCKET"]), telegram=_telegram(_config()),
        # Cost Explorer is a global service whose API lives in us-east-1 only.
        aws_cost=prior_day_aws_cost(boto3.client("ce", region_name="us-east-1"), now=now), now=now,
    )


def token_refresh_handler(event, context):
    from datetime import datetime, timezone

    import boto3
    import requests

    from ags.ctrader_tokens import SsmSecretStore
    from ags.handlers import token_refresh

    def token_endpoint(params: dict) -> dict:
        return requests.get("https://openapi.ctrader.com/apps/token", params=params, timeout=30).json()

    store = SsmSecretStore(boto3.client("ssm"), os.environ["SECRETS_PATH"])
    telegram = _telegram(_config())
    return token_refresh.handle(
        event, secret_store=store, token_endpoint=token_endpoint, alert=telegram.send, now=datetime.now(timezone.utc)
    )
