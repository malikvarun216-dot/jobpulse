# BLOCKED: Himalayas API is behind Cloudflare bot protection as of 2026-04-18.
# Lambda deployed but non-functional until Himalayas opens public API access.
data "archive_file" "himalayas_zip" {
  type        = "zip"
  source_file = "${path.module}/../../../ingestion/sources/himalayas/ingest_himalayas.py"
  output_path = "${path.module}/builds/ingest_himalayas.zip"
}

resource "aws_lambda_function" "himalayas" {
  function_name    = "${var.project}-ingest-himalayas-${var.env}"
  filename         = data.archive_file.himalayas_zip.output_path
  source_code_hash = data.archive_file.himalayas_zip.output_base64sha256
  handler          = "ingest_himalayas.lambda_handler"
  runtime          = "python3.12"
  role             = aws_iam_role.lambda_exec.arn
  timeout          = 300
  memory_size      = 256

  environment {
    variables = {
      BRONZE_BUCKET = aws_s3_bucket.layers["bronze"].bucket
    }
  }
}

# Remotive — free public API, no key, no Cloudflare, max 4 req/day per ToS
data "archive_file" "remotive_zip" {
  type        = "zip"
  source_file = "${path.module}/../../../ingestion/sources/remotive/ingest_remotive.py"
  output_path = "${path.module}/builds/ingest_remotive.zip"
}

resource "aws_lambda_function" "remotive" {
  function_name    = "${var.project}-ingest-remotive-${var.env}"
  filename         = data.archive_file.remotive_zip.output_path
  source_code_hash = data.archive_file.remotive_zip.output_base64sha256
  handler          = "ingest_remotive.lambda_handler"
  runtime          = "python3.12"
  role             = aws_iam_role.lambda_exec.arn
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      BRONZE_BUCKET = aws_s3_bucket.layers["bronze"].bucket
    }
  }
}

# Arbeitnow — free public API, no key, EU/remote focused, works from Lambda
# NOTE: RemoteOK tested but blocked by Cloudflare bot protection from Lambda IPs (same as Himalayas)
data "archive_file" "arbeitnow_zip" {
  type        = "zip"
  source_file = "${path.module}/../../../ingestion/sources/arbeitnow/ingest_arbeitnow.py"
  output_path = "${path.module}/builds/ingest_arbeitnow.zip"
}

resource "aws_lambda_function" "arbeitnow" {
  function_name    = "${var.project}-ingest-arbeitnow-${var.env}"
  filename         = data.archive_file.arbeitnow_zip.output_path
  source_code_hash = data.archive_file.arbeitnow_zip.output_base64sha256
  handler          = "ingest_arbeitnow.lambda_handler"
  runtime          = "python3.12"
  role             = aws_iam_role.lambda_exec.arn
  timeout          = 120
  memory_size      = 256

  environment {
    variables = {
      BRONZE_BUCKET = aws_s3_bucket.layers["bronze"].bucket
    }
  }
}
