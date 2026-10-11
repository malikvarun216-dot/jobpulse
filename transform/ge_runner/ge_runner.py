"""
Glue Python Shell job — validates today's silver partition with Great Expectations.

Runs between RunGlueJob (bronze→silver) and RunDbtGold in Step Functions.
Reads the silver partition for today's snapshot_date into pandas, runs 6 expectations,
and raises on failure so Step Functions routes to PipelineFailure → SNS alert.
"""
import json

import great_expectations as gx
import numpy as np
import pandas as pd


MIN_ROW_COUNT = 100
IST = pd.Timedelta(hours=5, minutes=30)  # snapshot_date is the IST calendar day of ingestion


def load_silver_df(silver_bucket: str, snapshot_date: str, region: str) -> pd.DataFrame:
    """Reads today's silver partition from S3 into a pandas DataFrame.

    Uses boto3 + pyarrow directly — avoids pafs.S3FileSystem which is unreliable
    in Glue Python Shell (same issue as pd.read_parquet engine discovery in Chat 15/16).
    """
    import io

    import boto3
    import pyarrow as pa
    import pyarrow.parquet as pq

    s3 = boto3.client("s3", region_name=region)
    prefix = f"snapshot_date={snapshot_date}/"
    print(f"[GE] Listing s3://{silver_bucket}/{prefix}")

    paginator = s3.get_paginator("list_objects_v2")
    tables = []
    for page in paginator.paginate(Bucket=silver_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                body = s3.get_object(Bucket=silver_bucket, Key=obj["Key"])["Body"].read()
                tables.append(pq.read_table(io.BytesIO(body)))

    if not tables:
        raise FileNotFoundError(
            f"No Parquet files found at s3://{silver_bucket}/{prefix} — "
            "check that the bronze→silver Glue job ran successfully for this snapshot_date."
        )

    df = pa.concat_tables(tables).to_pandas()
    # Arrow list columns arrive as numpy arrays, which GE cannot type-check; make them Python
    # lists. Anything that is not an array (e.g. a string from schema drift) is left as-is
    # so the tags expectation catches it.
    df["tags"] = df["tags"].map(lambda v: v.tolist() if isinstance(v, np.ndarray) else v)
    print(f"[GE] Loaded {len(df):,} rows from {len(tables)} Parquet files")
    return df


def add_ingested_date_ist(df: pd.DataFrame) -> pd.DataFrame:
    """IST calendar day each row was ingested, computed from the data itself.

    Before Chat 25 the check compared snapshot_date to a value the job had just written into
    the column — it could never fail. ingested_at comes from the bronze file, so a stale or
    misplaced partition (yesterday's data under today's date) now fails the gate.
    """
    df = df.copy()
    ingested = pd.to_datetime(df["ingested_at"], utc=True)
    df["ingested_date_ist"] = (ingested + IST).dt.strftime("%Y-%m-%d")
    return df


def validate_silver(df: pd.DataFrame, snapshot_date: str) -> None:
    """
    Runs GE expectations on the silver DataFrame.

    Uses an ephemeral in-memory context — no great_expectations.yml or S3 Data Docs needed.
    Raises ValueError on any failed expectation so the Glue job exits non-zero.

    Expectations:
      1. job_id       — no nulls
      2. title        — no nulls
      3. ingested_at  — no nulls
      4. row count    — > MIN_ROW_COUNT (volume sanity check)
      5. ingested_date_ist — only {snapshot_date} (freshness, read from the data)
      6. tags         — every value is a list (Aug 2026 drift: a source sent a string)
    """
    df = add_ingested_date_ist(df)

    context = gx.get_context(mode="ephemeral")

    datasource = context.data_sources.add_pandas("pandas_datasource")
    asset = datasource.add_dataframe_asset(name="silver_jobs")
    batch_def = asset.add_batch_definition_whole_dataframe(name="full_batch")

    suite = context.suites.add(gx.ExpectationSuite(name="silver_jobs_suite"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="job_id"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="title"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="ingested_at"))
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=MIN_ROW_COUNT))
    # Freshness: every row in this partition was ingested on the partition's IST date
    suite.add_expectation(
        gx.expectations.ExpectColumnDistinctValuesToBeInSet(
            column="ingested_date_ist",
            value_set=[snapshot_date],
        )
    )
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeOfType(column="tags", type_="list"))

    vd = context.validation_definitions.add(
        gx.ValidationDefinition(
            name="silver_jobs_validation",
            data=batch_def,
            suite=suite,
        )
    )
    checkpoint = context.checkpoints.add(
        gx.Checkpoint(
            name="silver_jobs_checkpoint",
            validation_definitions=[vd],
        )
    )

    result = checkpoint.run(batch_parameters={"dataframe": df})

    if not result.success:
        details = result.describe()
        print(f"[GE] FAILED:\n{json.dumps(details, default=str, indent=2)}")
        raise ValueError("[GE] Silver data quality check failed — see CloudWatch logs for details")

    print(f"[GE] All 6 expectations passed. Rows: {len(df):,}. Proceeding to dbt.")


if __name__ == "__main__":
    import argparse
    import sys

    # Flush every print to CloudWatch now, not at exit — a timed-out run otherwise logs nothing.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot_date", required=True)
    parser.add_argument("--silver_bucket", required=True)
    parser.add_argument("--region", default="ap-south-1")
    args, _ = parser.parse_known_args()

    df = load_silver_df(args.silver_bucket, args.snapshot_date, args.region)
    validate_silver(df, args.snapshot_date)
