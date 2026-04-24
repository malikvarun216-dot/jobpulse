"""
Glue Python Shell entry point for the embedding step.
Invoked by Step Functions after RunEnrichment.

Local usage (dry run):
  python genai/embedding_runner.py \
    --gold_bucket jobpulse-gold-dev \
    --silver_bucket jobpulse-silver-dev \
    --region ap-south-1 \
    --workgroup jobpulse-dev \
    --gold_database jobpulse_gold_dev \
    --dry_run true
"""

import argparse
import io
import json
import os
import sys
import time
import zipfile

import boto3
import pandas as pd

# ---------------------------------------------------------------------------
# Bootstrap: make genai package importable in both local dev and Glue 4.0.
# ---------------------------------------------------------------------------

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GENAI_EXTRACT_DIR = "/tmp/genai_pkg_embed"

if os.path.isdir(os.path.join(_repo_root, "genai")):
    sys.path.insert(0, _repo_root)
else:
    import argparse as _ap
    _pre = _ap.ArgumentParser(add_help=False)
    _pre.add_argument("--silver_bucket", default="jobpulse-silver-dev")
    _silver = _pre.parse_known_args()[0].silver_bucket
    _zip_local = "/tmp/genai_package_embed.zip"
    boto3.client("s3").download_file(_silver, "glue-scripts/genai_package.zip", _zip_local)
    with zipfile.ZipFile(_zip_local) as _z:
        _z.extractall(_GENAI_EXTRACT_DIR)
    sys.path.insert(0, _GENAI_EXTRACT_DIR)

from genai.embedding_agent import EmbeddingAgent

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser()
parser.add_argument("--gold_bucket",   required=True)
parser.add_argument("--silver_bucket", required=True)
parser.add_argument("--region",        default="ap-south-1")
parser.add_argument("--workgroup",     default="jobpulse-dev")
parser.add_argument("--gold_database", default="jobpulse_gold_dev")
parser.add_argument("--snapshot_date", default="")
parser.add_argument("--dry_run",       default="false")
args, _ = parser.parse_known_args()

GOLD_BUCKET = args.gold_bucket
SILVER_BUCKET = args.silver_bucket
REGION = args.region
WORKGROUP = args.workgroup
GOLD_DB = args.gold_database
DRY_RUN = args.dry_run.lower() == "true"
S3_STAGING = f"s3://{GOLD_BUCKET}/athena-results/"


# ---------------------------------------------------------------------------
# Athena helpers (same pattern as enrichment_runner.py)
# ---------------------------------------------------------------------------

def _run_athena_query(sql: str, database: str) -> list:
    athena = boto3.client("athena", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": S3_STAGING},
    )
    exec_id = resp["QueryExecutionId"]

    delay = 1.0
    while True:
        result = athena.get_query_execution(QueryExecutionId=exec_id)
        state = result["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            output_loc = result["QueryExecution"]["ResultConfiguration"]["OutputLocation"]
            break
        if state in ("FAILED", "CANCELLED"):
            reason = result["QueryExecution"]["Status"].get("StateChangeReason", "unknown")
            raise RuntimeError(f"Athena query {state}: {reason}")
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)

    path = output_loc.replace("s3://", "")
    bucket, key = path.split("/", 1)
    obj = s3.get_object(Bucket=bucket, Key=key)
    content = obj["Body"].read()
    if not content.strip():
        return []
    df = pd.read_csv(io.BytesIO(content))
    df = df.where(pd.notnull(df), None)
    return df.to_dict(orient="records")


def fetch_jobs(snapshot_date: str) -> list:
    if not snapshot_date:
        rows = _run_athena_query(
            "SELECT MAX(snapshot_date) AS max_date FROM fact_job_posting",
            GOLD_DB,
        )
        snapshot_date = str(rows[0]["max_date"])
        print(f"[embedding_runner] Using latest snapshot_date: {snapshot_date}")

    sql = f"""
    SELECT job_id, snapshot_date, description
    FROM {GOLD_DB}.fact_job_posting
    WHERE snapshot_date = DATE '{snapshot_date}'
      AND description IS NOT NULL
    """
    rows = _run_athena_query(sql, GOLD_DB)
    print(f"[embedding_runner] Fetched {len(rows)} jobs for snapshot_date={snapshot_date}")
    return rows, snapshot_date


def repair_embeddings_partition() -> None:
    # Glue catalog doesn't auto-discover new S3 prefixes — MSCK REPAIR registers them.
    # embeddings table is not a managed Athena table, so we create it here if needed.
    create_sql = f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {GOLD_DB}.job_embeddings (
        job_id STRING,
        snapshot_date STRING,
        embedding ARRAY<FLOAT>
    )
    STORED AS PARQUET
    LOCATION 's3://{GOLD_BUCKET}/embeddings/'
    TBLPROPERTIES ('parquet.compress' = 'SNAPPY')
    """
    _run_athena_query(create_sql, GOLD_DB)
    _run_athena_query(f"MSCK REPAIR TABLE {GOLD_DB}.job_embeddings", GOLD_DB)
    print("[embedding_runner] Partition registered for job_embeddings.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[embedding_runner] START | dry_run={DRY_RUN}")

    jobs, snapshot_date = fetch_jobs(args.snapshot_date)

    if not jobs:
        summary = {"status": "EMPTY", "embedded_new": 0}
        print(json.dumps(summary))
        sys.exit(0)

    agent = EmbeddingAgent(
        gold_bucket=GOLD_BUCKET,
        region=REGION,
        dry_run=DRY_RUN,
    )

    summary = agent.run(jobs, snapshot_date)

    if not DRY_RUN:
        repair_embeddings_partition()

    print(json.dumps(summary))
