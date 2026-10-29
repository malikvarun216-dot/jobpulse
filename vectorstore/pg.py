"""
vectorstore/pg.py
-----------------
Postgres + pgvector versions of the eval's search methods, as plain SQL:

  vector_search   ORDER BY embedding <=> query  (cosine distance; uses the HNSW index → approximate)
  keyword_search  tsvector @@ tsquery, ranked by ts_rank_cd (Postgres full-text search; uses the GIN index)
  hybrid_search   both of the above + Reciprocal Rank Fusion, in ONE query (CTEs + FULL OUTER JOIN)

Each returns best-first job_ids. Connection: local Docker (vectorstore/docker-compose.yml) unless PG_DSN is set.
"""

import os

import numpy as np
import psycopg

LOCAL_DSN = "host=127.0.0.1 port=5433 dbname=jobpulse user=jobpulse password=jobpulse_local"  # local-only dev DB
RRF_K = 60


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ.get("PG_DSN", LOCAL_DSN), autocommit=True)


def vec_literal(v) -> str:
    """pgvector's text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7g}" for x in np.asarray(v, dtype=np.float32)) + "]"


def _where(country: str | None, extra: str = "") -> tuple[str, list]:
    conds, params = ([extra] if extra else []), []
    if country:
        conds.append("country = %s")
        params.append(country)
    return ("WHERE " + " AND ".join(conds)) if conds else "", params


# OR-query: plainto_tsquery joins words with AND ('spark' & 'stream'), which drops any job missing one word.
# BM25 scores ANY overlap, so we swap & → | to match its behaviour.
TSQUERY = "replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery"


def vector_search(conn, qvec, k: int = 50, country: str | None = None, ef_search: int = 40,
                  iterative: bool = False, exact: bool = False, force_hnsw: bool = False) -> list[str]:
    where, params = _where(country)
    with conn.transaction(), conn.cursor() as cur:
        # SET LOCAL lasts only for this transaction — the knobs never leak into the next query.
        cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")  # candidates kept during the graph walk
        # With a filter, HNSW finds ef_search neighbours THEN filters → can return too few. Iterative scan keeps walking.
        cur.execute(f"SET LOCAL hnsw.iterative_scan = {'relaxed_order' if iterative else 'off'}")
        if exact:  # ground truth inside Postgres: force a full scan (no index) — must equal NumPy
            cur.execute("SET LOCAL enable_indexscan = off")
        if force_hnsw:  # benchmark only: stop the planner choosing a full scan or the country index instead of HNSW
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute("SET LOCAL enable_bitmapscan = off")
        cur.execute(f"SELECT job_id FROM jobs {where} ORDER BY embedding <=> %s::vector LIMIT %s",
                    [*params, vec_literal(qvec), k])
        return [r[0] for r in cur.fetchall()]


def keyword_search(conn, text: str, k: int = 50, country: str | None = None) -> list[str]:
    where, params = _where(country, f"tsv @@ {TSQUERY}")
    sql = f"SELECT job_id FROM jobs {where} ORDER BY ts_rank_cd(tsv, {TSQUERY}) DESC, job_id LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, [text, *params, text, k])
        return [r[0] for r in cur.fetchall()]


def hybrid_search(conn, text: str, qvec, k: int = 50, country: str | None = None,
                  depth: int = 100, ef_search: int = 100) -> list[str]:
    """RRF of vector top-`depth` and keyword top-`depth` in one round trip: score = Σ 1 / (60 + rank)."""
    vwhere, vparams = _where(country)
    kwhere, kparams = _where(country, f"tsv @@ {TSQUERY}")
    sql = f"""
    WITH v AS (
        SELECT job_id, row_number() OVER () AS r
        FROM (SELECT job_id FROM jobs {vwhere} ORDER BY embedding <=> %s::vector LIMIT %s) s
    ),
    kw AS (
        SELECT job_id, row_number() OVER () AS r
        FROM (SELECT job_id FROM jobs {kwhere} ORDER BY ts_rank_cd(tsv, {TSQUERY}) DESC, job_id LIMIT %s) s
    )
    SELECT job_id, COALESCE(1.0 / ({RRF_K} + v.r), 0) + COALESCE(1.0 / ({RRF_K} + kw.r), 0) AS score
    FROM v FULL OUTER JOIN kw USING (job_id)
    ORDER BY score DESC, job_id
    LIMIT %s"""
    params = [*vparams, vec_literal(qvec), depth, text, *kparams, text, depth, k]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")
        cur.execute(f"SET LOCAL hnsw.iterative_scan = {'relaxed_order' if country else 'off'}")
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]
