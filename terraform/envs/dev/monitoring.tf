resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts-${var.env}"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Alarm fires when any Step Functions execution fails
resource "aws_cloudwatch_metric_alarm" "sfn_failures" {
  alarm_name          = "${var.project}-sfn-failures-${var.env}"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.ingest_pipeline.arn
  }

  alarm_actions     = [aws_sns_topic.alerts.arn]
  alarm_description = "JobPulse ingestion pipeline failed — check Step Functions console"
}

# ---------------------------------------------------------------------------
# Log retention — 14 days (default is "never expire")
# Lambda and Glue create these groups on first run, so they already exist:
# the import blocks adopt them into state instead of failing on "already exists".
# The /aws-glue/* names are account-wide, but only JobPulse runs Glue in this account (checked Chat 24).
# ---------------------------------------------------------------------------

locals {
  log_groups = toset(concat(
    [for src in ["himalayas", "remotive", "adzuna", "arbeitnow", "greenhouse"] :
    "/aws/lambda/${var.project}-ingest-${src}-${var.env}"],
    [
      "/aws-glue/jobs/error",
      "/aws-glue/jobs/logs-v2",
      "/aws-glue/python-jobs/error",
      "/aws-glue/python-jobs/output",
    ],
  ))
}

import {
  for_each = local.log_groups
  to       = aws_cloudwatch_log_group.pipeline[each.key]
  id       = each.key
}

resource "aws_cloudwatch_log_group" "pipeline" {
  for_each          = local.log_groups
  name              = each.key
  retention_in_days = 14
}
