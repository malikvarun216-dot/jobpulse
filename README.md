# JobPulse

**A serverless AWS data pipeline that collects ~7,000 job postings a night from four public job APIs, models
them into a star schema, uses an LLM as a parser to extract skills and seniority, scores every job against a
personal profile, and supports semantic (meaning-based) job search.**

> **Status (Oct 2026):** ran unattended from late April to August 2026; nightly schedule paused since
> 2026-09-19 after the enrichment step outgrew its timeout. Being stabilized and extended with a measured
> AI layer (retrieval evaluation, vector store comparison, extraction evaluation) — see
> [`docs/roadmap.md`](docs/roadmap.md).

---

## What it answers

| Question | How |
|---|---|
| How many openings per role / country / source? | Star schema in Athena, dashboard filters |
| Which skills and companies show up most? | Tag frequency, company leaderboard |
| Which jobs are worth applying to? | Personal `match_score` (0–100) |
| Which jobs *mean* what I'm looking for, even with different words? | Embedding-based semantic search |

## Architecture

```
EventBridge (2:00 AM IST)
  └─ Step Functions (STANDARD)
      ├─ ParallelIngest   Lambda × 4: Remotive | Arbeitnow | Adzuna (7 countries) | Greenhouse (30 companies)
      │                   → S3 bronze   raw JSON.gz, 7-day lifecycle
      ├─ RunGlueJob       Glue Spark → S3 silver   Parquet, partitioned snapshot_date / country / role_family
      ├─ RunDataQuality   Great Expectations on silver (fail before gold)
      ├─ RunDbtGold       dbt-athena → gold star schema (fact_job_posting + dim_company / dim_role / dim_country)
      ├─ RunEnrichment    rules → Claude Haiku fallback → skills, seniority, YoE → match_score
      ├─ EmbedJDs         Voyage AI voyage-4-lite (512-d) → embeddings Parquet in S3
      └─ Complete / Failure → CloudWatch alarm → SNS email
Dashboard: Streamlit (Athena + NumPy cosine search + Claude "why these match"), run locally on demand
IaC: Terraform (S3 remote state + DynamoDB lock) · CI/CD: GitHub Actions (ruff + pytest → deploy on push to dev)
```

## Tech stack

| Layer | Tools |
|---|---|
| Ingestion | AWS Lambda (Python 3.12, stdlib + boto3 only) |
| Orchestration | EventBridge, Step Functions |
| Processing | AWS Glue (PySpark 4.0; Python Shell for dbt / GE / LLM / embeddings) |
| Storage & query | S3 (Parquet + Snappy), Glue Data Catalog, Athena (1 GB per-query scan cap) |
| Transformation | dbt-core 1.9 + dbt-athena-community |
| Data quality | Great Expectations (silver), dbt schema tests (gold) |
| GenAI | Claude Haiku 4.5 (extraction, explanations), Voyage AI embeddings |
| Dashboard | Streamlit + Plotly |
| IaC / CI/CD | Terraform, GitHub Actions |
| Monitoring | CloudWatch Logs + Alarms, SNS |

## Data sources

| Source | Jobs/run (approx.) | Notes |
|---|---|---|
| Greenhouse | ~4,000 | 30 company boards, public JSON |
| Adzuna | ~1,500–2,000 | 7 English-language markets; structured salary min/max |
| Arbeitnow | ~1,000 | public API |
| Remotive | ~20–30 | public API |

~6,600 jobs/run in April 2026, ~7,900 by August. Himalayas and RemoteOK are implemented but blocked by
Cloudflare from AWS IP addresses; Lever and Ashby were evaluated and dropped.

## GenAI layer (what exists today)

- **Rules first, LLM second:** regex against a skill whitelist + seniority patterns; Claude Haiku only when
  regex finds < 5 skills or no seniority. Took enrichment from a 57-min timeout to ~3–5 min for 3,400 jobs.
- **Guardrails:** Pydantic schemas, skill whitelist (no invented skills), retries with backoff, fallback to the
  rules result on any failure, $0.50/day budget cap.
- **Cost:** results cached in S3 by md5 of the description — the same description is never paid for twice;
  `--force_rescore` re-scores from cache with zero LLM spend after a profile change.
- **Match score (0–100):** skills 50 (core skills weighted 3×) · seniority 10 (YoE-aware) · location 15 ·
  role family 15 · salary 5 · freshness 5. Profile in `config/user_profile.yml`.
- **Semantic search:** query and job descriptions embedded with Voyage (512-d), cosine similarity in NumPy,
  top-50 blended with `match_score`.

**Not built yet** (planned, with measurement): retrieval evaluation, hybrid keyword + vector search, a vector
database (pgvector vs Amazon S3 Vectors), incremental / versioned embeddings, extraction evaluation, weekly
AI brief. See [`docs/roadmap.md`](docs/roadmap.md).

## Data model

```
fact_job_posting   grain: one row per posting per snapshot_date
  ├── dim_company   company_key = md5(lower(trim(company_name)))
  ├── dim_role      role_key on (role_family, category)
  └── dim_country
enrichment_scores  (separate table owned by the enrichment job — dbt rebuilds would wipe it otherwise)
```

Same-day cross-source duplicates are tagged with `source_apis[]` / `source_count`. Cross-day dedup, SCD Type 2,
`dim_date`, `dim_location` and skill bridge tables are not built.

## Engineering practices

- **Idempotent:** dynamic partition overwrite per `snapshot_date`; dbt CTAS rebuilds; reruns don't duplicate.
- **Cost guards:** S3 lifecycle (bronze 7 days, silver → Standard-IA 30 days, archive → Glacier IR 180 days),
  Athena scan cap, LLM daily cap.
- **Tested:** 228 test functions, every external call mocked, no AWS credentials in CI.
- **Least privilege:** ingestion Lambdas can write only to bronze.
- **Documented:** decisions, incidents (59), runbook, roadmap in [`docs/`](docs/).

## Repo layout

```
├── ingestion/sources/<source>/   one Lambda ingestor per API
├── spark/jobs/                   Glue bronze → silver job (+ spark/tests/)
├── dbt_project/                  staging + gold models, schema tests
├── transform/dbt_runner/         Glue Python Shell wrapper for dbt
├── transform/ge_runner/          Great Expectations quality gate
├── genai/                        enrichment agent, skill extractor, match scorer, guardrails, embeddings, search
├── dashboard/streamlit/          Streamlit app (+ Dockerfile)
├── config/user_profile.yml       personal profile + scoring weights
├── terraform/envs/dev/           all AWS infrastructure
├── tests/                        unit tests
├── .github/workflows/            ci.yml, deploy.yml
└── docs/                         progress, decisions, incidents, runbook, roadmap
```

## Local development (Windows PowerShell)

Work happens on `dev` in the main checkout; `main` gets milestone merges only.

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run the tests (same as CI):

```powershell
pytest tests/ spark/tests/ -v
```

Terraform secrets come from environment variables, never a committed file (`*.tfvars` is gitignored):

```powershell
$env:TF_VAR_adzuna_app_id = "<id>"; $env:TF_VAR_adzuna_app_key = "<key>"
```

Then plan from `terraform/envs/dev/` and read every line before applying:

```powershell
terraform plan
```

Dashboard, run locally against AWS:

```powershell
streamlit run dashboard/streamlit/app.py
```

---

*Personal learning project — data engineering + GenAI + AWS, built end to end.*
