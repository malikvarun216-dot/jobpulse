"""
vectorstore/load_pg.py
----------------------
Load the frozen eval corpus (corpus_v1, 9,266 jobs) + each job's same-day stored vector into Postgres, then build
the indexes. Idempotent: drops and recreates the table every run (it's a copy of frozen data, nothing is lost).

Run:  .venv\\Scripts\\python.exe vectorstore\\load_pg.py
"""

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.common import DEFAULT_CACHE, load_corpus, strip_html  # noqa: E402
from eval.search import same_day_lookup  # noqa: E402
from vectorstore.pg import connect, vec_literal  # noqa: E402


def main() -> None:
    t0 = time.perf_counter()
    corpus = load_corpus()
    vecs = same_day_lookup(corpus, DEFAULT_CACHE, "embeddings_", ["embedding"])["embedding"].to_numpy()
    if any(v is None for v in vecs):
        raise SystemExit("some corpus jobs have no stored vector")

    with connect() as conn:
        conn.execute((ROOT / "vectorstore" / "schema.sql").read_text(encoding="utf-8"))
        t1 = time.perf_counter()
        with conn.cursor() as cur, cur.copy(
            "COPY jobs (job_id, title, company_name, country, source, snapshot_date, body, embedding) FROM STDIN"
        ) as copy:  # COPY = Postgres bulk load, like Spark writing a whole partition instead of row-by-row INSERTs
            for r, v in zip(corpus.itertuples(), vecs):
                copy.write_row((r.job_id, r.title, r.company_name, r.country, r.source, r.snapshot_date,
                                strip_html(r.description), vec_literal(np.asarray(v))))
        t2 = time.perf_counter()
        conn.execute((ROOT / "vectorstore" / "indexes.sql").read_text(encoding="utf-8"))
        t3 = time.perf_counter()
        n = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        sizes = conn.execute("""
            SELECT pg_size_pretty(pg_table_size('jobs')),
                   pg_size_pretty(pg_relation_size('jobs_embedding_hnsw')),
                   pg_size_pretty(pg_relation_size('jobs_tsv_gin'))""").fetchone()

    print(f"Loaded {n:,} jobs | COPY {t2 - t1:.1f} s | index build {t3 - t2:.1f} s | total {time.perf_counter() - t0:.1f} s")
    print(f"Sizes: table {sizes[0]} | HNSW index {sizes[1]} | GIN index {sizes[2]}")


if __name__ == "__main__":
    main()
