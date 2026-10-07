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
      },
      {
        # Runners publish their own duration (Glue has no duration metric for Python Shell jobs)
        Sid      = "PublishRunMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "JobPulse" }
        }
      },
      {
        Sid      = "SecretsManagerAnthropicKey"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:jobpulse/anthropic_key_dev*"
      },
      {
        Sid      = "SecretsManagerVoyageKey"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:jobpulse/voyage_key_dev*"
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

# Code ownership (Chat 25): CI (deploy.yml) uploads every script and package on push to dev.
# Terraform creates each object once, then ignores its content — two deployers caused a
# permanent plan diff and a race over which version was live.
# Upload PySpark script to silver bucket under glue-scripts/ prefix
resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/bronze_to_silver.py"
  source = "${path.module}/../../../spark/jobs/bronze_to_silver.py"
  etag   = filemd5("${path.module}/../../../spark/jobs/bronze_to_silver.py")

  lifecycle {
    ignore_changes = [etag, tags_all] # content owned by CI (deploy.yml)
  }
}

resource "aws_glue_job" "bronze_to_silver" {
  name     = "${var.project}-bronze-to-silver-${var.env}"
  role_arn = aws_iam_role.glue_exec.arn

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/bronze_to_silver.py"
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

# dbt_project.zip is built and uploaded by CI (deploy.yml) — see "Code ownership" above.

resource "aws_s3_object" "dbt_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/dbt_runner.py"
  source = "${path.module}/../../../transform/dbt_runner/dbt_runner.py"
  etag   = filemd5("${path.module}/../../../transform/dbt_runner/dbt_runner.py")

  lifecycle {
    ignore_changes = [etag, tags_all] # content owned by CI (deploy.yml)
  }
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
    "--additional-python-modules"        = "dbt-core==1.9.10,dbt-athena-community==1.9.5,pyyaml"
  }

  glue_version = "4.0"
  max_capacity = 0.0625 # 1/16 DPU — cheapest Python Shell tier (~$0.004/run)
  timeout      = 30     # minutes

  tags = {
    project = var.project
    env     = var.env
    layer   = "gold"
  }

  depends_on = [aws_s3_object.dbt_runner_script]
}

# ---------------------------------------------------------------------------
# Enrichment runner — Glue Python Shell job
# ---------------------------------------------------------------------------

# genai_package.zip is built and uploaded by CI (deploy.yml) — see "Code ownership" above.

# Upload user_profile.yml separately so it can be updated without re-deploying the zip
resource "aws_s3_object" "user_profile" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "config/user_profile.yml"
  source = "${path.module}/../../../config/user_profile.yml"
  etag   = filemd5("${path.module}/../../../config/user_profile.yml")

  lifecycle {
    ignore_changes = [etag, tags_all] # content owned by CI (deploy.yml)
  }
}

resource "aws_s3_object" "enrichment_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/enrichment_runner.py"
  source = "${path.module}/../../../genai/enrichment_runner.py"
  etag   = filemd5("${path.module}/../../../genai/enrichment_runner.py")

  lifecycle {
    ignore_changes = [etag, tags_all] # content owned by CI (deploy.yml)
  }
}

resource "aws_glue_job" "enrichment_runner" {
  name     = "${var.project}-enrichment-${var.env}"
  role_arn = aws_iam_role.glue_exec.arn

  command {
    name            = "pythonshell"
    script_location = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/enrichment_runner.py"
    python_version  = "3.9"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--gold_bucket"                      = aws_s3_bucket.layers["gold"].bucket
    "--silver_bucket"                    = aws_s3_bucket.layers["silver"].bucket
    "--region"                           = var.aws_region
    "--workgroup"                        = aws_athena_workgroup.main.name
    "--gold_database"                    = aws_glue_catalog_database.gold.name
    "--silver_database"                  = aws_glue_catalog_database.silver.name
    "--dry_run"                          = "false"
    "--force_rescore"                    = "false"
    "--use_llm"                          = "false" # LLM extraction off until Chat 31 measures it
    "--job_name"                         = "${var.project}-enrichment-${var.env}"
    "--enable-continuous-cloudwatch-log" = "true"
    "--additional-python-modules"        = "anthropic==0.125.0,pydantic==2.13.5,pyyaml,pyarrow==14.0.2"
    "--extra-py-files"                   = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/genai_package.zip"
  }

  glue_version = "4.0"
  max_capacity = 0.0625 # measured Chat 25: rules + scoring for 5.7K JDs = 4 s of CPU; 1/16 DPU is plenty
  timeout      = 20     # was 60 — the 59-min runs were failed LLM calls sleeping, not work (Chat 25)

  tags = {
    project = var.project
    env     = var.env
    layer   = "enrichment"
  }

  depends_on = [aws_s3_object.enrichment_runner_script, aws_s3_object.user_profile]
}

# ---------------------------------------------------------------------------
# Embedding runner — Glue Python Shell job
# ---------------------------------------------------------------------------

resource "aws_s3_object" "embedding_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/embedding_runner.py"
  source = "${path.module}/../../../genai/embedding_runner.py"
  etag   = filemd5("${path.module}/../../../genai/embedding_runner.py")

  lifecycle {
    ignore_changes = [etag, tags_all] # content owned by CI (deploy.yml)
  }
}

resource "aws_glue_job" "embedding_runner" {
  name     = "${var.project}-embedding-${var.env}"
  role_arn = aws_iam_role.glue_exec.arn

  command {
    name            = "pythonshell"
    script_location = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/embedding_runner.py"
    python_version  = "3.9"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--gold_bucket"                      = aws_s3_bucket.layers["gold"].bucket
    "--silver_bucket"                    = aws_s3_bucket.layers["silver"].bucket
    "--region"                           = var.aws_region
    "--workgroup"                        = aws_athena_workgroup.main.name
    "--gold_database"                    = aws_glue_catalog_database.gold.name
    "--dry_run"                          = "false"
    "--job_name"                         = "${var.project}-embedding-${var.env}"
    "--enable-continuous-cloudwatch-log" = "true"
    "--additional-python-modules"        = "voyageai==0.5.0,pyarrow==14.0.2,pandas==2.3.3,numpy==1.26.4"
    "--extra-py-files"                   = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/genai_package.zip"
  }

  glue_version = "4.0"
  max_capacity = 0.0625
  timeout      = 60

  tags = {
    project = var.project
    env     = var.env
    layer   = "embedding"
  }

  depends_on = [aws_s3_object.embedding_runner_script]
}

# ---------------------------------------------------------------------------
# GE runner — Glue Python Shell job (data quality gate: silver → gold)
# ---------------------------------------------------------------------------

resource "aws_s3_object" "ge_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/ge_runner.py"
  source = "${path.module}/../../../transform/ge_runner/ge_runner.py"
  etag   = filemd5("${path.module}/../../../transform/ge_runner/ge_runner.py")

  lifecycle {
    ignore_changes = [etag, tags_all] # content owned by CI (deploy.yml)
  }
}

resource "aws_glue_job" "ge_runner" {
  name     = "${var.project}-ge-runner-${var.env}"
  role_arn = aws_iam_role.glue_exec.arn

  command {
    name            = "pythonshell"
    script_location = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/ge_runner.py"
    python_version  = "3.9"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--silver_bucket"                    = aws_s3_bucket.layers["silver"].bucket
    "--region"                           = var.aws_region
    "--enable-continuous-cloudwatch-log" = "true"
    # GE 1.x works on Python 3.9–3.12; pyarrow + pandas read the silver Parquet partition
    # Pinned (Chat 24) to the versions the last green runs installed — unpinned ">=" drifted night to night
    "--additional-python-modules" = "great-expectations==1.8.1,pandas==2.3.3,pyarrow==14.0.2"
  }

  glue_version = "4.0"
  max_capacity = 0.0625 # cheapest Python Shell tier (~$0.004/run), same as dbt_runner
  timeout      = 15     # GE install + validation finishes well under 5 min; 15 is safe headroom

  tags = {
    project = var.project
    env     = var.env
    layer   = "quality"
  }

  depends_on = [aws_s3_object.ge_runner_script]
}
