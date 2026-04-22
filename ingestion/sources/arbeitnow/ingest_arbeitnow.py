"""
ingest_arbeitnow.py
---------------------
Lambda function: fetches jobs from Arbeitnow public API (no key needed),
writes raw JSON to S3 bronze layer, partitioned by snapshot_date and source.

Trigger  : EventBridge scheduled rule (daily, via Step Functions Parallel state)
Output   : s3://{BRONZE_BUCKET}/snapshot_date=YYYY-MM-DD/source=arbeitnow/data.json.gz
Idempotent: YES — same run date always writes to same S3 key (safe to retry)
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

API_URL = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 10  # safety cap (~1000 jobs max per run)


def fetch_all_jobs() -> list[dict]:
    """Paginate Arbeitnow API until no more results."""
    all_jobs = []
    page = 1

    while page <= MAX_PAGES:
        url = f"{API_URL}?page={page}"
        logger.info("Fetching: %s", url)

        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "JobPipeline/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.error("HTTP %s on %s", e.code, url)
            raise
        except Exception as e:
            logger.error("Request failed: %s", str(e))
            raise

        jobs = body.get("data", [])
        if not jobs:
            break

        all_jobs.extend(jobs)
        logger.info("Page %d: %d jobs (total so far: %d)", page, len(jobs), len(all_jobs))

        # Arbeitnow pagination: stop if last_page reached
        meta = body.get("meta", {})
        last_page = meta.get("last_page", page)
        if page >= last_page:
            break

        page += 1

    return all_jobs


def build_salary_raw(job: dict) -> str | None:
    return None  # Arbeitnow does not expose salary data


def normalize_jobs(jobs: list[dict]) -> list[dict]:
    """Map Arbeitnow fields to the canonical bronze schema used by all ingestors."""
    normalized = []
    for job in jobs:
        # job_types is an array like ["full-time"]; use first element
        job_types = job.get("job_types") or []
        job_type = job_types[0] if job_types else None

        # location: "Remote" if remote=True and location is blank
        location = job.get("location") or ("Remote" if job.get("remote") else None)

        # publication_date: Arbeitnow provides created_at as unix timestamp
        created_at = job.get("created_at")
        publication_date = None
        if created_at:
            try:
                publication_date = datetime.fromtimestamp(int(created_at), tz=timezone.utc).isoformat()
            except (ValueError, TypeError, OSError):
                pass

        normalized.append({
            "job_id": job.get("slug"),
            "title": job.get("title"),
            "company_name": job.get("company_name"),
            "apply_url": job.get("url"),
            "description": job.get("description"),
            "tags": job.get("tags") or [],
            "location_raw": location,
            "salary": None,
            "job_type": job_type,
            "publication_date": publication_date,
        })
    return normalized


def build_s3_key(snapshot_date: str) -> str:
    return f"snapshot_date={snapshot_date}/source=arbeitnow/data.json.gz"


def write_to_s3(jobs: list[dict], snapshot_date: str) -> str:
    s3_key = build_s3_key(snapshot_date)
    payload = json.dumps(
        {
            "snapshot_date": snapshot_date,
            "source": "arbeitnow",
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
        Tagging="project=job-pipeline&layer=bronze&source=arbeitnow",
    )

    s3_uri = f"s3://{BRONZE_BUCKET}/{s3_key}"
    logger.info("Written %d jobs to %s", len(jobs), s3_uri)
    return s3_uri


def lambda_handler(event: dict, context) -> dict:
    ist = timezone(timedelta(hours=5, minutes=30))
    snapshot_date = event.get("snapshot_date") or datetime.now(ist).strftime("%Y-%m-%d")
    dry_run = event.get("dry_run", False)

    logger.info("START ingest_arbeitnow | snapshot_date=%s | dry_run=%s", snapshot_date, dry_run)

    raw_jobs = fetch_all_jobs()

    if not raw_jobs:
        logger.warning("Arbeitnow returned 0 jobs — possible API issue or empty feed.")
        return {
            "source": "arbeitnow",
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

    logger.info("DONE ingest_arbeitnow | jobs=%d | uri=%s", len(jobs), s3_uri)

    return {
        "source": "arbeitnow",
        "snapshot_date": snapshot_date,
        "record_count": len(jobs),
        "s3_uri": s3_uri,
        "status": "OK",
    }
