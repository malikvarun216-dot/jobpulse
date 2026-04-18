output "bucket_names" {
  value = { for k, v in aws_s3_bucket.layers : k => v.bucket }
}

output "lambda_exec_role_arn" {
  value = aws_iam_role.lambda_exec.arn
}

output "himalayas_lambda_arn" {
  value = aws_lambda_function.himalayas.arn
}

output "remotive_lambda_arn" {
  value = aws_lambda_function.remotive.arn
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.ingest_pipeline.arn
}

output "eventbridge_rule_arn" {
  value = aws_cloudwatch_event_rule.daily_ingest.arn
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "glue_job_name" {
  value = aws_glue_job.bronze_to_silver.name
}