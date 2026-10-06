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
  from the `.tf` files (or `terraform state rm`) instead. (`ec2.tf` removed in Chat 24.)
- **`No value for required variable "adzuna_app_id"`:** secrets are no longer in a committed file (Chat 24). They are
  stored as **User-scope** Windows env vars (persist across reboots; every *new* PowerShell window gets them).
  One-time setup (or after a key rotation):
  ```powershell
  [Environment]::SetEnvironmentVariable("TF_VAR_adzuna_app_id", "<id>", "User"); [Environment]::SetEnvironmentVariable("TF_VAR_adzuna_app_key", "<key>", "User")
  ```
  A window opened *before* the setup doesn't see them — load them into it:
  ```powershell
  $env:TF_VAR_adzuna_app_id = [Environment]::GetEnvironmentVariable("TF_VAR_adzuna_app_id","User"); $env:TF_VAR_adzuna_app_key = [Environment]::GetEnvironmentVariable("TF_VAR_adzuna_app_key","User")
  ```
  Check without printing the secret (expect `id length: 8, key length: 32`):
  ```powershell
  "id length: $($env:TF_VAR_adzuna_app_id.Length), key length: $($env:TF_VAR_adzuna_app_key.Length)"
  ```
  Don't also keep a `terraform.tfvars` with the key — a tfvars file beats env vars, so a stale key there would win.
  The value still ends up in the (encrypted) S3 state and in the Lambda's env vars — Secrets Manager would fix that (not done).
- **Plan shows every Lambda / Glue script "changed" but you edited nothing:**
  1. *Line endings.* A Windows checkout with `core.autocrlf=true` has CRLF files; Terraform hashes them → new md5 / zip hash.
     `.gitattributes` (`eol=lf`, Chat 24) prevents it. Existing checkout: `git ls-files --eol | grep w/crlf` → if any, re-checkout:
     only after committing — the guard refuses if anything is uncommitted (PowerShell):
     `if (git status --porcelain) { "STOP: uncommitted changes - commit first" } else { git rm -rq --cached .; git reset --hard HEAD }`
     Without the guard, `reset --hard` silently deletes uncommitted work (incidents, 2026-10-06).
  2. *Two deployers.* `deploy.yml` (CI) also uploads the Lambda code and Glue scripts. CI's `zip` gives different bytes than
     Terraform's `archive_file` (`source_code_hash` differs, same code) and `aws s3 cp` drops the object tags (`tags_all` diff).
     Harmless to apply — same code goes back. Known drift until one tool owns code deploys.
- **Plan wants to import log groups:** expected once (Chat 24 `import` blocks in `monitoring.tf`). After the first apply they are
  no-ops.

## 15. Cost check

- **Biggest silent growers:** `s3://jobpulse-gold-dev/athena-results/` (query CSVs — 1.5 GB / 5K objects by Oct 2026,
  no lifecycle yet), Glue job duration creeping up. CloudWatch logs: 14-day retention since Chat 24 (`monitoring.tf`).
  ```bash
  aws s3 ls s3://jobpulse-gold-dev/athena-results/ --recursive --summarize | tail -2
  ```
- ⚠️ **Do not put an expiry rule on `athena-results/`** (or delete files there by hand) while the gold tables live in
  `athena-results/tables/<uuid>/`. Check first:
  `aws glue get-tables --database-name jobpulse_gold_dev --query 'TableList[].StorageDescriptor.Location'`.
  Why they live there: the workgroup has `enforce_workgroup_configuration = true`, so dbt-athena drops the models'
  `s3_data_dir` and Athena writes CTAS output under the workgroup result location. Chat 25 moves them out.
- **Python package drift in Glue:** `--additional-python-modules` are pinned with `==` since Chat 24. Check what a run
  actually installed: CloudWatch `/aws-glue/python-jobs/output`, filter `"Successfully installed"`.
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

## 17. Local backup / restore

- Chat 24 snapshot: `C:\Users\malik\jobpulse_backup\2026-10-06\` — silver (all partitions), gold `embeddings/`,
  `enrichment-scores/`, `enrichment-cache/`, the 4 live dbt tables, and Glue logs Aug 1 – Sep 20 (`logs/*.json`, 122K events).
  `manifest.txt` has S3-vs-local file counts.
- Refresh: same commands with a new date folder — `aws s3 sync s3://<bucket>/<prefix> <local>`.
- Git Bash gotcha: with `MSYS_NO_PATHCONV=1` (needed for log-group names like `/aws-glue/...`), pass Windows paths
  (`C:/Users/...`) to `aws.exe` — `/c/Users/...` is taken literally and lands in `C:\c\Users\...`.
- Restore: `aws s3 sync <local> s3://<bucket>/<prefix>`, then `MSCK REPAIR TABLE` for partitioned tables.
