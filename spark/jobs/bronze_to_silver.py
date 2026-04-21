"""
bronze_to_silver.py
---------------------
Glue PySpark job: multi-source bronze JSON → silver Parquet.

Input:  s3://{bronze_bucket}/snapshot_date=*/source=*/data.json.gz
Output: s3://{silver_bucket}/snapshot_date=.../country=.../role_family=.../

Supported sources: remotive, remoteok
Adding a new source: write it to bronze using the canonical schema or the
source-native schema — COALESCE logic in build_silver_df() handles both.

Partitioning: snapshot_date / country / role_family
Idempotent:   dynamic partition overwrite — reruns only replace affected partitions.
"""

import sys

try:
    from pyspark.sql import functions as F, Window
    from pyspark.sql.types import ArrayType, IntegerType, StringType
except ImportError:
    pass  # not needed for pure-function unit tests

# ---------------------------------------------------------------------------
# Normalisation maps
# ---------------------------------------------------------------------------

COUNTRY_MAP = {
    "worldwide": "remote", "anywhere": "remote", "global": "remote",
    "remote": "remote",
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
    "london": "UK",
    "istanbul": "TR",
    "bangkok": "TH",
    "san francisco": "US", "new york": "US", "austin": "US",
    "chicago": "US", "boston": "US", "seattle": "US",
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

# Tags from RemoteOK that map to role families (RemoteOK has no category field)
TAG_ROLE_MAP = {
    "python": "SDE", "javascript": "SDE", "java": "SDE", "golang": "SDE",
    "react": "SDE", "node": "SDE", "backend": "SDE", "frontend": "SDE",
    "fullstack": "SDE", "engineer": "SDE",
    "data": "DATA", "sql": "DATA", "spark": "DATA", "kafka": "DATA",
    "analytics": "DATA", "ml": "DATA", "ai": "DATA", "machine learning": "DATA",
    "devops": "DevOps", "aws": "DevOps", "kubernetes": "DevOps", "docker": "DevOps",
    "infrastructure": "DevOps", "sre": "DevOps",
    "product": "PM", "manager": "PM",
    "design": "Design", "ui": "Design", "ux": "Design",
    "marketing": "Marketing", "growth": "Marketing", "seo": "Marketing",
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


def extract_role_family_from_tags(tags: list) -> str:
    """Fallback: infer role_family from job tags when no category is available."""
    if not tags:
        return "Other"
    tags_lower = [t.lower() for t in tags]
    for tag in tags_lower:
        for keyword, family in TAG_ROLE_MAP.items():
            if keyword in tag:
                return family
    return "Other"


def resolve_role_family(category: str, tags: list) -> str:
    """Use category if available, fall back to tags (for sources like RemoteOK)."""
    family = extract_role_family(category)
    if family != "Other":
        return family
    return extract_role_family_from_tags(tags or [])


# ---------------------------------------------------------------------------
# PySpark transformation (no Glue imports — testable with local SparkSession)
# ---------------------------------------------------------------------------


def build_silver_df(raw_df):
    """Transform multi-source bronze DataFrame (one row per file) to silver (one row per job).

    Field resolution (COALESCE handles Remotive, RemoteOK canonical, and Adzuna schemas):
      job_id      : job.job_id (remoteok canonical) ?? job.id (remotive/adzuna native)
      apply_url   : job.apply_url (canonical) ?? job.url (remotive) ?? job.redirect_url (adzuna)
      location_raw: job.location_raw (canonical) ?? job.candidate_required_location (remotive)
      category    : job.category (remotive/adzuna) ?? null (remoteok → role_family from tags)
    """
    country_udf = F.udf(extract_country, StringType())
    state_udf = F.udf(extract_state, StringType())
    role_family_udf = F.udf(resolve_role_family, StringType())

    jobs_df = raw_df.select(
        F.col("source").alias("_source"),
        F.col("snapshot_date").alias("_snapshot_date"),
        F.col("ingested_at").alias("_ingested_at"),
        F.explode("jobs").alias("job"),
    )

    # Resolve location: remoteok writes location_raw; remotive writes candidate_required_location
    location_col = F.coalesce(
        F.col("job.location_raw"),
        F.col("job.candidate_required_location"),
    )

    # Resolve apply_url: arbeitnow/adzuna normalize to apply_url; remotive writes url
    apply_url_col = F.coalesce(
        F.col("job.apply_url"),
        F.col("job.url"),
    )

    # Resolve job_id: remoteok writes job_id; remotive writes id
    job_id_col = F.coalesce(
        F.col("job.job_id"),
        F.col("job.id"),
    ).cast(StringType())

    return jobs_df.select(
        job_id_col.alias("job_id"),
        F.col("_source").alias("source"),
        F.to_date(F.col("_snapshot_date"), "yyyy-MM-dd").alias("snapshot_date"),
        F.col("job.title").alias("title"),
        F.col("job.company_name").alias("company_name"),
        F.col("job.category").alias("category"),
        role_family_udf(
            F.col("job.category"),
            F.col("job.tags").cast(ArrayType(StringType())),
        ).alias("role_family"),
        F.coalesce(F.col("job.job_type"), F.lit("full-time")).alias("job_type"),
        apply_url_col.alias("apply_url"),
        F.col("job.salary").cast(StringType()).alias("salary_raw"),
        location_col.alias("location_raw"),
        country_udf(location_col).alias("country"),
        state_udf(location_col).alias("state"),
        F.col("job.tags").cast(ArrayType(StringType())).alias("tags"),
        F.to_date(F.col("job.publication_date")).alias("publication_date"),
        F.col("job.description").alias("description"),
        F.to_timestamp(F.col("_ingested_at")).alias("ingested_at"),
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate_silver_df(df):
    """Exact dedup on (company_name, title, country) within each snapshot_date.

    Keeps earliest publication_date row as canonical. Collects source_apis array
    and source_count for all sources that listed this job on the same snapshot_date.
    """
    # Phase 1: dedup key — use normalized country (not location_raw) so
    # "Worldwide" (Remotive) and "Remote" (Arbeitnow) both hash to "remote"
    df = df.withColumn(
        "dedup_key",
        F.md5(F.concat_ws(
            "|",
            F.lower(F.trim(F.coalesce(F.col("company_name"), F.lit("")))),
            F.lower(F.trim(F.coalesce(F.col("title"),        F.lit("")))),
            F.lower(F.trim(F.coalesce(F.col("country"),      F.lit("")))),
        ))
    )

    # Phase 2: rank within (dedup_key, snapshot_date); keep earliest pub date.
    # row_number() guarantees exactly one rank-1 row even on ties.
    window = Window.partitionBy("dedup_key", "snapshot_date").orderBy(
        F.col("publication_date").asc_nulls_last(),
        F.col("ingested_at").asc(),
    )
    canonical_df = (
        df.withColumn("_rank", F.row_number().over(window))
          .filter(F.col("_rank") == 1)
          .drop("_rank")
    )

    # Phase 3: aggregate source_apis + source_count across ALL rows in the group
    # (groupBy on original df, not canonical_df, to capture every source)
    agg_df = df.groupBy("dedup_key", "snapshot_date").agg(
        F.collect_set("source").alias("source_apis"),
        F.count("*").cast(IntegerType()).alias("source_count"),
    )

    return canonical_df.join(agg_df, on=["dedup_key", "snapshot_date"], how="inner")


# ---------------------------------------------------------------------------
# Glue entry point
# ---------------------------------------------------------------------------


def main():
    from awsglue.context import GlueContext  # noqa: PLC0415
    from awsglue.job import Job  # noqa: PLC0415
    from awsglue.utils import getResolvedOptions  # noqa: PLC0415
    from pyspark.context import SparkContext  # noqa: PLC0415

    args = getResolvedOptions(
        sys.argv, ["JOB_NAME", "bronze_bucket", "silver_bucket"]
    )

    snapshot_date = next(
        (a.split("=", 1)[1] for a in sys.argv if a.startswith("--snapshot_date=")),
        None,
    )

    sc = SparkContext()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)

    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    bronze_bucket = args["bronze_bucket"]
    silver_bucket = args["silver_bucket"]

    if snapshot_date:
        input_path = f"s3://{bronze_bucket}/snapshot_date={snapshot_date}/source=*/data.json.gz"
    else:
        input_path = f"s3://{bronze_bucket}/snapshot_date=*/source=*/data.json.gz"

    raw_df = spark.read.option("multiline", "true").json(input_path)

    silver_df = build_silver_df(raw_df)
    silver_df = deduplicate_silver_df(silver_df)

    silver_df.write \
        .mode("overwrite") \
        .option("compression", "snappy") \
        .partitionBy("snapshot_date", "country", "role_family") \
        .parquet(f"s3://{silver_bucket}/")

    job.commit()


if __name__ == "__main__":
    main()
