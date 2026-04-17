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