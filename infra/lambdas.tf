locals {
  image_uri = "${data.aws_ecr_repository.main.repository_url}:${var.image_tag}"

  common_env = merge(var.limits, {
    AGS_SECRET_ID                 = aws_secretsmanager_secret.main.name
    AGS_BUCKET                    = aws_s3_bucket.data.id
    AGS_BREAKER_PARAM             = aws_ssm_parameter.breaker_reset_at.name
    LLM_PRICE_CACHE_HIT_PER_MTOK  = tostring(var.llm_prices_per_mtok.cache_hit)
    LLM_PRICE_CACHE_MISS_PER_MTOK = tostring(var.llm_prices_per_mtok.cache_miss)
    LLM_PRICE_OUTPUT_PER_MTOK     = tostring(var.llm_prices_per_mtok.output)
  })
}

resource "aws_lambda_function" "commodity_pipeline" {
  function_name = "ags-commodity-pipeline"
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  role          = aws_iam_role.lambda.arn
  timeout       = 600
  memory_size   = 2048

  image_config {
    command = ["ags.handlers.commodity_pipeline.handler"]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "portfolio" {
  function_name = "ags-portfolio"
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  role          = aws_iam_role.lambda.arn
  timeout       = 300
  memory_size   = 1024

  image_config {
    command = ["ags.handlers.portfolio.handler"]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "auditor" {
  function_name = "ags-auditor"
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  role          = aws_iam_role.lambda.arn
  timeout       = 900
  memory_size   = 1024

  image_config {
    command = ["ags.handlers.auditor.handler"]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "digest" {
  function_name = "ags-digest"
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  role          = aws_iam_role.lambda.arn
  timeout       = 120
  memory_size   = 512

  image_config {
    command = ["ags.handlers.digest.handler"]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "token_refresh" {
  function_name = "ags-token-refresh"
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  role          = aws_iam_role.token_refresh.arn
  timeout       = 60
  memory_size   = 256

  image_config {
    command = ["ags.handlers.token_refresh.handler"]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "notify" {
  function_name = "ags-notify"
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  role          = aws_iam_role.lambda.arn
  timeout       = 30
  memory_size   = 256

  image_config {
    command = ["ags.handlers.notify.handler"]
  }

  environment {
    variables = local.common_env
  }
}
