from datetime import datetime, timedelta

from ags.config import parameter_name, read_parameters

REFRESH_WINDOW = timedelta(days=7)


class TokenRefreshError(Exception):
    pass


def refresh_if_due(secret_store, token_endpoint, *, now: datetime) -> str:
    """Refresh the cTrader tokens held in the secret store when they expire within 7 days. Returns the outcome."""
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
    # Only the rotated keys are written; the refresh token first, since the old one is now spent.
    secret_store.write({
        "CTRADER_REFRESH_TOKEN": response["refreshToken"],
        "CTRADER_ACCESS_TOKEN": response["accessToken"],
        "CTRADER_ACCESS_TOKEN_EXPIRES_AT": (now + timedelta(seconds=response["expiresIn"])).isoformat(),
    })
    return "refreshed"


class SsmSecretStore:
    """The SSM parameters under /ags/<env>/ (SecureStrings), read and written as UPPER_SNAKE keys.

    `write` puts only the keys it is given, so unrelated secrets are never touched.
    """

    def __init__(self, client, path: str) -> None:
        self._client = client
        self._path = path.rstrip("/")

    def read(self) -> dict:
        return read_parameters(self._client, self._path)

    def write(self, values: dict) -> None:
        for key, value in values.items():
            self._client.put_parameter(
                Name=f"{self._path}/{parameter_name(key)}", Value=value, Type="SecureString", Overwrite=True
            )
