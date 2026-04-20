resource "aws_athena_workgroup" "main" {
  name          = "${var.project}-${var.env}"
  force_destroy = true

  configuration {
    result_configuration {
      output_location = "s3://${aws_s3_bucket.layers["gold"].bucket}/athena-results/"
    }
    bytes_scanned_cutoff_per_query = 1073741824 # 1 GB cap — cost guard
    enforce_workgroup_configuration = true
  }

  tags = {
    project = var.project
    env     = var.env
  }
}

resource "aws_glue_catalog_database" "silver" {
  name        = "${var.project}_silver_${var.env}"
  description = "External tables over S3 silver Parquet — read by dbt"
}

resource "aws_glue_catalog_database" "gold" {
  name        = "${var.project}_gold_${var.env}"
  description = "dbt-managed star schema — Athena CTAS into S3 gold"
}

# External table over silver Parquet — 14 data columns + 3 partition keys
resource "aws_glue_catalog_table" "silver_jobs" {
  database_name = aws_glue_catalog_database.silver.name
  name          = "silver_jobs"

  table_type = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.layers["silver"].bucket}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "job_id"
      type = "string"
    }
    columns {
      name = "source"
      type = "string"
    }
    columns {
      name = "title"
      type = "string"
    }
    columns {
      name = "company_name"
      type = "string"
    }
    columns {
      name = "category"
      type = "string"
    }
    columns {
      name = "job_type"
      type = "string"
    }
    columns {
      name = "apply_url"
      type = "string"
    }
    columns {
      name = "salary_raw"
      type = "string"
    }
    columns {
      name = "location_raw"
      type = "string"
    }
    columns {
      name = "state"
      type = "string"
    }
    columns {
      name = "tags"
      type = "array<string>"
    }
    columns {
      name = "publication_date"
      type = "date"
    }
    columns {
      name = "description"
      type = "string"
    }
    columns {
      name = "ingested_at"
      type = "timestamp"
    }
    columns {
      name = "dedup_key"
      type = "string"
    }
    columns {
      name = "source_apis"
      type = "array<string>"
    }
    columns {
      name = "source_count"
      type = "int"
    }
  }

  # Hive partition keys — match the Spark job's partitionBy order
  partition_keys {
    name = "snapshot_date"
    type = "date"
  }
  partition_keys {
    name = "country"
    type = "string"
  }
  partition_keys {
    name = "role_family"
    type = "string"
  }

  parameters = {
    "classification"      = "parquet"
    "parquet.compression" = "SNAPPY"
    "EXTERNAL"            = "TRUE"
  }
}

# External table over enrichment Parquet — written by JDEnrichmentAgent
resource "aws_glue_catalog_table" "enrichment_scores" {
  database_name = aws_glue_catalog_database.gold.name
  name          = "enrichment_scores"

  table_type = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.layers["gold"].bucket}/enrichment-scores/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "job_id"
      type = "string"
    }
    columns {
      name = "skills"
      type = "array<string>"
    }
    columns {
      name = "seniority"
      type = "string"
    }
    columns {
      name = "yoe_required"
      type = "int"
    }
    columns {
      name = "match_score"
      type = "double"
    }
    columns {
      name = "score_detail"
      type = "string"
    }
    columns {
      name = "extraction_source"
      type = "string"
    }
    columns {
      name = "enriched_at"
      type = "string"
    }
  }

  partition_keys {
    name = "snapshot_date"
    type = "string"
  }

  parameters = {
    "classification"      = "parquet"
    "parquet.compression" = "SNAPPY"
    "EXTERNAL"            = "TRUE"
  }
}
