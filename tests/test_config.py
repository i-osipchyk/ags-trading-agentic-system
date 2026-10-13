import os

import boto3
import pytest
from moto import mock_aws

import ags.config
from ags.config import DEFAULT_AWS_REGION, Config, ConfigError, load_env

_real_load_shared_ctrader = ags.config._load_shared_ctrader  # conftest stubs it per test


def test_from_env_raises_on_missing_required_key(monkeypatch):
    # Stubbed so this test doesn't depend on whether the developer's own
    # .env happens to have DEEPSEEK_API_KEY filled in.
    monkeypatch.setattr(ags.config, "load_dotenv", lambda path=None: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CTRADER_CLIENT_ID", "id")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "token")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "123")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")

    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        Config.from_env()


def test_aws_region_defaults_to_eu_west_1(monkeypatch, tmp_path):
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(key, raising=False)
    load_env(tmp_path / "missing.env")
    assert os.environ["AWS_REGION"] == DEFAULT_AWS_REGION == "eu-west-1"
    assert os.environ["AWS_DEFAULT_REGION"] == "eu-west-1"


def test_aws_region_env_wins(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    load_env(tmp_path / "missing.env")
    assert os.environ["AWS_DEFAULT_REGION"] == "eu-central-1"


_SECRETS = {
    "deepseek-api-key": "ds",
    "ctrader-account-id": "123",
    "tavily-api-key": "tv",
    "telegram-bot-token": "bot",
    "telegram-chat-id": "chat",
}
_SHARED = {
    "langfuse-public-key": "pk",
    "ctrader-client-id": "id",
    "ctrader-client-secret": "secret",
    "ctrader-access-token": "access",
    "ctrader-refresh-token": "refresh",
    "ctrader-access-token-expires-at": "2026-11-12T03:00:00+00:00",
}


@pytest.fixture
def on_lambda(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "ags-live-portfolio")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("SECRETS_PATH", "/ags/live")
    monkeypatch.setenv("SHARED_SECRETS_PATH", "/platform/shared")
    for name in {**_SECRETS, **_SHARED}:
        monkeypatch.delenv(name.replace("-", "_").upper(), raising=False)
    with mock_aws():
        ssm = boto3.client("ssm", region_name="eu-west-1")
        for name, value in _SECRETS.items():
            ssm.put_parameter(Name=f"/ags/live/{name}", Value=value, Type="SecureString")
        for name, value in _SHARED.items():
            ssm.put_parameter(Name=f"/platform/shared/{name}", Value=value, Type="SecureString")
        yield ssm


def test_on_lambda_config_comes_from_the_env_ssm_path(on_lambda):
    config = Config.from_env()

    assert config.deepseek_api_key == "ds"
    assert config.ctrader_account_id == 123
    assert config.telegram_bot_token == "bot"
    assert config.telegram_chat_id == "chat"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk"  # shared keys too


def test_ctrader_credentials_come_from_the_shared_platform_pair(on_lambda):
    config = Config.from_env()

    assert (config.ctrader_client_id, config.ctrader_client_secret) == ("id", "secret")
    assert config.ctrader_access_token == "access"


@pytest.mark.parametrize("refresh", [False, True])
def test_shared_ctrader_token_wins_over_a_stale_project_copy(on_lambda, refresh):
    on_lambda.put_parameter(Name="/ags/live/ctrader-access-token", Value="stale", Type="SecureString")

    assert Config.from_env(refresh=refresh).ctrader_access_token == "access"


def test_refresh_picks_up_a_token_rotated_after_the_cold_start(on_lambda):
    load_env()
    on_lambda.put_parameter(
        Name="/platform/shared/ctrader-access-token", Value="rotated", Type="SecureString", Overwrite=True
    )

    assert Config.from_env().ctrader_access_token == "access"  # a plain reload keeps what's loaded
    assert Config.from_env(refresh=True).ctrader_access_token == "rotated"


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(ags.config, "_load_shared_ctrader", _real_load_shared_ctrader)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.delenv("SHARED_SECRETS_PATH", raising=False)
    for name in _SHARED:
        monkeypatch.delenv(name.replace("-", "_").upper(), raising=False)
    with mock_aws():
        ssm = boto3.client("ssm", region_name="eu-west-1")
        for name, value in _SHARED.items():
            ssm.put_parameter(Name=f"/platform/shared/{name}", Value=value, Type="SecureString")
        yield tmp_path / "missing.env"


def test_local_run_reads_the_shared_ctrader_pair_from_ssm(local):
    load_env(local)

    assert os.environ["CTRADER_CLIENT_ID"] == "id"
    assert os.environ["CTRADER_ACCESS_TOKEN"] == "access"
    assert "CTRADER_REFRESH_TOKEN" not in os.environ  # nothing here refreshes, so nothing needs it
    assert "LANGFUSE_PUBLIC_KEY" not in os.environ  # only the cTrader keys; .env decides the rest


def test_local_dotenv_ctrader_values_win(local, monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "from-dotenv")
    load_env(local)

    assert os.environ["CTRADER_ACCESS_TOKEN"] == "from-dotenv"
    assert os.environ["CTRADER_CLIENT_ID"] == "id"


def test_missing_required_parameter_raises(on_lambda):
    on_lambda.delete_parameter(Name="/ags/live/tavily-api-key")

    with pytest.raises(ConfigError, match="TAVILY_API_KEY"):
        Config.from_env()
