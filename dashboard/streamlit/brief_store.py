"""
Read the weekly briefs for the dashboard's "Weekly brief" tab (Chat 32 part 4).

The Lambda (genai/brief_lambda.py) writes one folder per week:
    s3://jobpulse-gold-dev/briefs/week_end=<YYYY-MM-DD>/{brief.md, facts.json, llm_log.json}
(+ brief_inject.md / llm_log_inject.json after a prompt-injection test run).

No Streamlit here, so tests can pass a fake S3 client. The dashboard only reads: no LLM call, no Athena scan.
"""

from __future__ import annotations

import json
import re

PREFIX = "briefs/"
_WEEK = re.compile(r"week_end=(\d{4}-\d{2}-\d{2})/$")


def list_weeks(s3, bucket: str) -> list[str]:
    """week_end dates that have a folder, newest first. Delimiter="/" makes S3 return the folders
    (CommonPrefixes) instead of every file."""
    weeks, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": PREFIX, "Delimiter": "/", **({"ContinuationToken": token} if token else {})}
        resp = s3.list_objects_v2(**kw)
        weeks += [m.group(1) for p in resp.get("CommonPrefixes", []) if (m := _WEEK.search(p["Prefix"]))]
        if not resp.get("IsTruncated"):
            return sorted(weeks, reverse=True)
        token = resp["NextContinuationToken"]


def load_week(s3, bucket: str, week_end: str) -> dict:
    """{file name: content} for one week; .json files parsed. Lists first, so a missing file is simply absent."""
    prefix = f"{PREFIX}week_end={week_end}/"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])]
    out = {}
    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        name = key[len(prefix):]
        out[name] = json.loads(body) if name.endswith(".json") else body
    return out


def for_display(md: str) -> str:
    """Shift the brief's Markdown headings down two levels ("# Brief" → "### Brief"), so the brief sits under
    the dashboard's own title instead of competing with it. Display only; the stored brief.md is unchanged."""
    return re.sub(r"^(#{1,4}) ", r"\1## ", md, flags=re.M)


def run_summary(log: dict) -> dict:
    """The few numbers worth showing above the brief, from llm_log.json."""
    calls = log.get("calls", [])
    retrieval = log.get("retrieval", {})
    return {"faithful": bool(log.get("faithful")), "violations": log.get("violations", []),
            "attempts": len(calls), "usd": log.get("usd_est_total"),
            "model": calls[-1]["model"] if calls else log.get("requested_model"),
            "prompt_version": log.get("prompt_version"),
            "latency_s": round(sum(c.get("latency_s", 0) for c in calls), 2),
            "picked_by": retrieval.get("method"), "query": retrieval.get("query"),
            "candidates": retrieval.get("candidates"),
            "overlap_with_match_score_top_k": retrieval.get("overlap_with_match_score_top_k"),
            "passages": retrieval.get("passages")}
