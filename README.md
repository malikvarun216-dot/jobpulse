# JobPulse

**A production-grade, serverless data engineering pipeline that ingests 3,600+ job postings daily from multiple APIs, enriches them with a custom GenAI agent, and delivers market intelligence through an interactive dashboard.**

Built end-to-end on AWS free tier — runs unattended, 24/7, laptop-off.

---

## Demo

> Dashboard live on Streamlit — filter by role, country, seniority, salary, and match score. Semantic job search coming in the next release.

---

## What It Does

| Question | Answer |
|----------|--------|
| How many offers per role / location / seniority? | Live counts, updated daily |
| Which stacks and companies are trending? | Skill frequency + company leaderboard |
| Where's the best salary-to-cost-of-living ratio? | Salary arbitrage view (in progress) |
| Which jobs are worth applying to today? | Ranked by personal match score (0–100) |

---

## Architecture

```
APIs (20+ sources)
    │
    ▼
Lambda ingestors  ──  EventBridge (daily 2 AM IST)
    │
    ▼
S3 bronze  (raw JSON, 7-day lifecycle)
    │
    ▼  Step Functions
Glue Spark job
    │
    ▼
S3 silver  (Parquet, deduplicated, partitioned by date/country/role)
    │
    ▼  dbt + Athena
S3 gold  (star schema: fact_job_posting + 4 dimensions)
    │
    ▼
GenAI enrichment  (skills · seniority · match score)
    │
    ▼
Streamlit dashboard
```

---

## Tech Stack

| Layer | Tools |
|-------|-------|
| Ingestion | AWS Lambda, Python |
| Orchestration | AWS Step Functions, EventBridge |
| Processing | AWS Glue (PySpark), S3 |
| Warehousing | Athena, Glue Data Catalog |
| Transformation | dbt-core (Athena adapter) |
| GenAI | Claude API (Haiku), custom agentic pipeline |
| Dashboard | Streamlit |
| IaC | Terraform |
| CI/CD | GitHub Actions (planned) |
| Data Quality | dbt tests, Great Expectations (planned) |
| Monitoring | CloudWatch, SNS |

---

## Data Sources

Three live, more in progress:

| Source | Jobs/run | Salary data |
|--------|---------|-------------|
| Adzuna | ~2,500 | ✅ min / max (12 countries) |
| Arbeitnow | ~1,000 | — |
| Remotive | ~25 | — |

**Planned:** Greenhouse, Lever, USAJobs, Reed.co.uk, HN Algolia, Devpost hackathons

All sources tested for Cloudflare/bot-protection from Lambda datacenter IPs before implementation.

---

## GenAI Enrichment

A custom agentic pipeline — built in plain Python, no frameworks.

**Extracts per job description:**
- `skills[]` — normalized against a curated vocabulary
- `seniority` — intern → exec
- `role_family` — DE, SDE, PM, DS, ML, DevOps, etc.
- `remote_policy` — onsite / hybrid / remote
- `salary_min`, `salary_max`, `currency`
- `yoe_required`

**Architecture patterns used:**
- Orchestrator agent + specialized sub-agents (SkillExtractor, SalaryParser, SeniorityClassifier)
- Pre/post hooks for validation, budget enforcement, S3 cache writes
- Rules-first fast path — regex handles ~70% of jobs in <1ms, LLM only for the rest
- 16-thread parallel enrichment via `ThreadPoolExecutor`
- Thread-safe budget tracker with `threading.Lock`
- S3 response cache keyed by JD hash — never pays twice for the same job

**Result:** 3,400 jobs enriched in ~3–5 minutes. Cost capped at $0.50/day.

---

## Match Scoring

Every job is scored 0–100 against a personal profile (`config/user_profile.yml`):

| Signal | Weight |
|--------|--------|
| Skill overlap | 40% |
| Seniority fit | 20% |
| Location fit | 15% |
| Role family | 15% |
| Salary fit | 5% |
| Freshness | 5% |

---

## Data Model

Star schema on S3, queried through Athena:

```
fact_job_posting
    ├── dim_company      (SCD Type 2)
    ├── dim_location     (city · country · timezone)
    ├── dim_role         (role_family · seniority)
    └── dim_date
```

Deduplication: md5 hash on `(company + title + country)` — canonical row keeps earliest post date, tracks all source APIs in an array.

---

## Production Discipline

- **Serverless-first** — EventBridge + Lambda run unattended, no always-on server
- **Idempotent** — all Lambda and Glue jobs safe to rerun without side effects
- **Cost-guardrailed** — S3 lifecycle rules, Athena 1 GB scan cap, AWS Budgets alerts at $0.01 / $1 / $5
- **Monitored** — CloudWatch Logs + Alarms → SNS email on any pipeline failure
- **Tested** — 87 unit tests, all mock-based, no real AWS or API calls in CI

---

## Repo Structure

```
├── ingestion/sources/     # one folder per API (remotive, arbeitnow, adzuna, ...)
├── transform/spark/       # Glue bronze → silver Spark job
├── transform/dbt/         # gold layer — star schema models + dbt tests
├── genai/                 # enrichment agent, skill extractor, match scorer
├── config/                # user_profile.yml, aws_config.yml
├── terraform/envs/dev/    # all AWS infra as code
├── tests/                 # 87 unit tests
└── docs/                  # architecture decisions, runbook, incident log
```

---

## What's Coming

- **Semantic search** — JD embeddings via Claude API + cosine similarity, no managed vector DB
- **"Why this job" explanations** — per-result generated by Claude
- **More sources** — Greenhouse, Lever, USAJobs, Reed.co.uk (~15K–20K jobs/day at full scale)
- **CI/CD** — GitHub Actions lint → test → deploy
- **Salary arbitrage view** — same role across countries, cost-of-living normalized

---

*Built as a personal learning project — DE + GenAI + AWS, end-to-end, from scratch.*
