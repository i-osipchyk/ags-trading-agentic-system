data "aws_iam_policy_document" "sfn_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "ags-cycle-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_trust.json
}

resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.commodity_pipeline.arn, aws_lambda_function.portfolio.arn]
    }]
  })
}

resource "aws_sfn_state_machine" "cycle" {
  name     = "ags-cycle"
  role_arn = aws_iam_role.sfn.arn
  definition = templatefile("${path.module}/cycle.asl.json", {
    commodity_pipeline_arn = aws_lambda_function.commodity_pipeline.arn
    portfolio_arn          = aws_lambda_function.portfolio.arn
  })
}
