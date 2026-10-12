# The data bucket and the six Lambdas, on the platform modules (platform docs/conventions.md).
# Step Functions, the cycle schedule and the breaker parameter sit next to them in cycle.tf.

locals {
  name = "${var.project}-${var.env}"
}

# Source of truth for the PIT store, run logs, audits and the paper book. Versioned, private,
# prevent_destroy; the function role gets no DeleteObject, so stored data is append-only.
module "data" {
  source  = "git::https://github.com/i-osipchyk/platform.git//terraform/modules/data-bucket?ref=v0.1.1"
  project = var.project
  env     = var.env
}

module "app" {
  source    = "git::https://github.com/i-osipchyk/platform.git//terraform/modules/lambda-app?ref=v0.1.1"
  project   = var.project
  env       = var.env
  image_uri = var.image_uri

  # retries stay 0 (module default): the cycle Lambdas are invoked synchronously by the state
  # machine, which owns retries; the rest are idempotent scans that the next run catches up on.
  #
  # No reserved_concurrency: the account's Lambda concurrency limit is 10, and AWS keeps 10
  # unreserved, so any reservation fails (platform docs/deployment.md § Troubleshooting). The
  # paper book still has one writer: portfolio runs only as the last step of a cycle execution,
  # cycles are 12 h apart, and the deploy waits for running executions.
  functions = {
    commodity-pipeline = { handler = "ags.handlers.commodity_pipeline.handler", timeout = 600, memory = 2048 }
    portfolio          = { handler = "ags.handlers.portfolio.handler", timeout = 300, memory = 1024 }
    auditor            = { handler = "ags.handlers.auditor.handler", timeout = 900, memory = 1024 }
    digest             = { handler = "ags.handlers.digest.handler", timeout = 120, memory = 512 }
    token-refresh      = { handler = "ags.handlers.token_refresh.handler", timeout = 60, memory = 256 }
  }

  # America/New_York regardless of the deployment region. The twice-daily cycle starts the state
  # machine, which the module's scheduler role can't, so it lives in cycle.tf.
  schedules = {
    digest        = { function = "digest", expression = "cron(0 17 ? * MON-FRI *)", timezone = "America/New_York" }
    auditor       = { function = "auditor", expression = "cron(0 18 ? * MON-FRI *)", timezone = "America/New_York" }
    token-refresh = { function = "token-refresh", expression = "cron(0 3 * * ? *)", timezone = "America/New_York" }
  }
  schedules_enabled = var.schedules_enabled

  environment = merge(var.limits, {
    AGS_BUCKET        = module.data.name
    AGS_BREAKER_PARAM = aws_ssm_parameter.breaker_reset_at.name
  })
  write_bucket_arns = [module.data.arn]
  ssm_write         = true # token-refresh rotates the cTrader tokens under /ags/<env>/

  extra_policy_json = data.aws_iam_policy_document.functions_extra.json
}

data "aws_iam_policy_document" "functions_extra" {
  statement {
    sid       = "DigestAwsCost"
    actions   = ["ce:GetCostAndUsage"]
    resources = ["*"]
  }
}

# Drawdown-breaker reset, read by the portfolio stage on every cycle:
#   aws ssm put-parameter --name /ags/<env>/breaker-reset-at --value <ISO> --overwrite
# A plain String (not a secret); Terraform creates it and then ignores its value.
resource "aws_ssm_parameter" "breaker_reset_at" {
  name  = "/${var.project}/${var.env}/breaker-reset-at"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}
