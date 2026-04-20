"""
ingest_remoteok.py
-------------------
Lambda function: fetches all jobs from RemoteOK public API (no key needed),
writes raw JSON to S3 bronze layer, partitioned by snapshot_date and source.

Trigger  : EventBridge scheduled rule (daily, via Step Functions Parallel state)
Output   : s3://{BRONZE_BUCKET}/snapshot_date=YYYY-MM-DD/source=remoteok/data.json.gz
Idempotent: YES — same run date always writes to same S3 key (safe to retry)

ToS note: RemoteOK requires attribution — source=remoteok is recorded on every row.
"""

import gzip
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BRONZE_BUCKET = os.environ["BRONZE_BUCKET"]

API_URL = "https://remoteok.com/api"


def fetch_all_jobs() -> list[dict]:
    logger.info("Fetching: %s", API_URL)

    try:
        req = urllib.request.Request(
            API_URL,
            headers={
                "Accept": "application/json",
                # RemoteOK blocks generic UAs — use a standard browser UA to pass
                "User-Agent": "Mozilla/5.0 (compatible; JobPulse/1.0)",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.error("HTTP %s on %s", e.code, API_URL)
        raise
    except Exception as e:
        logger.error("Request failed: %s", str(e))
        raise

    # First element is a legal notice dict (has "legal" key, no "id"+"position")
    jobs = [item for item in body if isinstance(item, dict) and "id" in item and "position" in item]
    logger.info("Fetched %d jobs (raw array length=%d)", len(jobs), len(body))
    return jobs


def build_salary_raw(job: dict) -> str | None:
    sal_min = job.get("salary_min")
    sal_max = job.get("salary_max")
    if sal_min and sal_max:
        return f"${sal_min}-${sal_max}"
    if sal_min:
        return f"${sal_min}+"
    if sal_max:
        return f"up to ${sal_max}"
    return None


def normalize_jobs(jobs: list[dict]) -> list[dict]:
    """Map RemoteOK fields to the canonical bronze schema used by all ingestors."""
    normalized = []
    for job in jobs:
        normalized.append({
            "job_id": str(job.get("id", "")),
            "title": job.get("position"),
            "company_name": job.get("company"),
            "apply_url": job.get("apply_url") or job.get("url"),
            "description": job.get("description"),
            "tags": job.get("tags") or [],
            "location_raw": job.get("location"),
            "salary": build_salary_raw(job),
            "publication_date": job.get("date"),
        })
    return normalized


def build_s3_key(snapshot_date: str) -> str:
    return f"snapshot_date={snapshot_date}/source=remoteok/data.json.gz"


def write_to_s3(jobs: list[dict], snapshot_date: str) -> str:
    s3_key = build_s3_key(snapshot_date)
    payload = json.dumps(
        {
            "snapshot_date": snapshot_date,
            "source": "remoteok",
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
        Tagging="project=job-pipeline&layer=bronze&source=remoteok",
    )

    s3_uri = f"s3://{BRONZE_BUCKET}/{s3_key}"
    logger.info("Written %d jobs to %s", len(jobs), s3_uri)
    return s3_uri


def lambda_handler(event: dict, context) -> dict:
    snapshot_date = event.get("snapshot_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dry_run = event.get("dry_run", False)

    logger.info("START ingest_remoteok | snapshot_date=%s | dry_run=%s", snapshot_date, dry_run)

    raw_jobs = fetch_all_jobs()

    if not raw_jobs:
        logger.warning("RemoteOK returned 0 jobs — possible API issue or empty feed.")
        return {
            "source": "remoteok",
            "snapshot_date": snapshot_date,
            "record_count": 0,
            "s3_uri": None,
            "status": "EMPTY",
        }

    jobs = normalize_jobs(raw_jobs)

    s3_uri = None
    if not dry_run:
        s3_uri = write_to_s3(jobs, snapshot_date)
    else:
        logger.info("DRY RUN — skipping S3 write. Would have written %d jobs.", len(jobs))

    logger.info("DONE ingest_remoteok | jobs=%d | uri=%s", len(jobs), s3_uri)

    return {
        "source": "remoteok",
        "snapshot_date": snapshot_date,
        "record_count": len(jobs),
        "s3_uri": s3_uri,
        "status": "OK",
    }
