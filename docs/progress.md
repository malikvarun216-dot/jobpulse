Chat 1 — Repo Setup
- Created GitHub repo: jobpulse (private)
- Scaffolded full folder structure
- Branching strategy: dev → main

## Chat 2 — Himalayas Ingestor + AWS Setup
Date: 2025-04-17

### Built
- ingestion/sources/himalayas/ingest_himalayas.py
- tests/test_ingest_himalayas.py

### AWS
- Fresh account created, root MFA enabled
- IAM user varun-admin created with AdministratorAccess
- AWS CLI configured (ap-south-1)
- Free tier active, $100 credits, billing alarm set

### Key Decisions
- Himalayas first: no API key, clean JSON, sets pattern for all ingestors
- Gzip on bronze: ~5x compression, cheaper S3
- Hive partitioning: snapshot_date=YYYY-MM-DD/source=himalayas/
- No keys in code: boto3 reads ~/.aws/credentials locally, IAM role on Lambda in prod
- Mocking in tests: no real HTTP/S3 calls, fast and offline-safe
- dry_run flag: fetch without S3 write, safe for local testing
- lambda_handler returns dict: Step Functions reads status to decide next state

### Next
Chat 3 — Terraform: S3 buckets + lifecycle rules + IAM role for Lambda

## Chat 3 — Terraform: S3 + Lifecycle + IAM
Date: 2026-04-18

### Built
- terraform/envs/dev/main.tf
- terraform/envs/dev/variables.tf
- terraform/envs/dev/s3.tf
- terraform/envs/dev/iam.tf
- terraform/envs/dev/outputs.tf

### AWS Resources Created (14 total)
- 4 S3 buckets: jobpulse-bronze-dev, silver, gold, archive
- Public access blocked on all 4
- Lifecycle: bronze expires in 7d, silver→IA in 30d, archive→Glacier IR in 180d
- IAM role: jobpulse-lambda-exec-dev (Lambda execution role, least-privilege)
- IAM policy: S3 bronze write + CloudWatch Logs only

### Next
Chat 4 — Deploy Himalayas Lambda + wire IAM role + test live S3 write

## Chat 4 — Lambda Deploy + Live S3 Write
Date: 2026-04-18

### Built
- terraform/envs/dev/lambda.tf — aws_lambda_function for himalayas + remotive
- terraform/envs/dev/builds/.gitkeep — zip output dir
- ingestion/sources/remotive/ingest_remotive.py — Remotive ingestor

### AWS Resources Created
- Lambda: jobpulse-ingest-himalayas-dev (deployed, BLOCKED — Cloudflare on API)
- Lambda: jobpulse-ingest-remotive-dev (deployed, LIVE — 23 jobs written to S3)
- IAM policy updated: added s3:PutObjectTagging to bronze write permissions

### Verified
- Dry-run: status=OK, s3_uri=null ✓
- Live invoke: status=OK, record_count=23 ✓
- S3 object confirmed: s3://jobpulse-bronze-dev/snapshot_date=2026-04-18/source=remotive/data.json.gz (49 KB) ✓

### Incidents
- Himalayas API blocked by Cloudflare bot protection (cf-mitigated: challenge) — Lambda deployed but non-functional. Tracked as pending.
- terraform apply failed first run: worktree had empty tfstate, existing S3+IAM resources already in AWS from Chat 3. Fixed by copying tfstate from main dev dir.
- AWS_REGION is a reserved Lambda env var — cannot be set manually. Removed from lambda.tf; Lambda sets it automatically.
- IAM missing s3:PutObjectTagging — put_object with Tagging param requires it as a separate permission. Added to policy.

### Pending
- Himalayas: re-enable when Cloudflare protection removed or API changes
- EventBridge schedule: wire daily cron to trigger Remotive Lambda (Chat 5)

### Next
Chat 5 — EventBridge daily schedule + Step Functions orchestration

## Chat 5 — EventBridge + Step Functions + S3 Backend
Date: 2026-04-19

### Built
- terraform/envs/dev/step_functions.tf — state machine + IAM role/policy for SF
- terraform/envs/dev/eventbridge.tf — daily cron rule + target + IAM role/policy
- terraform/envs/dev/monitoring.tf — SNS topic, email subscription, CloudWatch alarm
- terraform/envs/dev/terraform.tfvars — alert_email (gitignored)
- terraform/envs/dev/variables.tf — added alert_email variable
- terraform/envs/dev/outputs.tf — added state_machine_arn, eventbridge_rule_arn, sns_topic_arn
- terraform/envs/dev/main.tf — migrated backend from local → S3

### AWS Resources Created (14 new)
- Step Functions state machine: jobpulse-ingest-pipeline-dev (STANDARD type)
- IAM role + policy: jobpulse-sfn-exec-dev (lambda:InvokeFunction on Remotive only)
- EventBridge rule: jobpulse-daily-ingest-dev (cron 8:30 PM UTC = 2AM IST, ENABLED)
- EventBridge target: triggers state machine on schedule
- IAM role + policy: jobpulse-eventbridge-exec-dev (states:StartExecution)
- SNS topic: jobpulse-alerts-dev
- SNS email subscription: jobpulse010@gmail.com (pending confirmation)
- CloudWatch alarm: jobpulse-sfn-failures-dev (ExecutionsFailed ≥ 1 → SNS)
- S3 bucket: jobpulse-tfstate-dev (versioning + AES256 + public access blocked)
- DynamoDB table: jobpulse-tfstate-lock (PAY_PER_REQUEST, LockID partition key)

### Verified
- terraform apply: 12 added, 2 changed, 0 destroyed ✓
- terraform init -migrate-state: local tfstate → S3 ✓
- terraform plan post-migration: No changes ✓
- Manual SF execution: SUCCEEDED, record_count=21, s3_uri confirmed ✓

### Incidents
- Worktree had no tfstate again (same as Chat 4) — fixed by copying from main dev dir + importing Lambda functions via terraform import
- Root cause fixed permanently: S3 backend means tfstate now lives in AWS, not on disk — no more worktree copies needed

### Next
Chat 6 — Glue Spark job: S3 bronze → S3 silver (clean + partition Parquet)

## Chat 6 — Glue Spark Job: Bronze → Silver
Date: 2026-04-19

### Built
- spark/jobs/bronze_to_silver_remotive.py — Glue PySpark job (bronze → silver)
- spark/tests/test_bronze_to_silver_remotive.py — 45 pure-function tests (no Spark needed locally)
- terraform/envs/dev/glue.tf — Glue IAM role, policy, job, S3 script upload
- terraform/envs/dev/step_functions.tf — updated: RunGlueJob state + Glue perms on SF policy
- terraform/envs/dev/outputs.tf — added glue_job_name

### AWS Resources Created (6 new, 2 updated)
- Glue job: jobpulse-bronze-to-silver-dev (G.1X, 2 workers, Glue 4.0, 10 min timeout)
- IAM role: jobpulse-glue-exec-dev (bronze read + silver read/write + AWSGlueServiceRole)
- IAM policy: jobpulse-glue-policy-dev
- S3 script: s3://jobpulse-silver-dev/glue-scripts/bronze_to_silver_remotive.py
- SF IAM policy updated: added glue:StartJobRun + glue:GetJobRun
- SF state machine updated: CheckRemotive → RunGlueJob (startJobRun.sync) → PipelineComplete

### Silver Schema
job_id, source, snapshot_date, title, company_name, category, role_family,
job_type, apply_url, salary_raw, location_raw, country, state, tags,
publication_date, description, ingested_at
Partitioned by: snapshot_date / country / role_family

### Verified
- terraform apply: 6 added, 2 changed, 0 destroyed ✓
- Glue job manual run: SUCCEEDED, silver Parquet confirmed in S3 ✓
- Partitions visible: snapshot_date=2026-04-18/country=US/role_family=SDE/... ✓
- Full Step Functions run: Lambda → Glue → SUCCEEDED (~2 min) ✓
- 45 unit tests pass, 4 skipped (PySpark not installed locally, expected) ✓

### Next
Chat 7 — dbt gold layer: Athena adapter + star schema models + dbt tests

## Chat 7 — dbt Gold Layer
Date: 2026-04-19

### Built
- dbt_project/dbt_project.yml — project config, staging=view, gold=table
- dbt_project/profiles.yml — Athena adapter, workgroup jobpulse-dev
- dbt_project/packages.yml — no hub packages (dbt-athena is pip-only)
- dbt_project/models/staging/stg_silver_jobs.sql — view over silver_jobs external table
- dbt_project/models/staging/schema.yml — source definition for silver_jobs
- dbt_project/models/gold/dim_company.sql — 16 distinct companies
- dbt_project/models/gold/dim_role.sql — 7 role_family+category combos
- dbt_project/models/gold/dim_country.sql — 5 distinct countries
- dbt_project/models/gold/fact_job_posting.sql — 51 job rows with FK surrogate keys
- dbt_project/models/gold/schema.yml — 20 dbt schema tests
- terraform/envs/dev/athena.tf — workgroup (1 GB scan cap), Glue databases, silver_jobs external table
- terraform/envs/dev/glue.tf — updated: Glue policy (Athena + Glue catalog + gold S3), dbt_runner Python Shell job
- terraform/envs/dev/step_functions.tf — updated: RunDbtGold state added, SFN policy updated
- terraform/envs/dev/outputs.tf — added athena_workgroup_name, gold_database_name, silver_database_name, dbt_glue_job_name
- transform/dbt_runner/dbt_runner.py — Glue Python Shell script: downloads dbt project from S3, MSCK REPAIR TABLE, dbt run

### AWS Resources Created (7 new, 4 updated)
- Athena workgroup: jobpulse-dev (1 GB scan cap, output → s3://jobpulse-gold-dev/athena-results/)
- Glue database: jobpulse_silver_dev (external tables over silver S3)
- Glue database: jobpulse_gold_dev (dbt CTAS output)
- Glue catalog table: silver_jobs (14 cols + 3 partition keys, Parquet/Snappy SerDe)
- Glue Python Shell job: jobpulse-dbt-runner-dev (0.0625 DPU, dbt-core 1.11.8, dbt-athena 1.10.0)
- S3 object: glue-scripts/dbt_runner.py uploaded to silver bucket
- null_resource: zips dbt_project/ and uploads to s3://jobpulse-silver-dev/dbt-project/dbt_project.zip on file changes
- IAM policy (glue_policy) updated: added ReadWriteGold + AthenaQuery + GlueCatalog statements
- IAM policy (sfn_policy) updated: added dbt_runner Glue job ARN to StartGlueJobs
- Step Functions state machine updated: RunGlueJob → RunDbtGold → PipelineComplete

### Gold Schema
- dim_company: company_key (md5 hex), company_name, created_at
- dim_role: role_key (md5 hex on role_family+category), role_family, category, created_at
- dim_country: country_key (md5 hex), country, created_at
- fact_job_posting: job_id, company_key, role_key, country_key, snapshot_date, publication_date, title, apply_url, job_type, salary_raw, tags, match_score (NULL), source, ingested_at

### Verified
- terraform apply: 7 added, 4 changed, 0 destroyed ✓
- dbt debug: All checks passed (Athena connection live) ✓
- dbt run: PASS=5 WARN=0 ERROR=0 — 51 fact rows, 16 companies, 5 countries, 7 roles ✓
- dbt test: PASS=20 WARN=0 ERROR=0 — all not_null, unique, accepted_values, relationships ✓
- Gold Parquet written to s3://jobpulse-gold-dev via Athena CTAS ✓

### Next
Chat 8 — Streamlit dashboard: Athena queries → job market visualizations

## Chat 8 — Streamlit Dashboard
Date: 2026-04-19

### Built
- dashboard/streamlit/requirements.txt — streamlit, boto3, pandas, plotly, pyarrow
- dashboard/streamlit/athena_client.py — boto3 Athena query runner: submit → poll → read S3 CSV
- dashboard/streamlit/app.py — single-page Streamlit dashboard with sidebar filters + 4 sections

### Dashboard Sections
- KPI row: total jobs, companies, countries
- Results table: st.dataframe with LinkColumn for clickable apply_url
- Charts row (3 cols): country breakdown (bar), company leaderboard top 10 (horizontal bar), role distribution (pie)
- Tags/skills frequency: top 20 tags across filtered jobs (bar chart)
- Sidebar filters: role_family, country, job_type (multiselect, empty = show all)
- Refresh button: clears st.cache_data to force Athena re-query

### Verified
- athena_client smoke test: COUNT(*) on fact_job_posting → 21 rows confirmed live ✓
- Flat 4-table JOIN query executed, shape (5, 6), titles + tags correct ✓
- parse_tags("[AI/ML, editing, startup]") → ['AI/ML', 'editing', 'startup'] ✓

### Next
Chat 9 — GenAI enrichment layer: Claude API for skill extraction + match scoring

## Chat 9 — GenAI Enrichment Layer
Date: 2026-04-19

### Built
- config/user_profile.yml — user skill profile + scoring weights (single source of truth)
- genai/__init__.py — marks genai/ as a Python package
- genai/guardrails.py — Pydantic schemas (ExtractionResult, EnrichmentRecord), SKILL_VOCAB whitelist (~120 terms), BudgetTracker
- genai/skill_extractor.py — SkillExtractor sub-agent (rule-based + Claude Haiku LLM fallback)
- genai/match_scorer.py — MatchScorer sub-agent (6-component weighted scorer)
- genai/jd_enrichment_agent.py — JDEnrichmentAgent orchestrator (pre-hooks, per-job processing, post-hooks)
- genai/enrichment_runner.py — Glue Python Shell entry point (argparse, Athena fetch, MSCK REPAIR)
- tests/test_genai.py — 22 unit tests, all mock-based (no real AWS or API calls)
- terraform/envs/dev/glue.tf — added Secrets Manager IAM perm, enrichment Glue job, genai package zip upload
- terraform/envs/dev/step_functions.tf — added RunEnrichment state, enrichment job ARN to SF policy
- terraform/envs/dev/outputs.tf — added enrichment_job_name output
- dashboard/streamlit/app.py — added enrichment_scores JOIN, match_score column, title search, score slider

### How the Scoring Works

**User profile** (`config/user_profile.yml`) defines the benchmark:
- Skills: python, sql, pyspark, aws, dbt, airflow, kafka, terraform, pandas, docker (10 skills)
- Seniority: mid, YoE: 2 years
- Preferred locations: remote, india
- Preferred role families: DATA, SDE
- Salary floor: $60,000 USD

**Six scoring components** (weights sum to 100):

| Component | Weight | Logic |
|---|---|---|
| skill_overlap | 40 | Jaccard similarity: |intersection| / |union| × 40. A job needing python+sql+aws where user knows all 3 scores 3/3=100% → 40 pts. A job needing java+kotlin+helm where user knows none scores 0/13 → 0 pts. |
| seniority_fit | 20 | YoE-aware (see below) |
| location_fit | 15 | "remote" anywhere in location/job_type/country → full 15 pts. Preferred country match → full 15 pts. Otherwise 0. |
| role_family_fit | 15 | Role family in [DATA, SDE] → 15 pts. Otherwise 0. |
| salary_fit | 5 | Parsed salary ≥ $60k → 5 pts. Below floor → 0. Unknown/unparseable → full 5 pts (benefit of the doubt). |
| freshness | 5 | ≤7 days old → 5 pts. ≤14 days → 3 pts. Older → 0. |

**YoE-aware seniority scoring** (the 20-pt component):

The job description is parsed for patterns like "3+ years experience", "minimum 5 years", "1-3 years exp".
The first number found becomes `yoe_required`. Gap = yoe_required − user.yoe (user has 2 years).

| Gap (years short) | Score |
|---|---|
| ≤ 0 (user meets or exceeds) | 20 pts (100%) |
| 1 year short | 15 pts (75%) |
| 2 years short | 10 pts (50%) |
| > 2 years short | 0 pts |

If no YoE number is found, falls back to title-based seniority distance:
- Same level (mid→mid) → 20 pts
- One level away (mid→senior or mid→junior) → 10 pts
- Further away → 0 pts

**Why skill_overlap dominates (40%):** A job asking for your exact stack but labelled "senior" is a better opportunity than a junior job in a completely different tech stack. Skills are the real filter; seniority is a soft signal.

**Extraction pipeline:**
1. Rule-based regex scans description against SKILL_VOCAB whitelist + seniority keyword patterns + YoE regex
2. If ≥5 skills found AND seniority identified → use rules (no API call)
3. If either is missing → Claude Haiku (`claude-haiku-4-5-20251001`) with `cache_control: ephemeral` on system prompt
4. LLM failure → fall back to rules result (never crash the batch)

**S3 cache:** Each description is hashed (md5). Cache miss writes extraction to `s3://gold/enrichment-cache/{hash}.json`. Same JD on a different snapshot date reuses the cached extraction — no duplicate API charges.

**Budget guard:** Daily $0.50 cap tracked in `s3://gold/enrichment-cache/budget-{date}.json`. `BudgetTracker.check_and_increment()` raises `BudgetExceededError` before any API call if cap would be exceeded. Fails open (zero spend assumed) if S3 unreachable.

### AWS Resources Created (4 new, 7 changed)
- Glue job: jobpulse-enrichment-dev (Python Shell, 0.0625 DPU, 20 min timeout)
- S3 object: glue-scripts/enrichment_runner.py (entry point)
- S3 object: glue-scripts/genai_package.zip (genai/ + config/user_profile.yml, re-uploaded on code change)
- null_resource: genai_package_upload (triggers on genai/*.py + user_profile.yml hash change)
- IAM policy (glue_policy) updated: SecretsManagerAnthropicKey perm added
- IAM policy (sfn_policy) updated: enrichment job ARN added to StartGlueJobs
- Step Functions state machine updated: RunDbtGold → RunEnrichment → PipelineComplete
- Lambda function zip hashes refreshed (no logic change)
- dbt_runner S3 object refreshed

### One-Time Manual Steps Completed
- Secrets Manager: `jobpulse/anthropic_key_dev` created with real Anthropic API key (ap-south-1)
- Athena DDL: `enrichment_scores` external table created in `jobpulse_gold_dev` (Parquet/Snappy, partitioned by snapshot_date)

### Pipeline Flow (complete)
```
EventBridge (2AM IST daily)
  → Step Functions
    → InvokeRemotive (Lambda) → CheckRemotive
    → RunGlueJob (bronze → silver, PySpark)
    → RunDbtGold (dbt star schema, Python Shell)
    → RunEnrichment (skill extract + match score, Python Shell)  ← NEW
    → PipelineComplete
```

### Dashboard Additions
- Match % column in results table (sorted by score descending by default)
- Title search box: free-text filter on job title (e.g. "Data Engineer")
- Min Match Score slider: hides jobs below threshold (0–100, step 5)
- enrichment_scores LEFT JOINed in FLAT_JOIN_SQL (COALESCE to -1 when no enrichment yet)

### Verified
- terraform apply: 4 added, 7 changed, 1 destroyed ✓
- 22 unit tests pass (pytest tests/test_genai.py -v) ✓
- Secrets Manager secret created ✓
- Athena enrichment_scores DDL executed ✓
- enrichment_job_name output: "jobpulse-enrichment-dev" ✓

### Blockers (Chat 9 — not fully verified)

1. **dbt-core pip conflict (Glue 5.1):** Glue Python Shell 5.1 pre-installs awscli 1.23.5 + aiobotocore 2.2.0 with locked botocore. Any dbt-core version (1.5–1.9) pulls newer botocore → conflict → dbt deps fails. Temporary fix: skipped RunDbtGold state in Step Functions (RunGlueJob → RunEnrichment directly).

2. **anthropic/pydantic pip conflict (Glue 5.1):** Same boto3/botocore vendoring issue affects enrichment_runner pip installs. Tried anthropic==0.28.0, pydantic==2.5.0, pyarrow==14.0.1 — still failing. Root cause same as above.

3. **genai_package.zip not found:** After pip issue, enrichment job failed with "Library file doesn't exist: /tmp/glue-python-libs-.../genai_package.zip". null_resource trigger for zip upload may not have fired. Need to verify S3 upload manually or fix trigger logic.

### Next
Chat 10 — Fix Glue 5.1 pip issues (try glue_version = "4.0" for Python Shell jobs), restore RunDbtGold, get RunEnrichment working, verify enrichment_scores in Athena, dashboard match_score live.

## Chat 10 — Fix Glue Pip Conflicts, Full Pipeline End-to-End
Date: 2026-04-19

### Goal
Fix three Chat 9 blockers and get the full pipeline running unattended: Lambda → Glue bronze→silver → dbt gold → enrichment → PipelineComplete.

### Built / Fixed
- **terraform/envs/dev/glue.tf** — added `glue_version = "4.0"` to `aws_glue_job.dbt_runner` and `aws_glue_job.enrichment_runner`; downgraded dbt to `dbt-core==1.9.10,dbt-athena-community==1.9.5` (last series supporting Python 3.9); pinned `pyarrow==14.0.2` (last version with Python 3.9 manylinux wheels)
- **terraform/envs/dev/athena.tf** — added `aws_glue_catalog_table.enrichment_scores` (brought manual DDL into Terraform); imported existing table with `terraform import 240939827246:jobpulse_gold_dev:enrichment_scores`
- **transform/dbt_runner/dbt_runner.py** — fixed zip extraction path: `dbt_project.zip` contains `dbt_project/dbt_project.yml` so `--project-dir` must point one level deeper (`/tmp/dbt_project/dbt_project` not `/tmp/dbt_project`)
- **dbt_project/models/gold/schema.yml** — removed `arguments:` wrapper from `accepted_values` and `relationships` tests (removed in dbt 1.8+)
- **genai/enrichment_runner.py** — full Glue-compatible bootstrap: detects Glue vs local dev environment; manually downloads + extracts genai_package.zip from S3 to add to sys.path (Glue 4.0 Python Shell does NOT auto-add --extra-py-files to sys.path); added NaN→None normalization after pd.read_csv() to handle Athena CSV nulls
- **genai/jd_enrichment_agent.py** — cast job_id to str() in both EnrichmentRecord instantiation sites (Pydantic v2 rejects int for str fields)
- **dashboard/streamlit/app.py** — fixed enrichment_scores JOIN: `CAST(f.snapshot_date AS VARCHAR) = e.snapshot_date` (Athena won't implicitly cast date→varchar in JOIN conditions)

### Incidents Hit (Chat 10 — 7 new)
1. Terraform import needs `catalog-id:database:table` format, not `database/table`
2. dbt-core ≥1.10 requires Python ≥3.10; Glue 4.0 is Python 3.9 → must use dbt-core 1.9.x
3. dbt zip structure: `dbt_project/` prefix in zip means project-dir must go one deeper
4. dbt schema.yml `arguments:` wrapper removed in dbt 1.8+
5. pyarrow ≥15 has no Python 3.9 manylinux wheels → must pin pyarrow==14.0.2
6. Glue 4.0 Python Shell: --extra-py-files downloaded but NOT added to sys.path
7. pandas NaN ≠ None: `(nan or "")` returns nan (truthy), breaking `.lower()` on null fields
8. Pydantic v2 rejects int for str field (no auto-coerce); job_id came in as int64 from CSV
9. Athena date vs varchar: no implicit cast in JOIN; must use `CAST(date AS VARCHAR)`

### Pipeline Flow (complete, fully verified)
```
EventBridge (2AM IST daily)
  → Step Functions (STANDARD)
    → InvokeRemotive (Lambda)   → CheckRemotive
    → RunGlueJob    (bronze→silver, PySpark, Glue 4.0 Spark)
    → RunDbtGold    (dbt star schema, Python Shell, Glue 4.0)
    → RunEnrichment (skill extract + match score, Python Shell, Glue 4.0)
    → PipelineComplete
```

### Verified
- terraform apply: 2 changed (glue jobs), 1 added (enrichment_scores table) ✓
- null_resource.genai_package_upload forced re-upload → zip in S3 ✓
- Step Functions execution: SUCCEEDED — all 4 states green ✓
- Athena enrichment_scores: 21 records, avg=20.3, max=43.0, latest=2026-04-19 ✓
- Dashboard flat JOIN with CAST: 21 rows, real match_scores (not -1) ✓
- dbt: PASS=5 models, PASS=30 tests ✓

### Next
Chat 11 — Add second data source (Himalayas/Adzuna/RemoteOK), expand volume, or add deduplication logic.

## Chat 11 — Add Arbeitnow as Second Data Source, Parallel Ingestion
Date: 2026-04-20

### Goal
Add a second working data source to prove multi-source pipeline. Increase job volume. Wire Step Functions Parallel state so both ingestors run concurrently.

### Built / Fixed

**New ingestors:**
- **ingestion/sources/arbeitnow/ingest_arbeitnow.py** — Lambda ingestor for Arbeitnow public API (no key). Paginates up to 10 pages (~1000 jobs). Maps: slug→job_id, url→apply_url, job_types[0]→job_type, created_at (unix ts)→publication_date, remote=True→"Remote" location. Same write_to_s3 / lambda_handler pattern as Remotive.
- **ingestion/sources/remoteok/ingest_remoteok.py** — created but NON-FUNCTIONAL from Lambda: RemoteOK is behind Cloudflare bot protection (confirmed: `server: cloudflare` header, 403 from Lambda IPs). Kept for reference. Tested RemoteOK before writing full code — same Cloudflare issue as Himalayas.

**Multi-source Spark job:**
- **spark/jobs/bronze_to_silver.py** — replaces `bronze_to_silver_remotive.py`. Reads `source=*/` (all sources). COALESCE for cross-source field resolution: `location_raw` / `candidate_required_location`, `apply_url` / `url`, `job_id` / `id`. New `extract_role_family_from_tags()` for tag-based role inference (Arbeitnow has no category field). Extended COUNTRY_MAP with: remote, london→UK, istanbul→TR, san francisco→US, bangkok→TH.

**Terraform:**
- **terraform/envs/dev/lambda.tf** — added `aws_lambda_function.arbeitnow` (timeout=120, memory=256). Comment notes RemoteOK blocked by Cloudflare.
- **terraform/envs/dev/step_functions.tf** — replaced sequential `InvokeRemotive` with `ParallelIngest` Parallel state: Branch 1 = Remotive, Branch 2 = Arbeitnow. Both run concurrently; pipeline waits for ALL branches. IAM policy updated: `lambda:InvokeFunction` now covers both ARNs. `RunGlueJob` and `RunEnrichment` read `$.parallel[0].snapshot_date` (Remotive branch output).
- **terraform/envs/dev/glue.tf** — script_location updated from `bronze_to_silver_remotive.py` → `bronze_to_silver.py`.

**Bug fix:**
- **genai/enrichment_runner.py** — fixed `EmptyDataError: No columns to parse from file`: Athena writes an empty CSV file (not 0 rows) when query returns no results. `pd.read_csv()` crashes on it. Fix: read raw bytes, check `content.strip()`, return `[]` before calling read_csv. Uploaded directly to S3 mid-session.

**Tests:**
- **tests/test_ingest_arbeitnow.py** — pagination (2 pages), empty response, field mapping, remote location, unix timestamp, missing fields, dry_run, empty lambda handler.
- **tests/test_ingest_remoteok.py** — legal notice skip, salary string construction (min+max / min-only / max-only / none), field mapping.
- **spark/tests/test_bronze_to_silver.py** — updated: `TestExtractRoleFamilyFromTags`, `TestResolveRoleFamily`, multi-source PySpark test verifying both `remotive` and `remoteok` sources in output.

### Incidents Hit (Chat 11)
1. RemoteOK blocked by Cloudflare from Lambda (same as Himalayas) — discovered by testing API before writing code
2. EmptyDataError in enrichment_runner: Athena empty result = empty CSV file, not 0-row CSV — pd.read_csv() crashes
3. Redrive of failed 2AM run failed: ran before fix was uploaded to S3; old script used by Glue

### Pipeline Flow (updated)
```
EventBridge (2AM IST daily)
  → Step Functions (STANDARD)
    → ParallelIngest (Parallel)
        Branch 1: InvokeRemotive → CheckRemotive
        Branch 2: InvokeArbeitnow → CheckArbeitnow
    → RunGlueJob    (bronze→silver, reads source=*/, PySpark, Glue 4.0 Spark)
    → RunDbtGold    (dbt star schema, Python Shell, Glue 4.0)
    → RunEnrichment (skill extract + match score, Python Shell, Glue 4.0)
    → PipelineComplete
```

### Verified
- Arbeitnow Lambda invoked manually: status=OK, record_count=100 ✓
- terraform apply: 1 added (arbeitnow Lambda), 3 changed (step_functions, glue, lambda policy) ✓
- Step Functions execution: SUCCEEDED — Parallel state both branches green ✓
- Bronze S3: source=remotive/ (21 jobs) + source=arbeitnow/ (100 jobs) ✓
- Silver Athena: 121 rows, source column has both 'remotive' and 'arbeitnow' ✓
- dbt gold: fact_job_posting 179 rows PASS ✓
- Enrichment: 121 jobs scored, s3://jobpulse-gold-dev/enrichment-scores/snapshot_date=2026-04-20/ ✓

### Next
Chat 12 — Deduplication (same job across sources), add third data source, or Great Expectations data quality layer.

## Chat 12 — Silver Layer Deduplication
Date: 2026-04-21

### Goal
Implement exact-match deduplication in the silver Spark layer so the same logical job appearing from multiple sources on the same snapshot_date collapses to one row, with cross-source metadata preserved.

### Built

**Deduplication:**
- **spark/jobs/bronze_to_silver.py** — new `deduplicate_silver_df(df)` function. Three-phase: (1) compute `dedup_key = md5(lower(trim(company_name)) | lower(trim(title)) | lower(trim(country)))`, (2) `ROW_NUMBER() OVER (PARTITION BY dedup_key, snapshot_date ORDER BY publication_date ASC, ingested_at ASC)` to select canonical row, (3) `groupBy(dedup_key, snapshot_date).agg(collect_set(source), count(*))` to produce `source_apis[]` and `source_count`, joined back to canonical rows. Wired into `main()` between `build_silver_df` and the write.

**Terraform:**
- **terraform/envs/dev/athena.tf** — added `dedup_key STRING`, `source_apis ARRAY<STRING>`, `source_count INT` to `aws_glue_catalog_table.silver_jobs`. ParquetHiveSerDe already handles array<string> natively (same as existing `tags` column).

**dbt gold layer:**
- **dbt_project/models/gold/fact_job_posting.sql** — added `j.source_count`
- **dbt_project/models/gold/schema.yml** — added `source_count` column with `not_null` test

**Dashboard:**
- **dashboard/streamlit/app.py** — `f.source_count` in FLAT_JOIN_SQL; `source_count` column in results table ("Sources"); "2+ sources (higher confidence)" sidebar checkbox filters to jobs confirmed by multiple sources

**Tests:**
- **spark/tests/test_bronze_to_silver.py** — 3 new PySpark tests: `test_cross_source_dedup` (same job from 2 sources → 1 row, source_apis={remotive,arbeitnow}, source_count=2), `test_different_country_not_deduped` (same company+title, US vs UK → 2 rows), `test_null_company_name_handled` (null company_name → no crash, dedup_key non-null)

### Silver Schema (now 20 columns)
Added: `dedup_key STRING`, `source_apis ARRAY<STRING>`, `source_count INT`

### Verified
- 60 unit tests pass, 9 PySpark tests skipped (PySpark not installed locally — expected) ✓

### AWS Steps (post-copy)
1. `terraform apply` — updates silver_jobs Glue catalog table (1 resource changed)
2. Re-run `jobpulse-bronze-to-silver-dev` Glue job
3. Athena check: `SELECT COUNT(*), SUM(source_count) FROM jobpulse_silver_dev.silver_jobs WHERE snapshot_date = DATE '2026-04-21'` — SUM > COUNT means dedup fired
4. `dbt run --select fact_job_posting && dbt test --select fact_job_posting`

### Next
Chat 13 — Great Expectations data quality layer on silver, OR add third data source (The Muse).

