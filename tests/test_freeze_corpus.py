"""
tests/test_freeze_corpus.py
---------------------------
Run locally: pytest tests/test_freeze_corpus.py -v
"""

import os
import sys

import pyarrow as pa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
import freeze_corpus as sut


def snapshot(date: str, rows: list[tuple]) -> pa.Table:
    """rows: (job_id, source, title, description)"""
    cols = {c: [None] * len(rows) for c in sut.COLUMNS}
    cols["job_id"] = [r[0] for r in rows]
    cols["source"] = [r[1] for r in rows]
    cols["title"] = [r[2] for r in rows]
    cols["description"] = [r[3] for r in rows]
    t = pa.table({c: pa.array(v, pa.string()) for c, v in cols.items()})
    return t.append_column("snapshot_date", pa.array([date] * len(rows), pa.string()))


def test_latest_snapshot_wins_and_job_ids_are_unique():
    day1 = snapshot("2026-10-12", [("1", "greenhouse", "DE", "old text"), ("2", "adzuna", "SDE", "x")])
    day2 = snapshot("2026-10-13", [("1", "greenhouse", "DE", "new text"), ("1", "greenhouse", "DE", "new text")])
    corpus = sut.build_corpus([day1, day2]).to_pandas().set_index("job_id")
    assert len(corpus) == 2
    assert corpus.loc["1", "description"] == "new text"
    assert corpus.loc["1", "snapshot_date"] == "2026-10-13"


def test_has_description_flags_empty_and_null():
    corpus = sut.build_corpus([snapshot("2026-10-12", [("1", "a", "t", "  "), ("2", "a", "t", None), ("3", "a", "t", "jd")])])
    flags = dict(zip(corpus["job_id"].to_pylist(), corpus["has_description"].to_pylist()))
    assert flags == {"1": False, "2": False, "3": True}


def test_content_hash_ignores_row_order_but_not_text():
    rows = [("1", "a", "t1", "d1"), ("2", "a", "t2", "d2")]
    h1 = sut.content_hash(sut.build_corpus([snapshot("2026-10-12", rows)]))
    h2 = sut.content_hash(sut.build_corpus([snapshot("2026-10-12", rows[::-1])]))
    h3 = sut.content_hash(sut.build_corpus([snapshot("2026-10-12", [rows[0], ("2", "a", "t2", "d2 edited")])]))
    assert h1 == h2
    assert h1 != h3


def test_manifest_counts_by_source():
    corpus = sut.build_corpus([snapshot("2026-10-12", [("1", "greenhouse", "t", "jd"), ("2", "greenhouse", "t", None), ("3", "adzuna", "t", "jd")])])
    m = sut.make_manifest(corpus, "v1", ["2026-10-12"])
    assert m["row_count"] == 3
    assert m["rows_with_description"] == 2
    assert m["by_source"]["greenhouse"] == {"rows": 2, "with_description": 1}
    assert len(m["content_sha256"]) == 64
