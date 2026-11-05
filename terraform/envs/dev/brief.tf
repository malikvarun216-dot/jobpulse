# Weekly AI brief on a schedule (Chat 32 part 2).
# EventBridge (Monday 08:30 IST) → Lambda genai/brief_lambda.py → Athena facts → Nova Pro on Bedrock
# → faithfulness check → s3://jobpulse-gold-dev/briefs/week_end=<d>/. ≈ $0.005 per brief.
# Code is deployed by CI (deploy.yml); this file creates the function once and ignores its code.

data "aws_caller_identity" "current" {}

locals {
  brief_name  = "${var.project}-weekly-brief-${var.env}"
  brief_model = "apac.amazon.nova-pro-v1:0" # same as genai/weekly_brief.py MODEL
  account_id  = data.aws_caller_identity.current.account_id
}

# First package only — CI rebuilds the same layout on every push (deploy.yml → "Deploy weekly brief Lambda").
# No genai/__init__.py here: Python 3 imports a folder without one (namespace package).
data "archive_file" "brief_zip" {
  type        = "zip"
  output_path = "${path.module}/builds/weekly_brief.zip"

  source {
    content  = file("${path.module}/../../../genai/brief_lambda.py")
    filename = "genai/brief_lambda.py"
  }
  source {
    content  = file("${path.module}/../../../genai/weekly_brief.py")
    filename = "genai/weekly_brief.py"
  }
  source {
    content  = file("${path.module}/../../../genai/brief_facts.py")
    filename = "genai/brief_facts.py"
  }
  source {
    content  = file("${path.module}/../../../dashboard/streamlit/athena_client.py")
    filename = "athena_client.py"
  }
}

# ---------------------------------------------------------------------------
# IAM — its own role, so the ingestors' role does not gain Athena or Bedrock
# ---------------------------------------------------------------------------

resource "aws_iam_role" "brief_exec" {
  name               = "${local.brief_name}-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json # iam.tf: only Lambda can assume it
}

data "aws_iam_policy_document" "brief_permissions" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.brief.arn}:*"]
  }

  # The one model, through the apac inference profile. A cross-region profile sends the request to a
  # model in one of its regions, so both the profile and the model in every region must be allowed.
  statement {
    sid     = "BedrockNovaPro"
    actions = ["bedrock:InvokeModel"] # the Converse API is authorised as InvokeModel
    resources = [
      "arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/${local.brief_model}",
      "arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0",
    ]
  }

  statement {
    sid = "AthenaWorkgroup"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = [aws_athena_workgroup.main.arn]
  }

  # Read-only catalog: Athena looks up table schemas and partitions as the caller
  statement {
    sid     = "GlueCatalogRead"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"]
    resources = [
      "arn:aws:glue:${var.aws_region}:${local.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${local.account_id}:database/${aws_glue_catalog_database.silver.name}",
      "arn:aws:glue:${var.aws_region}:${local.account_id}:database/${aws_glue_catalog_database.gold.name}",
      "arn:aws:glue:${var.aws_region}:${local.account_id}:table/${aws_glue_catalog_database.silver.name}/*",
      "arn:aws:glue:${var.aws_region}:${local.account_id}:table/${aws_glue_catalog_database.gold.name}/*",
    ]
  }

  # Athena reads the data files as the caller: silver Parquet + gold enrichment_scores
  statement {
    sid     = "ReadLakeData"
    actions = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      aws_s3_bucket.layers["silver"].arn, "${aws_s3_bucket.layers["silver"].arn}/*",
      aws_s3_bucket.layers["gold"].arn, "${aws_s3_bucket.layers["gold"].arn}/*",
    ]
  }

  # Writes: Athena's result CSVs (athena_client reads them back) and the brief files. Nothing else.
  statement {
    sid     = "WriteResultsAndBriefs"
    actions = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = [
      "${aws_s3_bucket.layers["gold"].arn}/athena-results/*",
      "${aws_s3_bucket.layers["gold"].arn}/briefs/*",
    ]
  }
}

resource "aws_iam_role_policy" "brief" {
  name   = "${local.brief_name}-policy"
  role   = aws_iam_role.brief_exec.id
  policy = data.aws_iam_policy_document.brief_permissions.json
}

# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

# Created before the function so Lambda never auto-creates it without a retention.
resource "aws_cloudwatch_log_group" "brief" {
  name              = "/aws/lambda/${local.brief_name}"
  retention_in_days = 90 # ~13 weekly runs; each log line has faithful / $ / violations
}

resource "aws_lambda_function" "brief" {
  function_name    = local.brief_name
  filename         = data.archive_file.brief_zip.output_path
  source_code_hash = data.archive_file.brief_zip.output_base64sha256
  handler          = "genai.brief_lambda.lambda_handler"
  runtime          = "python3.12"
  role             = aws_iam_role.brief_exec.arn
  timeout          = 300 # 8 Athena queries (~5 s each) + 1–2 Bedrock calls (~2 s each) ≈ 1 min; 5× headroom
  memory_size      = 512 # pandas import alone is ~150 MB

  # AWS-managed public layer "AWS SDK for pandas" 3.16.0 (pandas, numpy, pyarrow) for Python 3.12, x86_64.
  # Account 336392948345 is AWS's own; version 24 published 2026-04-07.
  layers = ["arn:aws:lambda:${var.aws_region}:336392948345:layer:AWSSDKPandas-Python312:24"]

  depends_on = [aws_cloudwatch_log_group.brief, aws_iam_role_policy.brief]

  lifecycle {
    ignore_changes = [filename, source_code_hash] # code deployed by CI (deploy.yml), Chat 25
  }
}

# EventBridge invokes Lambda asynchronously; by default Lambda retries a failed async call twice.
# Off: one brief a week, a failure emails (alarm below) and the rerun is one manual invoke.
resource "aws_lambda_function_event_invoke_config" "brief" {
  function_name          = aws_lambda_function.brief.function_name
  maximum_retry_attempts = 0
}

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

# Monday 03:00 UTC = 08:30 IST — after the Monday 02:00 IST nightly run (done by ~02:20).
resource "aws_cloudwatch_event_rule" "weekly_brief" {
  name                = "${local.brief_name}-schedule"
  schedule_expression = "cron(0 3 ? * MON *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "weekly_brief" {
  rule = aws_cloudwatch_event_rule.weekly_brief.name
  arn  = aws_lambda_function.brief.arn
}

# Resource-based policy on the function: lets this one rule (not any EventBridge rule) invoke it.
resource "aws_lambda_permission" "weekly_brief" {
  statement_id  = "AllowWeeklyBriefRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.brief.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_brief.arn
}

# ---------------------------------------------------------------------------
# Alarm — any failed run emails (stale data, cost cap, unfaithful brief, throttling, timeout)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "brief_errors" {
  alarm_name          = "${local.brief_name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching" # one run a week; silence in between is normal

  dimensions = {
    FunctionName = aws_lambda_function.brief.function_name
  }

  alarm_actions     = [aws_sns_topic.alerts.arn]
  alarm_description = "Weekly brief Lambda failed — read the last log line in /aws/lambda/${local.brief_name} (runbook §26)"
}
