# Applied once, by hand, with local state:  cd infra/bootstrap && terraform init && terraform apply
# The AWS provider reads the region from AWS_REGION; nothing here hard-codes it.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {}

data "aws_caller_identity" "current" {}

variable "github_repo" {
  type    = string
  default = "i-osipchyk/ags-trading-agentic-system"
}

# --- Terraform state -------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = "ags-tfstate-${data.aws_caller_identity.current.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- GitHub OIDC: no long-lived AWS keys in GitHub ---------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "ags-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust.json
}

# Terraform creates and manages every service below, so the actions are service-wide; the
# trust policy above (main branch of this repo only) is what scopes who can use them.
data "aws_iam_policy_document" "deploy" {
  statement {
    actions = [
      "s3:*", "lambda:*", "ecr:*", "states:*", "scheduler:*", "secretsmanager:*", "ssm:*",
      "cloudwatch:*", "logs:*", "sns:*", "iam:*", "events:*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "terraform-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "state_bucket" {
  value = aws_s3_bucket.state.id
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}

# --- Image registry --------------------------------------------------------
# Lives here, not in the main stack: the deploy workflow pushes the image *before* `terraform apply`,
# so on the first deploy the repository has to exist already.

resource "aws_ecr_repository" "main" {
  name                 = "ags"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "main" {
  repository = aws_ecr_repository.main.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

output "ecr_repository" {
  value = aws_ecr_repository.main.name
}
