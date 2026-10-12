resource "aws_sns_topic" "alarms" {
  name = "ags-alarms"
}

resource "aws_sns_topic_subscription" "notify" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.notify.arn
}

resource "aws_lambda_permission" "sns_notify" {
  statement_id  = "AllowSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notify.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alarms.arn
}

resource "aws_cloudwatch_metric_alarm" "cycle_failed" {
  alarm_name          = "ags-cycle-execution-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.cycle.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

locals {
  alarmed_lambdas = {
    commodity_pipeline = aws_lambda_function.commodity_pipeline.function_name
    portfolio          = aws_lambda_function.portfolio.function_name
    auditor            = aws_lambda_function.auditor.function_name
    digest             = aws_lambda_function.digest.function_name
    token_refresh      = aws_lambda_function.token_refresh.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = local.alarmed_lambdas
  alarm_name          = "ags-${each.key}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
