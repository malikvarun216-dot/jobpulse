"""
ingest_adzuna.py
---------------------
Lambda function: fetches jobs from Adzuna API v1 (app_id + app_key required),
writes raw JSON to S3 bronze layer, partitioned by snapshot_date and source.

Trigger  : EventBridge scheduled rule (daily, via Step Functions Parallel state)
Output   : s3://{BRONZE_BUCKET}/snapshot_date=YYYY-MM-DD/source=adzuna/data.json.gz
Idempotent: YES — same run date always writes to same S3 key (safe to retry)

Adzuna covers 7 countries: gb, us, au, ca, in, nz, za
Each country is paginated independently; results merged into a single S3 file.
"""

import gzip
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BRONZE_BUCKET = os.environ["BRONZE_BUCKET"]
ADZUNA_APP_ID = os.environ["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = os.environ["ADZUNA_APP_KEY"]

API_BASE = "https://api.adzuna.com/v1/api/jobs"
RESULTS_PER_PAGE = 50
MAX_PAGES_PER_COUNTRY = 6  # 6 × 50 = 300 jobs/country × 12 countries ≈ 3,600 total

COUNTRIES = ["gb", "us", "au", "ca", "in", "nz", "za"]

# Maps Adzuna 2-letter country codes to human-readable labels embedded in location_raw.
# The Spark extract_country() function looks for these strings in COUNTRY_MAP.
COUNTRY_LABELS = {
    "gb": "UK",
    "us": "US",
    "au": "AU",
    "ca": "CA",
    "in": "IN",
    "nz": "NZ",
    "za": "ZA",
}


def build_salary_str(salary_min, salary_max) -> str | None:
    """Format Adzuna's numeric salary fields into a parseable string."""
    has_min = salary_min is not None
    has_max = salary_max is not None
    if has_min and has_max:
        return f"${int(salary_min)}-${int(salary_max)}"
    if has_min:
        return f"${int(salary_min)}+"
    if has_max:
        return f"up to ${int(salary_max)}"
    return None


def fetch_country_jobs(country: str) -> list[dict]:
    """Fetch all pages for a single Adzuna country."""
    jobs = []
    for page in range(1, MAX_PAGES_PER_COUNTRY + 1):
        params = urllib.parse.urlencode({
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "results_per_page": RESULTS_PER_PAGE,
            "content-type": "application/json",
        })
        url = f"{API_BASE}/{country}/search/{page}?{params}"
        logger.info("Fetching: country=%s page=%d", country, page)

        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "JobPipeline/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.error("HTTP %s on %s/%s page %d", e.code, country, url, page)
            raise
        except Exception as e:
            logger.error("Request failed for country=%s page=%d: %s", country, page, str(e))
            raise

        page_results = body.get("results", [])
        if not page_results:
            logger.info("country=%s: empty page %d — stopping", country, page)
            break

        jobs.extend(page_results)
        logger.info("country=%s page=%d: %d jobs (running total: %d)", country, page, len(page_results), len(jobs))

        # Stop early if we've already seen all available results
        total_available = body.get("count", 0)
        if len(jobs) >= total_available:
            break

    return jobs


def fetch_all_jobs() -> list[dict]:
    """Fetch jobs from all 12 Adzuna countries in parallel (6 threads) and tag each with its country code."""
    all_jobs = []

    def _fetch_one(country: str) -> tuple[str, list[dict]]:
        jobs = fetch_country_jobs(country)
        for job in jobs:
            job["_country"] = country
        return country, jobs

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch_one, c): c for c in COUNTRIES}
        for future in as_completed(futures):
            country = futures[future]
            try:
                _, jobs = future.result()
                all_jobs.extend(jobs)
                logger.info("country=%s done: %d jobs", country, len(jobs))
            except Exception:
                logger.error("Skipping country=%s due to error", country)

    return all_jobs


def normalize_jobs(jobs: list[dict]) -> list[dict]:
    """Map Adzuna API fields to the canonical bronze schema."""
    normalized = []
    for job in jobs:
        country_code = job.get("_country", "")
        country_label = COUNTRY_LABELS.get(country_code, country_code.upper())

        location_display = (job.get("location") or {}).get("display_name", "")
        location_raw = f"{location_display}, {country_label}" if location_display else country_label

        publication_date = None
        created = job.get("created", "")
        if created:
            publication_date = created[:10]  # "2026-04-20T..." → "2026-04-20"

        normalized.append({
            "job_id": str(job.get("id", "")),
            "title": job.get("title", ""),
            "company_name": (job.get("company") or {}).get("display_name"),
            "apply_url": job.get("redirect_url", ""),
            "description": job.get("description", ""),
            "tags": [],  # Adzuna has no tags array; role_family inferred from category in Spark
            "location_raw": location_raw,
            "salary": build_salary_str(job.get("salary_min"), job.get("salary_max")),
            "job_type": (job.get("contract_time") or "").replace("_", "-"),
            "category": (job.get("category") or {}).get("label", ""),
            "publication_date": publication_date,
        })
    return normalized


def build_s3_key(snapshot_date: str) -> str:
    return f"snapshot_date={snapshot_date}/source=adzuna/data.json.gz"


def write_to_s3(jobs: list[dict], snapshot_date: str) -> str:
    s3_key = build_s3_key(snapshot_date)
    payload = json.dumps(
        {
            "snapshot_date": snapshot_date,
            "source": "adzuna",
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
        Tagging="project=job-pipeline&layer=bronze&source=adzuna",
    )

    s3_uri = f"s3://{BRONZE_BUCKET}/{s3_key}"
    logger.info("Written %d jobs to %s", len(jobs), s3_uri)
    return s3_uri


def lambda_handler(event: dict, context) -> dict:
    ist = timezone(timedelta(hours=5, minutes=30))
    snapshot_date = event.get("snapshot_date") or datetime.now(ist).strftime("%Y-%m-%d")
    dry_run = event.get("dry_run", False)

    logger.info("START ingest_adzuna | snapshot_date=%s | dry_run=%s", snapshot_date, dry_run)

    raw_jobs = fetch_all_jobs()

    if not raw_jobs:
        logger.warning("Adzuna returned 0 jobs — possible API issue.")
        return {
            "source": "adzuna",
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

    logger.info("DONE ingest_adzuna | jobs=%d | uri=%s", len(jobs), s3_uri)

    return {
        "source": "adzuna",
        "snapshot_date": snapshot_date,
        "record_count": len(jobs),
        "s3_uri": s3_uri,
        "status": "OK",
    }
