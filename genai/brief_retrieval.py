"""
Retrieval for the weekly brief (Chat 32 RAG): which of this week's new jobs go into the prompt, and which passage of each.

RAG = Retrieve → Augment → Generate:
  1. Retrieve jobs:     embed PROFILE_QUERY (input_type "query") and every candidate job (input_type "document"),
                        rank by cosine similarity, keep the top K.
  2. Pick passages:     each top job's "what you'll do" section (text_utils.duties_section, Chat 32 part 4); for a JD
                        without one, split it into sections (chunk_text) and keep the one closest to the query.
  3. Augment/generate:  weekly_brief.py puts those passages in <job_postings>; the model cites [job:<id>].

The job recipe is exactly eval method `vector_strip_doc` (HTML stripped, first 4,000 chars, Voyage input_type):
best of the measured methods on the 108 hand labels, nDCG@10 0.268 vs 0.225 for the nightly raw-HTML vectors
(provisional: only ~6% of each top 10 is labeled). eval/search.py variant_texts("strip_doc") builds the same text.

Voyage is called over plain HTTPS (urllib), so the Lambda zip needs no voyageai package.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable

import numpy as np
import pandas as pd

from genai.text_utils import chunk_text, duties_section, strip_html

EMBEDDING_MODEL = "voyage-4-lite"      # same model as the nightly vectors and the eval
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_SECRET = "jobpulse/voyage_key_dev"
METHOD = "vector_strip_doc"            # eval method name this retriever reproduces
MAX_CHARS = 4000                       # same cut as the eval and genai/embedding_agent.py
BATCH = 64                             # texts per request: 64 × ≤4,000 chars ≈ 64K tokens, well under Voyage's limit
TOP_K = 5
EXCERPT_CHARS = 600
# The reader in one sentence (target role + core stack from config/user_profile.yml). Role + descriptive wording,
# the two query types where vector search led on the labels. Change it here, or --query / event "query".
PROFILE_QUERY = ("mid-level data engineer building batch and streaming data pipelines with Spark, Kafka, "
                 "Airflow and dbt on AWS or GCP")

Embed = Callable[[list[str], str], tuple[np.ndarray, int]]   # (texts, input_type) -> (vectors, tokens used)


def voyage_key(secrets_client) -> str:
    """The secret is JSON ({"VOYAGE_API_KEY": "..."}) — sending the raw string was the Chat 26 401 bug."""
    raw = secrets_client.get_secret_value(SecretId=VOYAGE_SECRET)["SecretString"]
    return json.loads(raw)["VOYAGE_API_KEY"]


def voyage_embedder(api_key: str, timeout_s: int = 60) -> Embed:
    """Returns embed(texts, input_type) calling the Voyage REST API in batches; one retry per batch after 5 s."""
    def post(batch: list[str], input_type: str) -> dict:
        body = json.dumps({"input": batch, "model": EMBEDDING_MODEL, "input_type": input_type}).encode()
        req = urllib.request.Request(VOYAGE_URL, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read())

    def embed(texts: list[str], input_type: str) -> tuple[np.ndarray, int]:
        vecs, tokens = [], 0
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            try:
                out = post(batch, input_type)
            except Exception:          # 429 / timeout / 5xx: wait once, then let a second failure raise
                time.sleep(5)
                out = post(batch, input_type)
            vecs += [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]
            tokens += out.get("usage", {}).get("total_tokens", 0)
        return np.asarray(vecs, dtype=np.float32).reshape(len(vecs), -1), tokens

    return embed


def doc_text(description: str | None) -> str:
    return strip_html(description)[:MAX_CHARS]


def cosine(qvec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    return m @ (qvec / (np.linalg.norm(qvec) + 1e-9))


class Retriever:
    """Call with this week's candidate jobs (job_id, title, company, country, apply_url, match_score, description);
    returns the top K rows + `similarity` + `excerpt`. After a call, `.log` holds what to record per run."""

    method = METHOD

    def __init__(self, embed: Embed, query: str = PROFILE_QUERY, k: int = TOP_K):
        self.embed, self.query, self.k, self.log = embed, query, k, {}

    def __call__(self, candidates: pd.DataFrame) -> pd.DataFrame:
        cand = candidates.assign(_doc=candidates["description"].map(doc_text))
        cand = cand[cand["_doc"].str.len() > 0].reset_index(drop=True)
        if cand.empty:
            self.log = {"method": self.method, "query": self.query, "candidates": 0}
            return cand.assign(similarity=[], excerpt=[]).drop(columns="_doc")

        qvec, q_tokens = self.embed([self.query], "query")
        docs, d_tokens = self.embed(cand["_doc"].tolist(), "document")
        sims = cosine(qvec[0], docs)
        top = np.argsort(-sims, kind="stable")[: self.k]
        # Python floats rounded after the float32 → float cast: 0.503, not 0.503000020980835 (the checker compares exactly)
        picked = cand.iloc[top].assign(similarity=[round(float(x), 3) for x in sims[top]]).reset_index(drop=True)

        # Passage: the JD's "what you'll do" section when it has one (94% do); otherwise the section closest to the
        # query. Closest-to-query alone picked the requirements list (the query is a list of skills) — Chat 32 part 4.
        texts = [strip_html(d) for d in picked["description"]]
        excerpts = [duties_section(t, EXCERPT_CHARS) for t in texts]
        missing = [i for i, e in enumerate(excerpts) if not e]
        chunks = [chunk_text(texts[i]) for i in missing]
        flat = [c for cs in chunks for c in cs]
        c_tokens = 0
        if flat:
            cvecs, c_tokens = self.embed(flat, "document")
            csims, pos = cosine(qvec[0], cvecs), 0
            for i, cs in zip(missing, chunks):
                best = int(np.argmax(csims[pos:pos + len(cs)]))
                excerpts[i] = " ".join(cs[best].split())[:EXCERPT_CHARS]
                pos += len(cs)
        picked["excerpt"] = excerpts

        by_match = cand.sort_values(["match_score", "job_id"], ascending=[False, True], na_position="last")
        self.log = {"method": self.method, "model": EMBEDDING_MODEL, "query": self.query, "candidates": len(cand),
                    "k": self.k, "retrieved": [{"job_id": j, "similarity": float(s)}
                                               for j, s in zip(picked["job_id"], picked["similarity"])],
                    "passages": {"duties_section": len(picked) - len(missing), "closest_chunk": len(missing)},
                    "chunks_embedded": len(flat), "embed_tokens": q_tokens + d_tokens + c_tokens,
                    # same K by the old pick (query-independent match_score): how much retrieval changed the list
                    "overlap_with_match_score_top_k": len(set(picked["job_id"]) & set(by_match["job_id"].head(self.k)))}
        return picked.drop(columns="_doc")
