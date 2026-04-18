resource "aws_iam_role" "glue_exec" {
  name = "${var.project}-glue-exec-${var.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "glue.amazonaws.com" }
    }]
  })

  tags = {
    project = var.project
    env     = var.env
  }
}

resource "aws_iam_policy" "glue_policy" {
  name = "${var.project}-glue-policy-${var.env}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadBronze"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.layers["bronze"].arn,
          "${aws_s3_bucket.layers["bronze"].arn}/*"
        ]
      },
      {
        Sid    = "ReadWriteSilver"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.layers["silver"].arn,
          "${aws_s3_bucket.layers["silver"].arn}/*"
        ]
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "glue_attach" {
  role       = aws_iam_role.glue_exec.name
  policy_arn = aws_iam_policy.glue_policy.arn
}

# AWS managed policy: Glue service needs this for internal operations
resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

# Upload PySpark script to silver bucket under glue-scripts/ prefix
resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/bronze_to_silver_remotive.py"
  source = "${path.module}/../../../spark/jobs/bronze_to_silver_remotive.py"
  etag   = filemd5("${path.module}/../../../spark/jobs/bronze_to_silver_remotive.py")
}

resource "aws_glue_job" "bronze_to_silver" {
  name     = "${var.project}-bronze-to-silver-${var.env}"
  role_arn = aws_iam_role.glue_exec.arn

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/bronze_to_silver_remotive.py"
    python_version  = "3"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--job-bookmark-option"              = "job-bookmark-disable"
    "--bronze_bucket"                    = aws_s3_bucket.layers["bronze"].bucket
    "--silver_bucket"                    = aws_s3_bucket.layers["silver"].bucket
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = ""
  }

  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 10

  tags = {
    project = var.project
    env     = var.env
    layer   = "transform"
  }

  depends_on = [aws_s3_object.glue_script]
}
