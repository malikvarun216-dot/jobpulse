"""
ingest_remotive.py
-------------------
Lambda function: fetches all jobs from Remotive public API (no key needed),
writes raw JSON to S3 bronze layer, partitioned by snapshot_date and source.

Trigger  : EventBridge scheduled rule (daily)
Output   : s3://{BRONZE_BUCKET}/snapshot_date=YYYY-MM-DD/source=remotive/data.json.gz
Idempotent: YES — same run date always writes to same S3 key (safe to retry)

Note: Remotive ToS limits requests to max 4x/day. Daily EventBridge schedule is compliant.
"""

import gzip
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BRONZE_BUCKET = os.environ["BRONZE_BUCKET"]

API_URL = "https://remotive.com/api/remote-jobs"


def fetch_all_jobs() -> list[dict]:
    logger.info("Fetching: %s", API_URL)

    try:
        req = urllib.request.Request(
            API_URL,
            headers={"Accept": "application/json", "User-Agent": "JobPipeline/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.error("HTTP %s on %s", e.code, API_URL)
        raise
    except Exception as e:
        logger.error("Request failed: %s", str(e))
        raise

    jobs = body.get("jobs", [])
    logger.info("Fetched %d jobs (total-job-count=%s)", len(jobs), body.get("total-job-count"))
    return jobs


def build_s3_key(snapshot_date: str) -> str:
    return f"snapshot_date={snapshot_date}/source=remotive/data.json.gz"


def write_to_s3(jobs: list[dict], snapshot_date: str) -> str:
    s3_key = build_s3_key(snapshot_date)
    payload = json.dumps(
        {
            "snapshot_date": snapshot_date,
            "source": "remotive",
            "record_count": len(jobs),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "jobs": jobs,
        },
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")

    compressed = gzip.compress(payload)

    import boto3
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=BRONZE_BUCKET,
        Key=s3_key,
        Body=compressed,
        ContentType="application/json",
        ContentEncoding="gzip",
        Tagging="project=job-pipeline&layer=bronze&source=remotive",
    )

    s3_uri = f"s3://{BRONZE_BUCKET}/{s3_key}"
    logger.info("Written %d jobs to %s", len(jobs), s3_uri)
    return s3_uri


def lambda_handler(event: dict, context) -> dict:
    ist = timezone(timedelta(hours=5, minutes=30))
    snapshot_date = datetime.now(ist).strftime("%Y-%m-%d")
    dry_run = event.get("dry_run", False)

    logger.info("START ingest_remotive | snapshot_date=%s | dry_run=%s", snapshot_date, dry_run)

    jobs = fetch_all_jobs()

    if not jobs:
        logger.warning("Remotive returned 0 jobs — possible API issue or empty feed.")
        return {
            "source": "remotive",
            "snapshot_date": snapshot_date,
            "record_count": 0,
            "s3_uri": None,
            "status": "EMPTY",
        }

    s3_uri = None
    if not dry_run:
        s3_uri = write_to_s3(jobs, snapshot_date)
    else:
        logger.info("DRY RUN — skipping S3 write. Would have written %d jobs.", len(jobs))

    logger.info("DONE ingest_remotive | jobs=%d | uri=%s", len(jobs), s3_uri)

    return {
        "source": "remotive",
        "snapshot_date": snapshot_date,
        "record_count": len(jobs),
        "s3_uri": s3_uri,
        "status": "OK",
    }
