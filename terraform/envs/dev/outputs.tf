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