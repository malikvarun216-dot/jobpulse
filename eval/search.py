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
  dashboard_fixed   dashboard, but match_score uses the Chat 28 skill normalization (skill_full_at)

Chat 28 embedding variants (eval/embed_variants.py re-embeds the corpus; each gives vector_<name> and rrf_<name>):
  strip            HTML stripped before the 4,000-char cut (nightly vectors embed raw HTML)
  strip_doc        + Voyage input_type "document" for jobs / "query" for queries
  strip_doc_title  + the job title on the first line
  chunks           title + one section-sized chunk per vector, whole JD (no cut); a job scores its best chunk
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import chunk_text, strip_html  # noqa: E402

RRF_K = 60  # the constant from the original RRF paper (Cormack et al., 2009); damps the weight of rank 1 vs rank 2
MAX_CHARS = 4000  # same cut as genai/embedding_agent.py
SKILL_FULL_AT = 12  # dashboard_fixed: full skill points at 4 core skills' weight (top ~8% of jobs reach it)

VARIANTS = {
    "strip":           {"input_type": None,       "title": False, "chunks": False},
    "strip_doc":       {"input_type": "document", "title": False, "chunks": False},
    "strip_doc_title": {"input_type": "document", "title": True,  "chunks": False},
    "chunks":          {"input_type": "document", "title": True,  "chunks": True},
}


def variant_texts(title: str, description: str, variant: str) -> list[str]:
    """The text(s) embedded for one job under a variant — one string, or one per chunk."""
    v = VARIANTS[variant]
    body = strip_html(description)
    if v["chunks"]:
        return [f"{title}\n{c}" for c in chunk_text(body)]
    text = f"{title}\n{body}" if v["title"] else body
    return [text[:MAX_CHARS]]


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
        enr = same_day_lookup(corpus, cache, "enrichment_", ["match_score", "score_detail", "skills"])
        self.match = enr["match_score"].fillna(-1).to_numpy()
        self.match_fixed = fixed_match_scores(enr)
        self.cache = cache
        self.variants: dict[str, tuple[np.ndarray, np.ndarray]] = {}  # name -> (chunk matrix, corpus row per chunk)

    def bm25_scores(self, text: str) -> np.ndarray:
        return self.bm25.get_scores(tokenize(text))

    def similarities(self, qvec: np.ndarray) -> np.ndarray:
        return self.matrix @ (qvec / (np.linalg.norm(qvec) + 1e-9))

    def load_variant(self, name: str, version: str = "v1") -> None:
        df = pd.read_parquet(self.cache / f"variant_vectors_{version}_{name}.parquet")
        pos = pd.Series(np.arange(len(self.corpus)), index=self.corpus["job_id"].to_numpy())
        rows = pos.reindex(df["job_id"].to_numpy()).to_numpy()
        if np.isnan(rows).any() or df["job_id"].nunique() != len(self.corpus):
            raise SystemExit(f"variant {name}: vectors don't cover the corpus — re-run embed_variants.py")
        m = np.stack(df["embedding"].to_numpy()).astype(np.float32)
        self.variants[name] = (m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9), rows.astype(int))

    def variant_similarities(self, name: str, qvec: np.ndarray) -> np.ndarray:
        """Per job, the best cosine over its vectors (max-score roll-up; one vector per job = plain cosine)."""
        m, rows = self.variants[name]
        sims = m @ (qvec / (np.linalg.norm(qvec) + 1e-9))
        out = np.full(len(self.corpus), -np.inf, dtype=np.float32)
        np.maximum.at(out, rows, sims)
        return out


def fixed_match_scores(enr: pd.DataFrame, full_at: float = SKILL_FULL_AT) -> np.ndarray:
    """Nightly match_score with only the skill part re-scored by the Chat 28 normalization (the other parts —
    freshness depends on the run date — stay as stored)."""
    import json
    import yaml
    from genai.match_scorer import MatchScorer
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "user_profile.yml").read_text(encoding="utf-8"))
    scorer = MatchScorer({**raw["profile"], "weights": raw["weights"], "skill_full_at": full_at})
    out = []
    for score, detail, skills in zip(enr["match_score"], enr["score_detail"], enr["skills"]):
        if pd.isna(score):
            out.append(-1.0)
            continue
        old = json.loads(detail).get("skill_overlap", 0.0) if isinstance(detail, str) else 0.0
        new = scorer.skill_points(list(skills) if skills is not None else [])
        out.append(min(score - old + new, 100.0))
    return np.asarray(out)
