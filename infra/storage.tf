resource "aws_s3_bucket" "data" {
  bucket = "ags-data-${data.aws_caller_identity.current.account_id}"

  # Source of truth for the PIT store, run logs, audits and the paper book.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_ecr_repository" "main" {
  name = "ags"
}
