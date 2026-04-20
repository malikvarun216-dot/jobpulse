"""
tests/test_ingest_remoteok.py
------------------------------
Run locally: pytest tests/test_ingest_remoteok.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["BRONZE_BUCKET"] = "test-bronze-bucket"
os.environ["AWS_REGION"]    = "ap-south-1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion", "sources", "remoteok"))
import ingest_remoteok as sut


LEGAL_NOTICE = {
    "legal": "API Terms of Service: Please link back to RemoteOK.",
    "last_updated": 1776585633,
}

def make_raw_job(i: int, salary_min=None, salary_max=None) -> dict:
    return {
        "id": str(1000 + i),
        "position": f"Data Engineer {i}",
        "company": "Acme Corp",
        "apply_url": f"https://remoteok.com/jobs/{1000 + i}",
        "url": f"https://remoteok.com/jobs/{1000 + i}",
        "description": f"<p>Job description {i}</p>",
        "tags": ["python", "aws"],
        "location": "Remote",
        "date": "2026-04-18T10:00:00+00:00",
        "salary_min": salary_min,
        "salary_max": salary_max,
    }

FAKE_RAW_RESPONSE = [LEGAL_NOTICE, make_raw_job(1), make_raw_job(2), make_raw_job(3)]


class TestFetchAllJobs(unittest.TestCase):

    @patch("ingest_remoteok.urllib.request.urlopen")
    def test_skips_legal_notice(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(FAKE_RAW_RESPONSE).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        jobs = sut.fetch_all_jobs()
        # 4 elements total, 1 legal notice → 3 jobs
        self.assertEqual(len(jobs), 3)

    @patch("ingest_remoteok.urllib.request.urlopen")
    def test_empty_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([LEGAL_NOTICE]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        jobs = sut.fetch_all_jobs()
        self.assertEqual(jobs, [])


class TestBuildSalaryRaw(unittest.TestCase):

    def test_both_min_and_max(self):
        job = make_raw_job(1, salary_min=65000, salary_max=100000)
        result = sut.build_salary_raw(job)
        self.assertEqual(result, "$65000-$100000")

    def test_min_only(self):
        job = make_raw_job(1, salary_min=50000)
        result = sut.build_salary_raw(job)
        self.assertEqual(result, "$50000+")

    def test_max_only(self):
        job = make_raw_job(1, salary_max=80000)
        result = sut.build_salary_raw(job)
        self.assertEqual(result, "up to $80000")

    def test_no_salary(self):
        job = make_raw_job(1)
        result = sut.build_salary_raw(job)
        self.assertIsNone(result)


class TestNormalizeJobs(unittest.TestCase):

    def test_field_mapping(self):
        raw = [make_raw_job(1, salary_min=60000, salary_max=90000)]
        normalized = sut.normalize_jobs(raw)
        job = normalized[0]

        self.assertEqual(job["job_id"], "1001")
        self.assertEqual(job["title"], "Data Engineer 1")
        self.assertEqual(job["company_name"], "Acme Corp")
        self.assertEqual(job["apply_url"], "https://remoteok.com/jobs/1001")
        self.assertEqual(job["tags"], ["python", "aws"])
        self.assertEqual(job["location_raw"], "Remote")
        self.assertEqual(job["salary"], "$60000-$90000")

    def test_missing_fields_default_to_none(self):
        raw = [{"id": "999", "position": "Engineer"}]
        normalized = sut.normalize_jobs(raw)
        job = normalized[0]

        self.assertEqual(job["job_id"], "999")
        self.assertIsNone(job["company_name"])
        self.assertIsNone(job["location_raw"])
        self.assertIsNone(job["salary"])
        self.assertEqual(job["tags"], [])


class TestBuildS3Key(unittest.TestCase):

    def test_key_format(self):
        key = sut.build_s3_key("2026-04-18")
        self.assertEqual(key, "snapshot_date=2026-04-18/source=remoteok/data.json.gz")

    def test_hive_partition_structure(self):
        key = sut.build_s3_key("2026-04-18")
        self.assertIn("snapshot_date=", key)
        self.assertIn("source=remoteok", key)


class TestLambdaHandler(unittest.TestCase):

    @patch("ingest_remoteok.write_to_s3")
    @patch("ingest_remoteok.fetch_all_jobs")
    def test_happy_path(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_raw_job(1)]
        mock_write.return_value = "s3://test-bronze-bucket/snapshot_date=2026-04-18/source=remoteok/data.json.gz"

        result = sut.lambda_handler({"snapshot_date": "2026-04-18"}, None)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["source"], "remoteok")
        self.assertEqual(result["record_count"], 1)

    @patch("ingest_remoteok.fetch_all_jobs")
    def test_dry_run_skips_s3(self, mock_fetch):
        mock_fetch.return_value = [make_raw_job(1)]

        result = sut.lambda_handler({"snapshot_date": "2026-04-18", "dry_run": True}, None)
        self.assertIsNone(result["s3_uri"])
        self.assertEqual(result["status"], "OK")

    @patch("ingest_remoteok.fetch_all_jobs")
    def test_empty_jobs_returns_empty_status(self, mock_fetch):
        mock_fetch.return_value = []

        result = sut.lambda_handler({}, None)
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["record_count"], 0)

    @patch("ingest_remoteok.write_to_s3")
    @patch("ingest_remoteok.fetch_all_jobs")
    def test_defaults_snapshot_date_to_today(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_raw_job(1)]
        mock_write.return_value = "s3://..."

        result = sut.lambda_handler({}, None)
        from datetime import datetime
        datetime.strptime(result["snapshot_date"], "%Y-%m-%d")


if __name__ == "__main__":
    unittest.main()
