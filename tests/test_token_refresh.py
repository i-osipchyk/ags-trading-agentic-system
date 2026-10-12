from datetime import datetime, timedelta, timezone

import pytest

from ags.ctrader_tokens import TokenRefreshError, refresh_if_due

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class FakeSecretStore:
    def __init__(self, **values):
        self.values = values
        self.writes = 0

    def read(self):
        return dict(self.values)

    def write(self, values):
        # Like SSM: only the keys written change.
        self.values.update(values)
        self.writes += 1


class FakeTokenEndpoint:
    def __init__(self, response=None, error=None):
        self.response = response or {"accessToken": "new-access", "refreshToken": "new-refresh", "expiresIn": 2_628_000}
        self.error = error
        self.requests = []

    def __call__(self, params):
        self.requests.append(params)
        if self.error:
            raise self.error
        return self.response


def _store(expires_in_days):
    return FakeSecretStore(
        CTRADER_CLIENT_ID="id", CTRADER_CLIENT_SECRET="secret", CTRADER_ACCESS_TOKEN="old-access",
        CTRADER_REFRESH_TOKEN="old-refresh", DEEPSEEK_API_KEY="untouched",
        CTRADER_ACCESS_TOKEN_EXPIRES_AT=(NOW + timedelta(days=expires_in_days)).isoformat(),
    )


def test_token_within_seven_days_of_expiry_is_refreshed_and_written_back():
    store, endpoint = _store(expires_in_days=6), FakeTokenEndpoint()

    outcome = refresh_if_due(store, endpoint, now=NOW)

    assert outcome == "refreshed"
    assert endpoint.requests == [
        {"grant_type": "refresh_token", "refresh_token": "old-refresh", "client_id": "id", "client_secret": "secret"}
    ]
    assert store.values["CTRADER_ACCESS_TOKEN"] == "new-access"
    assert store.values["CTRADER_REFRESH_TOKEN"] == "new-refresh"
    assert store.values["CTRADER_ACCESS_TOKEN_EXPIRES_AT"] == (NOW + timedelta(seconds=2_628_000)).isoformat()
    assert store.values["DEEPSEEK_API_KEY"] == "untouched"


def test_token_with_more_than_seven_days_left_is_left_alone():
    store, endpoint = _store(expires_in_days=8), FakeTokenEndpoint()

    assert refresh_if_due(store, endpoint, now=NOW) == "not_due"
    assert endpoint.requests == []
    assert store.writes == 0


@pytest.mark.parametrize(
    "endpoint",
    [
        FakeTokenEndpoint(error=ConnectionError("endpoint down")),
        FakeTokenEndpoint(response={"errorCode": "CH_ACCESS_TOKEN_INVALID", "description": "Access token is invalid"}),
    ],
    ids=["network failure", "error response"],
)
def test_failed_refresh_raises_and_leaves_the_secret_untouched(endpoint):
    store = _store(expires_in_days=1)
    before = dict(store.values)

    with pytest.raises(TokenRefreshError):
        refresh_if_due(store, endpoint, now=NOW)

    assert store.values == before
    assert store.writes == 0
