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
# DISABLED until the gold tables have moved: until Chat 25 the dbt tables lived in
# athena-results/tables/<uuid>/, and enabling this then would delete the gold layer.
# Flip to "Enabled" only after the Glue catalog shows all 4 dbt tables under gold/models/
# (check: docs/runbook.md → "Gold tables location"). It also cleans the orphaned table folders.
resource "aws_s3_bucket_lifecycle_configuration" "gold" {
  bucket = aws_s3_bucket.layers["gold"].id

  rule {
    id     = "athena-results-expire"
    status = "Disabled"

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