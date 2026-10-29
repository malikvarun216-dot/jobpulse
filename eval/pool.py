"""
eval/pool.py
------------
Pooling: decide WHICH (query, job) pairs get labeled. Labeling all 50 x 9,266 pairs is impossible, so we take
the top-k from several different search methods and label only their union. A job none of them ranks high
is assumed not relevant — the standard TREC trick. Using methods that fail differently (keyword vs meaning)
is what keeps the pool fair to methods we add later (Chat 28: RRF, reranker).

Methods (eval/search.py; each searches only rows passing the query's filter):
  bm25    keyword ranking (rank_bm25) on title + HTML-stripped description
  vector  cosine on the stored nightly voyage-4-lite vectors (same text + 4,000-char cut as the dashboard)
  hybrid  today's dashboard: vector top-50 re-ranked by 0.5 * similarity + 0.5 * match_score,
          jobs with match_score <= 0 dropped (dashboard/streamlit/app.py)

Output: {cache}/pool_{version}.parquet — one row per (query_id, job_id) with each method's rank (null = not in its top-k).
Query vectors are cached in {cache}/query_vectors_voyage-4-lite.parquet, so a re-run only pays for new query texts.

Usage:
  python eval/pool.py            # uses VOYAGE_API_KEY, else Secrets Manager jobpulse/voyage_key_dev
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")  # for the Secrets Manager fallback; ~/.aws/config has no region
from eval.common import DEFAULT_CACHE, filter_mask, load_corpus, load_queries  # noqa: E402
from eval.search import Index, bm25_rank, dashboard_rank, top_k, tokenize, vector_rank  # noqa: E402,F401 (re-exported)
from genai.embedding_agent import EMBEDDING_MODEL, _get_voyage_key  # noqa: E402 — same model as the stored vectors


def query_vectors(texts: list[str], cache: Path, input_type: str | None = None) -> dict[str, np.ndarray]:
    """input_type=None matches the nightly vectors; "query" pairs with documents embedded as input_type="document"
    (Voyage prepends a different instruction to each side). Separate cache file per input_type."""
    suffix = f"_{input_type}" if input_type else ""
    path = cache / f"query_vectors_{EMBEDDING_MODEL}{suffix}.parquet"
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["text", "embedding"])
    missing = sorted(set(texts) - set(cached["text"]))
    if missing:
        import voyageai
        result = voyageai.Client(api_key=_get_voyage_key()).embed(missing, model=EMBEDDING_MODEL, input_type=input_type)
        cached = pd.concat([cached, pd.DataFrame({"text": missing, "embedding": result.embeddings})], ignore_index=True)
        cached.to_parquet(path, index=False)
        print(f"Embedded {len(missing)} new query texts ({result.total_tokens} tokens)")
    return {t: np.asarray(v, dtype=np.float32) for t, v in zip(cached["text"], cached["embedding"])}


def build_pool(corpus, queries, bm25_scores, vec_matrix, qvecs, match_score, depth=20, hybrid_candidates=50):
    """bm25_scores(text) -> scores per corpus row; vec_matrix rows are L2-normalised and aligned to corpus."""
    rows = []
    for q in queries:
        mask = filter_mask(corpus, q.get("filters")).to_numpy()
        sim = vec_matrix @ (qvecs[q["text"]] / (np.linalg.norm(qvecs[q["text"]]) + 1e-9))
        ranked = {
            "bm25": bm25_rank(bm25_scores(q["text"]), mask, depth),
            "vector": vector_rank(sim, mask, depth),
            "hybrid": dashboard_rank(sim, match_score, mask, depth, hybrid_candidates),
        }
        pairs = {}
        for method, idx in ranked.items():
            for rank, i in enumerate(idx, start=1):
                pairs.setdefault(i, {})[f"{method}_rank"] = rank
        for i, ranks in pairs.items():
            rows.append({"query_id": q["id"], "job_id": corpus["job_id"].iat[i], **ranks})
    cols = ["query_id", "job_id", "bm25_rank", "vector_rank", "hybrid_rank"]
    return pd.DataFrame(rows).reindex(columns=cols).astype({c: "Int64" for c in cols[2:]})


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v1")
    p.add_argument("--cache", default=str(DEFAULT_CACHE))
    p.add_argument("--depth", type=int, default=20)
    args = p.parse_args()
    cache = Path(args.cache)

    corpus = load_corpus(args.version, cache)
    queries = load_queries(args.version)
    ix = Index(corpus, cache)

    pool = build_pool(corpus, queries, ix.bm25_scores, ix.matrix,
                      query_vectors([q["text"] for q in queries], cache), ix.match, depth=args.depth)
    out = cache / f"pool_{args.version}.parquet"
    pool.to_parquet(out, index=False)

    per_q = pool.groupby("query_id").size()
    found_by = pool[["bm25_rank", "vector_rank", "hybrid_rank"]].notna()
    print(f"Pool: {len(pool):,} pairs, {per_q.size} queries, {per_q.min()}–{per_q.max()} per query (median {per_q.median():.0f})")
    print(f"Only BM25 found: {(found_by.sum(axis=1).eq(1) & found_by.bm25_rank).sum()} | "
          f"only vector/hybrid: {(~found_by.bm25_rank).sum()} | all three: {found_by.all(axis=1).sum()}")
    print(f"Written: {out}")


if __name__ == "__main__":
    main()
