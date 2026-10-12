terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Bucket and region come from `terraform init -backend-config=...` (backend blocks cannot use
  # variables, and the region must not be hard-coded). S3-native locking: no DynamoDB table.
  backend "s3" {
    key          = "ags/terraform.tfstate"
    use_lockfile = true
  }
}

# Region is read from AWS_REGION by the provider itself.
provider "aws" {}

data "aws_caller_identity" "current" {}
