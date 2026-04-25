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

output "dbt_glue_job_name" {
  value = aws_glue_job.dbt_runner.name
}

output "athena_workgroup_name" {
  value = aws_athena_workgroup.main.name
}

output "silver_database_name" {
  value = aws_glue_catalog_database.silver.name
}

output "gold_database_name" {
  value = aws_glue_catalog_database.gold.name
}

output "enrichment_job_name" {
  value = aws_glue_job.enrichment_runner.name
}

output "dashboard_url" {
  value       = "http://${aws_eip.dashboard.public_ip}:8501"
  description = "JobPulse Streamlit dashboard public URL"
}

output "dashboard_instance_id" {
  value       = aws_instance.dashboard.id
  description = "EC2 instance ID for SSH and console access"
}