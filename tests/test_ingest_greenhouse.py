"""
tests/test_ingest_greenhouse.py
---------------------------------
Run locally: pytest tests/test_ingest_greenhouse.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, mock_open

os.environ["BRONZE_BUCKET"] = "test-bronze-bucket"
os.environ["AWS_REGION"]    = "ap-south-1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion", "sources", "greenhouse"))
import ingest_greenhouse as sut


def make_raw_job(i: int, slug: str = "stripe") -> dict:
    return {
        "id": 1000 + i,
        "title": f"Data Engineer {i}",
        "updated_at": "2026-04-20T12:00:00-04:00",
        "absolute_url": f"https://boards.greenhouse.io/{slug}/jobs/{1000 + i}",
        "location": {"name": "San Francisco, CA"},
        "departments": [{"id": 1, "name": "Engineering"}],
        "offices": [{"id": 1, "name": "San Francisco"}],
    }


def make_api_response(jobs: list[dict]) -> bytes:
    return json.dumps({"jobs": jobs, "meta": {"total": len(jobs)}}).encode()


def _make_urlopen_ctx(response_bytes: bytes):
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


FAKE_SLUGS = {"slugs": ["stripe", "airbnb"]}


class TestLoadSlugs(unittest.TestCase):

    def test_returns_list_of_strings(self):
        slugs = sut.load_slugs()
        self.assertIsInstance(slugs, list)
        self.assertIn("stripe", slugs)
        self.assertGreater(len(slugs), 0)


class TestFetchCompanyJobs(unittest.TestCase):

    @patch("ingest_greenhouse.urllib.request.urlopen")
    def test_returns_jobs_on_200(self, mock_urlopen):
        raw_jobs = [make_raw_job(1)]
        mock_urlopen.return_value = _make_urlopen_ctx(make_api_response(raw_jobs))

        result = sut.fetch_company_jobs("stripe")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1001)

    @patch("ingest_greenhouse.urllib.request.urlopen")
    def test_returns_empty_list_on_404(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=404, msg="Not Found", hdrs=None, fp=None
        )
        result = sut.fetch_company_jobs("unknown-company-xyz")
        self.assertEqual(result, [])

    @patch("ingest_greenhouse.urllib.request.urlopen")
    def test_raises_on_non_404_http_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="Server Error", hdrs=None, fp=None
        )
        with self.assertRaises(urllib.error.HTTPError):
            sut.fetch_company_jobs("stripe")


class TestNormalizeJobs(unittest.TestCase):

    def test_field_mapping(self):
        raw = [make_raw_job(1, slug="stripe")]
        normalized = sut.normalize_jobs(raw, "stripe")
        job = normalized[0]

        self.assertEqual(job["job_id"], "greenhouse-stripe-1001")
        self.assertEqual(job["title"], "Data Engineer 1")
        self.assertEqual(job["company_name"], "stripe")
        self.assertEqual(job["apply_url"], "https://boards.greenhouse.io/stripe/jobs/1001")
        self.assertEqual(job["location_raw"], "San Francisco, CA")
        self.assertIn("Engineering", job["tags"])
        self.assertIn("San Francisco", job["tags"])
        self.assertIsNone(job["salary"])
        self.assertIsNone(job["job_type"])

    def test_job_id_includes_slug_prefix(self):
        raw = [make_raw_job(1, slug="airbnb")]
        normalized = sut.normalize_jobs(raw, "airbnb")
        self.assertTrue(normalized[0]["job_id"].startswith("greenhouse-airbnb-"))

    def test_missing_location_defaults_to_none(self):
        raw = [{"id": 1, "title": "Engineer"}]
        normalized = sut.normalize_jobs(raw, "stripe")
        self.assertIsNone(normalized[0]["location_raw"])

    def test_empty_departments_gives_empty_tags(self):
        raw = [{"id": 1, "title": "Engineer", "departments": [], "offices": []}]
        normalized = sut.normalize_jobs(raw, "stripe")
        self.assertEqual(normalized[0]["tags"], [])


class TestFetchAllJobs(unittest.TestCase):

    @patch("ingest_greenhouse.fetch_company_jobs")
    def test_aggregates_across_slugs(self, mock_fetch):
        mock_fetch.side_effect = [
            [make_raw_job(1)],
            [make_raw_job(2), make_raw_job(3)],
        ]
        jobs = sut.fetch_all_jobs(["stripe", "airbnb"])
        self.assertEqual(len(jobs), 3)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("ingest_greenhouse.fetch_company_jobs")
    def test_skips_slugs_with_zero_jobs(self, mock_fetch):
        mock_fetch.side_effect = [[], [make_raw_job(1)]]
        jobs = sut.fetch_all_jobs(["unknown", "stripe"])
        self.assertEqual(len(jobs), 1)


class TestBuildS3Key(unittest.TestCase):

    def test_key_format(self):
        key = sut.build_s3_key("2026-04-20")
        self.assertEqual(key, "snapshot_date=2026-04-20/source=greenhouse/data.json.gz")

    def test_hive_partition_structure(self):
        key = sut.build_s3_key("2026-04-20")
        self.assertIn("snapshot_date=", key)
        self.assertIn("source=greenhouse", key)


class TestLambdaHandler(unittest.TestCase):

    @patch("ingest_greenhouse.write_to_s3")
    @patch("ingest_greenhouse.fetch_all_jobs")
    @patch("ingest_greenhouse.load_slugs")
    def test_happy_path(self, mock_slugs, mock_fetch, mock_write):
        mock_slugs.return_value = ["stripe"]
        mock_fetch.return_value = [{"job_id": "greenhouse-stripe-1001", "title": "DE"}]
        mock_write.return_value = "s3://test-bronze-bucket/snapshot_date=2026-04-20/source=greenhouse/data.json.gz"

        result = sut.lambda_handler({"snapshot_date": "2026-04-20"}, None)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["source"], "greenhouse")
        self.assertEqual(result["record_count"], 1)

    @patch("ingest_greenhouse.fetch_all_jobs")
    @patch("ingest_greenhouse.load_slugs")
    def test_dry_run_skips_s3(self, mock_slugs, mock_fetch):
        mock_slugs.return_value = ["stripe"]
        mock_fetch.return_value = [{"job_id": "greenhouse-stripe-1001"}]

        result = sut.lambda_handler({"snapshot_date": "2026-04-20", "dry_run": True}, None)
        self.assertIsNone(result["s3_uri"])
        self.assertEqual(result["status"], "OK")

    @patch("ingest_greenhouse.fetch_all_jobs")
    @patch("ingest_greenhouse.load_slugs")
    def test_empty_jobs_returns_empty_status(self, mock_slugs, mock_fetch):
        mock_slugs.return_value = ["stripe"]
        mock_fetch.return_value = []

        result = sut.lambda_handler({}, None)
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["record_count"], 0)

    @patch("ingest_greenhouse.write_to_s3")
    @patch("ingest_greenhouse.fetch_all_jobs")
    @patch("ingest_greenhouse.load_slugs")
    def test_defaults_snapshot_date_to_today(self, mock_slugs, mock_fetch, mock_write):
        mock_slugs.return_value = ["stripe"]
        mock_fetch.return_value = [{"job_id": "greenhouse-stripe-1001"}]
        mock_write.return_value = "s3://..."

        result = sut.lambda_handler({}, None)
        from datetime import datetime
        datetime.strptime(result["snapshot_date"], "%Y-%m-%d")


if __name__ == "__main__":
    unittest.main()
