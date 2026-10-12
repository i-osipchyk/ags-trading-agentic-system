from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from ags.config import Config
from ags.handlers.runtime import build_runtime
from ags.storage import S3Storage

CONFIG = Config.from_mapping({
    "DEEPSEEK_API_KEY": "ds", "CTRADER_CLIENT_ID": "id", "CTRADER_CLIENT_SECRET": "s",
    "CTRADER_ACCESS_TOKEN": "a", "CTRADER_ACCOUNT_ID": "7", "TAVILY_API_KEY": "tv",
})


class FakeSsm:
    def __init__(self, value=None):
        self.value = value
        self.names = []

    def get_parameter(self, Name):
        self.names.append(Name)
        if self.value is None:
            raise self.exceptions.ParameterNotFound()
        return {"Parameter": {"Value": self.value}}

    class exceptions:
        class ParameterNotFound(Exception):
            pass


def _build(env, ssm=None):
    return build_runtime(
        env, config=CONFIG, ssm_client=ssm or FakeSsm(),
        chat_factory=lambda config: ("chat", config.deepseek_api_key),
        market_factory=lambda config: ("market", config.ctrader_account_id),
    )


@mock_aws
def test_runtime_is_wired_from_env_secret_and_ssm():
    boto3.client("s3", region_name="eu-west-1").create_bucket(
        Bucket="ags-data", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
    )
    env = {"AGS_BUCKET": "ags-data", "AGS_BREAKER_PARAM": "/ags/live/breaker-reset-at", "AWS_REGION": "eu-west-1",
           "STARTING_EQUITY_USD": "25000", "GROSS_LEVERAGE_CAP": "2.5"}

    ssm = FakeSsm("2026-06-01T00:00:00+00:00")
    runtime = _build(env, ssm)

    assert isinstance(runtime.storage, S3Storage)
    assert runtime.chat_client == ("chat", "ds")
    assert runtime.market_client == ("market", 7)
    assert runtime.portfolio_config.starting_equity == 25_000
    assert runtime.portfolio_config.guardrails.gross_leverage_cap == 2.5
    assert runtime.portfolio_config.guardrails.per_commodity_cap == 1.0  # architecture default
    assert runtime.breaker_reset_at() == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert ssm.names == ["/ags/live/breaker-reset-at"]
    assert runtime.now().tzinfo is not None


@mock_aws
def test_breaker_reset_is_none_when_the_parameter_is_unset():
    env = {"AGS_BUCKET": "ags-data", "AGS_BREAKER_PARAM": "/ags/live/breaker-reset-at", "AWS_REGION": "eu-west-1"}

    assert _build(env, FakeSsm(None)).breaker_reset_at() is None


@mock_aws
def test_runtime_alert_is_built_from_the_config():
    sent = []
    env = {"AGS_BUCKET": "ags-data", "AGS_BREAKER_PARAM": "/ags/live/breaker-reset-at", "AWS_REGION": "eu-west-1"}

    runtime = build_runtime(
        env, config=CONFIG, ssm_client=FakeSsm(), chat_factory=lambda c: None,
        market_factory=lambda c: None, alert_factory=lambda config: lambda message: sent.append((config.tavily_api_key, message)),
    )
    runtime.alert("boom")

    assert sent == [("tv", "boom")]
