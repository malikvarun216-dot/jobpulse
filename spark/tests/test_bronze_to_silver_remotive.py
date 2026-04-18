"""
Tests for bronze_to_silver_remotive.py

Pure function tests run without Spark.
Full transform test requires PySpark (run with: pytest spark/tests/ -v).
"""

import json
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))

from bronze_to_silver_remotive import (
    extract_country,
    extract_role_family,
    extract_state,
    build_silver_df,
)

# ---------------------------------------------------------------------------
# Pure function tests — no Spark needed
# ---------------------------------------------------------------------------


class TestExtractCountry:
    def test_worldwide(self):
        assert extract_country("Worldwide") == "remote"

    def test_anywhere(self):
        assert extract_country("Anywhere") == "remote"

    def test_usa(self):
        assert extract_country("USA Only") == "US"

    def test_united_states(self):
        assert extract_country("United States") == "US"

    def test_uk(self):
        assert extract_country("UK") == "UK"

    def test_united_kingdom(self):
        assert extract_country("United Kingdom") == "UK"

    def test_canada(self):
        assert extract_country("Canada") == "CA"

    def test_europe(self):
        assert extract_country("Europe") == "EU"

    def test_australia(self):
        assert extract_country("Australia") == "AU"

    def test_germany(self):
        assert extract_country("Germany") == "DE"

    def test_japan(self):
        assert extract_country("Japan") == "JP"

    def test_tokyo(self):
        assert extract_country("Tokyo, Japan") == "JP"

    def test_india(self):
        assert extract_country("India") == "IN"

    def test_bangalore(self):
        assert extract_country("Bangalore, India") == "IN"

    def test_hyderabad(self):
        assert extract_country("Hyderabad") == "IN"

    def test_singapore(self):
        assert extract_country("Singapore") == "SG"

    def test_netherlands(self):
        assert extract_country("Netherlands") == "NL"

    def test_unknown(self):
        assert extract_country("Mars") == "other"

    def test_none(self):
        assert extract_country(None) == "other"

    def test_empty(self):
        assert extract_country("") == "other"

    def test_case_insensitive(self):
        assert extract_country("WORLDWIDE") == "remote"


class TestExtractState:
    def test_bangalore(self):
        assert extract_state("Bangalore") == "Karnataka"

    def test_bengaluru(self):
        assert extract_state("Bengaluru, India") == "Karnataka"

    def test_mumbai(self):
        assert extract_state("Mumbai") == "Maharashtra"

    def test_pune(self):
        assert extract_state("Pune, Maharashtra") == "Maharashtra"

    def test_hyderabad(self):
        assert extract_state("Hyderabad") == "Telangana"

    def test_delhi(self):
        assert extract_state("Delhi") == "Delhi NCR"

    def test_noida(self):
        assert extract_state("Noida") == "Delhi NCR"

    def test_gurugram(self):
        assert extract_state("Gurugram") == "Delhi NCR"

    def test_chennai(self):
        assert extract_state("Chennai") == "Tamil Nadu"

    def test_kolkata(self):
        assert extract_state("Kolkata") == "West Bengal"

    def test_ahmedabad(self):
        assert extract_state("Ahmedabad") == "Gujarat"

    def test_non_india_returns_none(self):
        assert extract_state("London, UK") is None

    def test_none_returns_none(self):
        assert extract_state(None) is None

    def test_empty_returns_none(self):
        assert extract_state("") is None


class TestExtractRoleFamily:
    def test_software_development(self):
        assert extract_role_family("Software Development") == "SDE"

    def test_engineering(self):
        assert extract_role_family("Engineering") == "SDE"

    def test_data(self):
        assert extract_role_family("Data") == "DATA"

    def test_devops(self):
        assert extract_role_family("DevOps / Sysadmin") == "DevOps"

    def test_product(self):
        assert extract_role_family("Product") == "PM"

    def test_design(self):
        assert extract_role_family("Design") == "Design"

    def test_marketing(self):
        assert extract_role_family("Marketing") == "Marketing"

    def test_unknown(self):
        assert extract_role_family("Customer Support") == "Other"

    def test_none(self):
        assert extract_role_family(None) == "Other"

    def test_case_insensitive(self):
        assert extract_role_family("SOFTWARE DEVELOPMENT") == "SDE"


# ---------------------------------------------------------------------------
# Full transform test — requires PySpark
# ---------------------------------------------------------------------------

try:
    import pyspark  # noqa: F401
    _HAS_PYSPARK = True
except ImportError:
    _HAS_PYSPARK = False

pytestmark_pyspark = pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_bronze_to_silver")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


FIXTURE_JOB = {
    "id": 1234567,
    "url": "https://remotive.com/remote-jobs/software-dev/senior-data-engineer-1234567",
    "title": "Senior Data Engineer",
    "company_name": "Acme Corp",
    "company_logo": "https://remotive.com/logo.png",
    "company_logo_url": "https://remotive.com/logo.png",
    "category": "Software Development",
    "tags": ["python", "spark", "aws"],
    "job_type": "full_time",
    "publication_date": "2026-04-18T10:00:00",
    "candidate_required_location": "Worldwide",
    "salary": "$120,000 - $150,000",
    "description": "<p>We are hiring a Senior Data Engineer...</p>",
}

FIXTURE_BRONZE = {
    "snapshot_date": "2026-04-18",
    "source": "remotive",
    "record_count": 1,
    "ingested_at": "2026-04-18T10:30:00+00:00",
    "jobs": [FIXTURE_JOB],
}


@pytestmark_pyspark
def test_build_silver_df_schema(spark):
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(FIXTURE_BRONZE)])
    )
    silver = build_silver_df(raw_df)

    expected_cols = {
        "job_id", "source", "snapshot_date", "title", "company_name",
        "category", "role_family", "job_type", "apply_url", "salary_raw",
        "location_raw", "country", "state", "tags", "publication_date",
        "description", "ingested_at",
    }
    assert set(silver.columns) == expected_cols


@pytestmark_pyspark
def test_build_silver_df_values(spark):
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(FIXTURE_BRONZE)])
    )
    silver = build_silver_df(raw_df)
    row = silver.collect()[0]

    assert row["job_id"] == "1234567"
    assert row["source"] == "remotive"
    assert str(row["snapshot_date"]) == "2026-04-18"
    assert row["title"] == "Senior Data Engineer"
    assert row["company_name"] == "Acme Corp"
    assert row["category"] == "Software Development"
    assert row["role_family"] == "SDE"
    assert row["job_type"] == "full_time"
    assert row["country"] == "remote"
    assert row["state"] is None
    assert set(row["tags"]) == {"python", "spark", "aws"}


@pytestmark_pyspark
def test_build_silver_df_india_state(spark):
    job = {**FIXTURE_JOB, "candidate_required_location": "Bangalore, India"}
    bronze = {**FIXTURE_BRONZE, "jobs": [job]}

    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(bronze)])
    )
    silver = build_silver_df(raw_df)
    row = silver.collect()[0]

    assert row["country"] == "IN"
    assert row["state"] == "Karnataka"


@pytestmark_pyspark
def test_build_silver_df_drops_logo(spark):
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(FIXTURE_BRONZE)])
    )
    silver = build_silver_df(raw_df)
    assert "company_logo" not in silver.columns
    assert "company_logo_url" not in silver.columns
