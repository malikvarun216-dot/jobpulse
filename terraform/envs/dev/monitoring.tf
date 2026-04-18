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

  alarm_actions = [aws_sns_topic.alerts.arn]
  alarm_description = "JobPulse ingestion pipeline failed — check Step Functions console"
}
