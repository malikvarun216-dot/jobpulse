"""
eval/embed_variants.py
----------------------
Re-embed the frozen corpus under one Chat 28 variant (eval/search.py VARIANTS) so run_eval.py can compare it with
the nightly vectors. Lab-only: writes to the local cache, never to S3 and never touches the nightly pipeline.
A variant that wins on the eval is shipped later by changing genai/embedding_agent.py.

Output: {cache}/variant_vectors_{version}_{variant}.parquet — job_id, chunk_no, embedding (one row per chunk;
non-chunk variants have chunk_no 0). Saved every 20 batches, so a re-run after an error skips what's done.

Usage:
  python eval/embed_variants.py --variant strip --dry-run   # texts, chars, ~tokens, truncation — no API call
  python eval/embed_variants.py --variant strip             # embeds (Voyage; VOYAGE_API_KEY or Secrets Manager)
"""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
from eval.common import DEFAULT_CACHE, load_corpus  # noqa: E402
from eval.search import MAX_CHARS, VARIANTS, variant_texts  # noqa: E402
from genai.embedding_agent import BATCH_SIZE, EMBEDDING_MODEL, _get_voyage_key  # noqa: E402

CHECKPOINT_EVERY = 20  # batches


def build_texts(corpus: pd.DataFrame, variant: str) -> pd.DataFrame:
    rows = []
    for job_id, title, desc in zip(corpus["job_id"], corpus["title"], corpus["description"]):
        for n, text in enumerate(variant_texts(title or "", desc or "", variant)):
            rows.append({"job_id": job_id, "chunk_no": n, "text": text})
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", required=True, choices=sorted(VARIANTS))
    p.add_argument("--version", default="v1")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    corpus = load_corpus(args.version)
    texts = build_texts(corpus, args.variant)
    chars = texts["text"].str.len()
    per_job = texts.groupby("job_id").size()
    print(f"{args.variant}: {len(texts):,} texts for {len(corpus):,} jobs ({per_job.mean():.1f} per job, max {per_job.max()}) | "
          f"{chars.sum():,} chars ~ {chars.sum() / 4 / 1e6:.1f}M tokens")
    if not VARIANTS[args.variant]["chunks"]:
        print(f"Cut at {MAX_CHARS:,} chars: {(chars >= MAX_CHARS).mean():.0%} of jobs lose text")
    if args.dry_run:
        return

    out = DEFAULT_CACHE / f"variant_vectors_{args.version}_{args.variant}.parquet"
    done = pd.read_parquet(out) if out.exists() else pd.DataFrame(columns=["job_id", "chunk_no", "embedding"])
    key = set(zip(done["job_id"], done["chunk_no"]))
    todo = texts[[(j, n) not in key for j, n in zip(texts["job_id"], texts["chunk_no"])]].reset_index(drop=True)
    print(f"Already embedded {len(done):,}, to do {len(todo):,}")

    import voyageai
    vo = voyageai.Client(api_key=_get_voyage_key())
    parts, tokens = [done], 0
    for b, i in enumerate(range(0, len(todo), BATCH_SIZE), start=1):
        batch = todo.iloc[i: i + BATCH_SIZE]
        for attempt in range(3):
            try:
                res = vo.embed(batch["text"].tolist(), model=EMBEDDING_MODEL, input_type=VARIANTS[args.variant]["input_type"])
                break
            except Exception as exc:  # rate limit / transient — same retry as the nightly agent
                if attempt == 2:
                    raise
                print(f"Voyage error: {exc}. Retry in {2 ** attempt}s")
                time.sleep(2 ** attempt)
        tokens += res.total_tokens
        parts.append(batch[["job_id", "chunk_no"]].assign(embedding=res.embeddings))
        if b % CHECKPOINT_EVERY == 0 or i + BATCH_SIZE >= len(todo):
            pd.concat(parts, ignore_index=True).to_parquet(out, index=False)
            print(f"batch {b}: {min(i + BATCH_SIZE, len(todo)):,}/{len(todo):,} | {tokens:,} tokens so far")
    print(f"Written {out} | {tokens:,} tokens this run")


if __name__ == "__main__":
    main()
