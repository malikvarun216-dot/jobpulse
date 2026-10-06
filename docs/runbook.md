# JobPulse Runbook

What to do when something breaks. Each entry: **symptom → where to look → likely cause → fix → prevention.**
Region: `ap-south-1`. Environment: `dev`. Last updated: Chat 23 (2026-10-06).

> Windows / Git Bash tip: log-group names start with `/`, and Git Bash rewrites them into Windows paths.
> Prefix AWS CLI commands that take a log-group name with `MSYS_NO_PATHCONV=1`.

---

## 0. First triage — "the pipeline failed"

1. **Step Functions** → `jobpulse-ingest-pipeline-dev` → latest execution → the red state tells you which step.
   ```bash
   aws stepfunctions list-executions --region ap-south-1 \
     --state-machine-arn arn:aws:states:ap-south-1:240939827246:stateMachine:jobpulse-ingest-pipeline-dev \
     --max-results 5 --query 'executions[].[startDate,status]' --output table
   ```
2. Click the failed state → **Output / Error** — for Glue steps it contains `JobName`, `JobRunState`
   (`FAILED` vs `TIMEOUT`) and `ErrorMessage`.
3. Logs:

   | Step | Log group |
   |---|---|
   | Lambda ingestors | `/aws/lambda/jobpulse-ingest-<source>-dev` |
   | Glue Spark (`RunGlueJob`) | `/aws-glue/jobs/error`, `/aws-glue/jobs/logs-v2` |
   | Glue Python Shell (dbt, GE, enrichment, embeddings) | `/aws-glue/python-jobs/output` (stdout), `/aws-glue/python-jobs/error`; stream name = job run id |

4. Is it a **one-off** (one red night) or a **trend** (several)? Check the last 10 runs before fixing anything —
   a trend usually means volume growth or a source change, not a code bug.

---

## 1. Enrichment `JobRunState: TIMEOUT` (RunEnrichment)

- **Symptom:** `jobpulse-enrichment-dev` ends after exactly 60 min with `TIMEOUT`.
- **Look:** Glue → job runs → duration trend. In Aug 2026 it had been running 48–56 min for months before it tipped over.
  ```bash
  aws glue get-job-runs --job-name jobpulse-enrichment-dev --max-results 30 --region ap-south-1 \
    --query 'JobRuns[].[StartedOn,JobRunState,ExecutionTime]' --output table
  ```
- **Likely cause:** volume grew (6.6K → 7.9K jobs/day) and every job is processed every night.
- **Quick fix:** raise `timeout` in `terraform/envs/dev/glue.tf` (`aws_glue_job.enrichment_runner`) or DPU; re-run.
- **Real fix (Chat 25):** incremental enrichment (only jobs not already scored), per-stage timing logs.
- **Prevention:** alarm when duration > 70% of timeout.

## 2. `bronze_to_silver` fails with `AnalysisException … cannot cast string to array<string>`

- **Symptom:** RunGlueJob fails in ~1 min. Seen Aug 1–9 2026 on `job.tags`.
- **Cause:** one source changed a field's type (list → text). Spark infers JSON types across all sources; the
  inferred type changes and the explicit cast fails.
- **Find the source:** list today's bronze files and inspect the field in each:
  ```bash
  aws s3 ls s3://jobpulse-bronze-dev/snapshot_date=YYYY-MM-DD/ --recursive
  aws s3 cp s3://jobpulse-bronze-dev/snapshot_date=YYYY-MM-DD/source=<src>/data.json.gz - | gunzip | head -c 2000
  ```
  Do this **within 7 days** — bronze expires after 7 days.
- **Fix:** normalize the field's type in that source's ingestor (`normalize_jobs`) and/or handle both shapes in
  `spark/jobs/bronze_to_silver.py`. Re-run the Glue job for the failed date (`--snapshot_date`).
- **Prevention (Chat 25):** explicit schema in Spark; a GE type expectation; a unit test with both shapes.

## 3. Lambda returns HTTP 403 / `server: cloudflare`

- **Cause:** the API blocks AWS datacenter IPs (Himalayas, RemoteOK, Jooble, TokyoDev, DoraHacks are known).
- **Fix:** no code fix — choose another source. Keep the source out of the Step Functions `Parallel` state.
- **Prevention:** one test invoke from Lambda (`{"dry_run": true}`) before writing an ingestor.

## 4. Lambda `Runtime.ImportModuleError: No module named 'X'`

- **Cause:** a third-party import (e.g. `yaml`) — the Lambda runtime has only stdlib + boto3.
- **Fix:** remove the dependency (hardcode config) or bundle it / use a Layer.
- **Prevention:** `python -c "import ingest_<src>"` in a clean Python 3.12 before deploying.

## 5. Athena returns 0 rows right after a Glue job wrote data

- **Cause:** new partitions not registered in the Glue Data Catalog.
- **Fix:** `MSCK REPAIR TABLE jobpulse_silver_dev.silver_jobs;` (dbt_runner already does this before `dbt run`).
- **Future:** partition projection removes the need for repairs.

## 6. Python job crashes reading Athena output: `EmptyDataError`

- **Cause:** an Athena query with no rows writes a **0-byte** file (no header).
- **Fix:** read bytes first; `if not content.strip(): return []` before `pd.read_csv`.

## 7. Glue Python Shell pip / import errors

| Error | Cause | Fix |
|---|---|---|
| pip conflict on botocore (any modern package) | Glue **5.1** Python Shell vendors old botocore | `glue_version = "4.0"` |
| `dbt-core` needs Python ≥ 3.10 | Glue 4.0 = Python 3.9 | `dbt-core==1.9.10`, `dbt-athena-community==1.9.5` |
| pyarrow builds from source / fails | no 3.9 wheels after 14.x | `pyarrow==14.0.2` |
| `numpy.core.multiarray failed to import` | numpy 2.x or a 2nd numpy installed | don't install numpy, or pin `numpy==1.26.4` |
| `No module named 'genai'` | `--extra-py-files` not on `sys.path` in Glue 4.0 | bootstrap in the runner (download zip → extract → `sys.path.insert`) |
| `TypeError: unsupported operand … \|` | `str \| None` on Python 3.9 | `from __future__ import annotations` |

- **Prevention:** pin every package in `--additional-python-modules` (unpinned `anthropic>=0.40.0` resolved to
  0.125.0 in Sep 2026).

## 8. dbt step fails

- **Logs:** `/aws-glue/python-jobs/output` (dbt runs via `subprocess`, so output streams there).
- `COLUMN_NOT_FOUND` after a Spark change → the staging model has an explicit column list; add the new column.
- `FUNCTION_NOT_FOUND md5(varchar)` → Athena needs `to_hex(md5(to_utf8(col)))`.
- `Unsupported Hive type: timestamp with time zone` → use `localtimestamp`.
- **Note:** the runner calls `dbt run` (tests not executed) until Chat 25 switches it to `dbt build`.

## 9. Great Expectations step fails (RunDataQuality)

- **Row count < 100:** usually a source outage or an ingestor returning `EMPTY` — check Lambda logs for the night.
- **Null `job_id` / `title`:** a source changed its field names — compare with the ingestor's `normalize_jobs`.
- **Fix the cause, then re-run from RunGlueJob** for that date. Never lower the threshold to make it pass.

## 10. LLM / embedding API errors

- `AuthenticationError` → check the secret value (`jobpulse/anthropic_key_dev`, `jobpulse/voyage_key_dev`);
  secrets are JSON like `{"KEY_NAME": "value"}`; Voyage keys start with `pa-`.
- Daily budget reached (`BudgetExceededError` in logs) → **not a failure**: remaining jobs use the rules result.
  Ledger: `s3://jobpulse-gold-dev/enrichment-cache/budget-YYYY-MM-DD.json`.
- Re-score after a profile change without LLM spend:
  ```bash
  aws s3 cp config/user_profile.yml s3://jobpulse-silver-dev/config/user_profile.yml
  aws glue start-job-run --job-name jobpulse-enrichment-dev --region ap-south-1 \
    --arguments '{"--force_rescore":"true","--snapshot_date":"YYYY-MM-DD"}'
  ```

## 11. Re-running after a fix

- Upload the fixed script / zip, then **confirm the S3 object's LastModified** before re-running (a redrive
  that starts seconds before the upload uses the old script).
- Re-run one step: `aws glue start-job-run --job-name <job> --arguments '{"--snapshot_date":"YYYY-MM-DD"}'`.
- Re-run the whole night: start a new Step Functions execution (all steps are idempotent per `snapshot_date`).

## 12. Alarm emails every night

- `jobpulse-sfn-failures-dev` fires on `ExecutionsFailed ≥ 1` and **returns to OK ~15 min later** on its own.
  An OK email does **not** mean it's fixed — check the latest execution status.
- Planned (Chat 25): alarm on "no successful execution in 24 h", which stays red until a run succeeds.

## 13. Pause / resume the pipeline

- **Pause:** set `state = "DISABLED"` on `aws_cloudwatch_event_rule.daily_ingest` (`terraform/envs/dev/eventbridge.tf`)
  and apply, or in the console: EventBridge → Rules → `jobpulse-daily-ingest-dev` → Disable.
  Current status (2026-10-06): **DISABLED** since 2026-09-19.
- **Resume:** fix the failure first, then `state = "ENABLED"`. Watch the next 3 nights.

## 14. Terraform

- **`Error acquiring the state lock`:** a previous apply was interrupted. If you are sure nothing else is running:
  `terraform force-unlock <lock-id>`.
- **`plan` wants to destroy many resources you didn't touch:** you are on an **out-of-date checkout**. Stop.
  `git fetch origin && git status` → `git pull --ff-only origin dev`, then plan again.
  State lives in S3 and reflects the newest applied code.
- **`plan` says "No changes" but you added a file:** check the filename (`ls -la`) — a trailing space once hid `s3.tf`.
- **Resources deleted outside Terraform** (e.g. EC2 + Elastic IP, gone by Oct 2026) → plan will try to recreate them; remove them
  from the `.tf` files (or `terraform state rm`) instead.

## 15. Cost check

- **Biggest silent growers:** `s3://jobpulse-gold-dev/athena-results/` (query CSVs — 1.5 GB by Oct 2026, no
  lifecycle yet), CloudWatch log groups (no retention set), Glue job duration creeping up.
  ```bash
  aws s3 ls s3://jobpulse-gold-dev/athena-results/ --recursive --summarize | tail -2
  ```
- Glue Python Shell at 1/16 DPU ≈ $0.03 per hour of runtime; Glue Spark G.1X × 2 ≈ $0.03 per 2-min run (approx.).
- AWS Budgets (account-wide, shared with ledgerline): `ledgerline-monthly` $20 (50/80/100% + forecast), `ledgerline-daily-spike` $2/day, zero-spend $0.01.
- Plan state: `aws freetier get-account-plan-state --region us-east-1`.

## 16. Local checkout hygiene (before any session)

```bash
git fetch origin
git status            # "behind" = pull before doing anything
git log --oneline -1 origin/dev
```
Never `terraform apply` from a checkout that is behind `origin/dev`.
