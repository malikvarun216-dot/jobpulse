## Chat 1 — Repo Setup
- Created GitHub repo: jobpulse (private)
- Scaffolded full folder structure
- Branching strategy: dev → main

## Chat 2 — Himalayas Ingestor + AWS Setup
Date: 2026-04-17

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
Chat 13 — Add Adzuna as third data source (salary_min/salary_max fields, 12 countries, no Cloudflare). Fixes salary_fit scoring gap. Jumps volume to ~3,500 jobs/run.

## Chat 13 — Adzuna as Third Data Source
Date: 2026-04-21

### Goal
Add Adzuna as the third ingestor to unlock structured salary data and increase volume from ~120 to ~3,600 jobs/run.

### Built

**New ingestor:**
- **ingestion/sources/adzuna/ingest_adzuna.py** — Lambda ingestor for Adzuna API v1 (app_id + app_key). Iterates 12 countries (gb, us, au, ca, de, fr, br, in, nz, pl, ru, za), 6 pages × 50 results each. Per-country error isolation: one country failing does not abort the rest. `build_salary_str()` formats `salary_min`/`salary_max` integers as `"$80000-$120000"` for MatchScorer compatibility.

**Terraform:**
- **terraform/envs/dev/variables.tf** — added `adzuna_app_id` + `adzuna_app_key` (sensitive)
- **terraform/envs/dev/terraform.tfvars** — placeholder values (replace with real keys from developer.adzuna.com before apply)
- **terraform/envs/dev/lambda.tf** — `aws_lambda_function.adzuna` (timeout=300, memory=256; API keys injected as env vars)
- **terraform/envs/dev/step_functions.tf** — Adzuna ARN added to `InvokeLambda` IAM resource list; third branch added to `ParallelIngest` Parallel state (InvokeAdzuna → CheckAdzuna → AdzunaDone/AdzunaFailure)

**Spark job:**
- **spark/jobs/bronze_to_silver.py** — `redirect_url` added as third fallback in apply_url COALESCE (`apply_url` → `url` → `redirect_url`). No other changes needed — Adzuna data is auto-picked up by the existing `source=*/` glob.

**Tests:**
- **tests/test_ingest_adzuna.py** — 23 unit tests: pagination stop conditions, MAX_PAGES cap, salary string formats (both/min-only/max-only/neither), field mapping, per-country failure resilience, dry_run, empty status, lambda_handler happy path.
- **spark/tests/test_bronze_to_silver.py** — ADZUNA_JOB + ADZUNA_BRONZE fixtures; `test_adzuna_redirect_url_coalesced` verifies redirect_url → apply_url resolution, country=UK, salary_raw round-trip.

### Pipeline Flow (updated)
```
EventBridge (2AM IST daily)
  → Step Functions (STANDARD)
    → ParallelIngest (Parallel)
        Branch 1: InvokeRemotive   → CheckRemotive
        Branch 2: InvokeArbeitnow  → CheckArbeitnow
        Branch 3: InvokeAdzuna     → CheckAdzuna     ← NEW
    → RunGlueJob    (bronze→silver, reads source=*/, PySpark, Glue 4.0 Spark)
    → RunDbtGold    (dbt star schema, Python Shell, Glue 4.0)
    → RunEnrichment (skill extract + match score, Python Shell, Glue 4.0)
    → PipelineComplete
```

### Verified
- 184 unit tests pass, 14 PySpark tests skipped (PySpark not installed locally — expected) ✓

### Pre-work Before terraform apply
1. Sign up at developer.adzuna.com → get app_id and app_key
2. Replace placeholder values in terraform/envs/dev/terraform.tfvars

### AWS Steps (post-copy + terraform apply)
1. Lambda dry-run: `aws lambda invoke --function-name jobpulse-ingest-adzuna-dev --payload '{"dry_run": true}' /tmp/out.json`
2. Live invoke → verify S3 `source=adzuna/data.json.gz`
3. Full Step Functions run — ParallelIngest shows 3 branches green
4. Athena: `SELECT source, COUNT(*) FROM silver_jobs WHERE snapshot_date = DATE '2026-04-21' GROUP BY source`
5. Salary check: `SELECT salary_raw FROM silver_jobs WHERE source = 'adzuna' AND salary_raw IS NOT NULL LIMIT 5`

### Next
Chat 14 — Fix enrichment timeout: rules-first fast path + ThreadPoolExecutor.

## Chat 14 — Fix Enrichment Timeout + Adzuna Parallelization
Date: 2026-04-21

### Goal
Enrichment was timing out at 3,400 jobs (Glue Python Shell 60-min limit). Root causes: (1) S3 cache lookup on every job before running cheap regex rules, (2) sequential for-loop with no concurrency, (3) no per-call Claude API timeout, (4) Adzuna fetching 12 countries sequentially.

### Built / Fixed

**`genai/guardrails.py`** — Added `threading.Lock` to `BudgetTracker`. Wraps `check_and_increment` and `record_actual_usage` in a lock so 16 concurrent threads can't corrupt the shared spend ledger.

**`genai/skill_extractor.py`** — Added `timeout=10.0` to `client.messages.create()`. Added `anthropic.APITimeoutError` to the retry except clause — timeouts now retry with backoff instead of crashing the thread.

**`genai/jd_enrichment_agent.py`** — Two changes:
- **Rules-first fast path in `_process_job`**: runs `_rule_based_extract()` first (pure CPU, <1ms, zero I/O). If ≥5 skills + known seniority → return immediately, no S3 or LLM call. Only falls through to S3 cache + LLM for ambiguous JDs (~30%). Reuses already-computed `rules_result` as fallback when budget exceeded (no double computation).
- **`ThreadPoolExecutor(max_workers=16)` in `run()`**: replaces sequential for-loop. All 3,400 jobs processed concurrently (16 at a time). I/O-bound work (S3, Claude API) releases the GIL — true parallelism.
- **Lazy `pyarrow` import**: moved `import pyarrow` inside `_write_parquet_to_s3` (Glue-only path). Allows local tests to import the module without pyarrow installed.

**`ingestion/sources/adzuna/ingest_adzuna.py`** — `fetch_all_jobs()` now uses `ThreadPoolExecutor(max_workers=6)`: 12 countries in 2 parallel batches instead of 12 serial rounds. Error isolation preserved — one country failure still skips gracefully. Lambda timeout is 300s; 6 threads fit easily.

**Tests:**
- **`tests/test_ingest_adzuna.py`** — Fixed `test_continues_on_country_failure`: replaced list-based `side_effect` (breaks with parallel threads — call order non-deterministic) with callable `side_effect(country)` that matches by country name.
- **`tests/test_jd_enrichment_agent.py`** (new) — 8 tests: `test_rules_fast_path_skips_cache`, `test_rules_fast_path_score_in_range`, `test_cache_hit_skips_llm`, `test_cache_miss_calls_extractor`, `test_budget_exceeded_uses_rules_result`, `test_parallel_run_processes_all_jobs`, `test_parallel_run_skips_failed_jobs`, `test_lock_exists_on_budget_tracker`.

### Performance Improvement
| Scenario | Before | After |
|---|---|---|
| 70% jobs (rules-sufficient) | S3 lookup + rules ~100ms each | Rules only <1ms each |
| 30% jobs (LLM needed) | Sequential, no timeout | 16 parallel threads, 10s timeout/call |
| Adzuna country fetch | 12 serial rounds ~72s | 2 parallel batches ~12s |
| **Total wall time (3,400 jobs)** | **~57 min → timeout** | **~3-5 min** |

### Verified
- 87 unit tests pass ✓

### AWS Steps (post-copy + commit)
1. Re-zip and upload genai package: `zip -r genai_package.zip genai/ config/ && aws s3 cp genai_package.zip s3://jobpulse-silver-dev/glue-scripts/`
2. Run Step Functions execution manually
3. CloudWatch → `/aws/glue/jobs/jobpulse-run-enrichment-dev` → check duration (should be <10 min)
4. Athena: `SELECT extraction_source, COUNT(*) FROM enrichment_scores WHERE snapshot_date = '2026-04-21' GROUP BY extraction_source`

## Chat 14 Checkpoint — Dashboard Testing + Data Verification
Date: 2026-04-22 (2 AM — verified full pipeline)

### Findings

**Full pipeline working end-to-end:**
- Athena shows 6,428 jobs across 5 snapshots (Apr 18–22)
  - 2026-04-22: 1,734 jobs
  - 2026-04-21: 1,950 jobs
  - 2026-04-20: 2,682 jobs
  - 2026-04-19: 31 jobs
  - 2026-04-18: 31 jobs
- dbt ran successfully; gold tables created and partitions registered in Glue catalog
- All data queryable — no sync issues

**Dashboard limitation identified:**
- `dashboard/streamlit/app.py` line 41 had `LIMIT 500` in Athena query
- Caused dashboard to show only first 500 jobs even though 6,428 exist
- No data loss; purely a query limit

### Fixed
- Changed `LIMIT 500` → `LIMIT 20000` in dashboard
- Dashboard now shows all 6,428 jobs (full 5-day dataset)

### Decisions
- **Dashboard LIMIT:** 20,000 rows is reasonable middle ground: shows all current jobs (6.4K) + scales to future 50K/day without overwhelming browser. Better approach (Chat 16): replace hard LIMIT with dynamic filters (last 7 days, role='Data Engineer', is_remote=true).

### Test Status
- ✅ Full pipeline runs unattended (SF succeeds 2 AM daily)
- ✅ Bronze data: raw JSON from 3 sources (Remotive, Arbeitnow, Adzuna)
- ✅ Silver data: cleaned, deduplicated Parquet (partitioned by date/country/role)
- ✅ Gold data: dbt star schema, all tables populated
- ✅ Dashboard: functional, queryable, showing 6,428 jobs
- ✅ No errors or missing steps detected

### Post-Chat-14 Fix — IST Timezone for All Lambda Ingestors
Date: 2026-04-23

### Problem
Lambda ingestors were using `datetime.now(timezone.utc)` to compute `snapshot_date`. At 2 AM IST (8:30 PM UTC previous day), Lambda computed yesterday's date.
- Apr 23 2:00 AM IST pipeline wrote data to `snapshot_date=2026-04-22` (UTC date)
- Glue job picked up Apr 22 data even though it ran on Apr 23
- Dashboard showed stale data; no Apr 23 partition in S3 bronze

### Fixed
**All 3 Lambda ingestors updated:**
- `ingestion/sources/remotive/ingest_remotive.py` — lines 18, 88–90
- `ingestion/sources/arbeitnow/ingest_arbeitnow.py` — lines 18, 145–147
- `ingestion/sources/adzuna/ingest_adzuna.py` — lines 23, 203–205

**Pattern applied to all:**
```python
from datetime import datetime, timezone, timedelta
...
ist = timezone(timedelta(hours=5, minutes=30))
snapshot_date = event.get("snapshot_date") or datetime.now(ist).strftime("%Y-%m-%d")
```

### Verified
- Remotive Lambda manual invoke at 2026-04-22T21:17:05Z (= 2026-04-23 02:47 IST) → returned `snapshot_date=2026-04-23` ✓
- S3 bronze check: new data written to `snapshot_date=2026-04-23/source=remotive/` ✓
- Arbeitnow + Adzuna updated with same pattern ✓

### Implications
- Apr 24+ pipelines will write correct IST dates
- Apr 22 data remains in S3 (partition was overwritten with IST-correct fix)
- Dashboard will show fresh data on next refresh after Apr 24 2 AM execution
- Idempotent: re-running Apr 23 with IST fix overwrites Apr 22 data → no duplicates

### Next
Chat 15 — RAG semantic search layer + JD embeddings.

## Chat 15 — RAG Semantic Search Layer
Date: 2026-04-24

### Built
- **genai/embedding_agent.py** — batch embeds JDs via Voyage AI (`voyage-4-lite`, 512 dims). Skips already-embedded job_ids. Writes Parquet to `s3://gold/embeddings/snapshot_date=.../`. Pure pyarrow, no pandas (Glue engine discovery issue).
- **genai/embedding_runner.py** — Glue Python Shell entry point for EmbedJDs step. Same S3 bootstrap pattern as enrichment_runner. Fetches `description` from `fact_job_posting` via Athena.
- **genai/semantic_search.py** — loads embedding Parquet from S3, cosine similarity (NumPy dot product on normalized vectors), returns top-K `(job_id, score)` pairs.
- **dashboard/streamlit/app.py** — added Semantic Search tab: text query → embed → cosine search → results table. "Why this match?" expander calls Claude Haiku on top-3 results.
- **terraform/envs/dev/glue.tf** — `aws_glue_job.embedding_runner` (voyageai>=0.2.0, pyarrow==14.0.2). Voyage key fetched from Secrets Manager.
- **terraform/envs/dev/step_functions.tf** — `EmbedJDs` state added after `RunEnrichment`.

### Pipeline (final)
```
ParallelIngest → RunGlueJob → RunDbtGold → RunEnrichment → EmbedJDs → PipelineComplete
```

### Verified
- 3,528 job embeddings written to S3 ✓
- Semantic search tab returns ranked results ✓
- Claude Haiku "Why this match?" explanations rendering ✓

---

## Chat 16 — Bug Fixes (5 critical)
Date: 2026-04-24

### Bugs Fixed

1. **Dedup collapse: 2,525 bronze → 24 gold rows** — Adzuna defaulted missing company_name to `"Unknown"`, causing all Unknown-company jobs with same title+country to hash-collide into one row. Fix: return `None` instead of `"Unknown"`; redesigned dedup_key to `md5(source|job_id)` (per-source unique), cross_source_key separate for multi-source tracking.

2. **3-day pipeline failure: `FileNotFoundError: 'which'`** — `subprocess.run(["which", "dbt"])` fails because `which` is a shell builtin, not an executable. Fix: `shutil.which("dbt")` from Python stdlib.

3. **EmbedJDs COLUMN_NOT_FOUND: description** — `fact_job_posting.sql` never selected `j.description`. Fix: added `j.description` to the model SELECT list.

4. **Embedding job pyarrow engine error** — `pd.read_parquet()` couldn't discover pyarrow engine in Glue Python Shell. Fix: replaced all pandas parquet calls with direct pyarrow API (`pq.read_table`, `pq.write_table`, `pa.table`).

5. **ImportError: numpy.core.multiarray** — `numpy>=1.24.0` in embedding job's `--additional-python-modules` installed a second numpy alongside Glue's pre-installed one; C extensions compiled against different versions clash. Fix: removed numpy and pandas from embedding job's additional modules entirely.

### Verified
- 15,356 jobs in dashboard (was 24) ✓
- 3,528 embeddings, semantic search working ✓
- Full pipeline SUCCEEDED end-to-end ✓

---

## Chat 17 — Match Scoring Improvements
Date: 2026-04-24

### Built
- **config/user_profile.yml** — added `skill_tiers: {core, secondary, learning}` alongside flat `skills` list.
- **genai/match_scorer.py** — replaced flat Jaccard with tiered weighted scoring. Core skills (python, sql, pyspark, aws, dbt) = 3x weight; secondary (airflow, kafka, terraform, pandas) = 1.5x; learning (docker) = 1x. Normalized against total user skill weight so score is always [0, 1].
- **genai/jd_enrichment_agent.py** — added `force_rescore` param. When set, skips LLM entirely (rules + S3 cache only, zero API spend).
- **genai/enrichment_runner.py** — added `--force_rescore` CLI arg; in Glue env, downloads `user_profile.yml` fresh from S3 before falling back to the bundled zip copy.
- **terraform/envs/dev/glue.tf** — `aws_s3_object.user_profile` uploads profile to `s3://silver/config/user_profile.yml`; `--force_rescore = "false"` default arg on enrichment job.

### How to use after a profile update
```bash
# 1. Edit config/user_profile.yml
# 2. Push to S3 immediately (no terraform apply needed)
aws s3 cp config/user_profile.yml s3://jobpulse-silver-dev/config/user_profile.yml
# 3. Trigger rescore (zero LLM spend)
aws glue start-job-run --job-name jobpulse-enrichment-dev \
  --arguments '{"--force_rescore":"true","--snapshot_date":"2026-04-24"}'
```

---

## Chat 18 — Profile Rebuild + Skill Scoring Fixes
Date: 2026-04-24

### Built
- **config/user_profile.yml** — full profile rebuild from resume. 30 skills across 3 tiers (was 10 flat). Core: python, sql, pyspark, kafka, airflow, spark, hive, cassandra, delta lake, bigquery, gcp, aws. Secondary: flink, kinesis, databricks, hadoop, docker, git, github actions, linux, avro, data modeling, redshift, snowflake, iceberg. Learning: dbt, terraform, llm, rag, langchain. Weights: skill_overlap 50, seniority_fit 10 (was 40/20).
- **genai/match_scorer.py** — softened YoE gap: `gap == 3 → 25%` (was 0%). Senior roles asking 5 YoE no longer score zero on seniority; surfaces as stretch roles instead of disappearing.
- **genai/guardrails.py** — expanded SKILL_VOCAB from ~75 → ~95 terms. Added: kinesis, delta lake, iceberg, langchain, linux, avro, hdfs, dataproc, composer, git, spark streaming. Without these, JD skills silently dropped before scoring — matches were understated.

### Why weights changed
- skill_overlap 40→50: skills are the strongest DE hiring signal; role + location together equal seniority
- seniority_fit 20→10: YoE gap math works against 2 YoE targeting senior roles anyway; softened gap logic (gap=3→25%) partially compensates

### Why SKILL_VOCAB matters
Skills not in the vocab are dropped by both the rule extractor and the LLM extractor (LLM output is filtered through the vocab whitelist). A JD mentioning Kinesis, Delta Lake, or Iceberg would score 0 on those skills even if you have them. Now they're recognized.

## Chat 19 — CI/CD (GitHub Actions)
Date: 2026-04-25

### Goal
Close the resume gap: deploys were entirely manual (local `terraform apply`, hand-copying zips, `aws s3 cp`). Any merge to `dev` now triggers a fully automated deploy to AWS — zero manual steps.

### Built

**New files:**
- **`requirements.txt`** (repo root) — single source of truth for all Python deps needed to run tests locally and in CI: `boto3, anthropic, pydantic, pyarrow==14.0.2, pandas, requests, numpy, voyageai, pyyaml, ruff, pytest`
- **`.github/workflows/ci.yml`** — lint + test on every push (any branch) and every PR targeting `dev`
- **`.github/workflows/deploy.yml`** — on push to `dev` only: re-runs tests as gate, then deploys all AWS artifacts

**Fixed:**
- **`tests/test_ingest_adzuna.py`** line 184–188 — stale test `test_missing_company_defaults_to_unknown` was asserting `"Unknown"` but Chat 16 changed the ingestor to return `None` (dedup collapse fix). Updated to `test_missing_company_defaults_to_none` with `assertIsNone`.

---

### How CI Works (`ci.yml`)

**Triggers:** every `git push` to any branch, and every pull request targeting `dev`.

**Steps:**
1. Checkout code
2. Set up Python 3.12 (matches Lambda runtime — same interpreter = same behaviour)
3. `pip install -r requirements.txt` (cached between runs for speed)
4. `ruff check --select E,F --ignore E501,E402 .` — lint the entire repo
5. `pytest tests/ spark/tests/ -v --tb=short` — run all unit tests

**What passes CI:**
- 192 unit tests across 6 test files (ingestors × 4, genai × 2)
- 14 PySpark tests auto-skip (no PySpark installed in CI — expected and correct)
- All AWS and external API calls are mocked — no live network calls, no credentials needed for tests

**What CI does NOT do:** deploy. It only validates. Deploys happen separately via `deploy.yml`.

**Why ruff, not flake8 or pylint:**
- ruff is 10–100× faster (written in Rust), runs the full repo in <1s
- `--select E,F`: E = pycodestyle errors (syntax, indentation), F = pyflakes (unused imports, undefined names)
- `--ignore E501`: line length ignored — existing code has long lines, not worth the noise
- `--ignore E402`: module-level import not at top — all ingestor tests do `sys.path.insert()` then `import ingest_X as sut`, which is intentional and correct

**Env vars set in the workflow (not secrets):**
- `BRONZE_BUCKET=test-bronze-bucket` — all ingestor tests mock S3 but still read this env var on import
- `AWS_REGION=ap-south-1` — boto3 requires a region even when mocked
- `ANTHROPIC_API_KEY=sk-test-dummy` — genai tests check `os.environ.get("ANTHROPIC_API_KEY")` before trying Secrets Manager; dummy value prevents any real API call

---

### How Deploy Works (`deploy.yml`)

**Trigger:** push to `dev` only (i.e., after a PR is merged or a direct push to dev).

**Two jobs run sequentially:**

```
push to dev
  → job: test   (re-runs full lint + pytest gate)
  → job: deploy (needs: test — only runs if test passes)
```

**Why re-run tests in deploy.yml instead of depending on ci.yml:**
- GitHub's `workflow_run` trigger (depending on another workflow) is async and unreliable for this pattern
- Re-running is cheap (<60s), guaranteed sequential, and self-contained — no race condition
- Principle: the deploy job must never trust that some other workflow already ran. It validates itself.

**Deploy job steps:**

| Step | What it does |
|------|-------------|
| Configure AWS credentials | Reads `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` from GitHub Secrets; uses `aws-actions/configure-aws-credentials@v4` |
| Deploy remotive Lambda | `cd ingestion/sources/remotive && zip ingest_remotive.zip ingest_remotive.py && aws lambda update-function-code ...` |
| Deploy arbeitnow Lambda | Same pattern for arbeitnow |
| Deploy adzuna Lambda | Same pattern for adzuna |
| Upload Glue scripts | `aws s3 cp` each of: `bronze_to_silver.py`, `dbt_runner.py`, `enrichment_runner.py`, `embedding_runner.py` to `s3://jobpulse-silver-dev/glue-scripts/` |
| Build genai_package.zip | `zip -r genai_package.zip genai/ config/user_profile.yml` → upload to `s3://jobpulse-silver-dev/glue-scripts/genai_package.zip` |
| Build dbt_project.zip | `zip -r dbt_project.zip dbt_project/ --exclude target/* --exclude dbt_packages/*` → upload to `s3://jobpulse-silver-dev/dbt-project/dbt_project.zip` |
| Upload user_profile.yml | `aws s3 cp config/user_profile.yml s3://jobpulse-silver-dev/config/user_profile.yml` |

**Himalayas Lambda is NOT in the deploy list** — it's Cloudflare-blocked and removed from Step Functions. Deploying it would be wasteful.

**Why Lambda zips are single .py files (no bundled deps):**
- All 4 ingestors use only Python stdlib (`urllib.request`, `gzip`, `json`, `boto3`)
- `boto3` comes pre-installed in every Lambda Python 3.12 runtime — no bundling needed
- Single-file zip = smallest possible cold start, no dependency conflict possible

**Why genai_package.zip is built in CI, not Terraform:**
- Previously Terraform's `null_resource` built it locally on `terraform apply` — coupling code deploy to infra apply
- Now: code change → push to dev → CI builds and uploads the zip automatically
- Terraform still owns the Glue job definition (name, timeout, DPU) — it just doesn't manage the zip anymore

---

### GitHub Secrets (one-time setup)

Add these in: GitHub repo → Settings → Secrets and variables → Actions → New repository secret

| Secret name | What it is |
|-------------|-----------|
| `AWS_ACCESS_KEY_ID` | IAM user `varun-admin` access key (ap-south-1) |
| `AWS_SECRET_ACCESS_KEY` | Matching secret key |

These are the only secrets needed. `ANTHROPIC_API_KEY` is NOT a GitHub secret — it's a dummy value for tests, and the real key lives in AWS Secrets Manager (accessed by Glue jobs at runtime, not by CI).

---

### What Happens After You Push to dev

1. GitHub Actions starts two workflow runs simultaneously:
   - `CI` (from `ci.yml`) — runs on all pushes
   - `Deploy` (from `deploy.yml`) — runs on push to dev only
2. Both run the test gate. If either fails, the rest stops.
3. Deploy job updates AWS within ~2 minutes of merge:
   - Lambda functions are live immediately after `update-function-code`
   - Glue scripts: live on the next Glue job invocation (Glue downloads the script from S3 each run)
   - genai_package.zip + dbt_project.zip: live on the next enrichment/dbt Glue job run
4. No Step Functions restart needed — the nightly EventBridge cron picks up the new code automatically

---

### Test Counts (post Chat 20)
- 198 unit tests pass (was 192 — 6 new GE tests added)
- 14 PySpark tests skipped (expected, no PySpark in CI)

---

## Roadmap — Priority Order
Last updated: 2026-04-25

### Done ✅
- Full pipeline: ingest → silver → gold → enrich → embed → dashboard
- 3 sources (Remotive, Arbeitnow, Adzuna), ~1,500 jobs/day
- GenAI: skill extraction, match scoring (tiered), semantic search, budget guard
- Dynamic S3 profile, force_rescore, rules-first fast path, ThreadPoolExecutor
- Terraform IaC, S3 backend, Step Functions orchestration, EventBridge schedule
- dbt star schema, Athena workgroup, CloudWatch alarms + SNS
- CI/CD: GitHub Actions lint + test gate on PRs, auto-deploy to AWS on merge to dev

## Chat 20 — Great Expectations Data Quality Gate
Date: 2026-04-25

### Built
- **`transform/ge_runner/ge_runner.py`** — Glue Python Shell job. Reads today's silver partition from S3 into pandas, runs 5 GE expectations (not-null on job_id/title/snapshot_date, row count ≥ 100, freshness check), raises ValueError on failure.
- **`tests/test_ge_runner.py`** — 6 unit tests: happy path, empty df, low count, null job_id, null title, stale date. All passing.
- **`terraform/envs/dev/glue.tf`** — `aws_glue_job.ge_runner` (Python Shell, 0.0625 DPU, 15 min timeout, great-expectations 1.4.4).
- **`terraform/envs/dev/step_functions.tf`** — `RunDataQuality` state inserted between `RunGlueJob` → `RunDbtGold`. Error catch block routes to `PipelineFailure` on failure.
- **`.github/workflows/deploy.yml`** — added upload step for `ge_runner.py`.
- **`requirements.txt`** — added `great-expectations>=1.3`.

### Key Bugs Hit & Fixes

**Bug 1: Wrong S3 prefix path**
- **Problem:** Code looked for `silver_jobs/snapshot_date=2026-04-25/` but Spark wrote to `snapshot_date=2026-04-25/`
- **Root cause:** Glue job writes directly to bucket root with partition structure, not under a `silver_jobs/` subfolder
- **Fix:** Changed prefix in `load_silver_df()` from `f"silver_jobs/snapshot_date={snapshot_date}/"` to `f"snapshot_date={snapshot_date}/"`

**Bug 2: Partition column missing from DataFrame**
- **Problem:** `df["snapshot_date"]` raised `KeyError: 'snapshot_date'` even though files were found
- **Root cause:** Partition columns in S3 path (`snapshot_date=2026-04-25/`) are not stored inside the Parquet data itself. PyArrow reads just the data columns.
- **Fix:** Manually assigned `df["snapshot_date"] = snapshot_date` after reading Parquet files

**Bug 3: Python 3.10+ union syntax in enrichment_runner.py**
- **Problem:** `def func() -> str | None:` syntax not supported in Python 3.9 (Glue Python Shell)
- **Root cause:** Glue runs Python 3.9; `|` union syntax is Python 3.10+
- **Fix:** Added `from typing import Optional` and changed to `Optional[str]`

**Bug 4: Numpy 2.x binary incompatibility in embedding job**
- **Problem:** `ImportError: numpy.core.multiarray failed to import` in EmbedJDs state
- **Root cause:** `--additional-python-modules` had `numpy>=1.24.0` which resolved to numpy 2.x. PyArrow 14.0.2 was compiled against numpy 1.x; C extensions are incompatible.
- **Fix:** Pinned `numpy==1.26.4` (last stable 1.x release)

### Full Pipeline Verification
```
Input: 1,920 jobs (Remotive 20 + Arbeitnow 100 + Adzuna 1,800)
  ↓
RunGlueJob (bronze→silver)     ✅ SUCCEEDED (87 sec)
  ↓
RunDataQuality (GE validation) ✅ SUCCEEDED (47–100 sec)
  - Checked: no nulls on job_id, title, snapshot_date
  - Checked: row count ≥ 100 (got 1,920)
  - Checked: freshness (all snapshot_date == 2026-04-25)
  - Result: All 5 expectations PASSED
  ↓
RunDbtGold (dbt transforms)    ✅ SUCCEEDED (78–93 sec)
  ↓
RunEnrichment (skill extract)  ✅ SUCCEEDED (1,064 sec / 17.7 min)
  ↓
EmbedJDs (Voyage AI)           ✅ SUCCEEDED
  ↓
PipelineComplete               ✅ SUCCEEDED
```

### Pipeline after Chat 20
```
ParallelIngest → RunGlueJob → RunDataQuality → RunDbtGold → RunEnrichment → EmbedJDs → PipelineComplete
```

### Key API Lessons (GE 1.x)
- `ExpectTableRowCountToBeGreaterThan` does not exist → use `ExpectTableRowCountToBeBetween(min_value=N)`
- `ExpectColumnValuesToBeBetween` on strings silently misbehaves → use `ExpectColumnDistinctValuesToBeInSet` for freshness
- GE 1.x requires Python 3.9–3.12; `get_context(mode="ephemeral")` creates in-memory context (no Data Docs needed)
- Partition columns in S3 path are NOT in Parquet data — must assign manually after reading
- Local testing with Python 3.12 (system Python 3.14 too new)

### Backlog — Chat 21+: Volume Scale
**Greenhouse + Lever ATS ingestors** — one Lambda per ATS, slug list in S3, hits hundreds of company boards.
Expected: +5,000–20,000 jobs/run → pipeline crosses 100K+/month target.

### Backlog — Chat 22+: Dashboard Depth
- Salary arbitrage view (same role, different countries, cost-of-living normalized)
- Skill trend lines (techs rising/falling week-over-week)
- Hackathon radar (Devpost source, prize pool + deadline)
- Company leaderboard (hiring velocity for target role)

### Backlog — Stretch
- Cross-day deduplication (canonical_job_id that persists across snapshot_dates)
- "Why this match" Claude explanation per ranked job (Chat 15 groundwork already done)
- Weekly AI market brief auto-generated by Claude
- GCP migration (post-6-month AWS free tier)


## Chat 21 — Streamlit Dashboard Deployed to EC2
Date: 2026-04-26

### Built
- **dashboard/streamlit/Dockerfile** — containerizes Streamlit app (python:3.12-slim, port 8501, healthcheck). Build context is repo root so `genai/` is available inside container.
- **terraform/envs/dev/ec2.tf** — security group (8501+22), IAM instance profile (Athena+S3+Glue+SecretsManager), t3.micro EC2 (Amazon Linux 2023), Elastic IP. user_data installs docker + git.
- **terraform/envs/dev/outputs.tf** — dashboard_url + dashboard_instance_id outputs
- **dashboard/streamlit/app.py** — fetches Voyage + Anthropic keys from Secrets Manager via `_get_secret()` (env var fallback for local dev). Extracts secret value via `next(iter(parsed.values()))` to handle any JSON key name.
- **.github/workflows/deploy.yml** — added deploy-dashboard job: SSH into EC2, git pull, rebuild Docker from repo root with `-f dashboard/streamlit/Dockerfile .`, restart container
- **ingestion/sources/adzuna/ingest_adzuna.py** — narrowed COUNTRIES from 12 to 7 (gb, us, au, ca, in, nz, za). Removed pl, ru, de, fr, br — Adzuna's local sites geo-block Indian users.

### AWS Resources Provisioned
- EC2 t3.micro: i-0bdbfcad1985a7565 (final, after user_data and IAM fixes)
- Elastic IP: 3.7.125.66 (stable, persists across instance replacements)
- Security group: jobpulse-dashboard-sg-dev (port 8501 + 22)

### Bugs Hit & Fixed (4)
1. **EC2 missing git** — GitHub Actions deploy ran `git pull` but `git` wasn't in user_data. Fixed: added `git` to `yum install` in ec2.tf user_data, replaced instance.
2. **IAM missing S3 bucket-level perms** — Athena raised `InvalidRequestException: Unable to verify output bucket`. IAM policy only had object-level permissions (`s3:PutObject` on `athena-results/*`). Added `s3:ListBucket`, `s3:GetBucketLocation`, `s3:GetBucketVersioning` on the bucket ARNs.
3. **Wrong Secrets Manager key names** — app called `_get_secret("VOYAGE_API_KEY", "voyage_api_key")` but actual secrets were named `jobpulse/voyage_key_dev`. Fixed key names in the `_get_secret()` calls.
4. **Secrets Manager JSON key mismatch** — `_get_secret()` extracted `json.loads(raw)["value"]` but secrets were stored as `{"VOYAGE_API_KEY": "pa-..."}`. Fixed: `next(iter(parsed.values()))` — gets the first value regardless of key name.

### Verified
- Dashboard live at http://3.7.125.66:8501 ✓
- 19,930 jobs loaded from Athena ✓
- Sidebar filters, charts, tag frequency all rendering ✓
- Semantic Search tab working (Voyage key from Secrets Manager) ✓
- Claude Haiku "Why this match?" explanations rendering ✓

### GitHub Secrets Added
- `EC2_DASHBOARD_IP` = 3.7.125.66
- `EC2_SSH_KEY` = contents of ~/.ssh/jobpulse-dev.pem
- IAM role: jobpulse-dashboard-role-dev (instance profile, no hardcoded keys)
- IAM policy: Athena query + S3 gold/silver read + Glue catalog + Secrets Manager read

### Key Decisions
- EC2 t3.micro over ECS Fargate: free tier (6 months), SAA practice, zero cost
- IAM instance profile over .env keys: auto-rotated credentials via metadata service
- scp via GitHub Actions over git clone: private repo, no GitHub credentials needed on EC2
- Elastic IP: stable public IP for bookmarks/resume links without domain cost
- Secrets Manager for Voyage + Anthropic keys: consistent with enrichment runner pattern

### Incident
- First EC2 user data attempted git clone of private repo → failed (no GitHub credentials on instance)
- Fix: simplified user data to just install Docker + mkdir; code deployment delegated to GitHub Actions scp
- Instance terminated and recreated with fixed user data

### Next
- Push to dev → GitHub Actions deploys dashboard → verify http://3.7.125.66:8501 loads
- Chat 22: Greenhouse + Lever ATS ingestors (+5K-20K jobs/run)

## Chat 22 — Greenhouse ATS Ingestor
Date: 2026-04-26

### Goal
Add Greenhouse as the 4th ingestor. Greenhouse covers ~30 major companies (Stripe, Airbnb, Figma, etc.) with no auth required. Expected: +1,000–5,000 jobs/run.

### Phase 0 — API Verification from Datacenter IP (EC2 3.7.125.66)
- ✅ Greenhouse `boards-api.greenhouse.io/v1/boards/{slug}/jobs` — no Cloudflare, returns jobs cleanly
- ❌ Lever — tested 40+ slugs, found only 2 active boards (Sector7 + Unwind). Market shifted to Greenhouse. Deleted entirely.
- ❌ Ashby — API requires auth header (401). Not a free public API. Skipped.

### Built
- **`ingestion/sources/greenhouse/ingest_greenhouse.py`** — Lambda handler. No external dependencies. Hardcoded SLUGS list: 30 companies. Handles 404 silently (inactive boards). Returns `{"source": "greenhouse", "snapshot_date": ..., "record_count": n, "status": "OK/EMPTY"}`.
- **`tests/test_ingest_greenhouse.py`** — 16 unit tests: load_slugs, fetch_company_jobs (200/404/500), normalize_jobs (field mapping, slug prefix, missing location, empty departments), fetch_all_jobs, build_s3_key, lambda_handler (happy path, dry_run, empty, default date). All passing.
- **`terraform/envs/dev/lambda.tf`** — added `aws_lambda_function.greenhouse` (timeout=120, memory=256).
- **`terraform/envs/dev/step_functions.tf`** — added Greenhouse as 4th parallel branch. SF IAM policy updated.
- **`.github/workflows/deploy.yml`** — added Greenhouse Lambda deploy step.

### Lever deleted
- `ingestion/sources/lever/` removed (was only in worktree, never committed)
- Rationale: 2 active jobs out of 40+ slugs tested = not worth the maintenance cost

### Incident — Lambda YAML Import Error
- **Problem:** `Unable to import module 'ingest_greenhouse': No module named 'yaml'`
- **Root cause:** Terraform zipped `greenhouse_slugs.yml` alongside the handler. Lambda runtime has no pyyaml.
- **Fix:** Converted to hardcoded `SLUGS = [...]` list in Python. Removed YAML file entirely.
- **Verified:** Lambda invoked successfully, returned 4,115 jobs (dry_run=true).

### Pipeline Flow (4 ingestors)
```
ParallelIngest (Remotive | Arbeitnow | Adzuna | Greenhouse)
  → RunGlueJob → RunDataQuality → RunDbtGold → RunEnrichment → EmbedJDs → PipelineComplete
```

### Test count: 103 passing (16 new Greenhouse tests)
### Live volume: ~6,600 jobs/run (~198K/month across 4 sources)

---

## Production Run — Unattended (2026-04-26 → 2026-09-19)
Reconstructed in Chat 23 from AWS (Step Functions, Glue job history, CloudWatch alarm history). Not written at the time.

### What happened
- **Ran every night unattended for ~3 months.** Enrichment Glue job: **115 SUCCEEDED** runs on record.
- **Enrichment crept up to its timeout.** 15–20 min in late April → **48–56 min every night from Apr 29** (after Greenhouse, ~6.6K jobs/run) against a **60-min timeout**. First TIMEOUTs Jul 25–26; TIMEOUT every night from ~Aug 15. Total: 29 TIMEOUTs. Volume had grown to **7,855 jobs** (Aug 14).
- **Source schema drift.** `bronze_to_silver` FAILED Aug 1–9 (6 runs): `cannot resolve 'job.tags' … cannot cast string to array<string>`. Recovered by itself Aug 10. Source not identified — bronze (7-day lifecycle) had expired.
- **Alarm fired but self-reset.** `jobpulse-sfn-failures-dev` → ALARM ~2 AM, back to OK ~15 min later, every night. Emails delivered (subscription confirmed) but read like one-off blips.
- **Step Functions history (90-day window, Jul 10 → Sep 19):** 20 SUCCEEDED, 52 FAILED.
- **2026-09-19:** EventBridge rule `jobpulse-daily-ingest-dev` set to DISABLED (Varun; uncommitted `eventbridge.tf` edit, applied in AWS).
- **EC2 dashboard instance + Elastic IP** no longer exist in AWS (removed outside Terraform; date not recorded) but are still declared in `ec2.tf`.
- **Dependency drift:** unpinned `anthropic>=0.40.0` in the enrichment job resolved to 0.125.0 by September.

### Data left in S3 (2026-10-06)
| Location | Size | Note |
|---|---|---|
| `jobpulse-silver-dev` | 179 MB, 8,219 objects | **153 daily snapshots (Apr 18 – Sep 19)** with full descriptions |
| `jobpulse-gold-dev/embeddings/` | 633 MB, 96 files | one file per day; every job re-embedded daily |
| `jobpulse-gold-dev/athena-results/` | 1.5 GB, 5,016 objects | mostly old dashboard query CSVs (no lifecycle); dbt CTAS tables (85 MB) live under `tables/` |
| `jobpulse-gold-dev/enrichment-scores/` | 15 MB | |
| `jobpulse-gold-dev/enrichment-cache/` | 40 KB, 698 objects | LLM extraction cache + budget ledgers |
| bronze, archive | 0 | bronze expires after 7 days |

---

## Chat 23 — Recon, AWS Decision, Docs Overhaul
Date: 2026-10-06

### Goal
Pick the project back up after 5 months away, find out what happened in production, decide what to do about the
AWS free plan ending (~Oct 16), and turn JobPulse into an AI Data Engineer project with interview-ready docs.

### Found
- **Local checkout was stale:** main checkout on `dev` at `ccf0cc4` (Chat 9) while GitHub `dev` was at `98b1e4c` (Chat 22 + 3 EC2 deploy fixes). Running `terraform apply` from it would have planned to destroy everything built in Chats 10–22 (shared S3 state).
- **Production history** — see "Production Run" above.
- **Adzuna API key committed** in `terraform/envs/dev/terraform.tfvars` (docs claimed the file was gitignored).
- **Docs vs code gaps** (code is the truth):
  - README / CLAUDE.md describe things never built: `SalaryParser`, `SeniorityClassifier`, `DedupAgent`, Batch API, JSON guardrails, MinHash/LSH, SCD2 `dim_company`, `dim_date`, `dim_location`, "Claude embeddings".
  - `dbt_runner.py` runs `dbt run`, not `dbt build` → the 21 dbt tests never ran in production.
  - 2 of 5 GE expectations can't fail (`ge_runner.py` sets `snapshot_date` itself before checking it).
  - Dashboard "Why these match?" never receives the job description (`FLAT_JOIN_SQL` doesn't select it) → Claude explains jobs it never saw.
  - Embeddings use only the first 4,000 characters of each JD; every job re-embedded daily; search covers latest day only.
  - Budget tracker checks without reserving (race across 16 threads); price constants are Haiku 3.5's; prompt caching set on a prompt too short to cache.
  - Step Functions: `Catch` on every step, no `Retry` anywhere.
  - Real test count: 228 test functions (~214 run in CI; 14 PySpark tests skipped).
- **Three outside claims verified:** (1) "NumPy in-memory cosine" — true, but a vector DB is not the main gap at 8K vectors; (2) "nothing measures search quality" — true, and extraction quality isn't measured either; (3) "resume undersells semantic search / dbt" — false (both already on the resume); GE missing is true; "orchestrator/sub-agent" should NOT be added (not true in code).

### Decided (details in decisions.md)
- Stay on AWS, upgrade to a paid plan (not migrate to GCP / a $0 stack).
- No Redshift — Athena fits the workload.
- Dashboard runs locally on demand; EC2 hosting retired.
- Vector store chosen by evaluation: pgvector (RDS) vs Amazon S3 Vectors vs exact NumPy (Chat 29).
- Go live first (cheap on AWS), but evaluate on a frozen, versioned corpus.
- Roadmap Chats 24–33 in `docs/roadmap.md`.

### Built (docs only — no code or infra changes)
- **`docs/interview_guide.md`** (new) — pitch, one-posting story, full pipeline (current + target), layer-by-layer why/why-not, AWS services vs popular alternatives (Athena vs Redshift, Step Functions vs MWAA…), design patterns, AI theory in simple words mapped to JobPulse, 12 stories, numbers, honest limits, likely questions, resume bullets, timeline. Updated every chat from now on.
- **`docs/roadmap.md`** (new) — Chats 24–33 with done-criteria.
- **`docs/runbook.md`** — was empty; now 16 failure modes with commands.
- **`docs/incidents.md`** — 6 new incidents (timeout creep, tags drift, self-resetting alarm, empty RAG context, stale checkout, committed key).
- **`docs/decisions.md`** — Chat 23 decisions.
- **`README.md`** — rewritten to match the code.
- `progress.md` — Chat 1 heading + Chat 2 date (2025 → 2026) fixed; this entry + "Production Run".
- `CLAUDE.md` (local, gitignored) — current status, docs table, end-of-chat routine now includes the interview guide.

### Verified
- Account plan: `PAID / ACTIVE`, $68.13 credits remaining (shared with ledgerline). Budgets already in place and account-wide: $20/month, $2/day, $0.01 zero-spend.
- AWS read-only inventory: EventBridge rule DISABLED; EC2/EIP gone; 5 Lambdas, 5 Glue jobs, state machine, 2 secrets, tfstate bucket + DynamoDB lock still exist; SNS subscription confirmed; IAM user created 2026-04-16 (free plan likely ends ~2026-10-16).

### Next
Chat 24 — Stabilize: secrets out of git, remove EC2 from Terraform, cost hygiene (athena-results lifecycle, log retention, pinned deps), local backup, clean `terraform plan`. **Before it:** `git pull --ff-only origin dev` in the main checkout, rotate the Adzuna key. (Account already paid; budgets already cover it.)

---

## Chat 24 — Stabilize
Date: 2026-10-06

### Goal
Make the account and repo safe before going live again: secrets out of git, Terraform matching reality, cost hygiene,
a local backup, and a `terraform plan` that shows only intended changes.

### Built
- **Secrets:** `adzuna_app_id` / `adzuna_app_key` now come from `TF_VAR_` env vars (or a local tfvars); `*.tfvars` gitignored;
  `alert_email` got a default. `terraform.tfvars` must be untracked with `git rm --cached` (Varun).
- **EC2 retired from code:** `ec2.tf` deleted (SG, IAM role/policy/attachment, instance profile → destroyed on apply; instance +
  EIP were already gone), 2 outputs removed, `deploy-dashboard` job removed from `deploy.yml`.
- **Log retention:** 9 log groups (5 Lambda + 4 `/aws-glue/*`) at 14 days, adopted with `import` blocks in `monitoring.tf`.
  `required_version >= 1.7`.
- **Pinned Glue packages:** `anthropic==0.125.0`, `pydantic==2.13.5`, `voyageai==0.5.0`, `great-expectations==1.8.1`,
  `pandas==2.3.3` (versions the last green runs installed).
- **`.gitattributes`:** `* text=auto eol=lf`.
- **Local backup:** `C:\Users\malik\jobpulse_backup\2026-10-06\` — 943 MB; silver 8,219 / gold embeddings 96 /
  enrichment-scores 103 / enrichment-cache 698 / 4 gold tables (21 files) — all counts match S3. Plus Glue logs
  Aug 1 – Sep 20 (122K events) for the Chat 25 tags-drift + timeout investigation.
- Docs: runbook §14/§15/§17, decisions (6), incidents (2), interview guide (§3, §4, §5, story 13, §10, §11, §14).

### Not done (on purpose)
- **No `athena-results/` lifecycle rule.** The gold tables live in `athena-results/tables/<uuid>/` (workgroup enforces its
  output location → dbt's `s3_data_dir` ignored). An expiry rule would have deleted them. Moved to Chat 25.

### Found
- `core.autocrlf=true` made Terraform see every Python file as changed → `.gitattributes`.
- CI (`deploy.yml`) and Terraform both deploy Lambda code + Glue scripts → recurring benign diff (zip bytes, S3 tags).
- `deploy-dashboard` CI job failed on every push since the EC2 was removed.
- Glue logs show `anthropic` drifting 0.117 → 0.125 across nights (unpinned `>=`).

### Verified
- `terraform plan` (worktree, LF-normalized): 9 to import, 23 to change, 5 to destroy. Intended: 9 imports + retention,
  3 Glue jobs (pins), 5 EC2 leftovers destroyed. Benign (two deployers): 5 Lambdas `source_code_hash`, 6 S3 objects `tags_all`.
- `ruff` clean; pytest 208 passed / 14 skipped / 6 failed — the 6 are `test_ge_runner.py`, GE not installed in the local
  Python (repo's `great_expectations/` folder imports as an empty namespace package); CI installs it.

### Closed out (2026-10-07)
- Committed `7d7aedb` (18 files: incl. `git rm` of `ec2.tf`, `terraform.tfvars`; `interview_guide.md` untracked — now local only with `learning.md`).
- Applied: 9 imported, 23 changed, 5 destroyed. Verified: 9 log groups at 14 d; dashboard IAM role + SG gone; 3 Glue jobs pinned.
- Adzuna key rotated; first apply wrote the literal `<new key>` (stale PowerShell window) → fixed with a saved one-change plan;
  Lambda key verified 32 hex chars, equal to the User-scope value (incident logged).
- A skipped commit + `git reset --hard` wiped the uncommitted changes once → restored from the worktree (incident logged).
- Cost allocation tags `project` and `layer` activated. Worktrees removed; work now happens in the main checkout.

### Next
Chat 25 — fix the failures, move gold tables out of `athena-results/` then add the expiry, absence-of-success alarm, go live.
First step: one test invoke of the Adzuna Lambda to prove the rotated key works.

---

## Chat 25 — Fix the failures, go live
Date: 2026-10-08

### Goal
Find the real causes of the Aug–Sep failures, fix them, turn the quality checks into real gates, then go live again.

### Found (measured, from the Chat 24 log export + local backup)
- **Enrichment timeout = a failure path, not volume.** No Claude call has succeeded since 2026-04-21 (cache writes stop that day; `llm=0`, `$0` in every snapshot). Each failure: 3 retries with 1+2+4 s sleeps, then labelled "rules". Greenhouse JDs are empty (4,035 / 5,729 on Aug 14), so only 17 jobs passed the regex fast path. Rules for all 5,729 JDs: 4 s.
- **Glue Python Shell stdout is buffered** → timed-out runs logged nothing.
- **`tags` drift:** traceback confirmed (`cannot cast string to array<string>`); the source can't be identified (logs show only the Spark plan; bronze expired).
- **dbt tests never ran in prod.** Run against live gold: 19 pass, 2 fail — `dim_company` 368 duplicate keys (fact→company join 1,147,783 → 2,011,211 rows, +75%), `source_count` null in 24 pre-dedup rows.
- **9 of 14 PySpark tests failed** once Spark was available (always skipped in CI).
- `requirements.txt` let numpy 2 install next to pyarrow 14 (import fails) — pinned 1.26.4.
- Adzuna rotated key works: dry-run invoke fetched 1,200 jobs, wrote nothing.
- Adzuna descriptions are 500-char snippets; Greenhouse sends none (`?content=true` would — decision open).

### Built
- `genai/jd_enrichment_agent.py` — one decision path (rules → cache → LLM), empty JDs skip the LLM, circuit breaker (5 in a row), `use_llm` flag, per-source timing, progress every 1,000 jobs, `llm_errors` / `breaker_open` in the summary.
- `genai/skill_extractor.py` — own retry loop removed; `extract_llm()` raises so failures are counted.
- `genai/enrichment_runner.py`, `genai/embedding_runner.py` — line-buffered stdout, `[timing]` per stage, `--use_llm`, publish `JobPulse/JobDurationSeconds`. New `genai/run_metrics.py`.
- `spark/jobs/bronze_to_silver.py` — `bronze_schema()` (explicit, all strings), `parse_tags()`.
- `transform/ge_runner/ge_runner.py` — freshness from `ingested_at` (IST), `tags`-is-list expectation (6 total).
- `transform/dbt_runner/dbt_runner.py` — `dbt build`; `s3_data_dir` = `gold/models/`.
- dbt: `dim_company` one row per key; staging coalesces `source_count` / `source_apis`.
- `dashboard/streamlit/app.py` — "Why these match?" gets real description text from silver.
- Terraform: workgroup enforcement off; gold `athena-results/` 7-day expiry (**Disabled** until the tables move); enrichment timeout 60 → 20, `--use_llm false`; `cloudwatch:PutMetricData` (namespace JobPulse); absence-of-success alarm replaces the failure alarm; 2 duration alarms; CI owns code (`ignore_changes`, `null_resource`s removed).
- CI: `spark-tests` job (PySpark 3.3.2, Java 11) in `ci.yml` and gating `deploy.yml`; Greenhouse zip step fixed; `requirements.txt` pins numpy 1.26.4, GE 1.8.1.
- Tests: +6 `parse_tags`, +3 Spark tags/schema, +4 agent LLM guards, +2 GE; 2 contradictory dedup tests rewritten.

### Verified
- `ruff` clean; pytest without Spark (as in CI) **226 passed, 17 skipped**; PySpark tests with Spark 3.4.4 + JDK 11 locally: **128 passed** (`spark/tests/`).
- Agent on 5,729 real JDs: 4.4 s (LLM off), 14 s (LLM on, every call failing) — was ~60 min.
- GE on real silver (Aug 14, Jul 31): pass. dbt tests on live gold before the fix: 19/21. New `dim_company` SQL on Athena: 28,260 rows = 28,260 keys.
- `terraform validate` OK; `terraform plan`: **4 add, 4 change, 3 destroy** — exactly the intended set; the Chat 24 "two deployers" diff is gone.

### Go-live (2026-10-08)
- Committed `4de9a58`, CI green (test + spark-tests + deploy; deployed scripts match the commit by md5). Applied: 4 added, 4 changed, 3 destroyed.
- Manual run **SUCCEEDED in 9 min 36 s**: ingest 28 s · Spark 2 min 10 s · GE 6/6 on 6,891 rows · `dbt build` **26/26 PASS** (113 s) · enrichment **23 s** (6,923 jobs, rules 6,506 / cache 417, 0 LLM errors) · embeddings 39 s (2,442 new). Both runners published `JobPulse/JobDurationSeconds`.
- Runbook §18 passed: all 4 dbt tables under `gold/models/`. Fact = silver row for row (770,151) — the fact table had been ~55% inflated by the `dim_company` bug.
- Step Functions history (Jul 11 – Sep 19, 72 runs) split by duration: <1 s = **Arbeitnow `KeyError: 0`** (5 nights, new finding — PHP API sends objects for lists; fixed with `as_list()`), 2–3.5 min = `tags` drift, ~1 h 10 = enrichment timeout. The one August success (Aug 14) finished enrichment at 59.3 min.
- GitGuardian flagged the old Adzuna key in the public repo (from the Chat 24 deletion diff); key already rotated, live key verified different. `*.tfplan` gitignored.
- No-success alarm stayed `INSUFFICIENT_DATA` after creation: the success metric had expired during the 19-day pause (runbook §12).
- Phase 2 (commit `896c75e`): Arbeitnow `as_list()` deployed by CI; applied with the no-success alarm **replaced**
  (1 added, 2 changed, 1 destroyed). Verified: EventBridge rule `ENABLED`, gold lifecycle `athena-results-expire` `Enabled`.
- Saved plan files deleted (they hold the Adzuna key in plain text). GitGuardian alert: old key confirmed revoked at
  Adzuna, alert marked revoked.
- Recreated no-success alarm still `INSUFFICIENT_DATA` / "Unchecked: Initial alarm creation" minutes later — open (see Next).

### Not done
- Done-criteria: 3 green **scheduled** nights (Oct 9, 10, 11, 2:00 AM IST — the schedule was enabled after tonight's slot) and a
  deliberate failure that fires the no-success alarm once without auto-reset. Can't be fast-forwarded: they test the
  unattended trigger, new data each day, and the alarm's 26 h window — re-running today only overwrites the same partition.

### Next
- Chat 26 starts now, in parallel (notebook on exported data, no AWS changes). Each chat opens with a night check;
  Chat 25 closes when Oct 9–11 are green.
- If the alarm is still "Unchecked" after the Oct 9 run: rebuild it with metric math (`FILL(m1, 0)`, so an empty hour
  counts as 0 instead of missing).
- Why the Claude calls fail (key vs credits) — check console.anthropic.com.
- Done-criteria (3 green nights; a deliberate failure fires the alarm once and does not auto-reset) — after go-live.

