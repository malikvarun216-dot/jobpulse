"""
Glue Python Shell entry point for the GenAI enrichment step.
Invoked by Step Functions after RunDbtGold.

Local usage (dry run):
  python genai/enrichment_runner.py \
    --gold_bucket jobpulse-gold-dev \
    --silver_bucket jobpulse-silver-dev \
    --region ap-south-1 \
    --workgroup jobpulse-dev \
    --gold_database jobpulse_gold_dev \
    --silver_database jobpulse_silver_dev \
    --dry_run true
"""

import argparse
import io
import json
import os
import sys
import time
import zipfile
from typing import Optional

import boto3
import pandas as pd

# ---------------------------------------------------------------------------
# Bootstrap: make genai package importable in both local dev and Glue 4.0.
# Glue Python Shell 4.0 downloads --extra-py-files but does NOT add them to
# sys.path automatically.  We detect the environment and load accordingly.
# ---------------------------------------------------------------------------

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GENAI_EXTRACT_DIR = "/tmp/genai_pkg"

_S3_PROFILE_KEY = "config/user_profile.yml"


def _try_download_profile_from_s3(silver_bucket: str, region: str) -> Optional[str]:
    """Download user_profile.yml from S3; return local path or None on failure."""
    local_path = "/tmp/user_profile.yml"
    try:
        boto3.client("s3", region_name=region).download_file(
            silver_bucket, _S3_PROFILE_KEY, local_path
        )
        print(f"[profile] Downloaded from s3://{silver_bucket}/{_S3_PROFILE_KEY}")
        return local_path
    except Exception as exc:
        print(f"[profile] S3 download failed ({exc}) -- using bundled profile")
        return None


if os.path.isdir(os.path.join(_repo_root, "genai")):
    # Local dev: script lives at genai/enrichment_runner.py; repo root has genai/
    sys.path.insert(0, _repo_root)
    _DEFAULT_PROFILE_PATH = os.path.join(_repo_root, "config", "user_profile.yml")
else:
    # Glue Python Shell: parse --silver_bucket early, download and extract zip
    import argparse as _ap
    _pre = _ap.ArgumentParser(add_help=False)
    _pre.add_argument("--silver_bucket", default="jobpulse-silver-dev")
    _pre.add_argument("--region",        default="ap-south-1")
    _preargs = _pre.parse_known_args()[0]
    _silver  = _preargs.silver_bucket
    _region  = _preargs.region
    _zip_local = "/tmp/genai_package.zip"
    boto3.client("s3").download_file(_silver, "glue-scripts/genai_package.zip", _zip_local)
    with zipfile.ZipFile(_zip_local) as _z:
        _z.extractall(_GENAI_EXTRACT_DIR)
    sys.path.insert(0, _GENAI_EXTRACT_DIR)
    # Try fresh S3 profile first; fall back to the one bundled in the zip
    _s3_profile = _try_download_profile_from_s3(_silver, _region)
    _DEFAULT_PROFILE_PATH = _s3_profile or os.path.join(_GENAI_EXTRACT_DIR, "config", "user_profile.yml")

from genai.jd_enrichment_agent import JDEnrichmentAgent

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser()
parser.add_argument("--gold_bucket",     required=True)
parser.add_argument("--silver_bucket",   required=True)
parser.add_argument("--region",          default="ap-south-1")
parser.add_argument("--workgroup",       default="jobpulse-dev")
parser.add_argument("--gold_database",   default="jobpulse_gold_dev")
parser.add_argument("--silver_database", default="jobpulse_silver_dev")
parser.add_argument("--snapshot_date",   default="")
parser.add_argument("--dry_run",         default="false")
parser.add_argument("--force_rescore",   default="false")
parser.add_argument("--profile_path",    default=_DEFAULT_PROFILE_PATH)
args, _ = parser.parse_known_args()

GOLD_BUCKET   = args.gold_bucket
SILVER_BUCKET = args.silver_bucket
REGION        = args.region
WORKGROUP     = args.workgroup
GOLD_DB       = args.gold_database
SILVER_DB     = args.silver_database
DRY_RUN          = args.dry_run.lower() == "true"
FORCE_RESCORE    = args.force_rescore.lower() == "true"
PROFILE_PATH  = args.profile_path
S3_STAGING    = f"s3://{GOLD_BUCKET}/athena-results/"


# ---------------------------------------------------------------------------
# Athena helpers
# ---------------------------------------------------------------------------

def _run_athena_query(sql: str, database: str) -> list[dict]:
    athena = boto3.client("athena", region_name=REGION)
    s3     = boto3.client("s3",     region_name=REGION)

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
        state  = result["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            output_loc = result["QueryExecution"]["ResultConfiguration"]["OutputLocation"]
            break
        if state in ("FAILED", "CANCELLED"):
            reason = result["QueryExecution"]["Status"].get("StateChangeReason", "unknown")
            raise RuntimeError(f"Athena query {state}: {reason}")
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)

    path   = output_loc.replace("s3://", "")
    bucket, key = path.split("/", 1)
    obj     = s3.get_object(Bucket=bucket, Key=key)
    content = obj["Body"].read()
    if not content.strip():          # Athena writes empty file when query returns 0 rows
        return []
    df  = pd.read_csv(io.BytesIO(content))
    df  = df.where(pd.notnull(df), None)   # NaN → None for clean downstream handling
    return df.to_dict(orient="records")


def fetch_jobs(snapshot_date: str) -> list[dict]:
    if not snapshot_date:
        rows = _run_athena_query(
            "SELECT MAX(snapshot_date) AS max_date FROM fact_job_posting",
            GOLD_DB,
        )
        snapshot_date = str(rows[0]["max_date"])
        print(f"No snapshot_date provided -- using latest: {snapshot_date}")

    sql = f"""
    SELECT
        f.job_id,
        f.snapshot_date,
        f.title,
        f.salary_raw,
        f.job_type,
        r.role_family,
        co.country,
        s.location_raw,
        s.publication_date,
        s.description
    FROM {GOLD_DB}.fact_job_posting f
    LEFT JOIN {GOLD_DB}.dim_role     r  ON f.role_key    = r.role_key
    LEFT JOIN {GOLD_DB}.dim_country  co ON f.country_key = co.country_key
    LEFT JOIN {SILVER_DB}.silver_jobs s
        ON f.job_id         = s.job_id
        AND s.snapshot_date = DATE '{snapshot_date}'
    WHERE f.snapshot_date = DATE '{snapshot_date}'
    """
    rows = _run_athena_query(sql, GOLD_DB)
    print(f"Fetched {len(rows)} jobs for snapshot_date={snapshot_date}")
    return rows


def repair_enrichment_partition() -> None:
    _run_athena_query("MSCK REPAIR TABLE enrichment_scores", GOLD_DB)
    print("Partition registered for enrichment_scores.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[enrichment_runner] START | dry_run={DRY_RUN} | force_rescore={FORCE_RESCORE}")

    jobs = fetch_jobs(args.snapshot_date)

    if not jobs:
        summary = {"status": "EMPTY", "records_enriched": 0}
        print(json.dumps(summary))
        sys.exit(0)

    agent = JDEnrichmentAgent(
        gold_bucket=GOLD_BUCKET,
        region=REGION,
        profile_path=PROFILE_PATH,
        dry_run=DRY_RUN,
        force_rescore=FORCE_RESCORE,
    )

    summary = agent.run(jobs)

    if not DRY_RUN:
        repair_enrichment_partition()

    print(json.dumps(summary))
