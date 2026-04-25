"""
Load JD embeddings from S3 and run cosine similarity search.
No vector DB needed — NumPy in-memory at query time (~200ms for 15K jobs).

Cosine vs Euclidean:
  Cosine measures angle between vectors — scale-invariant.
  A short JD and long JD describing the same role score 1.0.
  Euclidean penalizes length differences, which makes it wrong for text.
"""

import io

import numpy as np
import pyarrow.parquet as pq


EMBEDDING_MODEL = "voyage-4-lite"


def load_embeddings(s3_client, gold_bucket: str, snapshot_date: str = None):
    """
    Load embeddings Parquet for the given snapshot_date (or latest if None).
    Returns (matrix [N, 512], parallel list of job_ids).
    """
    if not snapshot_date:
        resp = s3_client.list_objects_v2(
            Bucket=gold_bucket,
            Prefix="embeddings/snapshot_date=",
            Delimiter="/",
        )
        prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
        if not prefixes:
            return np.empty((0, 512), dtype=np.float32), []
        key = f"{sorted(prefixes)[-1]}data.parquet"
    else:
        key = f"embeddings/snapshot_date={snapshot_date}/data.parquet"

    obj = s3_client.get_object(Bucket=gold_bucket, Key=key)
    table = pq.read_table(io.BytesIO(obj["Body"].read()))

    job_ids = table.column("job_id").to_pylist()
    matrix = np.array(table.column("embedding").to_pylist(), dtype=np.float32)
    return matrix, job_ids


def cosine_top_k(query_vec, matrix: np.ndarray, job_ids: list, k: int = 20):
    """
    Returns top-k [(job_id, score)] sorted by cosine similarity descending.
    score is in [-1, 1]; for semantically similar texts typically 0.7-1.0.
    """
    if matrix.shape[0] == 0:
        return []

    k = min(k, len(job_ids))
    q = np.array(query_vec, dtype=np.float32)
    q_norm = q / (np.linalg.norm(q) + 1e-9)

    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    scores = (matrix / norms) @ q_norm  # shape [N]

    top_indices = np.argpartition(scores, -k)[-k:]
    top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

    return [(job_ids[i], float(scores[i])) for i in top_indices]


def search(query_text: str, voyage_client, s3_client, gold_bucket: str, k: int = 20, snapshot_date: str = None):
    """
    End-to-end semantic search.
    Returns top-k [(job_id, similarity_score)] sorted descending.
    Returns [] if no embeddings exist yet.
    """
    result = voyage_client.embed([query_text], model=EMBEDDING_MODEL)
    query_vec = result.embeddings[0]

    matrix, job_ids = load_embeddings(s3_client, gold_bucket, snapshot_date)
    if not job_ids:
        return []

    return cosine_top_k(query_vec, matrix, job_ids, k=k)
