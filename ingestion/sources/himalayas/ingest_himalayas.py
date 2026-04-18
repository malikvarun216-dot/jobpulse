"""
ingest_himalayas.py
--------------------
Lambda function: fetches all jobs from Himalayas public API (no key needed),
writes raw JSON to S3 bronze layer, partitioned by snapshot_date and source.

Trigger  : EventBridge scheduled rule (daily)
Output   : s3://{BRONZE_BUCKET}/snapshot_date=YYYY-MM-DD/source=himalayas/data.json.gz
Idempotent: YES — same run date always writes to same S3 key (safe to retry)
"""

import gzip
import json
import logging
import os
from datetime import datetime, timezone

import boto3
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BRONZE_BUCKET = os.environ["BRONZE_BUCKET"]
AWS_REGION    = os.environ.get("AWS_REGION", "ap-south-1")

BASE_URL   = "https://himalayas.app/jobs/api"
PAGE_LIMIT = 100


def fetch_all_jobs() -> list[dict]:
    all_jobs = []
    offset   = 0

    while True:
        url = f"{BASE_URL}?limit={PAGE_LIMIT}&offset={offset}"
        logger.info("Fetching: %s", url)

        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "JobPipeline/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.error("HTTP %s on %s", e.code, url)
            raise
        except Exception as e:
            logger.error("Request failed: %s", str(e))
            raise

        jobs = body.get("jobs", [])
        all_jobs.extend(jobs)

        logger.info("Page offset=%d → %d jobs (total so far: %d)", offset, len(jobs), len(all_jobs))

        if len(jobs) < PAGE_LIMIT:
            break

        offset += PAGE_LIMIT

    return all_jobs


def build_s3_key(snapshot_date: str) -> str:
    return f"snapshot_date={snapshot_date}/source=himalayas/data.json.gz"


def write_to_s3(jobs: list[dict], snapshot_date: str) -> str:
    s3_key  = build_s3_key(snapshot_date)
    payload = json.dumps(
        {
            "snapshot_date": snapshot_date,
            "source":        "himalayas",
            "record_count":  len(jobs),
            "ingested_at":   datetime.now(timezone.utc).isoformat(),
            "jobs":          jobs,
        },
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")

    compressed = gzip.compress(payload)

    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.put_object(
        Bucket          = BRONZE_BUCKET,
        Key             = s3_key,
        Body            = compressed,
        ContentType     = "application/json",
        ContentEncoding = "gzip",
        Tagging         = "project=job-pipeline&layer=bronze&source=himalayas",
    )

    s3_uri = f"s3://{BRONZE_BUCKET}/{s3_key}"
    logger.info("Written %d jobs to %s", len(jobs), s3_uri)
    return s3_uri


def lambda_handler(event: dict, context) -> dict:
    snapshot_date = event.get("snapshot_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dry_run       = event.get("dry_run", False)

    logger.info("START ingest_himalayas | snapshot_date=%s | dry_run=%s", snapshot_date, dry_run)

    jobs = fetch_all_jobs()

    if not jobs:
        logger.warning("Himalayas returned 0 jobs — possible API issue or empty feed.")
        return {
            "source":        "himalayas",
            "snapshot_date": snapshot_date,
            "record_count":  0,
            "s3_uri":        None,
            "status":        "EMPTY",
        }

    s3_uri = None
    if not dry_run:
        s3_uri = write_to_s3(jobs, snapshot_date)
    else:
        logger.info("DRY RUN — skipping S3 write. Would have written %d jobs.", len(jobs))

    logger.info("DONE ingest_himalayas | jobs=%d | uri=%s", len(jobs), s3_uri)

    return {
        "source":        "himalayas",
        "snapshot_date": snapshot_date,
        "record_count":  len(jobs),
        "s3_uri":        s3_uri,
        "status":        "OK",
    }