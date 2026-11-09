"""
vectorstore/bench_pg.py
-----------------------
Measure Postgres + pgvector against exact NumPy search on the 50 eval queries. No labels needed: the question here
is "does the fast index return the SAME neighbours as brute force?", not "are they relevant?".

  1. sanity      Postgres exact (index off) top-10 vs NumPy top-10 → proves the load and the distance match
  2. HNSW        recall@10 of the HNSW index FORCED on (planner overruled), ef_search 10…200, unfiltered and
                 country-filtered (iterative scan off / on — pgvector 0.8's fix for "filter after the walk")
  3. planner     what Postgres picks on its own (seq scan / country index / HNSW) and what that costs
  4. latency     keyword (tsvector) and hybrid (RRF in SQL)

Recall is TIE-AWARE: 12% of corpus jobs share an identical vector with another job (same JD posted per location),
so "the true top 10" isn't unique. A result counts if its cosine is >= the true 10th-best cosine (minus 1e-5).

Run (Docker Postgres up + load_pg.py done):  .venv\\Scripts\\python.exe vectorstore\\bench_pg.py
Writes {cache}/pg_bench_v1.csv.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.common import DEFAULT_CACHE, filter_mask, load_corpus, load_queries  # noqa: E402
from eval.pool import query_vectors  # noqa: E402
from eval.search import same_day_lookup, top_k  # noqa: E402
from vectorstore.pg import connect, hybrid_search, keyword_search, vector_search  # noqa: E402

K = 10
EF = [10, 20, 40, 100, 200]
TIE_EPS = 1e-5


def timed(fn, *a, **kw):
    t = time.perf_counter()
    out = fn(*a, **kw)
    return out, (time.perf_counter() - t) * 1000


def tie_aware_recall(got: list[str], sims: np.ndarray, row_of: dict, threshold: float) -> float:
    return sum(sims[row_of[j]] >= threshold - TIE_EPS for j in got[:K]) / K


def p95(s):
    return s.quantile(0.95)


def main() -> None:
    corpus = load_corpus()
    queries = load_queries()
    qvecs = query_vectors([q["text"] for q in queries], DEFAULT_CACHE)
    m = np.stack(same_day_lookup(corpus, DEFAULT_CACHE, "embeddings_", ["embedding"])["embedding"].to_numpy()).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    row_of = {j: i for i, j in enumerate(corpus["job_id"])}

    rows = []
    with connect() as conn:
        for q in queries[:3]:  # warm-up: load index pages into memory so the first timings aren't cold-cache outliers
            vector_search(conn, qvecs[q["text"]], K, force_hnsw=True)
        for q in queries:
            country = (q.get("filters") or {}).get("country")
            qv = qvecs[q["text"]] / np.linalg.norm(qvecs[q["text"]])
            sims = m @ qv
            mask = filter_mask(corpus, q.get("filters")).to_numpy()
            threshold = sims[top_k(sims, mask, K)[-1]]  # the true 10th-best cosine within the filter
            base = {"query_id": q["id"], "type": q["type"], "filtered": bool(country)}

            def add(method, got, ms, ef=None, it=None):
                rows.append({**base, "method": method, "ef": ef, "iterative": it, "ms": ms, "returned": len(got),
                             "recall@10": tie_aware_recall(got, sims, row_of, threshold)})

            got, ms = timed(vector_search, conn, qv, K, country, exact=True)
            add("pg_exact", got, ms)
            for ef in EF:
                got, ms = timed(vector_search, conn, qv, K, country, ef_search=ef)
                add("pg_planner", got, ms, ef)
                for it in ([False, True] if country else [False]):
                    got, ms = timed(vector_search, conn, qv, K, country, ef_search=ef, iterative=it, force_hnsw=True)
                    add("pg_hnsw_forced", got, ms, ef, it)
            got, ms = timed(keyword_search, conn, q["text"], K, country)
            rows.append({**base, "method": "pg_keyword", "ms": ms, "returned": len(got)})
            got, ms = timed(hybrid_search, conn, q["text"], qv, K, country)
            rows.append({**base, "method": "pg_hybrid_rrf", "ms": ms, "returned": len(got)})

    df = pd.DataFrame(rows)
    df.to_csv(DEFAULT_CACHE / "pg_bench_v1.csv", index=False)

    ex = df[df.method == "pg_exact"]
    print(f"1. Sanity — Postgres exact vs NumPy exact (tie-aware): recall@10 mean {ex['recall@10'].mean():.3f}, "
          f"min {ex['recall@10'].min():.2f} | p50 {ex.ms.median():.1f} ms\n")

    def table(d):
        d = d.copy()
        d["slice"] = np.where(~d.filtered, "unfiltered", np.where(d.iterative == True, "filtered+iterative", "filtered"))  # noqa: E712
        return d.groupby(["slice", "ef"]).agg(recall=("recall@10", "mean"), short=("returned", lambda s: int((s < K).sum())),
                                              p50_ms=("ms", "median"), p95_ms=("ms", p95)).round(3)

    print("2. HNSW forced on (short = queries that got fewer than 10 results):")
    print(table(df[df.method == "pg_hnsw_forced"]).to_string(), "\n")
    print("3. Planner's own choice:")
    print(table(df[df.method == "pg_planner"]).to_string(), "\n")
    o = df[df.method.isin(["pg_keyword", "pg_hybrid_rrf"])].groupby("method").agg(
        p50_ms=("ms", "median"), p95_ms=("ms", p95), empty=("returned", lambda s: int((s == 0).sum())))
    print("4. Keyword and hybrid latency (empty = queries with 0 results):")
    print(o.round(2).to_string())
    print(f"\nWritten {DEFAULT_CACHE / 'pg_bench_v1.csv'}")


if __name__ == "__main__":
    main()
