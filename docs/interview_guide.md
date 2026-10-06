# JobPulse — Interview Guide

> My ready-to-go interview prep for JobPulse. Simple words. Every answer should be sayable **out loud in
> under 90 seconds, with one real number**.
>
> **Last updated:** Chat 23 (2026-10-06)
> **Pipeline status:** paused since 2026-09-19 (nightly schedule disabled). Resumes in Chat 25.
>
> Legend: ✅ built and ran in production · 🔜 planned (chat number) · ⚠️ known weakness — say it before they find it

---

## 0. How to use this file

**Night before an interview:** read §1 (pitch), §2 (story), §3 (pipeline), §9 (stories), §12 (questions).
**Before an AI-focused round:** add §7 (AI theory).
**Before an AWS/system-design round:** add §4 (layers) and §5 (AWS services — why not the alternatives).

**Update rules (Claude does this at the end of every chat):**

| When this happens in a chat | Update these sections |
|---|---|
| Every chat | §3 pipeline + change log, §10 numbers, §14 timeline, "Last updated" line |
| New AWS service or a service replaced | §5 (service table: why this, why not the popular alternative) |
| New design pattern / pattern changed | §4, §6 |
| New AI concept built or measured | §7 (flip 🔜 to ✅, add the real number) |
| New bug / incident | §9 if it is a good story (problem → cause → fix → rule) |
| A weakness fixed or found | §11 honest limits |
| New measured result | §13 resume bullets |

---

## 1. The 30-second pitch

> "JobPulse is a job-market data pipeline I built on AWS. Every night it pulled about 7,000 job postings
> from four public job APIs, cleaned them with Spark, checked data quality, modelled them into a star schema
> with dbt on Athena, and then used an LLM — Claude Haiku — as a parser to pull skills and seniority out of
> each job description and score how well each job fits my profile. It also turns every description into an
> embedding, so I can search jobs by meaning, not keywords. It ran unattended for about three months.
> What I care about most is the engineering around the AI: rules before the LLM, caching by content hash,
> a daily cost cap — and the part most AI projects skip, which I'm building now: measuring whether the
> search and the extraction are actually right."

## 2. The 2-minute story — follow one job posting

Example: a "Platform Engineer – Real-time" posting on a company's Greenhouse job board. Its text says
"build event-driven pipelines on Kafka". Its title never says "data engineer".

1. **2:00 AM IST — the schedule fires.** EventBridge starts a Step Functions workflow. Four Lambda functions
   run **in parallel**, one per source (Remotive, Arbeitnow, Adzuna in 7 countries, Greenhouse for 30
   companies). The Greenhouse Lambda fetches our posting and writes the whole day's batch as one gzipped JSON
   file to S3 **bronze**: `snapshot_date=2026-07-15/source=greenhouse/data.json.gz`. The date is computed in
   **India time** — computing it in UTC once wrote a whole day into yesterday's folder (story 6).
2. **Silver — clean and shape.** A Glue Spark job reads all four sources, maps their different field names
   into one schema (`COALESCE`), works out `country` and `role_family`, and writes Parquet partitioned by
   `snapshot_date / country / role_family`. If the same job came from two sources it gets `source_count = 2`.
3. **Quality gate.** Great Expectations checks the new silver data: `job_id` and `title` not null, at least
   100 rows. If it fails, the workflow stops **before** gold is touched.
4. **Gold — star schema.** dbt runs on Athena and builds `fact_job_posting` plus `dim_company`, `dim_role`,
   `dim_country`.
5. **Enrichment — LLM as a parser.** Regex looks for known skills (`kafka`, `spark`, `python`…) and a
   seniority word. If it finds ≥5 skills and a seniority, it's done — **no LLM call**. If not, Claude Haiku
   reads the description and returns JSON; Pydantic validates it; skills are filtered through a whitelist;
   the result is cached by the md5 of the description, so the same description is never paid for twice.
   Then a `match_score` 0–100: 50% skills (core skills count 3×), 10% seniority, 15% location, 15% role,
   5% salary, 5% freshness.
6. **Embedding.** Voyage AI turns the description (first 4,000 characters) into 512 numbers, saved as
   Parquet in S3.
7. **Search.** On the dashboard I type "real-time pipelines with Kafka". My text becomes 512 numbers, gets
   compared with every job's numbers, and the top 50 are blended 50/50 with `match_score`. Our posting shows
   up **even though no keyword in its title matches**. That's semantic search.

## 3. The full pipeline (UPDATE EVERY CHAT)

### Current state (Chat 23)

```
EventBridge  cron(30 20 * * ? *) = 2:00 AM IST                     [DISABLED since 2026-09-19]
 └─ Step Functions (STANDARD)  jobpulse-ingest-pipeline-dev
     ├─ ParallelIngest   Lambda ×4: remotive | arbeitnow | adzuna | greenhouse     ✅
     │                   → S3 bronze  raw JSON.gz, deleted after 7 days
     ├─ RunGlueJob       Glue Spark 4.0, G.1X × 2 workers                          ✅
     │                   → S3 silver  Parquet, snapshot_date / country / role_family
     ├─ RunDataQuality   Glue Python Shell + Great Expectations (5 checks)         ✅ ⚠️ 2 checks can't fail
     ├─ RunDbtGold       Glue Python Shell + dbt-athena → gold star schema          ✅ ⚠️ dbt tests not run
     ├─ RunEnrichment    Glue Python Shell: rules → Claude Haiku → match_score      ✅ ⚠️ timed out from Aug
     ├─ EmbedJDs         Glue Python Shell: Voyage voyage-4-lite, 512-d → Parquet   ✅ ⚠️ re-embeds every job daily
     └─ PipelineComplete / PipelineFailure → CloudWatch alarm → SNS email           ✅ ⚠️ alarm self-resets
Dashboard   Streamlit — Athena query + NumPy cosine search + Claude "why these match"
            (was EC2 t3.micro; now runs locally on demand)                ⚠️ "why" gets no JD text
IaC         Terraform, S3 remote state + DynamoDB lock                              ✅
CI/CD       GitHub Actions: ruff + pytest on every push → deploy to AWS on push to dev  ✅
```

### Target state (after Chat 32)

```
… same ingestion / silver / quality / gold, plus:
 ├─ RunEnrichment   incremental (only new JDs), batch API, measured vs 150 labelled JDs     🔜 25, 31
 ├─ EmbedJDs        incremental, model-versioned, freshness (open postings only)            🔜 30
 ├─ LoadVectors     → pgvector (RDS) or S3 Vectors — chosen by eval                         🔜 29
 └─ WeeklyBrief     SQL facts → Claude → faithfulness check → dashboard / email              🔜 32
eval/               frozen corpus + ~50 labelled queries + harness (precision@k, nDCG, recall)   🔜 27, 28
Health metrics      ingested/day, truncation %, embed $, retrieval p95, eval score over time  🔜 30
Alarm               "no successful run in 24 h" + "duration > 70% of timeout"                  🔜 25
```

### Change log (one line per chat)
- **Chat 23:** no pipeline change. Recon of the Aug–Sep failures, AWS stays (paid), docs overhaul.

---

## 4. Layer by layer — what, why, and why not the alternative

**Ingestion — one Lambda per source.** ✅
- *What:* each Lambda fetches one API, maps fields to a common shape, writes one gzipped JSON file per day.
- *Why:* each fetch takes seconds, once a day. Lambda bills per millisecond and has nothing to keep running.
  One file per source means a new source = one new Lambda + one new branch; nothing else changes.
- *Why not one big ingestion script on EC2:* a server idling 23 h 59 min a day, and one broken source would
  block all the others.
- *Rules I learned:* test the API **from a Lambda** before writing code — Himalayas and RemoteOK block AWS IP
  addresses (Cloudflare). One country or company failing must not kill the run (per-country isolation,
  404 = skip).
- ⚠️ No retries on the Lambda steps; no dead-letter queue.

**Orchestration — EventBridge + Step Functions (STANDARD).** ✅
- *What:* EventBridge fires at 2 AM IST; Step Functions runs the steps in order, ingestion in parallel.
- *Why STANDARD not EXPRESS:* runs once a day, needs a full per-step history for debugging; EXPRESS is for
  high-volume, short (≤5 min) workflows.
- *Why `.sync` Glue integration:* Step Functions waits for the Glue job itself — I wrote no polling code.
- ⚠️ Every step has a `Catch` (go to failure) but **no `Retry`**. A network blip fails the whole night.

**Bronze — raw JSON in S3.** ✅
- *Why keep raw:* if a parsing rule is wrong, I can re-run silver from the original data.
- *Why gzip:* ~5× smaller.
- ⚠️ Kept only 7 days — when the `tags` field broke in August, the raw files had expired before I looked
  (story 4). Retention should cover your debugging time, not just your storage budget.

**Silver — Glue Spark, Parquet, partitioned.** ✅
- *Pattern:* normalize field names (`COALESCE`), derive `country` and `role_family`, write with **dynamic
  partition overwrite** (a rerun replaces only that day's folders → idempotent).
- *Why partition by `snapshot_date / country / role_family`:* the dashboard and dbt filter on these, so
  Athena reads only the matching folders (partition pruning). `state` is **not** a partition — too many values,
  too many tiny files.
- ⚠️ Spark *infers* the JSON schema. When one source sent `tags` as a string instead of a list, inference
  changed the type and the job failed for 6 nights (story 4). Explicit schema is planned (Chat 25).

**Quality — Great Expectations, as its own step.** ✅
- *Why its own step:* "transform crashed" and "data is bad" are different failures; separate step = separate log.
- *Why between silver and gold:* stop bad data **before** it spreads. A check after gold is a post-mortem.
- ⚠️ 2 of the 5 checks can never fail (the runner sets `snapshot_date` itself before checking it). Fix in Chat 25.

**Gold — dbt on Athena.** ✅
- *What:* a staging view and 4 tables (`fact_job_posting` + 3 dimensions), md5 surrogate keys, 21 tests.
- *Why dbt:* SQL in git, `ref()` builds the dependency order, tests and docs next to the model.
- *Why enrichment results are NOT in a dbt model:* dbt rebuilds tables (CTAS) on every run; scores written
  into the fact table would be wiped. So `enrichment_scores` is a separate table that dbt doesn't own; the
  dashboard joins them.
- ⚠️ The runner calls `dbt run`, not `dbt build` → the 21 tests never ran in production (Chat 25).

**Enrichment — rules first, LLM second.** ✅
- See §7.1. Cheap path first, cache by content hash, whitelist, Pydantic, $0.50/day cap, 16 threads.
- ⚠️ Never measured: is the LLM more accurate than the regex? (Chat 31). Budget check is not atomic across
  threads. Timed out every night from August (story 2).

**Embeddings + search.** ✅ (basic)
- See §7.2–7.6. Voyage 512-d, Parquet in S3, NumPy cosine, top-50 blended 50/50 with match_score.
- ⚠️ No evaluation, no keyword/hybrid search, no filters, re-embeds every job daily, only the latest day is
  searched. The 50/50 blend and k=50 were guesses.

**Dashboard — Streamlit.** ✅
- One cached Athena query, all charts computed in pandas (re-querying Athena on every click is slow and costs money).
- Was on EC2 t3.micro (free for 6 months). Now runs locally on demand — the *pipeline* must work with my
  laptop off; the *dashboard* only needs to exist while I'm looking at it.

**IaC + CI/CD.** ✅
- Terraform with remote state in S3 + DynamoDB lock (local state + git worktrees = drift; story 10).
- CI: ruff + 228 test functions, all external calls mocked, **no AWS credentials in CI**.
- Deploy workflow re-runs the tests itself before deploying (never trusts another workflow's result).

**Monitoring.** ✅ ⚠️
- CloudWatch alarm on `ExecutionsFailed ≥ 1` → SNS email. It fired every night in Aug–Sep — and reset itself
  to OK 15 minutes later each time, so it read like a blip (story 3). Planned: alarm on **absence of
  success** and on **duration vs timeout**.

---

## 5. AWS services — what I use, and why NOT the popular alternative

> Interview rule: never say "X is better". Say "for **this** workload (size, frequency, users, budget),
> X fits because…, and I'd switch to Y when…".

### The workload in one line
~7–8K postings/day · one run per night · <200 MB of clean data · one user · near-zero budget · laptop off.

| Need | I use | Popular alternative | Why not the alternative (for this workload) |
|---|---|---|---|
| Run ingestion code | **Lambda** | EC2, ECS Fargate | Each fetch takes seconds, once a day. Lambda: no server, pay per ms. Lambda's 15-min limit is fine (largest ingestor ~12 s after parallelizing). |
| Schedule | **EventBridge rule** | cron on an EC2 box | No machine to keep alive; free. |
| Orchestrate steps | **Step Functions** | **MWAA (managed Airflow)**, Glue Workflows | MWAA's smallest environment runs 24/7 and costs hundreds of $/month (approx.) — for a 7-step daily workflow Step Functions costs cents and calls Lambda/Glue natively. I use Airflow (Composer) at work, so this was a cost choice, not a skill gap. Glue Workflows only chain Glue jobs; I also needed Lambda. |
| Heavy transform (Spark) | **Glue Spark** | EMR, EMR Serverless, Databricks | EMR = cluster to manage. EMR Serverless is a fair alternative; Glue won on built-in Data Catalog + simpler IAM. Honest: at <200 MB/day Spark is more than needed — DuckDB could do it. Databricks = separate vendor (covered in my ledgerline project). |
| Light Python jobs (dbt, GE, LLM, embeddings) | **Glue Python Shell** (1/16 DPU) | Lambda, AWS Batch, ECS task | Lambda stops at 15 min; enrichment ran ~50 min. Python Shell is cheap and runs for hours. Cost of this choice: Python 3.9 and pip conflicts (story 8). |
| Storage | **S3** (bronze/silver/gold/archive) | EFS, RDS | Cheapest durable storage; Parquet on S3 can be queried in place; lifecycle rules (bronze 7 d, silver → Standard-IA 30 d, archive → Glacier Instant Retrieval 180 d). |
| SQL over the data | **Athena** | **Redshift** | See the deep-dive below. |
| Table metadata | **Glue Data Catalog** + `MSCK REPAIR TABLE` | Glue Crawler, partition projection | Crawler costs per run and adds minutes; MSCK is free and idempotent at my size. Partition projection (no repair at all) is the better future answer. |
| SQL transforms | **dbt-core** (Athena adapter) | hand-written CTAS/views, Glue SQL | Tests, docs, lineage, dependency order, code review in git. |
| Secrets | **Secrets Manager** (Anthropic, Voyage) · Lambda env vars (Adzuna) | **SSM Parameter Store** | Honest: Parameter Store (standard tier, free) would fit; Secrets Manager (~$0.40/secret/month) earns its price when you need automatic rotation. Classic SAA exam trap. |
| Alerts | **CloudWatch alarm → SNS email** | EventBridge rule on Step Functions status → SNS; Datadog | Built-in and free. The lesson was alarm *design*, not the tool (story 3). |
| Infrastructure as code | **Terraform** | CloudFormation, CDK | Same tool works on GCP (my day job); `plan` shows the diff. CloudFormation's advantage: AWS keeps the state, no state file to manage. |
| Terraform state lock | **S3 + DynamoDB table** | Terraform Cloud; S3-native lock file (newer Terraform) | Free, in my own account. Newer Terraform versions can lock with the S3 bucket alone — DynamoDB locking is becoming the legacy way. |
| Dashboard hosting | was **EC2 t3.micro + Elastic IP**, now local | Fargate, App Runner, QuickSight | EC2 was free for 6 months and good SAA practice. Fargate ~$15+/month; QuickSight is per-user. Now local: the dashboard only needs to run while I'm looking at it. |
| Embedding model | **Voyage AI** (external API) | **Bedrock** Titan Text Embeddings v2 / Cohere | Voyage: free tokens + simple SDK. Bedrock: IAM auth (no API key), stays inside AWS, one bill. I'll test Titan as the challenger in Chat 30. |
| LLM | **Claude API direct** (Haiku 4.5) | **Claude on Bedrock** | Direct = API key in Secrets Manager. Bedrock = IAM auth, CloudWatch metrics, VPC endpoints, one AWS bill. For an AWS-first design Bedrock is arguably better; I started direct for simplicity. |
| Vector search | **NumPy in memory** (vectors in S3 Parquet) | OpenSearch Serverless, pgvector (RDS/Aurora), **S3 Vectors**, Pinecone | At ~8K × 512, brute force is exact and <1 s. OpenSearch Serverless has a minimum capacity that bills 24/7 (well over $100/month) — wrong for one user. pgvector vs S3 Vectors: decided by eval in Chat 29. |
| Streaming (Kinesis / MSK) | **not used** | Kinesis, MSK | Sources are daily REST APIs; nothing arrives as a stream. Batch is the right shape. (Streaming lives in my ledgerline project.) |

### Deep-dive: why Athena and not Redshift

- **What Athena is:** serverless SQL directly on files in S3. You pay per data scanned (~$5 per TB). No servers.
- **What Redshift is:** a columnar data warehouse. Data is loaded into it (or read from S3 via Spectrum). You pay
  for compute that is running — nodes per hour (provisioned) or capacity-hours while active (Serverless, with a
  minimum base capacity).
- **My workload:** silver is 179 MB in total, one dbt run per night, a dashboard opened weekly. With Parquet +
  partitions each Athena query scans megabytes → fractions of a cent. A workgroup cap of 1 GB per query stops a
  runaway query.
- **When I would switch to Redshift:** many people running BI queries all day, sub-second dashboards on hot data,
  heavy repeated joins, workload management, materialized views — at a volume where paying for running compute
  is cheaper than paying per scan.
- **Redshift Spectrum:** lets an existing Redshift cluster query S3 — only makes sense if you already have Redshift.
- **Vectors:** Redshift has no approximate-nearest-neighbour index, so it doesn't help the AI side either.
- **One line to say:** *"Athena is pay-per-query over S3 — perfect for small, spiky workloads with one user.
  Redshift is a warehouse you keep running — it wins when many people query hot data all day."*
- **SAA trap:** "ad-hoc SQL on data in S3, no infrastructure" → Athena. "Complex BI, many concurrent users,
  large warehouse" → Redshift. "Query S3 from an existing Redshift" → Spectrum.

### Deep-dive: why Step Functions and not Airflow (MWAA)
- Airflow is the industry default and I use it at work. For **this** project: one run per day, 7 steps, AWS
  services only. Step Functions: no environment to run, pay per state transition (~a few cents a month),
  `.sync` integration with Glue, visual execution history.
- I'd pick Airflow when: many DAGs, cross-cloud tasks, backfills over date ranges, sensors on external
  systems, a team that already lives in Airflow.

---

## 6. Design patterns interviewers ask about — with JobPulse examples

**Medallion (bronze / silver / gold).** ✅ Raw as received → cleaned and typed → modelled for questions.
Why: you can rebuild any layer from the one below. JobPulse: JSON.gz → partitioned Parquet → star schema.

**Idempotency (safe to re-run).** ✅ Same input + re-run = same result, no duplicates.
JobPulse: dynamic partition overwrite (rerun replaces only that day); dbt CTAS rebuilds tables; the LLM cache
means a rerun doesn't pay again; `snapshot_date` comes from the event or IST clock, so a re-run of a given date
lands in the same folder. *Follow-up they ask:* "what if the job runs twice?" → same folders overwritten.

**Partitioning and pruning.** ✅ Split data into folders by columns you filter on, so queries skip folders.
JobPulse: `snapshot_date / country / role_family`. *Trap:* high-cardinality partitions (e.g. `state`) create
thousands of tiny files — kept as a normal column instead.

**Star schema, grain, surrogate keys.** ✅ Facts (events) + dimensions (descriptions).
JobPulse: grain = **one row per posting per snapshot_date**. Keys = `to_hex(md5(to_utf8(...)))` (Athena's
`md5` needs bytes). *Lesson:* the key must cover every column that defines the dimension's grain — `dim_role`
keyed on `role_family` alone produced duplicates; it needed `(role_family, category)`.

**Slowly Changing Dimensions (SCD).** ⚠️ Not built. `dim_company` is rebuilt every run with current values
(effectively Type 1). *How I'd do Type 2:* a dbt snapshot on company attributes → `valid_from`, `valid_to`,
`is_current`; facts join on the version valid at `snapshot_date`. *When it matters here:* company renames.

**Deduplication.** ✅ partial.
- Same job from two sources on the same day: a cross-source key `md5(company | title | country)` →
  `source_apis[]`, `source_count`.
- ⚠️ Not built: the same job across days (`canonical_job_id`), near-duplicates (embedding similarity).
- *Lesson (story 1):* never build a dedup key from a field with a default like `"Unknown"`.

**Schema drift.** ✅ partial. Normalize in each ingestor; `COALESCE` in Spark for names that differ by source.
⚠️ Types are inferred, so a type change breaks the job (story 4). Planned: explicit schema + a type check.

**Quality gates (fail fast).** ✅ GE between silver and gold; dbt tests on models. GE checks *files* before
modelling; dbt tests check *models*. ⚠️ see §11.

**Fan-out / fan-in.** ✅ Step Functions `Parallel` runs 4 ingestors at once; total time = slowest branch.
Trade-off: if any branch fails, the whole state fails. Inside a source: per-country isolation, 404 = skip.

**Cheap path first (cascade).** ✅ Rules handle most JDs in <1 ms; only ambiguous ones go to the LLM. This took
enrichment from ~57 min (timeout) to ~3–5 min for 3,400 jobs in April.

**Content-hash cache.** ✅ Key = md5 of the description. Same description on another day → no new LLM charge.

**Budget guard (circuit breaker).** ✅ $0.50/day cap, ledger in S3, falls back to rules when exceeded.
⚠️ "Check, then spend" is not atomic across 16 threads — they can all pass the check together.

**Separate ownership of tables.** ✅ dbt owns the star schema; the enrichment job owns `enrichment_scores`.
Neither overwrites the other.

**Config as data.** ✅ `user_profile.yml` in S3; changing my skills = upload a file + `--force_rescore`
(re-score from cache, $0 LLM spend), no code deploy.

**Infrastructure as code + remote state + locking.** ✅ Everything in Terraform; state in S3, lock in DynamoDB.

**CI gate before deploy; no credentials in CI.** ✅ Every external call mocked; deploy re-runs tests itself.

**Least privilege.** ✅ The ingestion Lambda role can write only to bronze. The dashboard used an instance
profile (temporary credentials), never stored keys. ⚠️ The Adzuna key was committed in `terraform.tfvars` (story 12).

**Business-time dates.** ✅ `snapshot_date` in IST, because EventBridge schedules are in UTC (story 6).

**Batch vs streaming.** Batch here: sources are daily REST APIs; the questions are daily/weekly. Streaming
adds cost and moving parts with no benefit.

**Alerting on absence of success.** 🔜 "No successful run in 24 h" catches failures *and* runs that never
started; "failed ≥ 1" alarms can reset themselves and be ignored (story 3).

---

## 7. AI in data engineering — theory in simple words

### 7.0 The translation table — you already know most of this

| AI term | The data-engineering thing you already know |
|---|---|
| LLM extraction | A parser / transformation step: messy text in, typed columns out |
| Embedding | A transformation: text column → `array<float>` column |
| Changing the embedding model | A schema migration + full backfill |
| Vector database / HNSW index | A table with a special index for "closest" instead of "equal" |
| Chunking | Choosing the grain |
| Re-embedding only changed documents | Incremental load |
| Retrieval evaluation (precision@k) | Data-quality tests, but for search results |
| RAG | ETL where the "BI tool" at the end is an LLM |
| Logging prompt + retrieved context | Lineage / audit log |

> The two problems all of this solves: **(1)** computers match *words*, not *meaning*;
> **(2)** an LLM doesn't know *your* data, and makes things up when asked about it.

### 7.1 LLM as a parser (structured extraction) ✅
- **Simple words:** give the LLM a messy job description, ask for JSON: skills, seniority, years of experience.
- **Why:** regex can't read "you'll own our event bus" as "Kafka".
- **JobPulse:** regex first; Claude Haiku only when regex finds <5 skills or no seniority; Pydantic validates
  the JSON; skills filtered through a whitelist (no invented skills); retries with backoff; on any failure,
  fall back to the regex result — never crash the batch; cache by md5 of the description.
- **Trap:** "how do you know the LLM is right?" — honest: not measured yet → Chat 31 (150 hand-labelled JDs,
  precision/recall/F1 per field, rules vs rules+LLM, $ per 1K JDs).

### 7.2 Embeddings ✅
- **Simple words:** a model reads text and outputs a list of numbers (a *vector*) — like GPS coordinates for
  meaning. Similar meaning → nearby coordinates.
- **JobPulse:** Voyage `voyage-4-lite`, 512 numbers per job description.
  "Kafka streaming engineer" lands near "event pipeline developer", far from "pastry chef".
- **DE view:** costs money per token, has rate limits, and **a new model = a new coordinate system**: every
  old vector must be recomputed. That's a schema migration (🔜 Chat 30: versioned index + eval gate).

### 7.3 Cosine similarity ✅
- **Simple words:** compare two vectors by the *angle* between them. ≈1 = same direction (same meaning),
  ≈0 = unrelated.
- **JobPulse:** `genai/semantic_search.py` normalizes all job vectors, does one matrix multiply with the query
  vector, and keeps the top 50.

### 7.4 Vector database and the ANN index 🔜 Chat 29
- **Simple words:** a database that stores vectors and finds the nearest ones *without* checking every row —
  like a B-tree index, but for "closest".
- **ANN = approximate nearest neighbour.** Trades a little accuracy (*recall*) for a lot of speed.
  - **HNSW:** a layered graph; the search hops from far to near. Knobs: `m`, `ef_construction`, and
    `ef_search` (higher = more accurate, slower). Most common.
  - **IVFFlat:** groups vectors into clusters; the search only looks in the nearest few clusters (`probes`).
- **Filters:** "only India, only senior" — pre-filter (filter then search) vs post-filter (search then filter;
  can return fewer than k results).
- **JobPulse today:** no vector DB — 8K vectors, exact search in <1 s, which is correct at this size. What a DB
  would add: filters, keyword + vector in one query, no full-file download per search.
- **Chat 29 plan:** pgvector on RDS vs S3 Vectors, measured against exact NumPy search as the ground truth
  (recall@10, nDCG@10, p95 latency, $/month).
- **Trap:** "Pinecone vs pgvector?" → answer in terms of scale, filters, ops, cost, team — not brand.
  pgvector: SQL, joins, filters, hybrid search in one place; you run Postgres. Managed services: less ops, more $,
  vendor lock-in.

### 7.5 Chunking 🔜 Chat 28
- **Simple words:** choosing **what one vector represents** — the grain. One vector for a whole document blurs
  every topic into one point; splitting it into pieces gives each piece a sharp point.
- **JobPulse today:** one vector per job, text cut at 4,000 characters — the end of long JDs (often the
  requirements) is thrown away. Never measured.
- **Plan:** compare whole-JD vs section chunks (responsibilities / requirements / benefits) on the eval set.
- **Trap:** "what chunk size?" → "depends on the documents; I measured it" beats "500 tokens with 100 overlap".

### 7.6 Keyword search (BM25) and hybrid search 🔜 Chat 28
- **BM25, simple words:** classic keyword scoring — rare words count more, repeating a word helps less and less,
  long documents are normalized.
- **Why hybrid:** vectors are weak at exact terms. To an embedding model "dbt", "Flink" or an error code are
  just short tokens; keyword search finds them exactly. Job descriptions are full of tool names — a perfect
  hybrid case.
- **Reciprocal Rank Fusion (RRF):** merge two ranked lists by rank, not score: `score = Σ 1 / (60 + rank)`. No need
  to make the two scores comparable.
- **JobPulse today:** vector only, blended 50/50 with `match_score` — a guess.

### 7.7 Reranking 🔜 Chat 28 (optional)
- **Simple words:** take the top 50 from the fast search, let a slower, smarter model re-order them, keep 10.
- **Cost:** extra latency + money per query → keep only if the eval says it helps.

### 7.8 Query rewriting 🔜 Chat 28 (optional)
- An LLM turns a vague query ("remote DE jobs good for me") into a better search text plus filters
  (`{country: remote, role: DATA}`). Keep only if the eval improves.

### 7.9 RAG (Retrieval-Augmented Generation)
- **Simple words:** (1) find the relevant data, (2) paste it into the prompt, (3) let the LLM answer **using
  only that**. Fixes "the LLM doesn't know my data" without retraining it.
- **The 7 steps:** ingest → chunk → embed → index → retrieve → augment (build the prompt) → generate.
  Steps 1–5 are data engineering. The LLM is the last ~10%.
- **JobPulse mapped to the 7 steps:**

| Step | JobPulse today | Gap → chat |
|---|---|---|
| 1 Ingest | 4 Lambda ingestors, metadata in silver | drift handling, no dead-letter queue → 25 |
| 2 Chunk | one vector per JD, 4,000-char cut | grain never measured → 28 |
| 3 Embed | Voyage, batches of 128, 3 retries | re-embeds every job daily, no model version → 30 |
| 4 Index | Parquet in S3, no index | pgvector vs S3 Vectors → 29 |
| 5 Retrieve | vector top-50 + 50/50 blend | no BM25 / filters / rerank / eval → 27–28 |
| 6 Augment | top-3 jobs sent to Claude for "why these match" | ⚠️ **job text never passed in** (story 5) → 25 |
| 7 Generate | short Haiku explanations | no logging of prompt + context + tokens → 32 |

- **Variants:** naive (one search, stuff, answer) → advanced (+ hybrid, rerank, query rewriting) → agentic
  (LLM decides to search again / call tools) → graph RAG (knowledge graph of entities). **Start naive, measure,
  climb only where the eval shows a gap.** JobPulse doesn't need agentic or graph RAG: trend questions are
  answered by SQL, which is more reliable than retrieval.
- **RAG vs fine-tuning:** RAG first — cheaper, fresh data, citable. Fine-tune only when good retrieval still
  can't hit the quality bar. "We need fine-tuning" is usually a bad retrieval pipeline in disguise.

### 7.10 Evaluation — the part that makes it engineering 🔜 Chat 27–28
- **Simple words:** write down ~50 real queries, mark which jobs are truly relevant for each, measure. Every
  change (model, chunking, blend weights) gets a number instead of a feeling.
- **Golden set / qrels:** the list of (query, job, relevance 0/1/2).
- **Pooling:** you can't label all 8K jobs per query — label the union of the top-20 from several search methods.
- **Metrics:**
  - **precision@k** = relevant results in the top k ÷ k. "Of the 10 I showed, how many were good?"
  - **recall@k** = relevant results in the top k ÷ all relevant ones (in the pool). "Did I miss good ones?"
  - **MRR** = average of 1 / (rank of the first good result). "How fast does the first good one appear?"
  - **nDCG@k** = rewards good results more when they are near the top, supports grades (0/1/2), scaled 0–1
    against the perfect ordering.
- **LLM-as-judge:** an LLM labels relevance with a written rubric — fast, but must be **validated**: hand-label
  ~300 pairs and measure agreement with **Cohen's kappa** (agreement corrected for luck; ≥ ~0.6 is commonly
  treated as good).
- **Freeze the corpus:** if the data changes every night, the metric moves because the *data* changed, not
  because the *search* improved. Eval runs on a pinned, versioned snapshot; the live pipeline keeps running.
- **Offline vs online:** offline = labelled set; online = clicks / applies (I'm the only user, so offline).
- **Trap:** "how did you know your search worked?" Today's honest answer: *"I didn't — the 50/50 blend was a
  guess. That's exactly why I'm building the eval set; here is the number it gave me."* (Fill in after Chat 28.)

### 7.11 Hallucination and grounding
- **Simple words:** the LLM writes something fluent and wrong. Grounding = give it the facts and tell it to
  use only them; check its output against the facts.
- **JobPulse:** "Why these match?" sent Claude the job titles but **not** the descriptions (story 5), so it
  explained matches it never read. Debug order: check the retrieved context first, then retrieval, and only
  then the model.

### 7.12 Prompt injection
- Job descriptions are text written by strangers. A JD could contain "ignore your instructions and…". Treat
  retrieved text as **data**, never instructions; validate output with a schema and whitelist (JobPulse
  already does this for skills). 🔜 Chat 32 adds a test JD with an injected instruction.

### 7.13 LLM cost and latency controls
- ✅ Rules first, LLM only when needed.
- ✅ Cache by content hash (never pay twice for the same description).
- ✅ Cheapest capable model (Haiku), `max_tokens = 300`, 10 s timeout, $0.50/day cap.
- ⚠️ Prompt caching was switched on, but the system prompt is a few hundred tokens — below the minimum
  cacheable length (at least 1,024 tokens on Claude models; higher on Haiku) — so it never cached.
- 🔜 Message Batches API: async, ~50% cheaper — fine for a nightly job that doesn't need answers in seconds.
- ⚠️ Price constants in code are old Haiku 3.5 prices → spend under-counted by ~20%.

### 7.14 Embedding model versioning 🔜 Chat 30
- Vectors from model A and model B can't be compared. Store `model_id` with every vector; new model → new index
  version → backfill → run the eval → switch only if it's better (blue/green). Same discipline as a schema
  migration.

### 7.15 Freshness 🔜 Chat 30
- Job postings close. Track `last_seen_date`; a posting unseen for N days = closed; search only open postings.
  Re-embed only new or changed descriptions (incremental), not all 7.8K every night.

### 7.16 Agents — careful with the word
- **An agent** = an LLM in a loop that decides its next action or tool call until done.
- **JobPulse's `JDEnrichmentAgent`** is an orchestrator class that makes **one** LLM call per job description
  — a pipeline step, not an agent. `SalaryParser`, `SeniorityClassifier`, `DedupAgent` were planned and never built.
- **Say it this way:** "I use agent-style structure — pre/post hooks, guardrails, budget checks — but it's a
  deterministic pipeline with one LLM call per document. I didn't need an agent loop."

### 7.17 Text-to-SQL — be skeptical
- Works on a small, well-documented set of tables; falls apart on large, messy warehouses. The fix is a
  semantic layer, not a bigger model. JobPulse's gold star (4 tables) would be a fair test bed — optional, only
  with measured execution accuracy.

---

## 8. AI layer — status at a glance

| Capability | Status | Number to quote |
|---|---|---|
| LLM extraction (rules → Haiku) | ✅ in production | 57 min → 3–5 min for 3,400 jobs (Apr); $0.50/day cap |
| Match score (6 parts, tiered skills) | ✅ | 50/10/15/15/5/5 weights |
| Embeddings | ✅ | voyage-4-lite, 512-d, 96 daily files (633 MB) |
| Semantic search (NumPy) | ✅ | top-50, 50/50 blend with match_score |
| Retrieval eval | 🔜 27–28 | — |
| Hybrid / rerank / chunk grain | 🔜 28 | — |
| Vector DB (pgvector vs S3 Vectors) | 🔜 29 | — |
| Incremental + versioned embeddings | 🔜 30 | — |
| Extraction eval | 🔜 31 | — |
| Grounded weekly brief | 🔜 32 | — |

---

## 9. Stories — problem → cause → fix → rule

> Template: *"The hardest bug was [X]. Symptoms: [Y]. I first thought [Z], but the root cause was [W].
> Fix: [V]. Now I always [rule]."*

1. **The dedup that ate 99% of the data.** One run: 2,525 jobs in bronze → 24 rows in gold, and every step
   said SUCCEEDED. Adzuna filled missing company names with the string `"Unknown"`; the dedup key was
   `md5(company | title | country)`, so hundreds of different jobs got the same key. Fix: missing = `NULL`, dedup
   key = `md5(source | job_id)`, the cross-source key only for counting. Result: 15,356 jobs visible (was 24).
   **Rule:** never build a key from a field with a default value; always compare row counts between layers.

2. **The job that ran at 90% of its timeout for three months.** Enrichment took 15–20 min in late April. After
   the 4th source was added it took **48–56 min every night against a 60-min timeout** — and still succeeded,
   so nobody looked. As volume grew (6.6K → 7.9K jobs/day) it started timing out (first Jul 25–26, every night
   from mid-August: 29 timeouts). **Rule:** alarm on *duration vs timeout*, not only on failure; process only
   new records (incremental), not everything every night.

3. **The alarm that cried wolf, then stopped being heard.** The failure alarm went to ALARM at ~2 AM and back to
   OK 15 minutes later — every night for weeks. Each email looked like a one-off blip. I disabled the schedule
   on Sep 19 after ~5 weeks of nightly failures that were still billing Glue. **Rule:** alarm on the *absence
   of success* ("no successful run in 24 h"), which stays red until fixed.

4. **The source that changed shape — and the evidence that expired.** Aug 1–9: the Spark job failed with
   "cannot cast string to array<string>" on `tags`. One source started sending `tags` as text instead of a
   list; Spark *infers* JSON types, so the inferred type changed and the cast broke. It recovered on its own
   on Aug 10 — and the raw files had already been deleted by the 7-day bronze lifecycle, so I can't prove which
   source. **Rules:** explicit schema, normalize types in the ingestor; keep raw data at least as long as your
   debugging window.

5. **The LLM that explained jobs it never read.** "Why these match?" sends the top 3 jobs to Claude. The
   dashboard query never selected `description`, so the check for it always failed and the prompt had an
   empty excerpt. Claude still wrote confident explanations. **Rule:** when an AI answer is wrong, look at
   the context that was sent *first* — the model is usually the last suspect.

6. **The data that landed in yesterday.** EventBridge runs at 2 AM IST = 8:30 PM UTC the *previous* day. The
   Lambdas computed `snapshot_date` in UTC, so the Apr 23 run wrote into the Apr 22 folder; every step
   succeeded, the dashboard was just stale. **Rule:** compute business dates in the business time zone;
   schedules are always UTC.

7. **57 minutes → 3 minutes, and why it came back.** At 3,400 jobs enrichment hit the 60-min timeout: it
   checked an S3 cache *before* running free regex, ran one job at a time, and had no API timeout. Fix: regex
   first (most jobs need no LLM), 16 threads, 10 s timeout → 3–5 min. Volume then doubled and it crept back to
   ~50 min (story 2). **Rule:** a performance fix holds only for the volume you tested at.

8. **The pip conflict I couldn't pin my way out of.** On Glue 5.1, every install of dbt / anthropic / pydantic
   failed. I tried pinning older versions — no luck. Cause: Glue 5.1 pre-installs awscli + aiobotocore that lock
   botocore to an old version. Fix: Glue 4.0, plus the last package versions that support its Python 3.9 (dbt
   1.9.x, pyarrow 14.0.2, numpy 1.26.4). **Rule:** the runtime version defines the whole environment; check
   `Requires-Python` and wheel availability before pinning.

9. **"Free public API" that blocks the cloud.** Himalayas and RemoteOK return 403 to AWS IP addresses
   (Cloudflare bot protection) even with no key needed. **Rule:** one test call *from a Lambda* before writing
   an ingestor.

10. **The checkout that would have destroyed production.** In October my local branch was at Chat 9 while
    GitHub was at Chat 22. Terraform state is shared in S3, so `terraform apply` from the old code would have
    planned to delete everything built in Chats 10–22 (EC2, three Lambdas, two Glue jobs…). Caught by
    comparing branches before touching anything. **Rule:** `git fetch` and compare with the remote before any
    `apply`; remote state makes an old checkout dangerous.

11. **The join that silently matched nothing.** Every `match_score` showed −1. The fact table's
    `snapshot_date` was a `date`; the scores table's was a `string`; Athena doesn't cast across types in a join,
    so it matched zero rows — no error. **Rule:** cast explicitly in joins; a suspicious default value is a
    failed join until proven otherwise.

12. **The key in git.** The Adzuna API key sat in `terraform.tfvars`, which an early doc said was gitignored —
    `.gitignore` never had a rule for it; the file was tracked from day one. **Rule:** secrets go in env vars /
    a secret store; check with `git check-ignore -v <file>`, don't trust the docs. (Rotating in Chat 24.)

---

## 10. Numbers to remember (UPDATE EVERY CHAT)

| | |
|---|---|
| Build time | Chats 1–22, Apr 16–26 2026 |
| Sources live | 4 (Remotive, Arbeitnow, Adzuna × 7 countries, Greenhouse × 30 companies) |
| Volume | ~6,600 jobs/run (Apr) → 7,855 (Aug 14) |
| Silver history | 153 daily snapshots (Apr 18 – Sep 19), 179 MB Parquet with full descriptions |
| Embeddings | 512-d, 96 daily files, 633 MB |
| Production run | enrichment SUCCEEDED 115 times; then 29 TIMEOUTs; Step Functions Jul 10–Sep 19: 20 ok / 52 failed |
| Enrichment speed | 57 min (timeout) → 3–5 min for 3,400 jobs (Apr); crept to 48–56 min at 6.6–7.9K jobs |
| Dedup bug | 2,525 → 24 rows; after fix 15,356 jobs visible |
| Tests | 228 test functions (~214 run in CI; 14 PySpark tests skipped there) |
| dbt | 5 models, 21 tests |
| Great Expectations | 5 expectations (3 effective) |
| Match score weights | skills 50 · seniority 10 · location 15 · role 15 · salary 5 · freshness 5 |
| LLM | Claude Haiku 4.5, max 300 output tokens, 10 s timeout, $0.50/day cap, 16 threads |
| Athena guard | 1 GB scan cap per query |
| S3 lifecycle | bronze deleted at 7 days · silver → Standard-IA at 30 days · archive → Glacier IR at 180 days |
| Glue | Spark G.1X × 2 (10-min timeout) · Python Shell 1/16 DPU |
| Incidents logged | 55 in `docs/incidents.md` (Chat 23) |

---

## 11. Honest limits — say them before you're asked

- **No evaluation yet** of search or LLM extraction. The 50/50 blend, k=50 and the 4,000-char cut were guesses.
  (Chats 27–31.)
- **Search is basic:** brute-force NumPy over the latest day only, no keyword/hybrid search, no filters, every
  job re-embedded every night.
- **Quality gates were weaker than they looked:** dbt's 21 tests never ran in the pipeline (`dbt run`, not
  `dbt build`); 2 of 5 GE checks can't fail. (Chat 25.)
- **Data model gaps:** no SCD Type 2; `dim_date`, `dim_location`, `bridge_job_skill`, `fact_hackathon` not built.
- **Dedup:** same-day exact matching only; no cross-day or near-duplicate detection.
- **"Agentic" is a stretch:** one orchestrator class, one LLM call per document, no agent loop.
- **Resilience:** Step Functions catches failures but has no retries; no dead-letter queue.
- **Operations:** it failed nightly for ~5 weeks before I acted — the alarm design was wrong.
- **Scale is small:** thousands of postings a day, not millions. Patterns, not scale, are the point.
- **Security slip:** an API key was committed to git (rotating in Chat 24).
- **Cost:** caps and estimates, but no real per-run cost tracking yet.

---

## 12. Likely interview questions → 90-second answers

**"Walk me through your pipeline."** → §2, the story of one posting. End with one number (e.g. 7,855 jobs a night).

**"Why Athena and not Redshift?"** → §5 deep-dive. *"Pay-per-query over S3 for a small, spiky, one-user
workload; Redshift when many people query hot data all day."*

**"Why Step Functions and not Airflow?"** → §5. *"I use Airflow at work. Here: 7 steps, once a day, AWS only —
Step Functions costs cents and needs no environment. I'd pick Airflow for many DAGs, backfills, cross-cloud."*

**"How do you make the pipeline idempotent?"** → dynamic partition overwrite, deterministic `snapshot_date`,
dbt CTAS rebuild, LLM cache by content hash. A re-run of a date overwrites that date's folders only.

**"How do you handle schema changes from sources?"** → normalize in each ingestor, `COALESCE` for name
differences, and the honest part: story 4 — inferred types broke; the fix is an explicit schema + type checks
+ keeping raw data long enough to debug.

**"How do you deduplicate?"** → same-day cross-source key + `source_count`; story 1 (2,525 → 24); next:
cross-day `canonical_job_id` and embedding-based near-duplicates with a threshold chosen on labelled pairs.

**"How do you control LLM cost?"** → rules first, cache by hash, cheap model, `max_tokens`, daily cap; next:
Batch API (~50% cheaper). Admit the prompt-cache mistake (prompt too short to cache).

**"How do you know the LLM output is correct?"** → Pydantic schema + whitelist stops *invalid* output; it doesn't
prove *correct* output. That's why Chat 31 measures F1 against 150 hand-labelled JDs.

**"How did you know semantic search worked?"** → *"Honestly, I didn't — I eyeballed it. So I built an eval set:
~50 queries, graded labels, LLM judge checked against my own labels with Cohen's kappa. It showed [number]."*
(Fill in after Chat 28.)

**"What is RAG? Is JobPulse RAG?"** → §7.9. Retrieval half yes (ingest → embed → retrieve); the "why these
match" button is a tiny RAG — and story 5 is what happens when the augment step is empty.

**"pgvector vs Pinecone vs OpenSearch?"** → scale, filters, ops, cost, team. At 8K vectors exact search wins;
pgvector gives SQL filters + hybrid in one query; OpenSearch Serverless has a 24/7 minimum cost; I chose by
eval (Chat 29: [result]).

**"What happens when you change the embedding model?"** → it's a schema migration: new index version, backfill,
eval gate, switch (blue/green), keep `model_id` on every vector.

**"What's the hardest bug you've fixed?"** → story 1 (silent data loss) or story 2+3 (silent operational decay).

**"What would you change at 100× scale?"** → incremental everything (no full re-processing); a real vector index;
Step Functions retries + DLQ; partition projection instead of MSCK; Batch API for the LLM; per-source schema
contracts; cost per run on a dashboard.

**"How do you test data pipelines?"** → unit tests with every external call mocked (228), GE on silver, dbt
tests on gold, CI with no credentials, deploy re-runs tests; next: eval sets as regression tests for the AI parts.

**"How do you handle secrets?"** → Secrets Manager for LLM keys, IAM roles / instance profiles instead of stored
keys, no credentials in CI — and story 12 (the key that slipped into git).

**"Why did you use an LLM at all?"** → regex can't read meaning ("own our event bus" = Kafka). But only for
the minority regex can't solve — cost and determinism.

**"What happens if an API is down?"** → per-country / per-company isolation; Step Functions catches the failure
and alerts. Weak spot: no retry, and the `Parallel` state fails if any source fails.

**"Batch or streaming here — why?"** → sources are daily REST APIs, decisions are weekly. Batch.

---

## 13. Resume bullets

**Current resume (checked against the code in Chat 23):**
- "aggregates 6,600+ listings daily across 8 countries" — ✅ (6.6K in Apr, 7.9K by Aug).
- "Modeled listings into analytics tables with dbt and SQL on AWS Athena" — ✅.
- "Claude Haiku (rules-first with LLM fallback) and semantic search via Voyage AI embeddings, cutting
  enrichment time by 95% (57 min to 3 min for 3,400 jobs)" — ✅ true in April; be ready for story 2.
- "198 automated tests" — understated; the code has 228 test functions.
- "Fully automated … minimal manual work" — true for ~3 months; be ready for story 3.
- Great Expectations is only under "Familiar" — ✅ correct until Chat 25 makes it a real gate.
- **Do not add** "orchestrator/sub-agent pattern" — not true in the code.

**Templates to fill after measurement:**
- "Built a retrieval evaluation (≈50 queries, N graded judgments; LLM judge validated against hand labels,
  κ = X); RRF hybrid search raised nDCG@10 from A to B over the hand-tuned blend." (Chat 28)
- "Compared pgvector (HNSW on RDS) and Amazon S3 Vectors against exact search — recall@10, p95 latency,
  $/month; chose Z." (Chat 29)
- "Made embeddings incremental and model-versioned, cutting embedding API calls by ~X%." (Chat 30)
- "Measured rules vs rules + Claude Haiku extraction on 150 labelled JDs: F1 X vs Y at $Z per 1K JDs." (Chat 31)
- "Ran unattended ~3 months; diagnosed a creeping Glue timeout, a source schema change and a self-resetting
  alarm; replaced it with absence-of-success alerting." (Chat 25)

---

## 14. Timeline — one line per chat (UPDATE EVERY CHAT)

| Chat | Date | What happened |
|---|---|---|
| 1 | Apr 2026 | Repo, folder structure, dev → main branching |
| 2 | Apr 17 | Himalayas ingestor; AWS account, IAM user, budgets |
| 3 | Apr 18 | Terraform: 4 S3 buckets, lifecycle rules, Lambda IAM role |
| 4 | Apr 18 | First Lambdas; Himalayas blocked by Cloudflare; Remotive live (23 jobs) |
| 5 | Apr 19 | EventBridge + Step Functions + SNS alarm; Terraform state → S3 + DynamoDB |
| 6 | Apr 19 | Glue Spark bronze → silver, partitioned Parquet |
| 7 | Apr 19 | dbt gold star schema on Athena, 20 tests |
| 8 | Apr 19 | Streamlit dashboard |
| 9 | Apr 19 | GenAI enrichment (rules + Haiku, match score); blocked by Glue 5.1 pip conflicts |
| 10 | Apr 19 | Glue 4.0 fix; full pipeline green end to end |
| 11 | Apr 20 | Arbeitnow; parallel ingestion |
| 12 | Apr 21 | Same-day cross-source dedup |
| 13 | Apr 21 | Adzuna (salary data) |
| 14 | Apr 21 | Enrichment 57 min → 3–5 min (rules first, 16 threads); IST date fix |
| 15 | Apr 24 | Embeddings (Voyage) + semantic search + "why this match" |
| 16 | Apr 24 | 5 critical bugs incl. dedup collapse (2,525 → 24) |
| 17–18 | Apr 24 | Tiered skill scoring, profile rebuild, `force_rescore` |
| 19 | Apr 25 | CI/CD with GitHub Actions |
| 20 | Apr 25 | Great Expectations quality gate |
| 21 | Apr 26 | Dashboard on EC2 |
| 22 | Apr 26 | Greenhouse (4th source) → ~6,600 jobs/run |
| — | Apr 26 – Sep 19 | Unattended production; enrichment creeps to its timeout; nightly failures from August; paused Sep 19 |
| 23 | Oct 6 | Recon of the failures, AWS stays (paid), docs overhaul, this guide, roadmap |
| 24 | | Stabilize (secrets, drift, cost hygiene, backup) |
| 25 | | Fix failures, real quality gates, go live |
| 26–33 | | AI track: concepts lab → eval → retrieval → vector stores → embedding pipeline → extraction eval → weekly brief → resume |
