"""Static invariants over infra/ and the CI / deploy workflows.

Terraform applies from GitHub Actions after the `live` approval, so the things that must never
silently change (destroy guards, schedules, the fixed run shape, platform pins, the OIDC deploy
path) are pinned here.
"""

import json
import re
from pathlib import Path

import pytest

from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES

ROOT = Path(__file__).parent.parent
INFRA = ROOT / "infra"
PLATFORM_REF = "v0.1.2"


def _tf(directory: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(directory.glob("*.tf")))


def _block(text: str, header: str) -> str:
    """The body of the first `<header> { ... }` by brace matching."""
    start = re.search(header + r"\s*\{", text)
    assert start, f"missing {header}"
    depth, i = 1, start.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start.end():i]


def _resource(text: str, rtype: str, name: str) -> str:
    return _block(text, rf'resource\s+"{rtype}"\s+"{name}"')


def _module(name: str) -> str:
    return _block(MAIN, rf'module\s+"{name}"')


MAIN = _tf(INFRA)


def _function(name: str) -> str:
    line = re.search(rf"^\s*{re.escape(name)}\s*=\s*\{{(.*)\}}\s*$", _block(_module("app"), r"functions\s*="), re.M)
    assert line, f"missing function {name}"
    return line.group(1)


def test_no_bootstrap_or_hand_rolled_lambda_stack_left_in_the_project():
    # State bucket, OIDC, deploy role and ECR are the platform bootstrap's; functions, roles,
    # schedules and alarms are the lambda-app module's.
    assert not (INFRA / "bootstrap").exists()
    for rtype in ("aws_lambda_function", "aws_s3_bucket", "aws_ecr_repository", "aws_sns_topic",
                  "aws_iam_openid_connect_provider", "aws_secretsmanager_secret"):
        assert not re.search(rf'resource\s+"{rtype}"', MAIN), rtype


@pytest.mark.parametrize("module,path", [("data", "data-bucket"), ("app", "lambda-app")])
def test_platform_modules_are_pinned_to_a_release(module, path):
    source = re.search(r'source\s*=\s*"([^"]+)"', _module(module)).group(1)
    assert source == f"git::https://github.com/i-osipchyk/platform.git//terraform/modules/{path}?ref={PLATFORM_REF}"


def test_data_bucket_comes_from_the_platform_module_with_no_expiry():
    # The module is versioned + prevent_destroy; noncurrent_version_days would expire history.
    assert "noncurrent_version_days" not in _module("data")
    assert "aws_s3_bucket_lifecycle_configuration" not in MAIN
    assert "write_bucket_arns = [module.data.arn]" in _module("app")


def test_region_comes_only_from_the_region_variable_defaulting_to_eu_west_1():
    variables = (INFRA / "variables.tf").read_text()
    assert re.search(r'variable\s+"region"\s*\{[^}]*default\s*=\s*"eu-west-1"', variables)
    assert MAIN.count("eu-west-1") == 1
    assert re.search(r"provider\s+\"aws\"\s*\{\s*region\s*=\s*var\.region", MAIN)


def test_state_is_remote_encrypted_with_native_locking_and_a_partial_config():
    backend = _block(MAIN, r'backend\s+"s3"')
    assert re.search(r"use_lockfile\s*=\s*true", backend) and re.search(r"encrypt\s*=\s*true", backend)
    assert "bucket" not in backend and "key" not in backend  # passed at init: ags/<env>.tfstate
    assert "dynamodb_table" not in MAIN


def test_default_tags_identify_the_project():
    tags = _block(MAIN, r"default_tags")
    assert "project    = var.project" in tags and "env        = var.env" in tags
    assert re.search(r'variable\s+"project"\s*\{[^}]*default\s*=\s*"ags"', MAIN)


def test_only_live_is_deployed():
    assert sorted(p.name for p in (INFRA / "env").glob("*.tfvars")) == ["live.tfvars"]
    assert re.search(r'^env\s*=\s*"live"', (INFRA / "env" / "live.tfvars").read_text(), re.M)


@pytest.mark.parametrize("name,module_path", [
    ("commodity-pipeline", "commodity_pipeline"), ("portfolio", "portfolio"), ("auditor", "auditor"),
    ("digest", "digest"),
])
def test_each_function_runs_its_own_handler_from_the_one_image(name, module_path):
    assert f'handler = "ags.handlers.{module_path}.handler"' in _function(name)


def test_exactly_the_four_functions_and_no_alarm_forwarder_or_token_refresher():
    # The cTrader tokens are the shared pair the platform refreshes (conventions.md § Secrets).
    functions = re.findall(r"^\s*([a-z-]+)\s*=\s*\{", _block(_module("app"), r"functions\s*="), re.M)
    assert sorted(functions) == ["auditor", "commodity-pipeline", "digest", "portfolio"]


def test_lambda_timeouts_match_the_architecture():
    assert "timeout = 600" in _function("commodity-pipeline")
    assert "timeout = 300" in _function("portfolio")
    assert "retries" not in _block(_module("app"), r"functions\s*=")  # module default 0


def test_no_reserved_concurrency_while_the_account_limit_is_10():
    # AWS keeps 10 unreserved, so at the default account limit of 10 any reservation fails the
    # deploy. Raise the quota first (platform docs/deployment.md § Troubleshooting).
    assert "reserved_concurrency" not in _block(_module("app"), r"functions\s*=")


@pytest.mark.parametrize("name,cron", [
    ("digest", "cron(0 5,17 ? * MON-FRI *)"),  # an hour after each cycle starts
    ("auditor", "cron(0 18 ? * MON-FRI *)"),
])
def test_function_schedules_run_in_new_york_time(name, cron):
    line = re.search(rf"^\s*{re.escape(name)}\s*=\s*\{{(.*)\}}\s*$", _block(_module("app"), r"schedules\s*="), re.M)
    assert line, f"missing schedule {name}"
    assert f'expression = "{cron}"' in line.group(1)
    assert 'timezone = "America/New_York"' in line.group(1)


def test_functions_get_no_cost_explorer_access():
    # The digest reports LLM spend only; AWS cost is left to the platform budget.
    assert "ce:" not in _tf(INFRA)


def test_cycle_schedule_starts_the_state_machine_twice_each_weekday_in_new_york_time():
    body = _resource(MAIN, "aws_scheduler_schedule", "cycle")
    assert 'schedule_expression          = "cron(0 4,16 ? * MON-FRI *)"' in body
    assert 'schedule_expression_timezone = "America/New_York"' in body
    assert "arn      = aws_sfn_state_machine.cycle.arn" in body
    # The placeholder must reach Scheduler unescaped: jsonencode() would turn <> into \u003c\u003e.
    target_input = re.search(r'^\s*input\s*=\s*(.+)$', body, re.M).group(1)
    assert "jsonencode" not in target_input
    assert json.loads(json.loads(target_input)) == {
        "scheduled_time": "<aws.scheduler.scheduled-time>", "off_schedule": False,
    }
    # In the module's group (the deploy role manages schedules only there) and under the same switch.
    assert "group_name                   = local.name" in body
    assert "var.schedules_enabled" in body
    assert "schedules_enabled = var.schedules_enabled" in _module("app")


def test_names_follow_the_project_env_prefix_the_deploy_role_is_scoped_to():
    assert 'name     = "${local.name}-cycle"' in _resource(MAIN, "aws_sfn_state_machine", "cycle")
    assert 'name              = "/aws/vendedlogs/states/${local.name}-cycle"' in _resource(MAIN, "aws_cloudwatch_log_group", "cycle")
    for role in ("sfn", "cycle_scheduler"):
        assert re.search(r'name\s*=\s*"\$\{local\.name\}-', _resource(MAIN, "aws_iam_role", role))


def test_cycle_state_machine_keeps_the_fixed_run_shape():
    asl = json.loads((INFRA / "cycle.asl.json").read_text())
    states = asl["States"]
    commodities = states[asl["StartAt"]]["Result"]
    assert commodities == ["corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa"]
    # The portfolio stage holds any commodity it gets no result for, so a renamed one would be held forever.
    assert commodities == list(CTRADER_SYMBOL_NAMES)

    mapper = next(s for s in states.values() if s["Type"] == "Map")
    assert mapper["MaxConcurrency"] == 7
    inner = mapper["ItemProcessor"]
    task = inner["States"][inner["StartAt"]]
    # A late start only gets later on retry, so it goes straight to MarkFailed; it must precede States.ALL.
    assert task["Retry"][0] == {"ErrorEquals": ["LateStartError"], "MaxAttempts": 0}
    assert task["Retry"][-1]["ErrorEquals"] == ["States.ALL"]
    assert task["Retry"][-1]["MaxAttempts"] == 2 and task["Retry"][-1]["BackoffRate"] > 1
    catch = task["Catch"][0]
    assert catch["ErrorEquals"] == ["States.ALL"]
    assert inner["States"][catch["Next"]]["Parameters"]["failed"] is True
    assert inner["States"][catch["Next"]]["Parameters"]["call"] is None

    portfolio = states[mapper["Next"]]
    assert portfolio["Type"] == "Task" and portfolio.get("End") is True
    assert portfolio["Parameters"]["results.$"] == "$.results"


def test_the_drawdown_breaker_trips_at_a_40_percent_drawdown():
    assert re.search(r'DRAWDOWN_BREAKER\s*=\s*"0.60"', _block(MAIN, r'variable\s+"limits"'))


def test_breaker_parameter_lives_under_the_env_path_and_terraform_ignores_its_value():
    body = _resource(MAIN, "aws_ssm_parameter", "breaker_reset_at")
    assert 'name  = "/${var.project}/${var.env}/breaker-reset-at"' in body
    assert 'type  = "String"' in body
    assert re.search(r"lifecycle\s*\{[^}]*ignore_changes\s*=\s*\[value\]", body)
    assert "AGS_BREAKER_PARAM = aws_ssm_parameter.breaker_reset_at.name" in _module("app")


def test_failed_cycles_alarm_to_the_shared_platform_topic():
    body = _resource(MAIN, "aws_cloudwatch_metric_alarm", "cycle_failed")
    assert 'metric_name         = "ExecutionsFailed"' in body
    assert "alarm_actions       = [module.app.alarm_topic_arn]" in body


def test_secret_values_are_never_managed_by_terraform():
    assert "SecureString" not in MAIN
    assert "aws_secretsmanager" not in MAIN
    # Only the platform writes the shared cTrader tokens; this project writes no SSM at all.
    assert not re.search(r"^\s*ssm_write\s*=", _module("app"), re.M)


CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
DEPLOY = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()


def test_ci_runs_the_platform_python_workflow_on_every_push():
    assert re.search(r"on:\s*\n\s*push:", CI)
    assert f"uses: i-osipchyk/platform/.github/workflows/python-ci.yml@{PLATFORM_REF}" in CI


def test_deploy_runs_only_after_ci_succeeds_on_main():
    assert re.search(r"workflow_run:\s*\n\s*workflows: \[CI\]\s*\n\s*types: \[completed\]\s*\n\s*branches: \[main\]", DEPLOY)
    assert "github.event.workflow_run.conclusion == 'success'" in DEPLOY
    assert "pull_request" not in DEPLOY and "push:" not in DEPLOY


def test_deploy_uses_the_platform_workflow_for_live_and_waits_for_running_cycles():
    assert f"uses: i-osipchyk/platform/.github/workflows/lambda-deploy.yml@{PLATFORM_REF}" in DEPLOY
    assert "project: ags" in DEPLOY and "environment: live" in DEPLOY
    assert "pre-apply: scripts/wait_for_cycle.sh ags-live-cycle" in DEPLOY
    assert "list-executions" in (ROOT / "scripts" / "wait_for_cycle.sh").read_text()


def test_deploy_authenticates_with_oidc_not_stored_keys():
    assert "id-token: write" in DEPLOY
    assert "AWS_SECRET_ACCESS_KEY" not in DEPLOY

