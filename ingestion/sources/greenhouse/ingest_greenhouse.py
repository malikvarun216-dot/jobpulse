"""
ingest_greenhouse.py
---------------------
Lambda function: fetches jobs from Greenhouse ATS boards (no auth needed),
writes raw JSON to S3 bronze layer, partitioned by snapshot_date and source.

API pattern: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
One request per company slug. Returns all open jobs for that company.
404 on unknown/inactive slug → silently skipped (company no longer uses Greenhouse).

Company slugs are hardcoded in SLUGS list (no external dependencies).
To add/remove companies: edit SLUGS list and redeploy.

Trigger  : EventBridge scheduled rule (daily, via Step Functions Parallel state)
Output   : s3://{BRONZE_BUCKET}/snapshot_date=YYYY-MM-DD/source=greenhouse/data.json.gz
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

API_BASE = "https://boards-api.greenhouse.io/v1/boards"

# Company slugs for Greenhouse boards (no YAML dependency needed)
SLUGS = [
    "stripe", "airbnb", "doordash", "pinterest", "figma", "notion", "asana",
    "robinhood", "coinbase", "brex", "plaid", "databricks", "snowflake",
    "confluent", "hashicorp", "netlify", "cockroachdb", "hubspot", "zendesk",
    "intercom", "dropbox", "squarespace", "twitch", "roblox", "duolingo",
    "canva", "gitlab", "mongodb", "elastic", "palantir",
]


def load_slugs() -> list[str]:
    return SLUGS


def fetch_company_jobs(slug: str) -> list[dict]:
    """Fetch all open jobs for one Greenhouse board. Returns [] on 404 (inactive board)."""
    url = f"{API_BASE}/{slug}/jobs"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "JobPipeline/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        jobs = body.get("jobs", [])
        logger.info("Greenhouse slug=%s | jobs=%d", slug, len(jobs))
        return jobs
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.warning("Greenhouse slug=%s not found (404) — skipping", slug)
            return []
        logger.error("HTTP %s on slug=%s", e.code, slug)
        raise


def normalize_jobs(raw_jobs: list[dict], slug: str) -> list[dict]:
    """Map Greenhouse fields to the canonical bronze schema."""
    normalized = []
    for job in raw_jobs:
        location = (job.get("location") or {}).get("name")

        # Use first department as a tag signal; offices give location context
        departments = [d.get("name") for d in job.get("departments", []) if d.get("name")]
        offices = [o.get("name") for o in job.get("offices", []) if o.get("name")]
        tags = departments + offices

        normalized.append({
            # Prefix slug so job_id is globally unique across all companies
            "job_id": f"greenhouse-{slug}-{job.get('id')}",
            "title": job.get("title"),
            # Greenhouse board API doesn't return company name; slug is the identifier
            "company_name": slug,
            "apply_url": job.get("absolute_url"),
            "description": None,  # list endpoint omits description; enrichment fills this
            "tags": tags,
            "location_raw": location,
            "salary": None,  # Greenhouse does not expose salary in the public board API
            "job_type": None,  # not available at list level
            "publication_date": job.get("updated_at"),
        })
    return normalized


def fetch_all_jobs(slugs: list[str]) -> list[dict]:
    """Iterate slugs sequentially; aggregate all normalized jobs."""
    all_jobs: list[dict] = []
    for slug in slugs:
        raw = fetch_company_jobs(slug)
        if raw:
            all_jobs.extend(normalize_jobs(raw, slug))
    return all_jobs


def build_s3_key(snapshot_date: str) -> str:
    return f"snapshot_date={snapshot_date}/source=greenhouse/data.json.gz"


def write_to_s3(jobs: list[dict], snapshot_date: str) -> str:
    import boto3
    s3_key = build_s3_key(snapshot_date)
    payload = json.dumps(
        {
            "snapshot_date": snapshot_date,
            "source": "greenhouse",
            "record_count": len(jobs),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "jobs": jobs,
        },
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")

    compressed = gzip.compress(payload)

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=BRONZE_BUCKET,
        Key=s3_key,
        Body=compressed,
        ContentType="application/json",
        ContentEncoding="gzip",
        Tagging="project=job-pipeline&layer=bronze&source=greenhouse",
    )

    s3_uri = f"s3://{BRONZE_BUCKET}/{s3_key}"
    logger.info("Written %d jobs to %s", len(jobs), s3_uri)
    return s3_uri


def lambda_handler(event: dict, context) -> dict:
    ist = timezone(timedelta(hours=5, minutes=30))
    snapshot_date = event.get("snapshot_date") or datetime.now(ist).strftime("%Y-%m-%d")
    dry_run = event.get("dry_run", False)

    slugs = load_slugs()
    logger.info(
        "START ingest_greenhouse | snapshot_date=%s | dry_run=%s | slugs=%d",
        snapshot_date, dry_run, len(slugs),
    )

    jobs = fetch_all_jobs(slugs)

    if not jobs:
        logger.warning("Greenhouse returned 0 jobs across all slugs.")
        return {
            "source": "greenhouse",
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

    logger.info("DONE ingest_greenhouse | jobs=%d | uri=%s", len(jobs), s3_uri)

    return {
        "source": "greenhouse",
        "snapshot_date": snapshot_date,
        "record_count": len(jobs),
        "s3_uri": s3_uri,
        "status": "OK",
    }
