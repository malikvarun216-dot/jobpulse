"""
Glue Python Shell job — validates today's silver partition with Great Expectations.

Runs between RunGlueJob (bronze→silver) and RunDbtGold in Step Functions.
Reads the silver partition for today's snapshot_date into pandas, runs 5 expectations,
and raises on failure so Step Functions routes to PipelineFailure → SNS alert.
"""
import json

import great_expectations as gx
import pandas as pd


MIN_ROW_COUNT = 100


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
    # snapshot_date is a partition column in S3 path, not stored in Parquet data
    df["snapshot_date"] = snapshot_date
    print(f"[GE] Loaded {len(df):,} rows from {len(tables)} Parquet files")
    return df


def validate_silver(df: pd.DataFrame, snapshot_date: str) -> None:
    """
    Runs GE expectations on the silver DataFrame.

    Uses an ephemeral in-memory context — no great_expectations.yml or S3 Data Docs needed.
    Raises ValueError on any failed expectation so the Glue job exits non-zero.

    Expectations:
      1. job_id       — no nulls
      2. title        — no nulls
      3. snapshot_date — no nulls
      4. row count    — > MIN_ROW_COUNT (volume sanity check)
      5. snapshot_date distinct values — must be {snapshot_date} only (freshness check)
    """
    # snapshot_date already set as string in load_silver_df()
    df = df.copy()

    context = gx.get_context(mode="ephemeral")

    datasource = context.data_sources.add_pandas("pandas_datasource")
    asset = datasource.add_dataframe_asset(name="silver_jobs")
    batch_def = asset.add_batch_definition_whole_dataframe(name="full_batch")

    suite = context.suites.add(gx.ExpectationSuite(name="silver_jobs_suite"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="job_id"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="title"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="snapshot_date"))
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=MIN_ROW_COUNT))
    # Freshness: all distinct snapshot_date values must be exactly {snapshot_date}
    suite.add_expectation(
        gx.expectations.ExpectColumnDistinctValuesToBeInSet(
            column="snapshot_date",
            value_set=[snapshot_date],
        )
    )

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

    print(f"[GE] All 5 expectations passed. Rows: {len(df):,}. Proceeding to dbt.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot_date", required=True)
    parser.add_argument("--silver_bucket", required=True)
    parser.add_argument("--region", default="ap-south-1")
    args, _ = parser.parse_known_args()

    df = load_silver_df(args.silver_bucket, args.snapshot_date, args.region)
    validate_silver(df, args.snapshot_date)
