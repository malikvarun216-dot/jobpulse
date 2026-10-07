locals {
  buckets = ["bronze", "silver", "gold", "archive"]
}

resource "aws_s3_bucket" "layers" {
  for_each = toset(local.buckets)

  bucket = "${var.project}-${each.key}-${var.env}"
}

# Block all public access on every bucket
resource "aws_s3_bucket_public_access_block" "layers" {
  for_each = aws_s3_bucket.layers

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle rules — bronze only (delete after 7 days)
resource "aws_s3_bucket_lifecycle_configuration" "bronze" {
  bucket = aws_s3_bucket.layers["bronze"].id

  rule {
    id     = "bronze-expire"
    status = "Enabled"

    filter {} # applies to all objects

    expiration {
      days = 7
    }
  }
}

# Lifecycle rules — silver (transition to IA after 30 days)
resource "aws_s3_bucket_lifecycle_configuration" "silver" {
  bucket = aws_s3_bucket.layers["silver"].id

  rule {
    id     = "silver-to-ia"
    status = "Enabled"

    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }
}

# Gold — expire Athena query-result CSVs after 7 days (1.5 GB of them in Oct 2026).
# Enabled 2026-10-08 only after runbook §18 passed: all 4 dbt tables live under gold/models/
# since Chat 25 (before, they sat in athena-results/tables/<uuid>/ and this rule would have
# deleted the gold layer). Also cleans the orphaned athena-results/tables/* folders.
# Before re-pointing any table at athena-results/, remove this rule.
resource "aws_s3_bucket_lifecycle_configuration" "gold" {
  bucket = aws_s3_bucket.layers["gold"].id

  rule {
    id     = "athena-results-expire"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }
}

# Lifecycle rules — archive (Glacier after 180 days)
resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  bucket = aws_s3_bucket.layers["archive"].id

  rule {
    id     = "archive-to-glacier"
    status = "Enabled"

    filter {}

    transition {
      days          = 180
      storage_class = "GLACIER_IR"
    }
  }
}