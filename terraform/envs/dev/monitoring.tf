resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts-${var.env}"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Absence-of-success alarm (Chat 25) — replaces the old "ExecutionsFailed >= 1" alarm.
# The old one went back to OK 5 minutes after each failure (missing data = not breaching),
# so a pipeline that failed every night for a month looked green in the console; and a run
# that never started (rule disabled, timeout) never fired it at all.
# This one asks the question that matters: "was there a successful run in the last 26 hours?"
# 26 one-hour buckets; it fires only if ALL of them have no success. Hours with no data count
# as breaching, so "nothing ran" alarms too. It stays in ALARM until a run succeeds.
# 26 h, not 24 h: leaves 2 h of slack for a slow night.
resource "aws_cloudwatch_metric_alarm" "sfn_no_success" {
  alarm_name          = "${var.project}-sfn-no-success-26h-${var.env}"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsSucceeded"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 26
  datapoints_to_alarm = 26
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.ingest_pipeline.arn
  }

  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn] # one "recovered" email when a run succeeds again
  alarm_description = "No successful JobPulse pipeline run in 26 h — check Step Functions executions (runbook §1)"
}

# Duration warnings (Chat 25): fire when a runner uses more than 70% of its Glue timeout,
# before it starts timing out. The runners publish JobPulse/JobDurationSeconds themselves.
locals {
  duration_alarm_jobs = {
    enrichment = aws_glue_job.enrichment_runner
    embedding  = aws_glue_job.embedding_runner
  }
}

resource "aws_cloudwatch_metric_alarm" "glue_duration" {
  for_each = local.duration_alarm_jobs

  alarm_name          = "${var.project}-${each.key}-duration-70pct-${var.env}"
  namespace           = "JobPulse"
  metric_name         = "JobDurationSeconds"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = each.value.timeout * 60 * 0.7
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching" # one datapoint per night; silence in between is normal

  dimensions = {
    JobName = each.value.name
  }

  alarm_actions     = [aws_sns_topic.alerts.arn]
  alarm_description = "${each.value.name} took > 70% of its ${each.value.timeout}-min timeout — see [timing] lines in its log"
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
