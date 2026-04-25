"""
Unit tests for the GenAI enrichment layer.
Run: pytest tests/test_genai.py -v

All Claude API calls and S3 operations are mocked.
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy")


# ---------------------------------------------------------------------------
# SkillExtractor tests
# ---------------------------------------------------------------------------

class TestSkillExtractorRuleBased(unittest.TestCase):

    def setUp(self):
        from genai.guardrails import BudgetTracker
        from genai.skill_extractor import SkillExtractor
        self.extractor = SkillExtractor(MagicMock(), MagicMock(spec=BudgetTracker))

    def test_extracts_known_skills(self):
        desc = "We need Python, SQL, AWS, Docker, Terraform, and dbt experience. Senior role."
        result, source = self.extractor.extract(desc)
        self.assertEqual(source, "rules")
        self.assertIn("python", result.skills)
        self.assertIn("sql", result.skills)
        self.assertIn("aws", result.skills)

    def test_detects_senior_seniority(self):
        desc = "Senior Data Engineer. Must know Python, SQL, AWS, Spark, dbt, Airflow."
        result, source = self.extractor.extract(desc)
        self.assertEqual(result.seniority, "senior")
        self.assertEqual(source, "rules")

    def test_detects_yoe_from_description(self):
        desc = "Requires 3+ years of experience with Python, SQL, AWS, dbt, Airflow, Spark. Senior preferred."
        result, _ = self.extractor.extract(desc)
        self.assertEqual(result.yoe_required, 3)

    def test_yoe_not_set_when_absent(self):
        desc = "Senior Data Engineer. Python, SQL, AWS, Spark, dbt, Airflow required."
        result, _ = self.extractor.extract(desc)
        self.assertIsNone(result.yoe_required)

    def test_vocabulary_whitelist_enforced(self):
        desc = "FakeSkill9999 and Python, SQL, AWS, Docker, Terraform experience. Senior engineer."
        result, _ = self.extractor.extract(desc)
        self.assertNotIn("fakeskill9999", result.skills)
        self.assertIn("python", result.skills)

    def test_unknown_seniority_falls_back_gracefully(self):
        from genai.skill_extractor import SkillExtractor
        mock_budget = MagicMock()
        mock_budget.check_and_increment.side_effect = Exception("mock budget block")
        extractor = SkillExtractor(MagicMock(), mock_budget)
        desc = "We need Python and SQL."  # <5 skills, seniority unknown -> tries LLM -> fails -> rules
        result, source = extractor.extract(desc)
        self.assertEqual(source, "rules")

    def test_llm_path_parses_mock_response(self):
        from genai.skill_extractor import SkillExtractor

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"skills": ["python", "aws"], "seniority": "mid", "yoe_required": 2}')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 30
        mock_response.usage.cache_read_input_tokens = 0

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        mock_budget = MagicMock()
        mock_budget.check_and_increment.return_value = None
        mock_budget.record_actual_usage.return_value = 0.001

        extractor = SkillExtractor(mock_client, mock_budget)
        result, source = extractor.extract("We need Python and AWS.")  # <5 skills -> LLM

        self.assertEqual(source, "llm")
        self.assertIn("python", result.skills)
        self.assertEqual(result.seniority, "mid")
        self.assertEqual(result.yoe_required, 2)


# ---------------------------------------------------------------------------
# MatchScorer tests
# ---------------------------------------------------------------------------

class TestMatchScorer(unittest.TestCase):

    def setUp(self):
        from genai.match_scorer import MatchScorer
        self.scorer = MatchScorer({
            "skills": ["python", "sql", "aws", "dbt", "pyspark"],
            "seniority": "mid",
            "yoe": 2,
            "preferred_locations": ["remote", "india"],
            "preferred_role_families": ["DATA", "SDE"],
            "salary_min_usd": 60000,
            "weights": {
                "skill_overlap":   40,
                "seniority_fit":   20,
                "location_fit":    15,
                "role_family_fit": 15,
                "salary_fit":       5,
                "freshness":        5,
            },
        })

    def _job(self, **overrides):
        base = {
            "job_id": "j1", "snapshot_date": "2026-04-19",
            "salary_raw": "100000 USD", "job_type": "full_time_remote",
            "location_raw": "remote", "role_family": "DATA",
            "country": "us", "publication_date": date.today().isoformat(),
        }
        base.update(overrides)
        return base

    def _extraction(self, skills=None, seniority="mid", yoe_required=None):
        from genai.guardrails import ExtractionResult
        return ExtractionResult(
            skills=skills or ["python", "sql", "aws", "dbt", "pyspark"],
            seniority=seniority,
            yoe_required=yoe_required,
        )

    def test_perfect_match_approaches_100(self):
        score, _ = self.scorer.score(self._extraction(), self._job())
        self.assertGreaterEqual(score, 95.0)

    def test_zero_skill_overlap_reduces_score(self):
        score, detail = self.scorer.score(self._extraction(skills=["java", "kubernetes", "helm"]), self._job())
        self.assertEqual(detail["skill_overlap"], 0.0)
        self.assertLessEqual(score, 60.0)

    def test_yoe_exact_match_full_points(self):
        _, detail = self.scorer.score(self._extraction(yoe_required=2), self._job())
        self.assertEqual(detail["seniority_fit"], 20.0)

    def test_yoe_gap_1_gives_75_percent(self):
        _, detail = self.scorer.score(self._extraction(yoe_required=3), self._job())
        self.assertEqual(detail["seniority_fit"], 15.0)  # 20 * 0.75

    def test_yoe_gap_2_gives_50_percent(self):
        _, detail = self.scorer.score(self._extraction(yoe_required=4), self._job())
        self.assertEqual(detail["seniority_fit"], 10.0)  # 20 * 0.50

    def test_yoe_gap_over_2_gives_zero(self):
        _, detail = self.scorer.score(self._extraction(yoe_required=6), self._job())
        self.assertEqual(detail["seniority_fit"], 0.0)

    def test_yoe_none_falls_back_to_seniority_distance(self):
        # user=mid, job=senior -> distance 1 -> 50%
        _, detail = self.scorer.score(self._extraction(seniority="senior", yoe_required=None), self._job())
        self.assertEqual(detail["seniority_fit"], 10.0)

    def test_salary_below_minimum_zero(self):
        _, detail = self.scorer.score(self._extraction(), self._job(salary_raw="40000 USD"))
        self.assertEqual(detail["salary_fit"], 0.0)

    def test_unknown_salary_full_points(self):
        _, detail = self.scorer.score(self._extraction(), self._job(salary_raw="Competitive"))
        self.assertEqual(detail["salary_fit"], 5.0)

    def test_old_job_zero_freshness(self):
        _, detail = self.scorer.score(self._extraction(), self._job(publication_date="2025-01-01"))
        self.assertEqual(detail["freshness"], 0.0)

    def test_detail_keys_match_weight_keys(self):
        _, detail = self.scorer.score(self._extraction(), self._job())
        self.assertEqual(
            set(detail.keys()),
            {"skill_overlap", "seniority_fit", "location_fit", "role_family_fit", "salary_fit", "freshness"},
        )


# ---------------------------------------------------------------------------
# Pydantic schema validation tests
# ---------------------------------------------------------------------------

class TestSchemaValidation(unittest.TestCase):

    def test_valid_enrichment_record(self):
        from genai.guardrails import EnrichmentRecord
        r = EnrichmentRecord(
            job_id="j1", snapshot_date="2026-04-19",
            skills=["python"], seniority="mid", yoe_required=3,
            match_score=75.5, score_detail={"skill_overlap": 32.0},
            extraction_source="rules", enriched_at="2026-04-19T10:00:00+00:00",
        )
        self.assertEqual(r.match_score, 75.5)
        self.assertEqual(r.yoe_required, 3)

    def test_match_score_out_of_range_raises(self):
        from genai.guardrails import EnrichmentRecord
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            EnrichmentRecord(
                job_id="j1", snapshot_date="2026-04-19",
                skills=[], seniority="mid", yoe_required=None,
                match_score=150.0,
                score_detail={}, extraction_source="rules",
                enriched_at="2026-04-19T10:00:00+00:00",
            )

    def test_extraction_result_whitelist(self):
        from genai.guardrails import ExtractionResult
        result = ExtractionResult(skills=["PYTHON", " SQL ", "fakeskill999", "aws"])
        self.assertIn("python", result.skills)
        self.assertIn("sql", result.skills)
        self.assertIn("aws", result.skills)
        self.assertNotIn("fakeskill999", result.skills)

    def test_extraction_result_yoe_optional(self):
        from genai.guardrails import ExtractionResult
        r1 = ExtractionResult(skills=["python"], seniority="mid", yoe_required=5)
        r2 = ExtractionResult(skills=["python"], seniority="mid")
        self.assertEqual(r1.yoe_required, 5)
        self.assertIsNone(r2.yoe_required)


if __name__ == "__main__":
    unittest.main()
