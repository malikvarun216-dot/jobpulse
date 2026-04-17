# Trust policy — only Lambda can assume this role
data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_exec" {
  name               = "${var.project}-lambda-exec-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

# What Lambda is allowed to do
data "aws_iam_policy_document" "lambda_permissions" {
  # Write raw JSON to bronze only
  statement {
    sid    = "S3BronzeWrite"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
    ]
    resources = [
      "${aws_s3_bucket.layers["bronze"].arn}/*"
    ]
  }

  # CloudWatch Logs — Lambda needs this to write logs
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_policy" "lambda_policy" {
  name   = "${var.project}-lambda-policy-${var.env}"
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

resource "aws_iam_role_policy_attachment" "lambda_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}