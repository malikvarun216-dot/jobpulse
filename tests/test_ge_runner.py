"""
tests/test_ge_runner.py
-----------------------
Unit tests for validate_silver() — no S3, no Glue, just pandas DataFrames.

Each test exercises one expectation failure path so every guard is verified.
Run locally: pytest tests/test_ge_runner.py -v
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "transform", "ge_runner"))
from ge_runner import MIN_ROW_COUNT, add_ingested_date_ist, validate_silver  # noqa: E402

TODAY = "2026-04-25"
# The 2 AM IST run ingests at 20:30 UTC the day before — still TODAY in IST
INGESTED_TODAY = pd.Timestamp("2026-04-24 20:30:00")
INGESTED_YESTERDAY = pd.Timestamp("2026-04-23 20:30:00")


def _make_df(
    n_rows: int = MIN_ROW_COUNT + 50,
    job_id_null: bool = False,
    title_null: bool = False,
    ingested_at: pd.Timestamp = INGESTED_TODAY,
    string_tags: bool = False,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "job_id": [None if job_id_null and i == 0 else f"job_{i}" for i in range(n_rows)],
            "title": [None if title_null and i == 0 else f"Engineer {i}" for i in range(n_rows)],
            "ingested_at": [ingested_at] * n_rows,
            "tags": ["python, aws" if string_tags and i == 0 else ["python", "aws"] for i in range(n_rows)],
            "company_name": ["Acme"] * n_rows,
            "role_family": ["SDE"] * n_rows,
            "country": ["IN"] * n_rows,
        }
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_df_passes():
    """Clean DataFrame with enough rows and today's date — no exception raised."""
    validate_silver(_make_df(), TODAY)


# ---------------------------------------------------------------------------
# Failure paths — one expectation broken per test
# ---------------------------------------------------------------------------


def test_empty_df_fails():
    """Zero rows violates row count expectation."""
    with pytest.raises(ValueError, match="data quality check failed"):
        validate_silver(_make_df(n_rows=0), TODAY)


def test_low_count_fails():
    """Row count below MIN_ROW_COUNT threshold triggers failure."""
    with pytest.raises(ValueError, match="data quality check failed"):
        validate_silver(_make_df(n_rows=MIN_ROW_COUNT - 50), TODAY)


def test_null_job_id_fails():
    """A null job_id violates the not_null expectation."""
    with pytest.raises(ValueError, match="data quality check failed"):
        validate_silver(_make_df(job_id_null=True), TODAY)


def test_null_title_fails():
    """A null title violates the not_null expectation."""
    with pytest.raises(ValueError, match="data quality check failed"):
        validate_silver(_make_df(title_null=True), TODAY)


def test_stale_data_fails():
    """Rows ingested yesterday (IST) sitting in today's partition fail the freshness check."""
    with pytest.raises(ValueError, match="data quality check failed"):
        validate_silver(_make_df(ingested_at=INGESTED_YESTERDAY), TODAY)


def test_ingested_date_uses_ist():
    """20:30 UTC on the 24th is 02:00 IST on the 25th — same snapshot day."""
    df = add_ingested_date_ist(_make_df(n_rows=1))
    assert df["ingested_date_ist"].iloc[0] == TODAY


def test_string_tags_fail():
    """Aug 2026 drift: one row with tags as a string fails the type expectation."""
    with pytest.raises(ValueError, match="data quality check failed"):
        validate_silver(_make_df(string_tags=True), TODAY)
