from __future__ import annotations

import hashlib
import io
import json
import os
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


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class JDEnrichmentAgent:
    """
    Orchestrates the full enrichment flow for one snapshot.

    Pre-hooks  : validate_inputs, budget_preflight
    Per-job    : S3 cache -> SkillExtractor -> MatchScorer -> EnrichmentRecord
    Post-hooks : log_cost_summary, validate_output, write_parquet_to_s3
    """

    def __init__(self, gold_bucket: str, region: str, profile_path: str, dry_run: bool = False, force_rescore: bool = False):
        self._gold_bucket = gold_bucket
        self._region = region
        self._dry_run = dry_run
        self._force_rescore = force_rescore
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

    def _process_job(self, job: dict[str, Any]) -> EnrichmentRecord:
        description = job.get("description") or ""
        now_iso = datetime.now(timezone.utc).isoformat()
        job_id = str(job["job_id"])
        snapshot_date = str(job["snapshot_date"])[:10]

        # Fast path: pure regex, zero I/O. Covers ~70% of well-written JDs.
        rules_result = _rule_based_extract(description)
        if len(rules_result.skills) >= RULES_MIN_SKILLS and rules_result.seniority != "unknown":
            score, detail = self._scorer.score(rules_result, job)
            return EnrichmentRecord(
                job_id=job_id,
                snapshot_date=snapshot_date,
                skills=rules_result.skills,
                seniority=rules_result.seniority,
                yoe_required=rules_result.yoe_required,
                match_score=score,
                score_detail=detail,
                extraction_source="rules",
                enriched_at=now_iso,
            )

        # Slow path: check S3 cache (rules were insufficient)
        cached = self._read_cache(description)
        if cached:
            score, detail = self._scorer.score(cached, job)
            return EnrichmentRecord(
                job_id=job_id,
                snapshot_date=snapshot_date,
                skills=cached.skills,
                seniority=cached.seniority,
                yoe_required=cached.yoe_required,
                match_score=score,
                score_detail=detail,
                extraction_source="cache",
                enriched_at=now_iso,
            )

        # force_rescore: skip LLM entirely — re-score using rules fallback
        if self._force_rescore:
            score, detail = self._scorer.score(rules_result, job)
            return EnrichmentRecord(
                job_id=job_id,
                snapshot_date=snapshot_date,
                skills=rules_result.skills,
                seniority=rules_result.seniority,
                yoe_required=rules_result.yoe_required,
                match_score=score,
                score_detail=detail,
                extraction_source="rules",
                enriched_at=now_iso,
            )

        # LLM path: reuse rules_result as fallback if budget exceeded
        try:
            extraction, source = self._extractor.extract(description)
        except BudgetExceededError as e:
            print(f"[budget] {e} -- using rules")
            extraction = rules_result
            source = "rules"

        score, detail = self._scorer.score(extraction, job)
        if source == "llm":
            self._write_cache(description, extraction)
        return EnrichmentRecord(
            job_id=job_id,
            snapshot_date=snapshot_date,
            skills=extraction.skills,
            seniority=extraction.seniority,
            yoe_required=extraction.yoe_required,
            match_score=score,
            score_detail=detail,
            extraction_source=source,
            enriched_at=now_iso,
        )

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
            f"llm={llm} rules={rules} cache={cache} | spend=${spend:.4f}"
        )

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

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(self._process_job, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    records.append(future.result())
                except Exception as e:
                    print(f"[warn] Skipped job {job.get('job_id')}: {e}")

        self._log_cost_summary(records)
        self._validate_output(records)
        s3_uri = self._write_parquet_to_s3(records, snapshot_date)
        return {
            "status":           "OK",
            "records_enriched": len(records),
            "snapshot_date":    snapshot_date,
            "s3_uri":           s3_uri,
            "spend_usd":        round(self._budget.current_spend(), 4),
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
