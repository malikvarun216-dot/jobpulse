from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import anthropic
import boto3
import pandas as pd
import yaml

from genai.guardrails import (
    BudgetExceededError,
    BudgetTracker,
    DAILY_CAP_USD,
    EnrichmentRecord,
    ExtractionResult,
)
from genai.skill_extractor import SkillExtractor, _rule_based_extract, RULES_MIN_SKILLS
from genai.match_scorer import MatchScorer


# Circuit breaker: after this many LLM failures in a row, stop calling the LLM for the
# rest of the run. One bad key or an empty credit balance then costs 5 calls, not 7,855.
LLM_BREAKER_THRESHOLD = 5
PROGRESS_EVERY = 1000


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class JDEnrichmentAgent:
    """
    Orchestrates the full enrichment flow for one snapshot.

    Pre-hooks  : validate_inputs, budget_preflight
    Per-job    : rules -> S3 cache -> LLM (only if use_llm) -> MatchScorer -> EnrichmentRecord
    Post-hooks : log_cost_summary, validate_output, write_parquet_to_s3
    """

    def __init__(self, gold_bucket: str, region: str, profile_path: str, dry_run: bool = False,
                 force_rescore: bool = False, use_llm: bool = False):
        self._gold_bucket = gold_bucket
        self._region = region
        self._dry_run = dry_run
        self._force_rescore = force_rescore
        self._use_llm = use_llm
        self._lock = threading.Lock()
        self._llm_failures_in_row = 0
        self._llm_errors = 0
        self._breaker_open = False
        self._seconds: dict[str, float] = defaultdict(float)  # time spent per extraction_source
        self._s3 = boto3.client("s3", region_name=region)
        with open(profile_path) as f:
            raw = yaml.safe_load(f)
        self._profile = {**raw["profile"], "weights": raw["weights"]}
        self._budget    = BudgetTracker(gold_bucket, region)
        self._client    = anthropic.Anthropic(api_key=self._get_api_key())
        self._extractor = SkillExtractor(self._client, self._budget)
        self._scorer    = MatchScorer(self._profile)

    # ------------------------------------------------------------------
    # Pre-hooks
    # ------------------------------------------------------------------

    def _validate_inputs(self, jobs: list[dict]) -> None:
        if not jobs:
            raise ValueError("No jobs passed to enrichment agent.")
        missing = {"job_id", "description", "snapshot_date"} - set(jobs[0].keys())
        if missing:
            raise ValueError(f"Job rows missing keys: {missing}")

    def _budget_preflight(self) -> None:
        spend = self._budget.current_spend()
        pct = (spend / DAILY_CAP_USD) * 100
        print(f"[pre-hook] Daily spend: ${spend:.4f} ({pct:.1f}% of ${DAILY_CAP_USD:.2f} cap)")
        if pct > 80:
            print("[pre-hook] WARNING: >80% of daily budget consumed.")

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_key(self, description: str) -> str:
        return f"enrichment-cache/{_md5(description)}.json"

    def _read_cache(self, description: str) -> ExtractionResult | None:
        if self._dry_run:
            return None
        try:
            obj = self._s3.get_object(Bucket=self._gold_bucket, Key=self._cache_key(description))
            return ExtractionResult(**json.loads(obj["Body"].read()))
        except Exception:
            return None

    def _write_cache(self, description: str, result: ExtractionResult) -> None:
        if self._dry_run:
            return
        self._s3.put_object(
            Bucket=self._gold_bucket,
            Key=self._cache_key(description),
            Body=result.model_dump_json().encode(),
            ContentType="application/json",
        )

    # ------------------------------------------------------------------
    # Per-job processing
    # ------------------------------------------------------------------

    def _record_llm_failure(self, exc: Exception) -> None:
        with self._lock:
            self._llm_errors += 1
            self._llm_failures_in_row += 1
            if self._llm_errors == 1:
                print(f"[llm] first failure: {type(exc).__name__}: {str(exc)[:300]}")
            if not self._breaker_open and self._llm_failures_in_row >= LLM_BREAKER_THRESHOLD:
                self._breaker_open = True
                print(f"[llm] {LLM_BREAKER_THRESHOLD} failures in a row -- circuit OPEN, "
                      "rules only for the rest of this run")

    def _extract(self, description: str) -> tuple[ExtractionResult, str]:
        """Cheapest source first: rules -> S3 cache -> LLM. Returns (extraction, source)."""
        # Fast path: pure regex, zero I/O.
        rules_result = _rule_based_extract(description)
        if len(rules_result.skills) >= RULES_MIN_SKILLS and rules_result.seniority != "unknown":
            return rules_result, "rules"

        # Empty JD (all Greenhouse rows until Chat 25): nothing to cache or send to the LLM.
        if not description.strip():
            return rules_result, "rules"

        cached = self._read_cache(description)
        if cached:
            return cached, "cache"

        # LLM off (default until Chat 31 measures it), force_rescore, or breaker open → rules.
        if not self._use_llm or self._force_rescore or self._breaker_open:
            return rules_result, "rules"

        try:
            extraction = self._extractor.extract_llm(description)
        except BudgetExceededError as e:
            print(f"[budget] {e} -- using rules")
            return rules_result, "rules"
        except Exception as e:
            self._record_llm_failure(e)
            return rules_result, "rules"

        with self._lock:
            self._llm_failures_in_row = 0
        self._write_cache(description, extraction)
        return extraction, "llm"

    def _process_job(self, job: dict[str, Any]) -> EnrichmentRecord:
        started = time.perf_counter()
        description = job.get("description") or ""
        extraction, source = self._extract(description)
        score, detail = self._scorer.score(extraction, job)
        record = EnrichmentRecord(
            job_id=str(job["job_id"]),
            snapshot_date=str(job["snapshot_date"])[:10],
            skills=extraction.skills,
            seniority=extraction.seniority,
            yoe_required=extraction.yoe_required,
            match_score=score,
            score_detail=detail,
            extraction_source=source,
            enriched_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._seconds[source] += time.perf_counter() - started
        return record

    # ------------------------------------------------------------------
    # Post-hooks
    # ------------------------------------------------------------------

    def _log_cost_summary(self, records: list[EnrichmentRecord]) -> None:
        spend = self._budget.current_spend()
        llm   = sum(1 for r in records if r.extraction_source == "llm")
        rules = sum(1 for r in records if r.extraction_source == "rules")
        cache = sum(1 for r in records if r.extraction_source == "cache")
        print(
            f"[post-hook] {len(records)} jobs enriched -- "
            f"llm={llm} rules={rules} cache={cache} | llm_errors={self._llm_errors} "
            f"breaker_open={self._breaker_open} | spend=${spend:.4f}"
        )
        # Summed across the 16 threads, so this is work time, not wall-clock time
        per_source = " ".join(f"{k}={v:.1f}s" for k, v in sorted(self._seconds.items()))
        print(f"[timing] per-job time by source: {per_source}")

    def _validate_output(self, records: list[EnrichmentRecord]) -> None:
        if not records:
            raise RuntimeError("Enrichment produced 0 records.")
        bad = [r for r in records if not (0 <= r.match_score <= 100)]
        if bad:
            raise RuntimeError(f"{len(bad)} records have out-of-range match_score.")
        print(f"[post-hook] Validation passed: {len(records)} records, all scores in [0,100].")

    def _write_parquet_to_s3(self, records: list[EnrichmentRecord], snapshot_date: str) -> str:
        if self._dry_run:
            print(f"[post-hook] DRY RUN -- would write {len(records)} records to S3.")
            return "dry-run"

        import pyarrow as pa       # noqa: PLC0415 — Glue-only dep, lazy to allow local testing
        import pyarrow.parquet as pq  # noqa: PLC0415

        rows = [{
            "job_id":            r.job_id,
            "snapshot_date":     r.snapshot_date,
            "skills":            r.skills,
            "seniority":         r.seniority,
            "yoe_required":      r.yoe_required,
            "match_score":       r.match_score,
            "score_detail":      json.dumps(r.score_detail),
            "extraction_source": r.extraction_source,
            "enriched_at":       r.enriched_at,
        } for r in records]

        df = pd.DataFrame(rows)
        schema = pa.schema([
            ("job_id",            pa.string()),
            ("snapshot_date",     pa.string()),
            ("skills",            pa.list_(pa.string())),
            ("seniority",         pa.string()),
            ("yoe_required",      pa.int32()),
            ("match_score",       pa.float64()),
            ("score_detail",      pa.string()),
            ("extraction_source", pa.string()),
            ("enriched_at",       pa.string()),
        ])
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pandas(df, schema=schema), buf, compression="snappy")
        buf.seek(0)

        s3_key = f"enrichment-scores/snapshot_date={snapshot_date}/data.parquet"
        self._s3.put_object(
            Bucket=self._gold_bucket, Key=s3_key,
            Body=buf.read(), ContentType="application/octet-stream",
        )
        s3_uri = f"s3://{self._gold_bucket}/{s3_key}"
        print(f"[post-hook] Written -> {s3_uri}")
        return s3_uri

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, jobs: list[dict[str, Any]]) -> dict:
        self._validate_inputs(jobs)
        self._budget_preflight()
        snapshot_date = str(jobs[0]["snapshot_date"])[:10]
        records: list[EnrichmentRecord] = []

        print(f"[run] {len(jobs)} jobs | use_llm={self._use_llm} | force_rescore={self._force_rescore}")
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(self._process_job, job): job for job in jobs}
            for done, future in enumerate(as_completed(futures), start=1):
                job = futures[future]
                try:
                    records.append(future.result())
                except Exception as e:
                    print(f"[warn] Skipped job {job.get('job_id')}: {e}")
                if done % PROGRESS_EVERY == 0:
                    print(f"[progress] {done}/{len(jobs)} jobs | {time.perf_counter() - started:.0f}s")

        self._log_cost_summary(records)
        self._validate_output(records)
        s3_uri = self._write_parquet_to_s3(records, snapshot_date)
        return {
            "status":           "OK",
            "records_enriched": len(records),
            "snapshot_date":    snapshot_date,
            "s3_uri":           s3_uri,
            "spend_usd":        round(self._budget.current_spend(), 4),
            "llm_errors":       self._llm_errors,
            "breaker_open":     self._breaker_open,
        }

    # ------------------------------------------------------------------
    # Private helper
    # ------------------------------------------------------------------

    def _get_api_key(self) -> str:
        env_key = os.environ.get("ANTHROPIC_API_KEY")
        if env_key:
            return env_key
        sm = boto3.client("secretsmanager", region_name=self._region)
        return sm.get_secret_value(SecretId="jobpulse/anthropic_key_dev")["SecretString"]
