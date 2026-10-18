"""
eval/run_eval.py
----------------
Score every search method on the 50 queries against the answer key (qrels). Chat 28's baseline + every later
change runs through this one script, so numbers are always comparable.

Metrics per query (k = 10 unless named):
  precision@10   share of the top 10 that are clearly relevant (grade 2)
  recall@50      share of all grade-2 jobs (in the qrels) that appear in the top 50
  mrr            1 / rank of the first grade-2 job (0 if none in the top 50)
  ndcg@10        graded: gain = 2^grade - 1 (2 → 3, 1 → 1), discounted by log2(rank + 1), divided by the best possible
  judged@10      share of the top 10 that have a label at all. Low = the method finds jobs outside the pool,
                 so its score is a lower bound (pool bias) — label those before trusting a win.
Unlabeled jobs count as grade 0. Queries with no grade-2 job get NaN recall/MRR and are left out of those means.

Output: one row per (run, method, query) appended to eval/results_{version}.parquet; summary printed.

Usage:
  python eval/run_eval.py                         # all methods, eval/qrels_v1.parquet
  python eval/run_eval.py --methods bm25 rrf --note "rrf first try"
"""

import argparse
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import DEFAULT_CACHE, EVAL_DIR, filter_mask, load_corpus, load_queries  # noqa: E402
from eval.search import Index, bm25_rank, dashboard_rank, rrf_rank, vector_rank  # noqa: E402

DEPTH = 50  # every method returns its top 50; metrics read the top 10 or 50 of that


def ndcg_at(grades: list[int], ideal: list[int], k: int = 10) -> float:
    def dcg(gs):
        return sum((2 ** g - 1) / math.log2(r + 1) for r, g in enumerate(gs[:k], start=1))
    best = dcg(sorted(ideal, reverse=True))
    return dcg(grades) / best if best > 0 else float("nan")


def query_metrics(ranked_ids: list[str], labels: dict[str, int]) -> dict:
    """ranked_ids: best-first job_ids from one method; labels: job_id -> grade for this query."""
    grades = [labels.get(j, 0) for j in ranked_ids]
    n_rel = sum(1 for g in labels.values() if g == 2)
    first = next((r for r, g in enumerate(grades, start=1) if g == 2), None)
    return {
        "precision@10": sum(g == 2 for g in grades[:10]) / 10,
        "recall@50": sum(g == 2 for g in grades[:50]) / n_rel if n_rel else float("nan"),
        "mrr": (1 / first if first else 0.0) if n_rel else float("nan"),
        "ndcg@10": ndcg_at(grades, list(labels.values()), 10),
        "judged@10": sum(j in labels for j in ranked_ids[:10]) / max(1, min(10, len(ranked_ids))),
        "returned": len(ranked_ids),
    }


def methods_for(ix: Index, mask: np.ndarray, text: str, qvec: np.ndarray) -> dict:
    """Each entry: a zero-arg function returning best-first row positions. Each recomputes its own scores so the
    timing is fair (no method reuses another's work). Query embedding time is excluded: vectors are cached."""
    return {
        "bm25": lambda: bm25_rank(ix.bm25_scores(text), mask, DEPTH),
        "vector": lambda: vector_rank(ix.similarities(qvec), mask, DEPTH),
        "dashboard": lambda: dashboard_rank(ix.similarities(qvec), ix.match, mask, DEPTH),
        "rrf": lambda: rrf_rank([bm25_rank(ix.bm25_scores(text), mask, 100),
                                 vector_rank(ix.similarities(qvec), mask, 100)], DEPTH),
    }


def summarize(res: pd.DataFrame) -> pd.DataFrame:
    cols = ["ndcg@10", "precision@10", "recall@50", "mrr", "judged@10"]
    out = res.groupby("method")[cols].mean()
    out["p50_ms"] = res.groupby("method")["ms"].median()
    out["p95_ms"] = res.groupby("method")["ms"].quantile(0.95)
    return out.sort_values("ndcg@10", ascending=False).round(3)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v1")
    p.add_argument("--qrels", default=None, help="default eval/qrels_{version}.parquet")
    p.add_argument("--methods", nargs="+", default=["bm25", "vector", "dashboard", "rrf"])
    p.add_argument("--note", default="", help="what changed in this run, saved with the results")
    p.add_argument("--no-save", action="store_true", help="print only; don't append to results (smoke tests)")
    args = p.parse_args()

    qrels_path = Path(args.qrels) if args.qrels else EVAL_DIR / f"qrels_{args.version}.parquet"
    if not qrels_path.exists():
        raise SystemExit(f"No answer key at {qrels_path} — finish labeling first (runbook §23).")
    qrels = pd.read_parquet(qrels_path)
    by_query = {q: dict(zip(g.job_id, g.label.astype(int))) for q, g in qrels.groupby("query_id")}

    from eval.pool import query_vectors  # cached query vectors; embeds (and pays) only for new query texts
    corpus = load_corpus(args.version)
    queries = load_queries(args.version)
    ix = Index(corpus, DEFAULT_CACHE)
    qvecs = query_vectors([q["text"] for q in queries], DEFAULT_CACHE)
    job_ids = corpus["job_id"].to_numpy()

    rows = []
    for q in queries:
        mask = filter_mask(corpus, q.get("filters")).to_numpy()
        fns = methods_for(ix, mask, q["text"], qvecs[q["text"]])
        for name in args.methods:
            t0 = time.perf_counter()
            ranked = fns[name]()
            ms = (time.perf_counter() - t0) * 1000
            rows.append({"query_id": q["id"], "type": q["type"], "method": name, "ms": ms,
                         **query_metrics(list(job_ids[ranked]), by_query.get(q["id"], {}))})

    res = pd.DataFrame(rows)
    sha = subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True).stdout.strip()
    res["run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    res["git_sha"], res["note"], res["qrels"] = sha, args.note, qrels_path.name

    print(summarize(res).to_string())
    print("\nnDCG@10 by query type:")
    print(res.pivot_table(index="type", columns="method", values="ndcg@10", aggfunc="mean").round(3).to_string())

    if args.no_save:
        return
    out = EVAL_DIR / f"results_{args.version}.parquet"
    if out.exists():
        res = pd.concat([pd.read_parquet(out), res], ignore_index=True)
    res.to_parquet(out, index=False)
    print(f"\nAppended to {out} ({len(res):,} rows total)")


if __name__ == "__main__":
    main()
