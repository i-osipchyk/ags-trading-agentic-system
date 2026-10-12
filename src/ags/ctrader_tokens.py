import json
from datetime import datetime, timedelta

REFRESH_WINDOW = timedelta(days=7)


class TokenRefreshError(Exception):
    pass


def refresh_if_due(secret_store, token_endpoint, *, now: datetime) -> str:
    """Refresh the cTrader tokens held in the secret when they expire within 7 days. Returns the outcome."""
    secret = secret_store.read()
    expires_at = secret.get("CTRADER_ACCESS_TOKEN_EXPIRES_AT")
    if expires_at and datetime.fromisoformat(expires_at) - now > REFRESH_WINDOW:
        return "not_due"
    try:
        response = token_endpoint({
            "grant_type": "refresh_token", "refresh_token": secret["CTRADER_REFRESH_TOKEN"],
            "client_id": secret["CTRADER_CLIENT_ID"], "client_secret": secret["CTRADER_CLIENT_SECRET"],
        })
    except Exception as exc:
        raise TokenRefreshError(f"{type(exc).__name__}: {exc}") from exc
    if response.get("errorCode"):
        raise TokenRefreshError(f"{response['errorCode']}: {response.get('description')}")
    secret_store.write({
        **secret,
        "CTRADER_ACCESS_TOKEN": response["accessToken"],
        "CTRADER_REFRESH_TOKEN": response["refreshToken"],
        "CTRADER_ACCESS_TOKEN_EXPIRES_AT": (now + timedelta(seconds=response["expiresIn"])).isoformat(),
    })
    return "refreshed"


class SecretsManagerStore:
    """The one JSON secret holding every credential; written back whole so unrelated keys survive."""

    def __init__(self, client, secret_id: str) -> None:
        self._client = client
        self._secret_id = secret_id

    def read(self) -> dict:
        return json.loads(self._client.get_secret_value(SecretId=self._secret_id)["SecretString"])

    def write(self, values: dict) -> None:
        self._client.put_secret_value(SecretId=self._secret_id, SecretString=json.dumps(values))
