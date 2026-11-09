"""
eval/freeze_corpus.py
---------------------
Freeze the retrieval-eval corpus: distinct jobs from pinned silver snapshot dates → one Parquet file
plus a manifest, written once and never changed. A new corpus = a new version folder (corpus_v2/ ...).

Why frozen: labels (qrels) are tied to exact job_ids and texts. If the corpus moved every night,
last week's precision@10 and this week's would measure different things.

Output: s3://jobpulse-gold-dev/eval/corpus_{version}/corpus.parquet + manifest.json
        (and a local copy under --cache, for the labeling notebook)

Usage (dry run prints the manifest, writes nothing to S3):
  python eval/freeze_corpus.py --dates 2026-10-12 2026-10-13 2026-10-14 --dry-run
  python eval/freeze_corpus.py --dates 2026-10-12 2026-10-13 2026-10-14
"""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

SILVER_BUCKET = "jobpulse-silver-dev"
GOLD_BUCKET = "jobpulse-gold-dev"
REGION = "ap-south-1"
COLUMNS = [
    "job_id", "source", "title", "company_name", "country", "role_family",
    "location_raw", "publication_date", "apply_url", "description",
]


def download_silver(s3, date: str, cache: Path) -> Path:
    """Copy one silver snapshot to the local cache (read-only GETs). Skips if already cached."""
    local_root = cache / f"silver_{date}"
    if local_root.exists():
        return local_root
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=SILVER_BUCKET, Prefix=f"snapshot_date={date}/"):
        for obj in page.get("Contents", []):
            if obj["Size"] == 0:  # Glue/Hadoop "country=AU_$folder$" markers, not data
                continue
            local = local_root / obj["Key"].split("/", 1)[1]
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(SILVER_BUCKET, obj["Key"], str(local))
    if not local_root.exists():
        raise SystemExit(f"No silver data for snapshot_date={date}")
    return local_root


def load_snapshot(root: Path, date: str) -> pa.Table:
    files = [str(f) for f in root.rglob("*.parquet")]  # older caches may still hold zero-byte $folder$ markers
    t = ds.dataset(files, format="parquet", partitioning="hive", partition_base_dir=str(root)).to_table()
    t = t.select(COLUMNS)
    t = t.set_column(0, "job_id", pc.cast(t["job_id"], pa.string()))
    return t.append_column("snapshot_date", pa.array([date] * t.num_rows, pa.string()))


def build_corpus(snapshots: list[pa.Table]) -> pa.Table:
    """One row per job_id: the latest snapshot wins (a JD edited on day 2 keeps the day-2 text)."""
    df = pa.concat_tables(snapshots).to_pandas()
    df = df.sort_values(["job_id", "snapshot_date"]).drop_duplicates("job_id", keep="last")
    df["has_description"] = df["description"].fillna("").str.strip().str.len() > 0
    return pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)


def content_hash(corpus: pa.Table) -> str:
    """sha256 over (job_id, title, description) in job_id order — same rows → same hash, on any machine."""
    h = hashlib.sha256()
    rows = sorted(zip(corpus["job_id"].to_pylist(), corpus["title"].to_pylist(), corpus["description"].to_pylist()))
    for job_id, title, desc in rows:
        h.update(f"{job_id}\x1f{title or ''}\x1f{desc or ''}\x1e".encode("utf-8"))
    return h.hexdigest()


def make_manifest(corpus: pa.Table, version: str, dates: list[str]) -> dict:
    df = corpus.select(["source", "has_description"]).to_pandas()
    by_source = df.groupby("source")["has_description"].agg(["size", "sum"])
    try:
        # "-dirty" = frozen with uncommitted code changes (corpus_v1 was: the $folder$ fix, committed after)
        git_sha = subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True).stdout.strip()
    except OSError:
        git_sha = None
    return {
        "version": version,
        "snapshot_dates": sorted(dates),
        "row_count": corpus.num_rows,
        "rows_with_description": int(df["has_description"].sum()),
        "by_source": {s: {"rows": int(r["size"]), "with_description": int(r["sum"])} for s, r in by_source.iterrows()},
        "content_sha256": content_hash(corpus),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dates", nargs="+", required=True, help="silver snapshot_date values, YYYY-MM-DD")
    p.add_argument("--version", default="v1")
    p.add_argument("--cache", default=r"C:\Users\malik\jobpulse_lab_data")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    import boto3
    s3 = boto3.client("s3", region_name=REGION)
    cache = Path(args.cache)
    corpus = build_corpus([load_snapshot(download_silver(s3, d, cache), d) for d in args.dates])
    manifest = make_manifest(corpus, args.version, args.dates)
    print(json.dumps(manifest, indent=2))
    if args.dry_run:
        return

    prefix = f"eval/corpus_{args.version}/"
    if s3.list_objects_v2(Bucket=GOLD_BUCKET, Prefix=prefix, MaxKeys=1).get("KeyCount"):
        raise SystemExit(f"s3://{GOLD_BUCKET}/{prefix} already exists — a frozen corpus is never overwritten. Use a new --version.")

    out = cache / f"corpus_{args.version}"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(corpus, out / "corpus.parquet", compression="snappy")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name in ("corpus.parquet", "manifest.json"):
        s3.upload_file(str(out / name), GOLD_BUCKET, prefix + name)
    print(f"Frozen: s3://{GOLD_BUCKET}/{prefix} ({manifest['row_count']:,} rows) + local copy {out}")


if __name__ == "__main__":
    main()
