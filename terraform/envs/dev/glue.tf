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

# Upload PySpark script to silver bucket under glue-scripts/ prefix
resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/bronze_to_silver.py"
  source = "${path.module}/../../../spark/jobs/bronze_to_silver.py"
  etag   = filemd5("${path.module}/../../../spark/jobs/bronze_to_silver.py")
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

  depends_on = [aws_s3_object.dbt_runner_script, null_resource.dbt_project_upload]
}

# ---------------------------------------------------------------------------
# Enrichment runner — Glue Python Shell job
# ---------------------------------------------------------------------------

locals {
  genai_source_files = fileset("${path.module}/../../../genai", "*.py")
}

resource "null_resource" "genai_package_upload" {
  triggers = {
    genai_hash = sha256(join("", [
      for f in local.genai_source_files
      : filesha256("${path.module}/../../../genai/${f}")
    ]))
    profile_hash = filesha256("${path.module}/../../../config/user_profile.yml")
  }

  provisioner "local-exec" {
    command = <<-EOT
      cd ${path.module}/../../../
      zip -r /tmp/genai_package.zip genai/ config/user_profile.yml
      aws s3 cp /tmp/genai_package.zip \
        s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/genai_package.zip \
        --region ${var.aws_region}
    EOT
  }

  depends_on = [aws_s3_bucket.layers]
}

# Upload user_profile.yml separately so it can be updated without re-deploying the zip
resource "aws_s3_object" "user_profile" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "config/user_profile.yml"
  source = "${path.module}/../../../config/user_profile.yml"
  etag   = filemd5("${path.module}/../../../config/user_profile.yml")
}

resource "aws_s3_object" "enrichment_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/enrichment_runner.py"
  source = "${path.module}/../../../genai/enrichment_runner.py"
  etag   = filemd5("${path.module}/../../../genai/enrichment_runner.py")
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
    "--enable-continuous-cloudwatch-log" = "true"
    "--additional-python-modules"        = "anthropic>=0.40.0,pydantic>=2.0.0,pyyaml,pyarrow==14.0.2"
    "--extra-py-files"                   = "s3://${aws_s3_bucket.layers["silver"].bucket}/glue-scripts/genai_package.zip"
  }

  glue_version = "4.0"
  max_capacity = 0.0625
  timeout      = 60

  tags = {
    project = var.project
    env     = var.env
    layer   = "enrichment"
  }

  depends_on = [aws_s3_object.enrichment_runner_script, aws_s3_object.user_profile, null_resource.genai_package_upload]
}

# ---------------------------------------------------------------------------
# Embedding runner — Glue Python Shell job
# ---------------------------------------------------------------------------

resource "aws_s3_object" "embedding_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/embedding_runner.py"
  source = "${path.module}/../../../genai/embedding_runner.py"
  etag   = filemd5("${path.module}/../../../genai/embedding_runner.py")
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
    "--enable-continuous-cloudwatch-log" = "true"
    "--additional-python-modules"        = "voyageai>=0.2.0,pyarrow==14.0.2,pandas>=2.0.0,numpy==1.26.4"
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

  depends_on = [aws_s3_object.embedding_runner_script, null_resource.genai_package_upload]
}

# ---------------------------------------------------------------------------
# GE runner — Glue Python Shell job (data quality gate: silver → gold)
# ---------------------------------------------------------------------------

resource "aws_s3_object" "ge_runner_script" {
  bucket = aws_s3_bucket.layers["silver"].id
  key    = "glue-scripts/ge_runner.py"
  source = "${path.module}/../../../transform/ge_runner/ge_runner.py"
  etag   = filemd5("${path.module}/../../../transform/ge_runner/ge_runner.py")
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
    "--additional-python-modules"        = "great-expectations>=1.3.0,pandas>=2.0.0,pyarrow==14.0.2"
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
