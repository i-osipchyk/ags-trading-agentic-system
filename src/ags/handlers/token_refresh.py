from ags.ctrader_tokens import TokenRefreshError, refresh_if_due


def handle(event: dict, *, secret_store, token_endpoint, alert, now) -> dict:
    try:
        return {"outcome": refresh_if_due(secret_store, token_endpoint, now=now)}
    except TokenRefreshError as exc:
        alert(f"cTrader token refresh failed: {exc}")
        raise


def handler(event, context):
    from ags.handlers.entry import token_refresh_handler

    return token_refresh_handler(event, context)
