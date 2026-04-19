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

### Next
Chat 10 — End-to-end Step Functions run verifying RunEnrichment, Athena query on enrichment_scores, dashboard match_score column live

