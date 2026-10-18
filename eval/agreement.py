"""
eval/agreement.py
-----------------
Can the LLM judge stand in for Varun? Compares human labels with judge labels on the same pairs.

Cohen's kappa = (observed agreement - agreement expected by chance) / (1 - chance agreement).
Raw "% agree" flatters a judge when most pairs are 0 — answering 0 always would already agree a lot;
kappa subtracts that. Quadratic-weighted kappa also counts 1-vs-2 as a smaller miss than 0-vs-2.
Rule of thumb (roadmap): kappa >= ~0.6 → trust the judge for the remaining pairs.

Usage:
  python eval/agreement.py --model claude-haiku-5-5                 # kappa on the human sample
  python eval/agreement.py --model claude-haiku-5-5 --write-qrels   # + eval/qrels_v1.parquet from the "all" run
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import DEFAULT_CACHE, EVAL_DIR  # noqa: E402

LEVELS = [0, 1, 2]


def cohen_kappa(a, b, weights: str | None = None) -> float:
    """a, b: equal-length label sequences over LEVELS. weights=None (unweighted) or 'quadratic'."""
    n = len(LEVELS)
    obs = np.zeros((n, n))
    for x, y in zip(a, b):
        obs[LEVELS.index(x), LEVELS.index(y)] += 1
    obs /= obs.sum()
    exp = np.outer(obs.sum(axis=1), obs.sum(axis=0))  # what independent raters with these label rates would hit
    i, j = np.indices((n, n))
    w = (i != j).astype(float) if weights is None else ((i - j) / (n - 1)) ** 2  # disagreement cost
    return float(1 - (w * obs).sum() / (w * exp).sum())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--version", default="v1")
    p.add_argument("--write-qrels", action="store_true")
    args = p.parse_args()

    human = pd.read_csv(DEFAULT_CACHE / f"human_labels_{args.version}.csv", dtype={"job_id": str})
    human = human.drop_duplicates(["query_id", "job_id"], keep="last")
    judge = pd.read_parquet(DEFAULT_CACHE / f"judge_{args.version}_human_{args.model}.parquet")
    m = human.merge(judge[judge.label.notna()], on=["query_id", "job_id"], suffixes=("_human", "_judge"))
    a, b = m.label_human.astype(int), m.label_judge.astype(int)

    print(f"{len(m)} pairs labeled by both | agree {np.mean(a == b):.0%}")
    print(f"Cohen's kappa {cohen_kappa(a, b):.2f} | quadratic-weighted {cohen_kappa(a, b, 'quadratic'):.2f}")
    print("Rows = you, columns = judge:")
    print(pd.crosstab(a, b, rownames=["human"], colnames=["judge"]))
    worst = m[(a - b).abs() == 2][["query_id", "job_id", "label_human", "label_judge", "reason"]]
    if len(worst):
        print(f"\n0-vs-2 disagreements ({len(worst)}) — read these first:\n{worst.to_string(index=False)}")

    if args.write_qrels:
        allj = pd.read_parquet(DEFAULT_CACHE / f"judge_{args.version}_all_{args.model}.parquet")
        q = allj[allj.label.notna()][["query_id", "job_id", "label"]].assign(label_source=args.model)
        hum = human[["query_id", "job_id", "label"]].assign(label_source="human")
        qrels = pd.concat([hum, q]).drop_duplicates(["query_id", "job_id"], keep="first")  # human label wins
        qrels["label"] = qrels["label"].astype("int8")
        out = EVAL_DIR / f"qrels_{args.version}.parquet"
        qrels.sort_values(["query_id", "job_id"]).to_parquet(out, index=False)
        print(f"\nWritten {out}: {len(qrels):,} pairs, {qrels.label_source.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
