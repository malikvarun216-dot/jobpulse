"""
Tests for bronze_to_silver.py (multi-source)

Pure function tests run without Spark.
Full transform test requires PySpark (run with: pytest spark/tests/ -v).
"""

import json
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))

from bronze_to_silver import (
    extract_country,
    extract_role_family,
    extract_role_family_from_tags,
    resolve_role_family,
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

    def test_remote(self):
        assert extract_country("Remote") == "remote"

    def test_usa(self):
        assert extract_country("USA Only") == "US"

    def test_united_states(self):
        assert extract_country("United States") == "US"

    def test_uk(self):
        assert extract_country("UK") == "UK"

    def test_united_kingdom(self):
        assert extract_country("United Kingdom") == "UK"

    def test_london(self):
        assert extract_country("London, UK") == "UK"

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

    def test_istanbul(self):
        assert extract_country("Istanbul, Turkiye") == "TR"

    def test_san_francisco(self):
        assert extract_country("San Francisco") == "US"

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


class TestExtractRoleFamilyFromTags:
    """Tag-based role_family inference — used for RemoteOK (no category field)."""

    def test_python_tag(self):
        assert extract_role_family_from_tags(["python", "aws"]) == "SDE"

    def test_data_tag(self):
        assert extract_role_family_from_tags(["data", "sql"]) == "DATA"

    def test_devops_tag(self):
        assert extract_role_family_from_tags(["devops", "kubernetes"]) == "DevOps"

    def test_design_tag(self):
        assert extract_role_family_from_tags(["ui", "ux"]) == "Design"

    def test_product_tag(self):
        assert extract_role_family_from_tags(["product", "manager"]) == "PM"

    def test_no_match(self):
        assert extract_role_family_from_tags(["sales", "support"]) == "Other"

    def test_empty_tags(self):
        assert extract_role_family_from_tags([]) == "Other"

    def test_none_tags(self):
        assert extract_role_family_from_tags(None) == "Other"


class TestResolveRoleFamily:
    def test_uses_category_when_available(self):
        assert resolve_role_family("Data", ["sales"]) == "DATA"

    def test_falls_back_to_tags_when_category_other(self):
        assert resolve_role_family("Other Category", ["python", "aws"]) == "SDE"

    def test_falls_back_to_tags_when_no_category(self):
        assert resolve_role_family(None, ["devops", "kubernetes"]) == "DevOps"


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


# --- Remotive fixture ---

REMOTIVE_JOB = {
    "id": 1234567,
    "url": "https://remotive.com/remote-jobs/software-dev/senior-data-engineer-1234567",
    "title": "Senior Data Engineer",
    "company_name": "Acme Corp",
    "category": "Software Development",
    "tags": ["python", "spark", "aws"],
    "job_type": "full_time",
    "publication_date": "2026-04-18T10:00:00",
    "candidate_required_location": "Worldwide",
    "salary": "$120,000 - $150,000",
    "description": "<p>We are hiring a Senior Data Engineer...</p>",
}

REMOTIVE_BRONZE = {
    "snapshot_date": "2026-04-18",
    "source": "remotive",
    "record_count": 1,
    "ingested_at": "2026-04-18T10:30:00+00:00",
    "jobs": [REMOTIVE_JOB],
}

# --- RemoteOK fixture ---

REMOTEOK_JOB = {
    "job_id": "999001",
    "title": "Data Engineer",
    "company_name": "Beta Corp",
    "apply_url": "https://remoteok.com/jobs/999001",
    "description": "<p>Data Engineer role...</p>",
    "tags": ["python", "data", "aws"],
    "location_raw": "Remote",
    "salary": "$80000-$110000",
    "publication_date": "2026-04-18T09:00:00+00:00",
}

REMOTEOK_BRONZE = {
    "snapshot_date": "2026-04-18",
    "source": "remoteok",
    "record_count": 1,
    "ingested_at": "2026-04-18T10:30:00+00:00",
    "jobs": [REMOTEOK_JOB],
}


@pytestmark_pyspark
def test_remotive_silver_schema(spark):
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(REMOTIVE_BRONZE)])
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
def test_remotive_silver_values(spark):
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(REMOTIVE_BRONZE)])
    )
    silver = build_silver_df(raw_df)
    row = silver.collect()[0]

    assert row["job_id"] == "1234567"
    assert row["source"] == "remotive"
    assert str(row["snapshot_date"]) == "2026-04-18"
    assert row["title"] == "Senior Data Engineer"
    assert row["company_name"] == "Acme Corp"
    assert row["role_family"] == "SDE"
    assert row["country"] == "remote"
    assert row["state"] is None
    assert set(row["tags"]) == {"python", "spark", "aws"}


@pytestmark_pyspark
def test_remoteok_silver_values(spark):
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(REMOTEOK_BRONZE)])
    )
    silver = build_silver_df(raw_df)
    row = silver.collect()[0]

    assert row["job_id"] == "999001"
    assert row["source"] == "remoteok"
    assert row["title"] == "Data Engineer"
    assert row["apply_url"] == "https://remoteok.com/jobs/999001"
    assert row["country"] == "remote"
    assert row["role_family"] in ("DATA", "SDE")  # tags: python, data, aws → DATA or SDE


@pytestmark_pyspark
def test_multi_source_bronze(spark):
    """Both sources in one Spark read — schema merge, both rows present."""
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([
            json.dumps(REMOTIVE_BRONZE),
            json.dumps(REMOTEOK_BRONZE),
        ])
    )
    silver = build_silver_df(raw_df)
    rows = silver.collect()
    sources = {r["source"] for r in rows}
    assert sources == {"remotive", "remoteok"}
    assert len(rows) == 2


@pytestmark_pyspark
def test_india_state(spark):
    job = {**REMOTIVE_JOB, "candidate_required_location": "Bangalore, India"}
    bronze = {**REMOTIVE_BRONZE, "jobs": [job]}
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(bronze)])
    )
    silver = build_silver_df(raw_df)
    row = silver.collect()[0]
    assert row["country"] == "IN"
    assert row["state"] == "Karnataka"


@pytestmark_pyspark
def test_no_logo_columns(spark):
    raw_df = spark.read.option("multiline", "true").json(
        spark.sparkContext.parallelize([json.dumps(REMOTIVE_BRONZE)])
    )
    silver = build_silver_df(raw_df)
    assert "company_logo" not in silver.columns
    assert "company_logo_url" not in silver.columns
