data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "ags-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.cycle.arn },
      {
        Effect = "Allow", Action = ["lambda:InvokeFunction"],
        Resource = [
          aws_lambda_function.digest.arn, aws_lambda_function.auditor.arn, aws_lambda_function.token_refresh.arn,
        ]
      },
    ]
  })
}

# All schedules are America/New_York regardless of the deployment region.
resource "aws_scheduler_schedule" "cycle" {
  name                         = "ags-cycle"
  schedule_expression          = "cron(0 4,16 ? * MON-FRI *)"
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.cycle.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ scheduled_time = "<aws.scheduler.scheduled-time>", off_schedule = false })
  }
}

resource "aws_scheduler_schedule" "digest" {
  name                         = "ags-digest"
  schedule_expression          = "cron(0 17 ? * MON-FRI *)"
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.digest.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
  }
}

resource "aws_scheduler_schedule" "auditor" {
  name                         = "ags-auditor"
  schedule_expression          = "cron(0 18 ? * MON-FRI *)"
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.auditor.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
  }
}

resource "aws_scheduler_schedule" "token_refresh" {
  name                         = "ags-token-refresh"
  schedule_expression          = "cron(0 3 * * ? *)"
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.token_refresh.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
  }
}
