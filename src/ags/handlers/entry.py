"""Lambda entry-point plumbing shared by every handler module.

A fresh Runtime per invocation: the secret is re-read (so a token refreshed while this container
was warm is picked up) and the cTrader connection is never left stale between invocations.
"""

import os


_AUTH_ERROR_MARKERS = ("ACCESS_TOKEN", "CANT_ROUTE_REQUEST", "ACCOUNT_NOT_AUTHORIZED")


def _is_auth_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _AUTH_ERROR_MARKERS)


def invoke(handle, event: dict, *, build):
    """Build a runtime and run the handler. On a cTrader auth error, re-read the secret once and retry."""
    try:
        return _invoke_once(handle, event, build)
    except Exception as exc:
        if not _is_auth_error(exc):
            raise
    return _invoke_once(handle, event, build)


def _invoke_once(handle, event: dict, build):
    runtime = build()
    try:
        return handle(event, runtime)
    finally:
        close = getattr(runtime.market_client, "close", None)
        if close:
            close()


def build_default_runtime():
    import boto3

    from ags.llm.deepseek_client import DeepSeekChatClient
    from ags.handlers.runtime import build_runtime
    from ags.tools.sources.prices import CTraderPricesClient

    region = os.environ.get("AWS_REGION")
    return build_runtime(
        os.environ, secrets_client=boto3.client("secretsmanager", region_name=region),
        ssm_client=boto3.client("ssm", region_name=region), chat_factory=DeepSeekChatClient,
        market_factory=CTraderPricesClient, alert_factory=lambda config: _telegram(config).send,
    )


def _telegram(config):
    from ags.notify.telegram import TelegramClient

    return TelegramClient(config.telegram_bot_token, config.telegram_chat_id)


def _config():
    import boto3

    from ags.config import Config

    return Config.from_secret(boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION")), os.environ["AGS_SECRET_ID"])


def digest_handler(event, context):
    from datetime import datetime, timezone

    import boto3

    from ags.handlers import digest
    from ags.notify.aws_cost import prior_day_aws_cost
    from ags.notify.spend import LlmPrices
    from ags.storage import S3Storage

    now = datetime.now(timezone.utc)
    # No defaults: a wrong price table would silently mis-report spend, so a missing one fails the digest loudly.
    prices = LlmPrices(
        cache_hit=float(os.environ["LLM_PRICE_CACHE_HIT_PER_MTOK"]),
        cache_miss=float(os.environ["LLM_PRICE_CACHE_MISS_PER_MTOK"]),
        output=float(os.environ["LLM_PRICE_OUTPUT_PER_MTOK"]),
    )
    return digest.handle(
        event, storage=S3Storage(os.environ["AGS_BUCKET"], region_name=os.environ.get("AWS_REGION")),
        telegram=_telegram(_config()), prices=prices,
        aws_cost=prior_day_aws_cost(boto3.client("ce", region_name="us-east-1"), now=now), now=now,
    )


def notify_handler(event, context):
    from ags.handlers import notify

    return notify.handle(event, telegram=_telegram(_config()))


def token_refresh_handler(event, context):
    from datetime import datetime, timezone

    import boto3
    import requests

    from ags.ctrader_tokens import SecretsManagerStore
    from ags.handlers import token_refresh

    def token_endpoint(params: dict) -> dict:
        return requests.get("https://openapi.ctrader.com/apps/token", params=params, timeout=30).json()

    store = SecretsManagerStore(
        boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION")), os.environ["AGS_SECRET_ID"]
    )
    telegram = _telegram(_config())
    return token_refresh.handle(
        event, secret_store=store, token_endpoint=token_endpoint, alert=telegram.send, now=datetime.now(timezone.utc)
    )
