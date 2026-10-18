"""
eval/pool.py
------------
Pooling: decide WHICH (query, job) pairs get labeled. Labeling all 50 x 9,266 pairs is impossible, so we take
the top-k from several different search methods and label only their union. A job none of them ranks high
is assumed not relevant — the standard TREC trick. Using methods that fail differently (keyword vs meaning)
is what keeps the pool fair to methods we add later (Chat 28: RRF, reranker).

Methods (each searches only rows passing the query's filter):
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
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")  # for the Secrets Manager fallback; ~/.aws/config has no region
from eval.common import DEFAULT_CACHE, filter_mask, load_corpus, load_queries, strip_html  # noqa: E402
from genai.embedding_agent import EMBEDDING_MODEL, _get_voyage_key  # noqa: E402 — same model as the stored vectors


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def same_day_lookup(corpus: pd.DataFrame, cache: Path, prefix: str, columns: list[str]) -> pd.DataFrame:
    """Per job, the row from the nightly file of the job's own corpus snapshot_date (embeddings_/enrichment_ files)."""
    parts = []
    for date in sorted(corpus["snapshot_date"].unique()):
        df = pd.read_parquet(cache / f"{prefix}{date}.parquet", columns=["job_id"] + columns)
        df["job_id"] = df["job_id"].astype(str)
        df["snapshot_date"] = date
        parts.append(df.drop_duplicates("job_id"))
    keyed = corpus[["job_id", "snapshot_date"]].merge(pd.concat(parts), on=["job_id", "snapshot_date"], how="left")
    assert len(keyed) == len(corpus)
    return keyed


def query_vectors(texts: list[str], cache: Path) -> dict[str, np.ndarray]:
    path = cache / f"query_vectors_{EMBEDDING_MODEL}.parquet"
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["text", "embedding"])
    missing = sorted(set(texts) - set(cached["text"]))
    if missing:
        import voyageai
        result = voyageai.Client(api_key=_get_voyage_key()).embed(missing, model=EMBEDDING_MODEL)
        cached = pd.concat([cached, pd.DataFrame({"text": missing, "embedding": result.embeddings})], ignore_index=True)
        cached.to_parquet(path, index=False)
        print(f"Embedded {len(missing)} new query texts ({result.total_tokens} tokens)")
    return {t: np.asarray(v, dtype=np.float32) for t, v in zip(cached["text"], cached["embedding"])}


def top_k(scores: np.ndarray, mask: np.ndarray, k: int) -> np.ndarray:
    """Row positions of the k best scores among rows where mask is True, best first."""
    s = np.where(mask, scores, -np.inf)
    idx = np.argsort(-s, kind="stable")[: min(k, int(mask.sum()))]
    return idx[np.isfinite(s[idx])]


def build_pool(corpus, queries, bm25_scores, vec_matrix, qvecs, match_score, depth=20, hybrid_candidates=50):
    """bm25_scores(text) -> scores per corpus row; vec_matrix rows are L2-normalised and aligned to corpus."""
    rows = []
    for q in queries:
        mask = filter_mask(corpus, q.get("filters")).to_numpy()
        qv = qvecs[q["text"]] / (np.linalg.norm(qvecs[q["text"]]) + 1e-9)
        sim = vec_matrix @ qv

        bm = bm25_scores(q["text"])
        # bm25 > 0 only: a job sharing no query word is not a keyword hit, even when fewer than k jobs match
        ranked = {"bm25": top_k(bm, mask & (bm > 0), depth), "vector": top_k(sim, mask, depth)}
        cand = top_k(sim, mask, hybrid_candidates)
        cand = cand[match_score[cand] > 0]
        combined = sim[cand] * 100 * 0.5 + match_score[cand] * 0.5
        ranked["hybrid"] = cand[np.argsort(-combined, kind="stable")][:depth]

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

    from rank_bm25 import BM25Okapi

    corpus = load_corpus(args.version, cache)
    queries = load_queries(args.version)

    bm25 = BM25Okapi([tokenize(f"{t} {strip_html(d)}") for t, d in zip(corpus["title"], corpus["description"])])
    vecs = same_day_lookup(corpus, cache, "embeddings_", ["embedding"])
    if vecs["embedding"].isna().any():
        raise SystemExit(f"{vecs['embedding'].isna().sum()} corpus jobs have no stored vector")
    matrix = np.stack(vecs["embedding"].to_numpy()).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    match = same_day_lookup(corpus, cache, "enrichment_", ["match_score"])["match_score"].fillna(-1).to_numpy()

    pool = build_pool(corpus, queries, lambda text: bm25.get_scores(tokenize(text)), matrix,
                      query_vectors([q["text"] for q in queries], cache), match, depth=args.depth)
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
