"""
tests/test_ingest_himalayas.py
-------------------------------
Run locally: pytest tests/test_ingest_himalayas.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["BRONZE_BUCKET"] = "test-bronze-bucket"
os.environ["AWS_REGION"]    = "ap-south-1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion", "sources", "himalayas"))
import ingest_himalayas as sut


def make_job(i: int) -> dict:
    return {
        "id":          f"job-{i}",
        "title":       f"Data Engineer {i}",
        "companyName": "Acme Corp",
        "location":    "Remote",
        "url":         f"https://himalayas.app/jobs/job-{i}",
    }

FAKE_JOBS_PAGE_1 = [make_job(i) for i in range(100)]
FAKE_JOBS_PAGE_2 = [make_job(i) for i in range(100, 140)]


class TestFetchAllJobs(unittest.TestCase):

    @patch("ingest_himalayas.urllib.request.urlopen")
    def test_paginates_correctly(self, mock_urlopen):
        responses = [
            json.dumps({"jobs": FAKE_JOBS_PAGE_1}).encode(),
            json.dumps({"jobs": FAKE_JOBS_PAGE_2}).encode(),
        ]
        mock_resp = MagicMock()
        mock_resp.read.side_effect = responses
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        jobs = sut.fetch_all_jobs()
        self.assertEqual(len(jobs), 140)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("ingest_himalayas.urllib.request.urlopen")
    def test_empty_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"jobs": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        jobs = sut.fetch_all_jobs()
        self.assertEqual(jobs, [])


class TestBuildS3Key(unittest.TestCase):

    def test_key_format(self):
        key = sut.build_s3_key("2025-04-17")
        self.assertEqual(key, "snapshot_date=2025-04-17/source=himalayas/data.json.gz")

    def test_hive_partition_structure(self):
        key = sut.build_s3_key("2025-04-17")
        self.assertIn("snapshot_date=", key)
        self.assertIn("source=himalayas", key)


class TestLambdaHandler(unittest.TestCase):

    @patch("ingest_himalayas.write_to_s3")
    @patch("ingest_himalayas.fetch_all_jobs")
    def test_happy_path(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_job(1)]
        mock_write.return_value = "s3://test-bronze-bucket/snapshot_date=2025-04-17/source=himalayas/data.json.gz"

        result = sut.lambda_handler({"snapshot_date": "2025-04-17"}, None)
        self.assertEqual(result["status"],       "OK")
        self.assertEqual(result["record_count"], 1)

    @patch("ingest_himalayas.fetch_all_jobs")
    def test_dry_run_skips_s3(self, mock_fetch):
        mock_fetch.return_value = [make_job(1)]

        result = sut.lambda_handler({"snapshot_date": "2025-04-17", "dry_run": True}, None)
        self.assertIsNone(result["s3_uri"])
        self.assertEqual(result["status"], "OK")

    @patch("ingest_himalayas.fetch_all_jobs")
    def test_empty_jobs_returns_empty_status(self, mock_fetch):
        mock_fetch.return_value = []

        result = sut.lambda_handler({}, None)
        self.assertEqual(result["status"],       "EMPTY")
        self.assertEqual(result["record_count"], 0)

    @patch("ingest_himalayas.write_to_s3")
    @patch("ingest_himalayas.fetch_all_jobs")
    def test_defaults_snapshot_date_to_today(self, mock_fetch, mock_write):
        mock_fetch.return_value = [make_job(1)]
        mock_write.return_value = "s3://..."

        result = sut.lambda_handler({}, None)
        from datetime import datetime
        datetime.strptime(result["snapshot_date"], "%Y-%m-%d")


if __name__ == "__main__":
    unittest.main()