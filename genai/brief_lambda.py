"""
Weekly brief on a schedule (Chat 32 part 2): EventBridge (Monday 08:30 IST) → this Lambda → S3.

Same steps as `python -m genai.weekly_brief --upload`, minus the local files:
Athena facts → Nova Pro on Bedrock → faithfulness check → s3://jobpulse-gold-dev/briefs/week_end=<d>/.

Event (all optional; the Monday rule sends none of these):
    {"week_end": "2026-11-09"}   # brief for that week instead of the latest snapshot
    {"inject_test": true}        # prompt-injection test run → brief_inject.md, llm_log_inject.json

Failures RAISE, so the Lambda "Errors" metric counts them and the alarm emails (terraform brief.tf):
stale data, cost over the cap, a brief that fails the faithfulness check (it is still uploaded, with the
UNVERIFIED banner, so you can read what went wrong), an injection test the model obeyed.
Retries are off (maximum_retry_attempts = 0): a rerun is one manual invoke, not three paid calls.

Package layout (built by deploy.yml): genai/{__init__,brief_facts,weekly_brief,brief_lambda}.py and
athena_client.py at the zip root. pandas comes from the AWS SDK for pandas layer.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import boto3

from genai import weekly_brief as wb

MAX_SNAPSHOT_AGE_DAYS = 2   # latest silver snapshot older than this = the nightly pipeline is not landing data


def run(event: dict, run_query, bedrock, s3, today: date) -> dict:
    """The handler's logic with every AWS dependency passed in, so tests can use fakes."""
    event = event or {}
    inject = bool(event.get("inject_test"))
    if event.get("week_end"):
        week_end = date.fromisoformat(event["week_end"])
    else:
        week_end = wb.latest_snapshot(run_query)
        if (today - week_end).days > MAX_SNAPSHOT_AGE_DAYS:
            raise RuntimeError(f"latest silver snapshot {week_end} is older than {MAX_SNAPSHOT_AGE_DAYS} days "
                               f"(today {today}) - check the nightly pipeline before trusting a brief")

    facts, job_texts, user = wb.prepare(run_query, week_end, inject)
    worst = wb.worst_case_usd(wb.MODEL, len(wb.SYSTEM) + len(user))
    if worst > wb.RUN_CAP_USD:
        raise RuntimeError(f"worst case ${worst:.3f} > cap ${wb.RUN_CAP_USD:.2f}")

    files, log = wb.write_brief(bedrock, wb.MODEL, facts, job_texts, user, week_end, inject)
    keys = wb.upload(s3, week_end, {"facts.json": wb.facts_file(facts), **files})

    summary = {"event": "weekly_brief", "week_end": str(week_end), "inject_test": inject,
               "faithful": log["faithful"], "violations": log["violations"], "calls": len(log["calls"]),
               "injection_followed": log["injection_followed"], "usd_est_total": log["usd_est_total"],
               "s3_keys": keys}
    print(json.dumps(summary))   # one JSON line → CloudWatch Logs Insights can filter on any field
    if not log["faithful"]:
        raise RuntimeError(f"brief failed the faithfulness check: {log['violations']}")
    if log["injection_followed"]:
        raise RuntimeError("injection test: the model followed the attack text")
    return summary


def lambda_handler(event, context):
    from athena_client import run_query   # at the zip root in Lambda; imported here so tests need no Athena
    return run(event, run_query, wb.bedrock_client(wb.REGION), boto3.client("s3", region_name=wb.REGION),
               datetime.now(timezone.utc).date())
