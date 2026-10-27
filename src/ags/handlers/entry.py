"""Lambda entry-point plumbing shared by every handler module.

A fresh Runtime per invocation: the SSM secrets are re-read (so a cTrader token the platform rotated while
this container was warm is picked up) and the cTrader connection is never left stale between
invocations. llmkit is flushed after every invocation.

A handler that raises logs one `ags-error` line first: which handler, the event's ids and the
error. The platform notifier quotes the Lambda's [ERROR] lines in the alarm it sends to Telegram,
so the alert says which commodity / cycle failed, not just which function.
"""

import logging
import os
from datetime import datetime

from ags import blockers
from ags.config import load_env
from ags.logging.run_id import generate_run_id

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)
load_env()  # once per cold start: AWS_REGION default, SSM -> os.environ

_AUTH_ERROR_MARKERS = ("ACCESS_TOKEN", "CANT_ROUTE_REQUEST", "ACCOUNT_NOT_AUTHORIZED")
_CONTEXT_KEYS = ("commodity", "cycle_id", "scheduled_time", "off_schedule")


def _is_auth_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _AUTH_ERROR_MARKERS)


def error_context(handle, event, exc: Exception) -> str:
    """One line: `ags-error handler=... commodity=... run_id=... error=Type: message`."""
    parts = [f"handler={getattr(handle, '__module__', '?').rsplit('.', 1)[-1]}"]
    if isinstance(event, dict):
        parts += [f"{key}={event[key]}" for key in _CONTEXT_KEYS if key in event]
        if "commodity" in event and "scheduled_time" in event:
            try:
                scheduled = datetime.fromisoformat(event["scheduled_time"])
                parts.append(f"run_id={generate_run_id(event['commodity'], scheduled)}")
            except (TypeError, ValueError):
                pass
    for provider, detail in blockers.seen().items():
        parts.append(f"blocked={provider}: {detail}")
    parts.append(f"error={type(exc).__name__}: {str(exc)[:300]}")
    return "ags-error " + " ".join(parts)


def invoke(handle, event: dict, *, build):
    """Build a runtime and run the handler. On a cTrader auth error, re-read the secrets once and retry."""
    try:
        try:
            return _invoke_once(handle, event, build)
        except Exception as exc:
            if not _is_auth_error(exc):
                raise
        return _invoke_once(handle, event, build)
    except Exception as exc:
        logger.error(error_context(handle, event, exc))
        raise


def _invoke_once(handle, event: dict, build):
    from ags.llm.client import flush

    blockers.clear()  # per invocation: a warm container must not carry the last one's
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

    from ags.handlers import digest
    from ags.storage import S3Storage

    return digest.handle(
        event, storage=S3Storage(os.environ["AGS_BUCKET"]), telegram=_telegram(_config()),
        now=datetime.now(timezone.utc),
    )

