resource "aws_iam_role" "eventbridge_exec" {
  name = "${var.project}-eventbridge-exec-${var.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "events.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "eventbridge_policy" {
  name = "${var.project}-eventbridge-policy-${var.env}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "StartStateMachine"
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.ingest_pipeline.arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eventbridge_attach" {
  role       = aws_iam_role.eventbridge_exec.name
  policy_arn = aws_iam_policy.eventbridge_policy.arn
}

# Fires daily at 2:00 AM IST (8:30 PM UTC)
resource "aws_cloudwatch_event_rule" "daily_ingest" {
  name                = "${var.project}-daily-ingest-${var.env}"
  schedule_expression = "cron(30 20 * * ? *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "trigger_sfn" {
  rule     = aws_cloudwatch_event_rule.daily_ingest.name
  arn      = aws_sfn_state_machine.ingest_pipeline.arn
  role_arn = aws_iam_role.eventbridge_exec.arn
}
