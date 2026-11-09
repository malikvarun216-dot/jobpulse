"""
tests/test_vectorstore.py
-------------------------
The pure parts of vectorstore/pg.py (no database needed). The SQL itself is checked by vectorstore/bench_pg.py
against exact NumPy (tie-aware recall@10 = 1.000 for the exact path, 2026-10-29).
Run locally: pytest tests/test_vectorstore.py -v
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vectorstore import pg  # noqa: E402


def test_vec_literal_is_pgvector_text_format():
    assert pg.vec_literal([0.5, -1, 2.25]) == "[0.5,-1,2.25]"
    assert pg.vec_literal(np.zeros(3, dtype=np.float64)) == "[0,0,0]"


def test_where_adds_country_only_when_given():
    assert pg._where(None) == ("", [])
    assert pg._where("IN") == ("WHERE country = %s", ["IN"])
    assert pg._where("IN", "tsv @@ x") == ("WHERE tsv @@ x AND country = %s", ["IN"])


def test_keyword_query_uses_or_not_and():
    # plainto_tsquery ANDs the words; BM25 scores any overlap, so the SQL swaps & for |
    assert "replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery" == pg.TSQUERY
