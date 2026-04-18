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
        Sid    = "ReadWriteGold"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.layers["gold"].arn,
          "${aws_s3_bucket.layers["gold"].arn}/*"
        ]
      },
      {
        Sid    = "AthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup",
          "athena:ListWorkGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "GlueCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
          "glue:BatchCreatePartition",
          "glue:GetPartition",
          "glue:GetPartitions",
          "glue:UpdatePartition",
          "glue:BatchDeletePartition"
        ]
        Resource = "*"
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

# ---------------------------------------------------------------------------
# dbt runner — Glue Python Shell job
# ---------------------------------------------------------------------------

locals {
  dbt_project_dir = "${path.module}/../../../dbt_project"
  dbt_source_files = [
    for f in fileset("${path.module}/../../../dbt_project", "**")
    : f if !endswith(f, ".gitkeep")
       && !startswith(f, "target/")
       && !startswith(f, "dbt_packages/")
  ]
}

# Zip and re-upload whenever SQL/YAML files change
resource "null_resource" "dbt_project_upload" {
  triggers = {
    dbt_hash = sha256(join("", [
      for f in local.dbt_source_files
      : filesha256("${local.dbt_project_dir}/${f}")
    ]))
  }

  provisioner "local-exec" {
    command = <<-EOT
      cd ${path.module}/../../../
      zip -r /tmp/dbt_project.zip dbt_project/ \
        --exclude "dbt_project/.gitkeep" \
        --exclude "dbt_project/models/.gitkeep" \
        --exclude "dbt_project/models/bronze/.gitkeep" \
        --exclude "dbt_project/models/silver/.gitkeep" \
        --exclude "dbt_project/models/gold/.gitkeep" \
        --exclude "dbt_project/macros/.gitkeep" \
        --exclude "dbt_project/tests/.gitkeep" \
        --exclude "dbt_project/target/*" \
        --exclude "dbt_project/dbt_packages/*"
      aws s3 cp /tmp/dbt_project.zip \
        s3://${aws_s3_bucket.layers["silver"].bucket}/dbt-project/dbt_project.zip \
        --region ${var.aws_region}
    EOT
  }

  depends_on = [aws_s3_bucket.layers]
}

resource "aws_s3_object" "dbt_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/dbt_runner.py"
  source = "${path.module}/../../../transform/dbt_runner/dbt_runner.py"
  etag   = filemd5("${path.module}/../../../transform/dbt_runner/dbt_runner.py")
}

resource "aws_glue_job" "dbt_runner" {
  name     = "${var.project}-dbt-runner-${var.env}"
  role_arn = aws_iam_role.glue_exec.arn

  command {
    name            = "pythonshell"
    script_location = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/dbt_runner.py"
    python_version  = "3.9"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--silver_bucket"                    = aws_s3_bucket.layers["silver"].bucket
    "--gold_bucket"                      = aws_s3_bucket.layers["gold"].bucket
    "--region"                           = var.aws_region
    "--workgroup"                        = aws_athena_workgroup.main.name
    "--gold_database"                    = aws_glue_catalog_database.gold.name
    "--silver_database"                  = aws_glue_catalog_database.silver.name
    "--silver_table"                     = aws_glue_catalog_table.silver_jobs.name
    "--enable-continuous-cloudwatch-log" = "true"
    "--additional-python-modules"        = "dbt-core==1.11.8,dbt-athena-community==1.10.0,pyyaml"
  }

  max_capacity = 0.0625 # 1/16 DPU — cheapest Python Shell tier (~$0.004/run)
  timeout      = 30     # minutes

  tags = {
    project = var.project
    env     = var.env
    layer   = "gold"
  }

  depends_on = [aws_s3_object.dbt_runner_script, null_resource.dbt_project_upload]
}
