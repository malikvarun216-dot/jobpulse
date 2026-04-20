"""
tests/test_ingest_arbeitnow.py
-------------------------------
Run locally: pytest tests/test_ingest_arbeitnow.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["BRONZE_BUCKET"] = "test-bronze-bucket"
os.environ["AWS_REGION"]    = "ap-south-1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion", "sources", "arbeitnow"))
import ingest_arbeitnow as sut


def make_raw_job(i: int, remote=False) -> dict:
    return {
        "slug": f"data-engineer-{1000 + i}",
        "company_name": "Acme Corp",
        "title": f"Data Engineer {i}",
        "description": f"<p>Job description {i}</p>",
        "remote": remote,
        "url": f"https://arbeitnow.com/view/data-engineer-{1000 + i}",
        "tags": ["python", "aws"],
        "job_types": ["full-time"],
        "location": "Berlin" if not remote else "",
        "created_at": 1713427200,  # 2024-04-18 00:00:00 UTC
    }


FAKE_PAGE_1 = {"data": [make_raw_job(i) for i in range(3)], "meta": {"last_page": 2}}
FAKE_PAGE_2 = {"data": [make_raw_job(i) for i in range(3, 5)], "meta": {"last_page": 2}}
FAKE_EMPTY_RESPONSE = {"data": [], "meta": {"last_page": 1}}


class TestFetchAllJobs(unittest.TestCase):

    @patch("ingest_arbeitnow.urllib.request.urlopen")
    def test_paginates_correctly(self, mock_urlopen):
        responses = [
            json.dumps(FAKE_PAGE_1).encode(),
            json.dumps(FAKE_PAGE_2).encode(),
        ]
        mock_resp = MagicMock()
        mock_resp.read.side_effect = responses
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        jobs = sut.fetch_all_jobs()
        self.assertEqual(len(jobs), 5)  # 3 from page 1 + 2 from page 2
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("ingest_arbeitnow.urllib.request.urlopen")
    def test_empty_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(FAKE_EMPTY_RESPONSE).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        jobs = sut.fetch_all_jobs()
        self.assertEqual(jobs, [])


class TestNormalizeJobs(unittest.TestCase):

    def test_field_mapping(self):
        raw = [make_raw_job(1)]
        normalized = sut.normalize_jobs(raw)
        job = normalized[0]

        self.assertEqual(job["job_id"], "data-engineer-1001")
        self.assertEqual(job["title"], "Data Engineer 1")
        self.assertEqual(job["company_name"], "Acme Corp")
        self.assertEqual(job["apply_url"], "https://arbeitnow.com/view/data-engineer-1001")
        self.assertEqual(job["tags"], ["python", "aws"])
        self.assertEqual(job["location_raw"], "Berlin")
        self.assertEqual(job["job_type"], "full-time")
        self.assertIsNone(job["salary"])

    def test_remote_job_uses_remote_location(self):
        raw = [make_raw_job(1, remote=True)]
        normalized = sut.normalize_jobs(raw)
        self.assertEqual(normalized[0]["location_raw"], "Remote")

    def test_publication_date_from_unix_timestamp(self):
        raw = [make_raw_job(1)]
        normalized = sut.normalize_jobs(raw)
        self.assertIn("2024-04-18", normalized[0]["publication_date"])

    def test_missing_fields_default_to_none(self):
        raw = [{"slug": "test-job-1", "title": "Engineer"}]
        normalized = sut.normalize_jobs(raw)
        job = normalized[0]

        self.assertEqual(job["job_id"], "test-job-1")
        self.assertIsNone(job["company_name"])
        self.assertIsNone(job["location_raw"])
        self.assertIsNone(job["salary"])
        self.assertIsNone(job["job_type"])
        self.assertEqual(job["tags"], [])


class TestBuildS3Key(unittest.TestCase):

    def test_key_format(self):
        key = sut.build_s3_key("2026-04-20")
        self.assertEqual(key, "snapshot_date=2026-04-20/source=arbeitnow/data.json.gz")

    def test_hive_partition_structure(self):
        key = sut.build_s3_key("2026-04-20")
        self.assertIn("snapshot_date=", key)
        self.assertIn("source=arbeitnow", key)


class TestLambdaHandler(unittest.TestCase):

    @patch("ingest_arbeitnow.write_to_s3")
    @patch("ingest_arbeitnow.fetch_all_jobs")
    def test_happy_path(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_raw_job(1)]
        mock_write.return_value = "s3://test-bronze-bucket/snapshot_date=2026-04-20/source=arbeitnow/data.json.gz"

        result = sut.lambda_handler({"snapshot_date": "2026-04-20"}, None)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["source"], "arbeitnow")
        self.assertEqual(result["record_count"], 1)

    @patch("ingest_arbeitnow.fetch_all_jobs")
    def test_dry_run_skips_s3(self, mock_fetch):
        mock_fetch.return_value = [make_raw_job(1)]

        result = sut.lambda_handler({"snapshot_date": "2026-04-20", "dry_run": True}, None)
        self.assertIsNone(result["s3_uri"])
        self.assertEqual(result["status"], "OK")

    @patch("ingest_arbeitnow.fetch_all_jobs")
    def test_empty_jobs_returns_empty_status(self, mock_fetch):
        mock_fetch.return_value = []

        result = sut.lambda_handler({}, None)
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["record_count"], 0)

    @patch("ingest_arbeitnow.write_to_s3")
    @patch("ingest_arbeitnow.fetch_all_jobs")
    def test_defaults_snapshot_date_to_today(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_raw_job(1)]
        mock_write.return_value = "s3://..."

        result = sut.lambda_handler({}, None)
        from datetime import datetime
        datetime.strptime(result["snapshot_date"], "%Y-%m-%d")


if __name__ == "__main__":
    unittest.main()
