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
from ge_runner import MIN_ROW_COUNT, validate_silver  # noqa: E402

TODAY = "2026-04-25"
YESTERDAY = "2026-04-24"


def _make_df(
    n_rows: int = MIN_ROW_COUNT + 50,
    job_id_null: bool = False,
    title_null: bool = False,
    date_override: str | None = None,
) -> pd.DataFrame:
    date = date_override or TODAY
    return pd.DataFrame(
        {
            "job_id": [None if job_id_null and i == 0 else f"job_{i}" for i in range(n_rows)],
            "title": [None if title_null and i == 0 else f"Engineer {i}" for i in range(n_rows)],
            "snapshot_date": [date] * n_rows,
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


def test_stale_date_fails():
    """snapshot_date from yesterday fails the freshness (between) expectation."""
    with pytest.raises(ValueError, match="data quality check failed"):
        validate_silver(_make_df(date_override=YESTERDAY), TODAY)
