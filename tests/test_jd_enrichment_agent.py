"""
tests/test_jd_enrichment_agent.py
------------------------------------
Unit tests for JDEnrichmentAgent: rules-first fast path, parallel processing,
cache path, budget fallback.

Run locally: pytest tests/test_jd_enrichment_agent.py -v
"""

import os
import sys
import unittest
from unittest.mock import patch, mock_open

# Minimal env vars so guardrails / boto3 imports don't explode
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from genai.guardrails import BudgetExceededError, ExtractionResult
from genai.skill_extractor import RULES_MIN_SKILLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROFILE_YAML = """\
profile:
  skills: [python, spark, sql, aws, dbt]
  seniority: senior
  yoe: 4
  preferred_locations: [remote, us]
  preferred_role_families: [DATA, SDE]
  salary_min_usd: 80000
weights:
  skill_overlap: 40
  seniority_fit: 20
  location_fit: 15
  role_family_fit: 15
  salary_fit: 5
  freshness: 5
"""


def _make_job(i: int, description: str = "") -> dict:
    return {
        "job_id": str(i),
        "snapshot_date": "2026-04-21",
        "title": f"Data Engineer {i}",
        "salary_raw": None,
        "job_type": "full-time",
        "role_family": "data_engineering",
        "country": "US",
        "location_raw": "New York, US",
        "publication_date": "2026-04-20",
        "description": description,
    }


def _rich_description() -> str:
    """A description with enough skills (≥5) and clear seniority for the rules fast path."""
    return (
        "Senior Data Engineer — Python, Spark, SQL, Airflow, AWS, dbt, Kafka. "
        "5+ years of experience. Lead ETL pipelines, data modeling, data warehousing."
    )


def _sparse_description() -> str:
    """A description too thin for rules — triggers cache/LLM path."""
    return "We need someone great with data. Apply now."


def _make_agent(dry_run: bool = True):
    """Build a JDEnrichmentAgent with all external calls mocked."""
    from genai.jd_enrichment_agent import JDEnrichmentAgent

    with patch("genai.jd_enrichment_agent.BudgetTracker") as MockBudget, \
         patch("genai.jd_enrichment_agent.anthropic.Anthropic"), \
         patch("genai.jd_enrichment_agent.boto3.client"), \
         patch("builtins.open", mock_open(read_data=PROFILE_YAML)):
        mock_budget = MockBudget.return_value
        mock_budget.current_spend.return_value = 0.0
        agent = JDEnrichmentAgent(
            gold_bucket="test-bucket",
            region="ap-south-1",
            profile_path="/fake/profile.yml",
            dry_run=dry_run,
        )
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRulesFastPath(unittest.TestCase):
    """Rules-sufficient JDs must bypass S3 cache and return extraction_source='rules'."""

    def test_rules_fast_path_skips_cache(self):
        agent = _make_agent()
        job = _make_job(1, _rich_description())

        with patch.object(agent, "_read_cache") as mock_cache, \
             patch.object(agent, "_write_cache") as mock_write:
            record = agent._process_job(job)

        mock_cache.assert_not_called()
        mock_write.assert_not_called()
        self.assertEqual(record.extraction_source, "rules")
        self.assertGreaterEqual(len(record.skills), RULES_MIN_SKILLS)
        self.assertNotEqual(record.seniority, "unknown")

    def test_rules_fast_path_score_in_range(self):
        agent = _make_agent()
        job = _make_job(1, _rich_description())
        record = agent._process_job(job)
        self.assertGreaterEqual(record.match_score, 0.0)
        self.assertLessEqual(record.match_score, 100.0)


class TestCachePath(unittest.TestCase):
    """Sparse JDs that miss fast path must check S3 cache before calling LLM."""

    def test_cache_hit_skips_llm(self):
        agent = _make_agent()
        job = _make_job(2, _sparse_description())
        cached = ExtractionResult(skills=["python", "sql"], seniority="mid", yoe_required=3)

        with patch.object(agent, "_read_cache", return_value=cached) as mock_cache, \
             patch.object(agent._extractor, "extract") as mock_llm:
            record = agent._process_job(job)

        mock_cache.assert_called_once()
        mock_llm.assert_not_called()
        self.assertEqual(record.extraction_source, "cache")

    def test_cache_miss_calls_extractor(self):
        agent = _make_agent()
        job = _make_job(3, _sparse_description())
        llm_result = ExtractionResult(skills=["python", "sql"], seniority="junior", yoe_required=1)

        with patch.object(agent, "_read_cache", return_value=None), \
             patch.object(agent._extractor, "extract", return_value=(llm_result, "llm")) as mock_llm, \
             patch.object(agent, "_write_cache"):
            record = agent._process_job(job)

        mock_llm.assert_called_once()
        self.assertEqual(record.extraction_source, "llm")


class TestBudgetFallback(unittest.TestCase):
    """When budget is exceeded during LLM, must fall back to rules result (already computed)."""

    def test_budget_exceeded_uses_rules_result(self):
        agent = _make_agent()
        job = _make_job(4, _sparse_description())

        with patch.object(agent, "_read_cache", return_value=None), \
             patch.object(agent._extractor, "extract", side_effect=BudgetExceededError("cap")), \
             patch.object(agent, "_write_cache") as mock_write:
            record = agent._process_job(job)

        # Falls back to rules result — cache write must NOT happen (rules are cheap, no point caching)
        mock_write.assert_not_called()
        self.assertEqual(record.extraction_source, "rules")


class TestParallelRun(unittest.TestCase):
    """run() with ThreadPoolExecutor must process all jobs and return correct record count."""

    def test_parallel_run_processes_all_jobs(self):
        agent = _make_agent(dry_run=True)
        # Use rich descriptions so fast path handles all — no S3/LLM calls needed
        jobs = [_make_job(i, _rich_description()) for i in range(20)]

        result = agent.run(jobs)

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["records_enriched"], 20)
        self.assertEqual(result["snapshot_date"], "2026-04-21")

    def test_parallel_run_skips_failed_jobs(self):
        agent = _make_agent(dry_run=True)
        jobs = [_make_job(i, _rich_description()) for i in range(5)]

        # Make _process_job raise on job 2 to verify skip-and-continue
        original = agent._process_job

        def patched(job):
            if job["job_id"] == "2":
                raise RuntimeError("simulated failure")
            return original(job)

        agent._process_job = patched
        result = agent.run(jobs)

        self.assertEqual(result["records_enriched"], 4)


class TestBudgetTrackerThreadSafety(unittest.TestCase):
    """BudgetTracker with threading.Lock must not lose updates under concurrent access."""

    def test_lock_exists_on_budget_tracker(self):
        import threading
        from genai.guardrails import BudgetTracker

        with patch("genai.guardrails.boto3.client"):
            tracker = BudgetTracker("test-bucket", "ap-south-1")

        self.assertTrue(hasattr(tracker, "_lock"))
        self.assertIsInstance(tracker._lock, type(threading.Lock()))


if __name__ == "__main__":
    unittest.main()
