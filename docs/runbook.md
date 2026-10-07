# JobPulse Runbook

What to do when something breaks. Each entry: **symptom → where to look → likely cause → fix → prevention.**
Region: `ap-south-1`. Environment: `dev`. Last updated: Chat 25 (2026-10-08).

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

## 1. Enrichment `JobRunState: TIMEOUT` (RunEnrichment) — or the duration alarm fires

- **Symptom:** `jobpulse-enrichment-dev` ends at its timeout (20 min since Chat 25; was 60) with `TIMEOUT`, or the
  alarm `jobpulse-enrichment-duration-70pct-dev` fires (run took > 14 min).
- **Look:** the run's log in `/aws-glue/python-jobs/output` (stream = job run id). Since Chat 25 every line is flushed
  live, so even a killed run shows how far it got:
  - `[timing] fetch_jobs=…s`, `enrich_and_write=…s`, `repair_partition=…s`, `total=…s` — which stage is slow
  - `[progress] N/M jobs | Xs` every 1,000 jobs
  - `[post-hook] … llm=… rules=… cache=… | llm_errors=… breaker_open=…` and `[timing] per-job time by source`
  - `[llm] first failure: <error>` and `circuit OPEN` — the LLM is failing (key, credits, model id)
- **Aug–Sep 2026 root cause (Chat 25):** every Claude call failed (none succeeded after Apr 21); the code retried each
  failure 3× with 1+2+4 s sleeps and labelled the job "rules". ~5.7K jobs × ~7 s ÷ 16 threads ≈ the full hour.
  Greenhouse JDs are empty, so ~70% of jobs skipped the regex fast path and went to the LLM path.
  Fixed: no own retry loop (SDK retries only transient errors), empty JDs never go to the LLM, circuit breaker after 5
  failures in a row, LLM off by default (`--use_llm false`). Measured on 5,729 real JDs: 4 s (LLM off), 14 s (LLM on, all failing).
- **If logs show `[llm] first failure`:** check the key / credit balance at console.anthropic.com. The run still
  succeeds on rules — this is a warning, not an outage.
- **Fix if it is real volume:** check `[progress]` rate; raise `timeout` in `glue.tf` only after reading the timings.
## 2. `bronze_to_silver` fails with `AnalysisException … cannot cast string to array<string>`

- **Symptom:** RunGlueJob fails in ~1 min. Seen Aug 1–9 2026 on `job.tags` (traceback in the Chat 24 log export).
- **Cause:** one source sent `tags` as text instead of a list. Spark *inferred* the JSON schema across all sources,
  the column became `string`, and `cast(tags as array<string>)` failed. Which source: unknown — the error doesn't say
  and bronze had expired (7-day lifecycle). Suspects: remotive (passes raw API jobs through) or arbeitnow (passes `tags` through).
- **Fixed (Chat 25):** `bronze_schema()` — explicit schema, every job field read as `string` (a list arrives as its
  JSON text); `parse_tags()` turns `'["a","b"]'`, `"a, b"` or null into `array<string>`. Missing fields come back null
  instead of `No such struct field`. Tests: `TestParseTags`, `test_tags_as_plain_string`, `test_tags_string_and_array_same_day`.
  GE expectation 6 checks every silver `tags` value is a list.
- **A new field in an ingestor** must be added to `BRONZE_JOB_FIELDS` in `spark/jobs/bronze_to_silver.py`, or Spark
  silently drops it (that's the price of an explicit schema).
- **Find a drifting source:** within 7 days, inspect each bronze file:
  ```bash
  aws s3 cp s3://jobpulse-bronze-dev/snapshot_date=YYYY-MM-DD/source=<src>/data.json.gz - | gunzip | head -c 2000
  ```
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
- **Since Chat 25 the runner calls `dbt build`:** a failing test fails the step. Find it: filter the log for `FAIL`.
  Run a test locally (read-only): `dbt test --select <test_name> --profiles-dir .` from `dbt_project/`.
  Never delete a test to make the night green — fix the model or the data.
- `unique_dim_company_company_key` failed in Chat 25 (368 keys): casing variants of one company. Fixed by grouping on
  the normalized name (`dim_company.sql`). If it fails again, a new spelling slipped past `lower(trim())`.

## 9. Great Expectations step fails (RunDataQuality)

- **Row count < 100:** usually a source outage or an ingestor returning `EMPTY` — check Lambda logs for the night.
- **Null `job_id` / `title`:** a source changed its field names — compare with the ingestor's `normalize_jobs`.
- **`ingested_date_ist` not in {snapshot_date}** (Chat 25): rows in today's partition were ingested on another IST
  day — a stale or mis-dated bronze file, or a backfill re-run of an old date with new data. Check `ingested_at` per source.
- **`tags` not a list:** schema drift reached silver (see §2).
- **Fix the cause, then re-run from RunGlueJob** for that date. Never lower the threshold to make it pass.

## 10. LLM / embedding API errors

- `AuthenticationError` → check the secret value (`jobpulse/anthropic_key_dev`, `jobpulse/voyage_key_dev`);
  secrets are JSON like `{"KEY_NAME": "value"}`; Voyage keys start with `pa-`.
- Enrichment calls the LLM **only with `--use_llm true`** (default false since Chat 25 — no Claude call has succeeded
  since 2026-04-21 and nobody noticed; Chat 31 decides if it is worth paying for). Turn on for one run:
  `--arguments '{"--use_llm":"true"}'`.
- Circuit breaker: 5 LLM failures in a row → no more LLM calls this run (`[llm] … circuit OPEN`).
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

## 12. Alarms

| Alarm | Fires when | Clears when |
|---|---|---|
| `jobpulse-sfn-no-success-26h-dev` | no successful Step Functions execution in 26 h (incl. "nothing ran") | the next successful run (sends an OK email) |
| `jobpulse-enrichment-duration-70pct-dev` | enrichment took > 14 min (70% of 20) | on its own after an hour — it's a warning |
| `jobpulse-embedding-duration-70pct-dev` | embeddings took > 42 min (70% of 60) | same |

- The no-success alarm replaced `jobpulse-sfn-failures-dev` (Chat 25), which returned to OK ~15 min after every
  failure — a month of red nights looked green in the console.
- **Expected:** it is in ALARM whenever the pipeline is paused (nothing succeeds). That is correct, not noise.
- **Caveat seen 2026-10-08:** created while the pipeline had been paused 19 days, it sat in `INSUFFICIENT_DATA`
  ("Unchecked: Initial alarm creation") instead of ALARM. CloudWatch drops a metric after ~15 days without data, so
  `ExecutionsSucceeded` for this state machine didn't exist and there was nothing to evaluate. So: **after a pause
  longer than ~2 weeks, this alarm stays silent** — check Step Functions by hand when resuming.
- Test the email path without breaking anything:
  `aws cloudwatch set-alarm-state --alarm-name jobpulse-sfn-no-success-26h-dev --state-value ALARM --state-reason "test" --region ap-south-1`
  (it re-evaluates to the real state within minutes).
## 13. Pause / resume the pipeline

- **Pause:** set `state = "DISABLED"` on `aws_cloudwatch_event_rule.daily_ingest` (`terraform/envs/dev/eventbridge.tf`)
  and apply, or in the console: EventBridge → Rules → `jobpulse-daily-ingest-dev` → Disable.
  Status: DISABLED 2026-09-19 → re-enabled 2026-10-08 (Chat 25) after a green manual run.
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
  2. *Two deployers* (fixed Chat 25). CI (`deploy.yml`) now owns all code: Lambda zips, Glue scripts, `genai_package.zip`,
     `dbt_project.zip`, `user_profile.yml`. Terraform creates those resources and then ignores their content
     (`lifecycle { ignore_changes }`). **So `terraform apply` no longer deploys code — push to `dev` does.**
     A brand-new environment needs one CI deploy after the first apply.
- **`Error: Too many command line arguments`** on `plan -out=x.tfplan` in **PowerShell 5.1**: PowerShell splits
  `-flag=value.ext` at the dot before Terraform sees it. Quote it: `terraform plan "-out=chat25.tfplan"`.
  Saved plan files hold every variable **in plain text** — `*.tfplan` is gitignored; delete them after apply.
- **Plan wants to import log groups:** expected once (Chat 24 `import` blocks in `monitoring.tf`). After the first apply they are
  no-ops.

## 15. Cost check

- **Biggest silent growers:** `s3://jobpulse-gold-dev/athena-results/` (query CSVs — 1.5 GB / 5K objects by Oct 2026,
  no lifecycle yet), Glue job duration creeping up. CloudWatch logs: 14-day retention since Chat 24 (`monitoring.tf`).
  ```bash
  aws s3 ls s3://jobpulse-gold-dev/athena-results/ --recursive --summarize | tail -2
  ```
- ⚠️ **`athena-results/` expiry (`aws_s3_bucket_lifecycle_configuration.gold`) stays `Disabled` until §18 passes.**
  Chat 25 turned off workgroup enforcement so dbt writes tables to `gold/models/`; the old tables lived in
  `athena-results/tables/<uuid>/` and an expiry would have deleted the gold layer.
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

## 18. Gold tables location (before enabling the `athena-results/` expiry)

All 4 dbt tables must point under `s3://jobpulse-gold-dev/models/`, none under `athena-results/`:
```bash
aws glue get-tables --database-name jobpulse_gold_dev --region ap-south-1 \
  --query 'TableList[].[Name,StorageDescriptor.Location]' --output table
```
Expected: `dim_company`, `dim_country`, `dim_role`, `fact_job_posting` → `.../models/jobpulse_gold_dev/<table>/<uuid>`;
`enrichment_scores` → `.../enrichment-scores/`; `job_embeddings` → `.../embeddings`; `stg_silver_jobs` (a view) has none.
Only then set `status = "Enabled"` in `s3.tf` and apply. The rule also cleans the orphaned `athena-results/tables/*`.
If a table still points at `athena-results/`: the workgroup is enforcing again (`aws athena get-work-group --work-group jobpulse-dev`).
