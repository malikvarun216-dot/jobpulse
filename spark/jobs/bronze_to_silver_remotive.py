"""
bronze_to_silver_remotive.py
------------------------------
Glue PySpark job: Remotive bronze JSON → silver Parquet.

Input:  s3://{bronze_bucket}/snapshot_date=*/source=remotive/data.json.gz
Output: s3://{silver_bucket}/snapshot_date=.../country=.../role_family=.../

Partitioning: snapshot_date / country / role_family
Idempotent:   dynamic partition overwrite — reruns only replace affected partitions.
"""

import sys

try:
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType, StringType
except ImportError:
    pass  # not needed for pure-function unit tests

# ---------------------------------------------------------------------------
# Normalisation maps
# ---------------------------------------------------------------------------

COUNTRY_MAP = {
    "worldwide": "remote", "anywhere": "remote", "global": "remote",
    "usa": "US", "united states": "US", "u.s.": "US",
    "uk": "UK", "united kingdom": "UK", "britain": "UK",
    "canada": "CA",
    "europe": "EU",
    "australia": "AU",
    "germany": "DE",
    "japan": "JP", "tokyo": "JP",
    "india": "IN", "bangalore": "IN", "bengaluru": "IN",
    "mumbai": "IN", "hyderabad": "IN", "delhi": "IN",
    "pune": "IN", "chennai": "IN", "kolkata": "IN",
    "ahmedabad": "IN", "noida": "IN", "gurgaon": "IN", "gurugram": "IN",
    "singapore": "SG",
    "netherlands": "NL",
}

STATE_MAP = {
    "bangalore": "Karnataka", "bengaluru": "Karnataka",
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra",
    "hyderabad": "Telangana",
    "delhi": "Delhi NCR", "new delhi": "Delhi NCR", "noida": "Delhi NCR",
    "gurgaon": "Delhi NCR", "gurugram": "Delhi NCR",
    "chennai": "Tamil Nadu",
    "kolkata": "West Bengal",
    "ahmedabad": "Gujarat",
}

ROLE_FAMILY_MAP = {
    "software development": "SDE", "engineering": "SDE",
    "data": "DATA",
    "devops": "DevOps", "sysadmin": "DevOps",
    "product": "PM",
    "design": "Design",
    "marketing": "Marketing",
}

# ---------------------------------------------------------------------------
# Pure Python normalisation functions (testable without Spark)
# ---------------------------------------------------------------------------


def extract_country(location: str) -> str:
    if not location:
        return "other"
    loc = location.lower()
    for keyword, country in COUNTRY_MAP.items():
        if keyword in loc:
            return country
    return "other"


def extract_state(location: str) -> str:
    if not location:
        return None
    loc = location.lower()
    for keyword, state in STATE_MAP.items():
        if keyword in loc:
            return state
    return None


def extract_role_family(category: str) -> str:
    if not category:
        return "Other"
    cat = category.lower()
    for keyword, family in ROLE_FAMILY_MAP.items():
        if keyword in cat:
            return family
    return "Other"


# ---------------------------------------------------------------------------
# PySpark transformation (no Glue imports — testable with local SparkSession)
# ---------------------------------------------------------------------------


def build_silver_df(raw_df):
    """Transform bronze DataFrame (one row per file) to silver (one row per job)."""
    country_udf = F.udf(extract_country, StringType())
    state_udf = F.udf(extract_state, StringType())
    role_family_udf = F.udf(extract_role_family, StringType())

    jobs_df = raw_df.select(
        F.col("snapshot_date").alias("_snapshot_date"),
        F.col("ingested_at").alias("_ingested_at"),
        F.explode("jobs").alias("job"),
    )

    return jobs_df.select(
        F.col("job.id").cast(StringType()).alias("job_id"),
        F.lit("remotive").alias("source"),
        F.to_date(F.col("_snapshot_date"), "yyyy-MM-dd").alias("snapshot_date"),
        F.col("job.title").alias("title"),
        F.col("job.company_name").alias("company_name"),
        F.col("job.category").alias("category"),
        role_family_udf(F.col("job.category")).alias("role_family"),
        F.col("job.job_type").alias("job_type"),
        F.col("job.url").alias("apply_url"),
        F.col("job.salary").cast(StringType()).alias("salary_raw"),
        F.col("job.candidate_required_location").alias("location_raw"),
        country_udf(F.col("job.candidate_required_location")).alias("country"),
        state_udf(F.col("job.candidate_required_location")).alias("state"),
        F.col("job.tags").cast(ArrayType(StringType())).alias("tags"),
        F.to_date(F.col("job.publication_date")).alias("publication_date"),
        F.col("job.description").alias("description"),
        F.to_timestamp(F.col("_ingested_at")).alias("ingested_at"),
    )


# ---------------------------------------------------------------------------
# Glue entry point
# ---------------------------------------------------------------------------


def main():
    # Import Glue libs here so the module is still importable in local tests
    from awsglue.context import GlueContext  # noqa: PLC0415
    from awsglue.job import Job  # noqa: PLC0415
    from awsglue.utils import getResolvedOptions  # noqa: PLC0415
    from pyspark.context import SparkContext  # noqa: PLC0415

    args = getResolvedOptions(
        sys.argv, ["JOB_NAME", "bronze_bucket", "silver_bucket"]
    )

    # snapshot_date is optional — passed as --snapshot_date=YYYY-MM-DD
    snapshot_date = next(
        (a.split("=", 1)[1] for a in sys.argv if a.startswith("--snapshot_date=")),
        None,
    )

    sc = SparkContext()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)

    # Dynamic partition overwrite: only replaces partitions we write to
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    bronze_bucket = args["bronze_bucket"]
    silver_bucket = args["silver_bucket"]

    if snapshot_date:
        input_path = f"s3://{bronze_bucket}/snapshot_date={snapshot_date}/source=remotive/data.json.gz"
    else:
        input_path = f"s3://{bronze_bucket}/snapshot_date=*/source=remotive/data.json.gz"

    raw_df = spark.read.option("multiline", "true").json(input_path)

    silver_df = build_silver_df(raw_df)

    silver_df.write \
        .mode("overwrite") \
        .option("compression", "snappy") \
        .partitionBy("snapshot_date", "country", "role_family") \
        .parquet(f"s3://{silver_bucket}/")

    job.commit()


if __name__ == "__main__":
    main()
