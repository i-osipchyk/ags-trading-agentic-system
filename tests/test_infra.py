"""Static invariants over infra/ and the deploy workflow.

Terraform applies automatically on push to main with no plan review, so the things that must never
silently change (destroy guards, schedules, the fixed run shape, the OIDC trust) are pinned here.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
INFRA = ROOT / "infra"


def _tf(directory: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(directory.glob("*.tf")))


def _resource(text: str, rtype: str, name: str) -> str:
    """The body of `resource "<rtype>" "<name>" { ... }` by brace matching."""
    start = re.search(rf'resource\s+"{rtype}"\s+"{name}"\s*\{{', text)
    assert start, f"missing resource {rtype}.{name}"
    depth, i = 1, start.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start.end():i]


MAIN, BOOTSTRAP = _tf(INFRA), _tf(INFRA / "bootstrap")


@pytest.mark.parametrize("text,rtype,name", [
    (MAIN, "aws_s3_bucket", "data"),
    (MAIN, "aws_secretsmanager_secret", "main"),
    (BOOTSTRAP, "aws_s3_bucket", "state"),
])
def test_irreplaceable_resources_cannot_be_destroyed(text, rtype, name):
    assert re.search(r"prevent_destroy\s*=\s*true", _resource(text, rtype, name))


def test_data_bucket_is_versioned_and_has_no_lifecycle_expiry():
    assert 'status = "Enabled"' in _resource(MAIN, "aws_s3_bucket_versioning", "data")
    assert "aws_s3_bucket_lifecycle_configuration" not in MAIN


def test_region_is_never_hard_coded():
    for text in (MAIN, BOOTSTRAP):
        assert not re.search(r"eu-west-1", text)
        assert not re.search(r'\bregion\s*=\s*"', text)


def test_state_is_remote_with_native_locking():
    assert re.search(r"use_lockfile\s*=\s*true", MAIN)
    assert "dynamodb_table" not in MAIN


def test_bootstrap_trusts_only_the_main_branch_of_this_repo():
    assert re.search(r'default\s*=\s*"i-osipchyk/ags-trading-agentic-system"', BOOTSTRAP)
    assert "repo:${var.github_repo}:ref:refs/heads/main" in BOOTSTRAP
    assert "StringLike" not in BOOTSTRAP  # exact match on the subject, no wildcards
    assert "token.actions.githubusercontent.com" in BOOTSTRAP


def test_all_six_lambdas_use_the_one_arm64_image_with_their_own_handler():
    handlers = {
        "commodity_pipeline", "portfolio", "auditor", "digest", "token_refresh", "notify",
    }
    for name in handlers:
        body = _resource(MAIN, "aws_lambda_function", name)
        assert re.search(r'package_type\s*=\s*"Image"', body) and '"arm64"' in body
        assert f"ags.handlers.{name}.handler" in body
    assert len(re.findall(r'resource\s+"aws_lambda_function"', MAIN)) == 6


def test_lambda_timeouts_match_the_architecture():
    assert re.search(r"timeout\s*=\s*600", _resource(MAIN, "aws_lambda_function", "commodity_pipeline"))
    assert re.search(r"timeout\s*=\s*300", _resource(MAIN, "aws_lambda_function", "portfolio"))


@pytest.mark.parametrize("name,cron", [
    ("cycle", "cron(0 4,16 ? * MON-FRI *)"),
    ("digest", "cron(0 17 ? * MON-FRI *)"),
    ("auditor", "cron(0 18 ? * MON-FRI *)"),
    ("token_refresh", None),
])
def test_schedules_run_in_new_york_time(name, cron):
    body = _resource(MAIN, "aws_scheduler_schedule", name)
    assert 'schedule_expression_timezone = "America/New_York"' in body
    if cron:
        assert re.search(rf'schedule_expression\s*=\s*"{re.escape(cron)}"', body)
    else:
        assert re.search(r'schedule_expression\s*=\s*"(cron\(.* \* \* \? \*\)|rate\(1 day\))"', body)


def test_cycle_state_machine_keeps_the_fixed_run_shape():
    asl = json.loads((INFRA / "cycle.asl.json").read_text())
    states = asl["States"]
    commodities = states[asl["StartAt"]]["Result"]
    assert commodities == ["corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa"]

    mapper = next(s for s in states.values() if s["Type"] == "Map")
    assert mapper["MaxConcurrency"] == 7
    inner = mapper["ItemProcessor"]
    task = inner["States"][inner["StartAt"]]
    assert task["Retry"][0]["MaxAttempts"] == 2 and task["Retry"][0]["BackoffRate"] > 1
    catch = task["Catch"][0]
    assert catch["ErrorEquals"] == ["States.ALL"]
    assert inner["States"][catch["Next"]]["Parameters"]["failed"] is True
    assert inner["States"][catch["Next"]]["Parameters"]["call"] is None

    portfolio = states[mapper["Next"]]
    assert portfolio["Type"] == "Task" and portfolio.get("End") is True
    assert portfolio["Parameters"]["results.$"] == "$.results"


def test_breaker_parameter_and_alarms_exist():
    assert "aws_ssm_parameter" in MAIN and "breaker_reset_at" in MAIN
    assert "ExecutionsFailed" in MAIN and "aws_cloudwatch_metric_alarm" in MAIN
    assert re.search(r"lifecycle\s*\{[^}]*ignore_changes\s*=\s*\[value\]", _resource(MAIN, "aws_ssm_parameter", "breaker_reset_at"))


def test_secret_value_is_never_managed_by_terraform():
    assert "aws_secretsmanager_secret_version" not in MAIN


WORKFLOW = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()


def test_deploy_runs_only_on_push_to_main():
    assert re.search(r"on:\s*\n\s*push:\s*\n\s*branches:\s*\[main\]", WORKFLOW)
    assert "pull_request" not in WORKFLOW


def test_deploy_steps_run_in_the_documented_order():
    order = ["pytest", "docker build", "docker push", "list-executions", "terraform apply -auto-approve"]
    positions = [WORKFLOW.index(marker) for marker in order]
    assert positions == sorted(positions), "tests -> build -> push -> wait for no RUNNING execution -> apply"


def test_deploy_authenticates_with_oidc_not_stored_keys():
    assert "id-token: write" in WORKFLOW and "role-to-assume" in WORKFLOW
    assert "AWS_SECRET_ACCESS_KEY" not in WORKFLOW
