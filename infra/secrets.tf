# The secret's *value* (JSON of the .env keys + CTRADER_REFRESH_TOKEN, TELEGRAM_*) is set by hand and
# rewritten by the token_refresh Lambda, so Terraform never manages a version of it.
resource "aws_secretsmanager_secret" "main" {
  name = "ags/prod"

  lifecycle {
    prevent_destroy = true
  }
}

# Drawdown-breaker reset: `aws ssm put-parameter --name /ags/breaker_reset_at --value <ISO> --overwrite`.
resource "aws_ssm_parameter" "breaker_reset_at" {
  name  = "/ags/breaker_reset_at"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}
