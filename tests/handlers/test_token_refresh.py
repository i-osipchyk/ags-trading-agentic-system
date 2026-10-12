import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from ags.ctrader_tokens import SecretsManagerStore, TokenRefreshError
from ags.handlers.token_refresh import handle
from tests.test_token_refresh import NOW, FakeTokenEndpoint, _store


def test_successful_refresh_reports_the_outcome_without_alerting():
    alerts = []

    result = handle({}, secret_store=_store(1), token_endpoint=FakeTokenEndpoint(), alert=alerts.append, now=NOW)

    assert result == {"outcome": "refreshed"}
    assert alerts == []


def test_failed_refresh_alerts_and_still_raises_so_the_lambda_error_alarm_fires():
    alerts = []

    with pytest.raises(TokenRefreshError):
        handle({}, secret_store=_store(1), token_endpoint=FakeTokenEndpoint(error=ConnectionError("down")),
               alert=alerts.append, now=NOW)

    assert len(alerts) == 1
    assert "token refresh failed" in alerts[0].lower()


@mock_aws
def test_secrets_manager_store_round_trips_the_whole_secret_json():
    client = boto3.client("secretsmanager", region_name="eu-west-1")
    client.create_secret(Name="ags/prod", SecretString=json.dumps({"A": "1", "B": "2"}))
    store = SecretsManagerStore(client, "ags/prod")

    store.write({**store.read(), "B": "3"})

    assert json.loads(client.get_secret_value(SecretId="ags/prod")["SecretString"]) == {"A": "1", "B": "3"}
