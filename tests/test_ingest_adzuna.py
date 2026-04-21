"""
tests/test_ingest_adzuna.py
-------------------------------
Run locally: pytest tests/test_ingest_adzuna.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

os.environ["BRONZE_BUCKET"]   = "test-bronze-bucket"
os.environ["ADZUNA_APP_ID"]   = "test-app-id"
os.environ["ADZUNA_APP_KEY"]  = "test-app-key"
os.environ["AWS_REGION"]      = "ap-south-1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion", "sources", "adzuna"))
import ingest_adzuna as sut


def make_raw_job(i: int, country: str = "gb", salary_min=None, salary_max=None) -> dict:
    return {
        "id": f"adzuna-{country}-{1000 + i}",
        "title": f"Data Engineer {i}",
        "company": {"display_name": "TechCorp"},
        "location": {"display_name": f"London, Buckinghamshire"},
        "salary_min": salary_min,
        "salary_max": salary_max,
        "contract_time": "full_time",
        "created": "2026-04-20T12:00:00Z",
        "redirect_url": f"https://www.adzuna.co.uk/land/vacancy/{1000 + i}",
        "category": {"label": "IT Jobs", "tag": "it-jobs"},
        "description": f"Job description {i}",
        "_country": country,
    }


def make_api_response(jobs: list[dict], total: int | None = None) -> bytes:
    total = total if total is not None else len(jobs)
    return json.dumps({"results": jobs, "count": total}).encode()


class TestBuildSalaryStr(unittest.TestCase):

    def test_both_fields_returns_range(self):
        result = sut.build_salary_str(80000, 120000)
        self.assertEqual(result, "$80000-$120000")

    def test_min_only_returns_plus_format(self):
        result = sut.build_salary_str(60000, None)
        self.assertEqual(result, "$60000+")

    def test_max_only_returns_up_to_format(self):
        result = sut.build_salary_str(None, 100000)
        self.assertEqual(result, "up to $100000")

    def test_neither_returns_none(self):
        result = sut.build_salary_str(None, None)
        self.assertIsNone(result)

    def test_floats_are_truncated(self):
        result = sut.build_salary_str(80000.9, 120000.1)
        self.assertEqual(result, "$80000-$120000")


class TestFetchCountryJobs(unittest.TestCase):

    def _make_urlopen_mock(self, responses: list[bytes]) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.read.side_effect = responses
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("ingest_adzuna.urllib.request.urlopen")
    def test_paginates_until_empty_results(self, mock_urlopen):
        responses = [
            make_api_response([make_raw_job(i) for i in range(3)], total=100),
            make_api_response([]),  # empty page → stop
        ]
        mock_urlopen.return_value = self._make_urlopen_mock(responses)

        jobs = sut.fetch_country_jobs("gb")
        self.assertEqual(len(jobs), 3)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("ingest_adzuna.urllib.request.urlopen")
    def test_stops_when_all_results_fetched(self, mock_urlopen):
        jobs_data = [make_raw_job(i) for i in range(3)]
        responses = [make_api_response(jobs_data, total=3)]  # total=3 = len → stop after page 1
        mock_urlopen.return_value = self._make_urlopen_mock(responses)

        jobs = sut.fetch_country_jobs("gb")
        self.assertEqual(len(jobs), 3)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("ingest_adzuna.urllib.request.urlopen")
    def test_respects_max_pages_cap(self, mock_urlopen):
        # Always return 50 jobs with large total — cap at MAX_PAGES_PER_COUNTRY
        page_response = make_api_response([make_raw_job(i) for i in range(50)], total=99999)
        mock_resp = MagicMock()
        mock_resp.read.return_value = page_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        jobs = sut.fetch_country_jobs("gb")
        self.assertEqual(mock_urlopen.call_count, sut.MAX_PAGES_PER_COUNTRY)
        self.assertEqual(len(jobs), sut.MAX_PAGES_PER_COUNTRY * 50)


class TestFetchAllJobs(unittest.TestCase):

    @patch("ingest_adzuna.fetch_country_jobs")
    def test_fetches_all_countries(self, mock_fetch):
        mock_fetch.return_value = [make_raw_job(1)]

        jobs = sut.fetch_all_jobs()
        self.assertEqual(mock_fetch.call_count, len(sut.COUNTRIES))
        # Each job tagged with _country
        self.assertTrue(all("_country" in j for j in jobs))

    @patch("ingest_adzuna.fetch_country_jobs")
    def test_continues_on_country_failure(self, mock_fetch):
        # First country raises, rest succeed
        mock_fetch.side_effect = [Exception("API down")] + [[make_raw_job(1)] for _ in sut.COUNTRIES[1:]]

        jobs = sut.fetch_all_jobs()
        # Should have jobs from all countries except the first
        self.assertEqual(len(jobs), len(sut.COUNTRIES) - 1)


class TestNormalizeJobs(unittest.TestCase):

    def test_field_mapping(self):
        raw = [make_raw_job(1, country="gb")]
        normalized = sut.normalize_jobs(raw)
        job = normalized[0]

        self.assertEqual(job["job_id"], "adzuna-gb-1001")
        self.assertEqual(job["title"], "Data Engineer 1")
        self.assertEqual(job["company_name"], "TechCorp")
        self.assertEqual(job["apply_url"], "https://www.adzuna.co.uk/land/vacancy/1001")
        self.assertIn("UK", job["location_raw"])
        self.assertEqual(job["job_type"], "full-time")
        self.assertEqual(job["category"], "IT Jobs")
        self.assertEqual(job["publication_date"], "2026-04-20")
        self.assertEqual(job["tags"], [])

    def test_salary_both_fields(self):
        raw = [make_raw_job(1, salary_min=80000, salary_max=120000)]
        job = sut.normalize_jobs(raw)[0]
        self.assertEqual(job["salary"], "$80000-$120000")

    def test_salary_min_only(self):
        raw = [make_raw_job(1, salary_min=60000)]
        job = sut.normalize_jobs(raw)[0]
        self.assertEqual(job["salary"], "$60000+")

    def test_salary_neither_is_none(self):
        raw = [make_raw_job(1)]
        job = sut.normalize_jobs(raw)[0]
        self.assertIsNone(job["salary"])

    def test_location_raw_contains_country_label(self):
        raw = [make_raw_job(1, country="us")]
        job = sut.normalize_jobs(raw)[0]
        self.assertIn("US", job["location_raw"])

    def test_full_time_contract_time_normalised(self):
        raw = [make_raw_job(1)]
        raw[0]["contract_time"] = "full_time"
        job = sut.normalize_jobs(raw)[0]
        self.assertEqual(job["job_type"], "full-time")

    def test_missing_company_defaults_to_unknown(self):
        raw = [make_raw_job(1)]
        raw[0].pop("company")
        job = sut.normalize_jobs(raw)[0]
        self.assertEqual(job["company_name"], "Unknown")


class TestBuildS3Key(unittest.TestCase):

    def test_key_format(self):
        key = sut.build_s3_key("2026-04-21")
        self.assertEqual(key, "snapshot_date=2026-04-21/source=adzuna/data.json.gz")

    def test_hive_partition_structure(self):
        key = sut.build_s3_key("2026-04-21")
        self.assertIn("snapshot_date=", key)
        self.assertIn("source=adzuna", key)


class TestLambdaHandler(unittest.TestCase):

    @patch("ingest_adzuna.write_to_s3")
    @patch("ingest_adzuna.fetch_all_jobs")
    def test_happy_path(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_raw_job(1)]
        mock_write.return_value = "s3://test-bronze-bucket/snapshot_date=2026-04-21/source=adzuna/data.json.gz"

        result = sut.lambda_handler({"snapshot_date": "2026-04-21"}, None)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["source"], "adzuna")
        self.assertEqual(result["record_count"], 1)

    @patch("ingest_adzuna.fetch_all_jobs")
    def test_dry_run_skips_s3(self, mock_fetch):
        mock_fetch.return_value = [make_raw_job(1)]

        result = sut.lambda_handler({"snapshot_date": "2026-04-21", "dry_run": True}, None)
        self.assertIsNone(result["s3_uri"])
        self.assertEqual(result["status"], "OK")

    @patch("ingest_adzuna.fetch_all_jobs")
    def test_empty_jobs_returns_empty_status(self, mock_fetch):
        mock_fetch.return_value = []

        result = sut.lambda_handler({}, None)
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["record_count"], 0)

    @patch("ingest_adzuna.write_to_s3")
    @patch("ingest_adzuna.fetch_all_jobs")
    def test_defaults_snapshot_date_to_today(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_raw_job(1)]
        mock_write.return_value = "s3://..."

        result = sut.lambda_handler({}, None)
        from datetime import datetime
        datetime.strptime(result["snapshot_date"], "%Y-%m-%d")


if __name__ == "__main__":
    unittest.main()
