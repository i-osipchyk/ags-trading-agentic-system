# The twice-daily cycle: seven commodity pipelines in parallel, then the portfolio stage
# (cycle.asl.json). Project-specific, so it sits next to the module (platform docs/adoption.md).

data "aws_iam_policy_document" "sfn_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name}-cycle-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_trust.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid     = "InvokeCycleFunctions"
    actions = ["lambda:InvokeFunction"]
    resources = [
      module.app.function_arns["commodity-pipeline"],
      module.app.function_arns["portfolio"],
    ]
  }

  # Vended-logs delivery for the execution log; these actions take no resource scoping.
  statement {
    sid = "ExecutionLogs"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${local.name}-cycle-sfn"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_cloudwatch_log_group" "cycle" {
  name              = "/aws/vendedlogs/states/${local.name}-cycle"
  retention_in_days = 90
}

resource "aws_sfn_state_machine" "cycle" {
  name     = "${local.name}-cycle"
  role_arn = aws_iam_role.sfn.arn
  definition = templatefile("${path.module}/cycle.asl.json", {
    commodity_pipeline_arn = module.app.function_arns["commodity-pipeline"]
    portfolio_arn          = module.app.function_arns["portfolio"]
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.cycle.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.sfn]
}

# --- schedule: 04:00 and 16:00 ET, Monday-Friday ---------------------------------------------

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "cycle_scheduler" {
  name               = "${local.name}-cycle-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "cycle_scheduler" {
  name = "${local.name}-start-cycle"
  role = aws_iam_role.cycle_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.cycle.arn
    }]
  })
}

resource "aws_scheduler_schedule" "cycle" {
  name = "cycle"
  # The module's schedule group (<project>-<env>); the deploy role manages schedules only there.
  group_name                   = local.name
  schedule_expression          = "cron(0 4,16 ? * MON-FRI *)"
  schedule_expression_timezone = "America/New_York"
  state                        = var.schedules_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.cycle.arn
    role_arn = aws_iam_role.cycle_scheduler.arn
    # A literal string, not jsonencode(): jsonencode escapes < and > to \u003c / \u003e, Scheduler
    # then doesn't recognise the placeholder, and the state machine gets it verbatim.
    input = "{\"scheduled_time\": \"<aws.scheduler.scheduled-time>\", \"off_schedule\": false}"

    # Retries of the StartExecution call only. A cycle that still starts > 1 h late aborts itself.
    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 2
    }
  }

  depends_on = [module.app] # creates the schedule group
}

# --- alarm -> platform-alarms -> Telegram ----------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "cycle_failed" {
  alarm_name          = "${local.name}-cycle-failed"
  alarm_description   = "${var.project} (${var.env}): a cycle execution failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.cycle.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [module.app.alarm_topic_arn]
  ok_actions          = [module.app.alarm_topic_arn]
}
