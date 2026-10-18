"""
eval/search.py
--------------
The search methods the eval compares, in one place so pooling (pool.py) and scoring (run_eval.py) rank the
same way. Every method takes per-row scores over the frozen corpus plus a filter mask, and returns row positions,
best first.

  bm25        keyword ranking; jobs sharing no query word are not hits
  vector      cosine similarity on the stored nightly vectors
  dashboard   today's dashboard "hybrid": vector top-50 re-sorted by 0.5 * similarity + 0.5 * match_score
              (match_score is the same for every query — it's the profile fit, not relevance)
  rrf         Reciprocal Rank Fusion of bm25 + vector: score = sum over methods of 1 / (60 + rank).
              Uses ranks only, so BM25 scores (0…30) and cosines (0…1) never need to be put on one scale.
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import strip_html  # noqa: E402

RRF_K = 60  # the constant from the original RRF paper (Cormack et al., 2009); damps the weight of rank 1 vs rank 2


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def top_k(scores: np.ndarray, mask: np.ndarray, k: int) -> np.ndarray:
    """Row positions of the k best scores among rows where mask is True, best first."""
    s = np.where(mask, scores, -np.inf)
    idx = np.argsort(-s, kind="stable")[: min(k, int(mask.sum()))]
    return idx[np.isfinite(s[idx])]


def bm25_rank(bm: np.ndarray, mask: np.ndarray, k: int) -> np.ndarray:
    return top_k(bm, mask & (bm > 0), k)


def vector_rank(sim: np.ndarray, mask: np.ndarray, k: int) -> np.ndarray:
    return top_k(sim, mask, k)


def dashboard_rank(sim: np.ndarray, match_score: np.ndarray, mask: np.ndarray, k: int, candidates: int = 50) -> np.ndarray:
    cand = top_k(sim, mask, candidates)
    cand = cand[match_score[cand] > 0]  # the dashboard drops jobs with no profile overlap
    combined = sim[cand] * 100 * 0.5 + match_score[cand] * 0.5
    return cand[np.argsort(-combined, kind="stable")][:k]


def rrf_rank(rankings: list[np.ndarray], k: int, c: int = RRF_K) -> np.ndarray:
    """Fuse several best-first rankings of row positions; ties keep the first-seen order."""
    score: dict[int, float] = {}
    for ranked in rankings:
        for rank, i in enumerate(ranked, start=1):
            score[int(i)] = score.get(int(i), 0.0) + 1.0 / (c + rank)
    fused = sorted(score, key=lambda i: -score[i])  # sorted() is stable → first-seen wins ties
    return np.array(fused[:k], dtype=int)


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


class Index:
    """Everything the methods need, built once from the frozen corpus + the nightly vector/score files."""

    def __init__(self, corpus: pd.DataFrame, cache: Path):
        from rank_bm25 import BM25Okapi
        self.corpus = corpus
        self.bm25 = BM25Okapi([tokenize(f"{t} {strip_html(d)}") for t, d in zip(corpus["title"], corpus["description"])])
        vecs = same_day_lookup(corpus, cache, "embeddings_", ["embedding"])
        if vecs["embedding"].isna().any():
            raise SystemExit(f"{vecs['embedding'].isna().sum()} corpus jobs have no stored vector")
        m = np.stack(vecs["embedding"].to_numpy()).astype(np.float32)
        self.matrix = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
        self.match = same_day_lookup(corpus, cache, "enrichment_", ["match_score"])["match_score"].fillna(-1).to_numpy()

    def bm25_scores(self, text: str) -> np.ndarray:
        return self.bm25.get_scores(tokenize(text))

    def similarities(self, qvec: np.ndarray) -> np.ndarray:
        return self.matrix @ (qvec / (np.linalg.norm(qvec) + 1e-9))
