from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from ags.ctrader_tokens import SsmSecretStore, TokenRefreshError
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
def test_ssm_store_reads_the_env_path_and_writes_only_the_given_keys():
    client = boto3.client("ssm", region_name="eu-west-1")
    client.put_parameter(Name="/ags/live/ctrader-refresh-token", Value="old", Type="SecureString")
    client.put_parameter(Name="/ags/live/deepseek-api-key", Value="ds", Type="SecureString")
    client.put_parameter(Name="/ags/demo/ctrader-refresh-token", Value="other-env", Type="SecureString")
    store = SsmSecretStore(client, "/ags/live")

    assert store.read() == {"CTRADER_REFRESH_TOKEN": "old", "DEEPSEEK_API_KEY": "ds"}

    store.write({"CTRADER_REFRESH_TOKEN": "new", "CTRADER_ACCESS_TOKEN_EXPIRES_AT": "2026-07-01T00:00:00+00:00"})

    assert store.read() == {
        "CTRADER_REFRESH_TOKEN": "new", "DEEPSEEK_API_KEY": "ds",
        "CTRADER_ACCESS_TOKEN_EXPIRES_AT": "2026-07-01T00:00:00+00:00",
    }
    parameter = client.get_parameter(Name="/ags/live/ctrader-refresh-token")["Parameter"]
    assert parameter["Type"] == "SecureString"
    assert client.get_parameter(Name="/ags/demo/ctrader-refresh-token", WithDecryption=True)["Parameter"]["Value"] == "other-env"
