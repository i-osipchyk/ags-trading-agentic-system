terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial config: bucket/key/region come from `terraform init -backend-config=...`
  # (CI: platform lambda-deploy workflow, state key ags/<env>.tfstate; locally: README).
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = var.project
      env        = var.env
      managed_by = "terraform"
      repo       = "i-osipchyk/ags-trading-agentic-system"
    }
  }
}

data "aws_caller_identity" "current" {}
