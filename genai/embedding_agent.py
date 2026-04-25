"""
Batch-embed job descriptions via Voyage AI (voyage-3-lite, 512 dims).
Stores embeddings as Parquet in:
  s3://{gold_bucket}/embeddings/snapshot_date={date}/data.parquet

Caches by job_id — skips jobs already embedded for this snapshot_date.

Local usage (dry run):
  VOYAGE_API_KEY=va-... python genai/embedding_agent.py
"""

import io
import json
import os
import time

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import voyageai


EMBEDDING_MODEL = "voyage-4-lite"  # 512 dimensions, 200M free tokens/month
BATCH_SIZE = 128                   # Voyage API limit per call
MAX_CHARS_PER_JD = 4000            # Voyage context limit


def _get_voyage_key() -> str:
    key = os.environ.get("VOYAGE_API_KEY")
    if key:
        return key
    client = boto3.client("secretsmanager")
    secret = client.get_secret_value(SecretId="jobpulse/voyage_key_dev")
    return json.loads(secret["SecretString"])["VOYAGE_API_KEY"]


class EmbeddingAgent:
    def __init__(self, gold_bucket: str, region: str, dry_run: bool = False):
        self.gold_bucket = gold_bucket
        self.dry_run = dry_run
        self.s3 = boto3.client("s3", region_name=region)
        self.vo = voyageai.Client(api_key=_get_voyage_key())

    def _load_existing_job_ids(self, snapshot_date: str) -> set:
        key = f"embeddings/snapshot_date={snapshot_date}/data.parquet"
        try:
            obj = self.s3.get_object(Bucket=self.gold_bucket, Key=key)
            table = pq.read_table(io.BytesIO(obj["Body"].read()), columns=["job_id"])
            return set(table.column("job_id").to_pylist())
        except self.s3.exceptions.NoSuchKey:
            return set()
        except Exception as exc:
            print(f"[embedding_agent] Could not load existing embeddings: {exc}")
            return set()

    def _write_parquet(self, records: list, snapshot_date: str) -> None:
        key = f"embeddings/snapshot_date={snapshot_date}/data.parquet"

        existing_table = None
        try:
            obj = self.s3.get_object(Bucket=self.gold_bucket, Key=key)
            existing_table = pq.read_table(io.BytesIO(obj["Body"].read()))
        except Exception:
            pass

        new_table = pa.table({
            "job_id":        pa.array([r["job_id"] for r in records], type=pa.string()),
            "snapshot_date": pa.array([r["snapshot_date"] for r in records], type=pa.string()),
            "embedding":     pa.array([r["embedding"] for r in records], type=pa.list_(pa.float32())),
        })
        combined = pa.concat_tables([existing_table, new_table]) if existing_table is not None else new_table

        buf = io.BytesIO()
        pq.write_table(combined, buf, compression="snappy")
        buf.seek(0)
        self.s3.put_object(Bucket=self.gold_bucket, Key=key, Body=buf.read())
        print(f"[embedding_agent] Wrote {combined.num_rows} total embeddings to s3://{self.gold_bucket}/{key}")

    def run(self, jobs: list, snapshot_date: str) -> dict:
        existing_ids = self._load_existing_job_ids(snapshot_date)
        new_jobs = [
            j for j in jobs
            if str(j["job_id"]) not in existing_ids and j.get("description")
        ]

        print(
            f"[embedding_agent] Total: {len(jobs)} | "
            f"Already embedded: {len(existing_ids)} | "
            f"New: {len(new_jobs)} | dry_run={self.dry_run}"
        )

        if not new_jobs:
            return {"status": "OK", "embedded_new": 0, "skipped": len(jobs)}

        if self.dry_run:
            return {"status": "DRY_RUN", "embedded_new": 0, "would_embed": len(new_jobs)}

        records = []
        for i in range(0, len(new_jobs), BATCH_SIZE):
            batch = new_jobs[i: i + BATCH_SIZE]
            texts = [str(j["description"])[:MAX_CHARS_PER_JD] for j in batch]

            for attempt in range(3):
                try:
                    result = self.vo.embed(texts, model=EMBEDDING_MODEL)
                    break
                except Exception as exc:
                    if attempt == 2:
                        raise
                    wait = 2 ** attempt
                    print(f"[embedding_agent] Voyage error: {exc}. Retry in {wait}s...")
                    time.sleep(wait)

            for job, vec in zip(batch, result.embeddings):
                records.append({
                    "job_id": str(job["job_id"]),
                    "snapshot_date": snapshot_date,
                    "embedding": vec,
                })

            print(f"[embedding_agent] Batch {i // BATCH_SIZE + 1}: embedded {len(batch)} jobs")

        self._write_parquet(records, snapshot_date)
        return {"status": "OK", "embedded_new": len(records), "skipped": len(existing_ids)}
