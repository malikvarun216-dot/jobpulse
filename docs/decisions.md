# Architectural Decisions

## Terraform local backend (Chat 3)
- Used local state for now (terraform.tfstate on disk)
- Will migrate to S3 backend after first apply to avoid state loss
- Trade-off: simple to start, risky if laptop lost

## GLACIER_IR over GLACIER (Chat 3)
- Glacier Instant Retrieval chosen for archive bucket
- Reason: millisecond access vs hours for standard Glacier
- Cost: ~68% cheaper than Standard, no retrieval latency penalty
- Trade-off: slightly more expensive than GLACIER, acceptable for occasional access

## Least-privilege Lambda IAM policy (Chat 3, updated Chat 4)
- Lambda role scoped to bronze bucket only (PutObject + GetObject + PutObjectTagging)
- Cannot touch silver/gold — blast radius limited if Lambda is compromised
- CloudWatch Logs added separately (required for Lambda to emit logs)
- PutObjectTagging added in Chat 4: required when using Tagging param in put_object (separate IAM action from PutObject)

## archive_file over manual zip (Chat 4)
- Terraform archive_file data source zips the handler automatically on apply
- No build script needed — zip is regenerated whenever source_code_hash changes
- Trade-off: zip lives in terraform/builds/ (gitignored), must be present at apply time

## Remotive over Himalayas as first live source (Chat 4)
- Himalayas added Cloudflare bot protection — blocks all non-browser clients including Lambda
- Remotive is genuinely open (no key, no Cloudflare, ToS allows max 4 req/day)
- Himalayas Lambda kept deployed — re-enable if they open the API again
- Pattern established: one Lambda file per source, reuse same IAM role

## S3 backend + DynamoDB locking for Terraform state (Chat 5)
- Migrated from local tfstate file → S3 bucket (jobpulse-tfstate-dev) with versioning + AES256
- DynamoDB table (jobpulse-tfstate-lock) added for state locking — prevents concurrent apply corruption
- Bootstrap bucket created via AWS CLI (not Terraform) — Terraform can't manage its own backend bucket
- Trade-off: adds 2 resources outside Terraform management, but eliminates the worktree tfstate copy problem permanently
- Any worktree, any machine: terraform init pulls real state from S3 automatically

## Step Functions STANDARD over EXPRESS (Chat 5)
- STANDARD: exactly-once execution, full audit trail per state, max 1 year duration, 4K free transitions/mo
- EXPRESS: at-least-once, max 5 min, cheaper at high volume but less visibility
- Choice: STANDARD — pipeline is low-frequency (1/day), audit trail matters for debugging failures

## Direct Lambda ARN in Step Functions Task (Chat 5)
- Used aws_lambda_function.remotive.arn directly as Resource in Task state
- Response is the raw Lambda return dict (e.g. {"status": "OK", "record_count": 21})
- Alternative: arn:aws:states:::lambda:invoke SDK integration — wraps response in {"Payload": ...}
- Choice: direct ARN — simpler, fewer characters in state machine, $.remotive.status works cleanly

## Lambda sizing: timeout=300/memory=256 (Himalayas), timeout=60/memory=128 (Remotive) (Chat 4)
- Himalayas: 300s timeout for potential multi-page pagination; 256 MB for larger payloads
- Remotive: single request, 23 jobs — 60s/128MB is sufficient, keeps cost minimal

## Silver partition columns frozen at snapshot_date / country / role_family (Chat 6)
- Adding partition columns later requires full historical rewrite — design right once
- state (Karnataka, Maharashtra, etc.) is a regular column, not a partition key
- Reason: too high cardinality (35 states × N countries × dates = thousands of tiny files)
- Dashboard filter WHERE country='IN' AND state='Karnataka' works via Athena predicate pushdown on non-partition columns — slightly slower than partition pruning but acceptable at our scale
- location_raw kept in silver raw for future city-level extraction in v2

## Dynamic partition overwrite for Glue job (Chat 6)
- spark.sql.sources.partitionOverwriteMode = dynamic on the SparkSession
- Ensures reruns only replace the partitions being written, not the entire silver bucket
- Without this, mode("overwrite") + partitionBy wipes ALL existing partitions on every run
- Critical for idempotency: same snapshot_date re-run only touches that date's partitions

## dbt-athena-community is pip-only — not on dbt Hub (Chat 7)
- dbt Hub (packages.yml) is for macro/model packages like dbt_utils, not adapter plugins
- Adapters (dbt-athena-community, dbt-bigquery, etc.) are pip packages installed separately
- packages.yml is correct to have no entries until we add macro packages
- In Glue: `--additional-python-modules` handles the pip install automatically

## Surrogate key via to_hex(md5(to_utf8(...))) not md5(varchar) (Chat 7)
- Athena's md5() function requires varbinary input, not varchar
- Pattern: `to_hex(md5(to_utf8(lower(trim(col)))))` produces a 32-char hex string
- Same hash function must be used in both the dim model and the fact join condition
- Normalization: lower() + trim() before hashing ensures casing/spacing variants merge

## dim_role key on (role_family, category) not role_family alone (Chat 7)
- Silver has distinct role_family + category combos (e.g., SDE+Software Dev, SDE+Engineering)
- Keying only on role_family produced duplicate role_key values → uniqueness test failure
- Fix: composite key = concat(role_family, '|', coalesce(category, ''))
- Fact join must use the same composite key expression

## MSCK REPAIR TABLE before dbt run (Chat 7)
- Glue Spark job writes Hive-partitioned Parquet but does NOT register partitions in Glue catalog
- Athena can see the S3 files but queries return 0 rows until partitions are registered
- Fix: run MSCK REPAIR TABLE silver_jobs in the dbt_runner.py before dbt run
- Alternative considered: Glue crawler (adds latency + cost); partition projection (complex config)
- MSCK REPAIR TABLE is idempotent, fast for small datasets, and zero extra AWS cost

## localtimestamp over current_timestamp in Athena CTAS (Chat 7)
- current_timestamp returns timestamp(3) WITH TIME ZONE in Athena (Trino/Presto)
- Parquet format does not support timestamp with time zone via Athena CTAS
- localtimestamp returns plain timestamp(3) — Parquet-compatible
- This is an Athena-specific gotcha, not a general SQL rule

## Single cached Athena query — all charts derived in-memory (Chat 8)
- Dashboard runs one flat 4-table JOIN on load, cached via @st.cache_data(ttl=3600)
- All charts (country, company leaderboard, role pie, tags frequency) computed from the cached DataFrame using pandas — no separate Athena queries
- Reason: Athena has per-query latency (~2-5s) and a scan cost; re-querying on every sidebar interaction would be slow and wasteful
- Sidebar filters apply a pandas boolean mask — rerenders are <100ms after initial load
- "Refresh data" button calls load_jobs.clear() to force a fresh Athena query when needed
- LIMIT 500 in the SQL as a cost guard; adjustable as data volume grows

## Rule-based extraction before LLM (Chat 9)
- SkillExtractor tries regex against SKILL_VOCAB + seniority patterns first
- Only calls Claude Haiku if rules find <5 skills OR seniority is "unknown"
- At ~50 Remotive jobs/day, fewer than 20% are expected to need an LLM call
- Keeps daily API cost well under the $0.50 budget cap; rules are instant and free

## Separate enrichment_scores table — dbt does not own it (Chat 9)
- fact_job_posting is fully managed by dbt; every `dbt run` recreates it via CTAS
- Writing match_score into fact_job_posting would be overwritten on the next dbt run
- Solution: standalone `enrichment_scores` external table (Parquet, partitioned by snapshot_date)
- Dashboard LEFT JOINs fact_job_posting ↔ enrichment_scores — each team owns its table

## S3 cache keyed by md5(description) (Chat 9)
- Same job description re-posted on a different snapshot_date should not incur a second API charge
- md5 of raw description is the cache key; cache entry stores ExtractionResult JSON
- Writes to s3://gold/enrichment-cache/{hash}.json after a successful LLM call
- cache hit → source="cache"; extraction still runs MatchScorer for freshness/salary recalc

## Claude Haiku with cache_control: ephemeral on system prompt (Chat 9)
- System prompt (~200 tokens) is sent with `cache_control: {"type": "ephemeral"}`
- After the first call in a Glue job run, subsequent calls pay 10× less for the system prompt (Anthropic prompt caching)
- Model: claude-haiku-4-5-20251001 — cheapest Claude model, ~$0.0008 per 1K output tokens
- max_tokens=300, description truncated to 4000 chars — prevents runaway costs on long JDs

## YoE-aware seniority scoring (Chat 9)
- Job descriptions commonly say "3+ years experience" rather than a seniority title
- Regex extracts the first number from patterns like "3+ years", "minimum 5 years", "1-3 years exp"
- Gap scoring: user has 2 years — gap=0 → 100%, gap=1 → 75%, gap=2 → 50%, gap>2 → 0%
- Partial credit ensures "right skills, 1 year short" still scores well (skill_overlap=40% dominates)
- Falls back to seniority-title distance if no YoE number is found in the description

## Skill overlap weighted at 40% — highest single component (Chat 9)
- A job requiring your exact tech stack is more actionable than one matching your title
- Skills are objective (python in → python out); seniority titles vary by company
- Jaccard similarity used (|intersection| / |union|) — penalises both gaps AND irrelevant extras
- Location (15%) + role family (15%) together match seniority (20%) — location matters as much as level

## score_detail stored as JSON string in Parquet (Chat 9)
- Parquet schema stays flat (no nested structs), which Athena handles cleanly
- score_detail value example: '{"skill_overlap": 32.0, "seniority_fit": 15.0, ...}'
- Queryable in Athena via json_extract(score_detail, '$.skill_overlap')
- Alternative (map<string,double>) would require Athena STRUCT casting — more complex DDL

## Glue version 4.0 over 5.1 for Python Shell jobs (Chat 10)
- Glue 5.1 Python Shell pre-installs awscli 1.23.5 + aiobotocore 2.2.0 which lock botocore to 1.25.x
- Any modern package (dbt-core, anthropic, pydantic) pulls botocore 1.42.x → hard pip conflict at install time
- Glue 4.0 Python Shell has a cleaner environment: no vendored awscli conflict, compatible with Python 3.9 + modern packages
- Always set explicit `glue_version` on Python Shell jobs; never rely on Glue default (picks latest = most restrictive)

## dbt 1.9.x as ceiling for Glue 4.0 Python Shell (Chat 10)
- Glue 4.0 Python Shell runs Python 3.9
- dbt-core 1.10+ raises `Requires-Python >= 3.10`
- dbt-core 1.9.10 + dbt-athena-community 1.9.5 is the last series supporting Python 3.9
- If Glue ever adds Python 3.10 support, can upgrade to dbt 1.10+

## pyarrow==14.0.2 pin for Glue 4.0 Python Shell (Chat 10)
- pyarrow dropped Python 3.9 manylinux wheels in version 15.0
- Without a pre-built wheel, pip tries to compile from C++ source → fails in Glue (no dev headers)
- 14.0.2 is the last version with confirmed `cp39-cp39-manylinux2014_x86_64` wheel on PyPI
- Pin until Glue runtime upgrades to Python 3.10+

## Manual sys.path bootstrap for Glue 4.0 Python Shell extra-py-files (Chat 10)
- Glue 4.0 Python Shell downloads --extra-py-files to /tmp but does NOT add to sys.path
- Cannot rely on --extra-py-files for imports; must write a bootstrap block
- Pattern: detect environment (no local genai/ dir = Glue), download zip from S3, extract to /tmp, sys.path.insert(0, extract_dir)
- Keeps enrichment_runner.py self-contained and runnable both locally and in Glue without code changes

## enrichment_scores Terraform-managed as aws_glue_catalog_table (Chat 10)
- Table was originally created via one-time Athena DDL in Chat 9 (outside Terraform)
- Risk: `terraform destroy` would not drop the table, but a fresh `terraform apply` on a new account would miss it
- Fix: added `aws_glue_catalog_table.enrichment_scores` to athena.tf; imported existing table with its catalog-id:database:table ID
- Terraform now owns the full gold layer schema — reproducible from scratch

## Arbeitnow over RemoteOK as second data source (Chat 11)
- RemoteOK was first choice: free JSON endpoint, no key, strong remote-job coverage
- Tested RemoteOK from Lambda before writing full code — received 403 with `server: cloudflare` header
- Lambda runs on AWS datacenter IPs which Cloudflare's bot protection blocks (same root cause as Himalayas in Chat 4)
- Arbeitnow: free public API, no Cloudflare, paginated, 100+ jobs/run — tested live first, confirmed working
- Rule: always curl-test a new API source from Lambda (or simulate datacenter IP) before writing ingestor code

## Step Functions Parallel state for concurrent ingestion (Chat 11)
- Sequential `InvokeRemotive → InvokeArbeitnow` would add ~60s to pipeline wall time per new source
- Parallel state runs all ingestor branches concurrently; total wait = slowest branch (not sum)
- Output is an array: `$.parallel[0]` = Remotive result, `$.parallel[1]` = Arbeitnow result — downstream states reference `$.parallel[0].snapshot_date`
- Parallel state fails entire execution if ANY branch fails (not partial success) — acceptable because both sources are required for a meaningful daily snapshot
- Adding a third source = one new branch in the Parallel state, no structural change

## COALESCE field resolution for multi-source Spark job (Chat 11)
- Each source has different field names for the same concept (job_id: `slug` in Arbeitnow, `id` in RemoteOK; apply_url: `url` in some sources)
- Normalization happens in the Lambda ingestor (each source maps to canonical schema before writing to bronze)
- For fields that slip through (legacy sources or schema drift), Spark job uses COALESCE at read time
- This keeps the Spark job source-agnostic: adding a new source only requires updating the Lambda ingestor, not the Spark job

## Dedup key uses country (normalized) not location_raw (Chat 12)
- location_raw is the raw string from the source ("Worldwide", "Remote", "anywhere" — all mean the same job)
- country is the UDF-normalized output: all three map to "remote"
- Using location_raw would give different hashes for the same logical job across sources
- Using country ensures "Remotive says Worldwide" and "Arbeitnow says Remote" collapse to the same dedup group

## row_number() over rank() for dedup canonical selection (Chat 12)
- rank() can produce ties when both publication_date and ingested_at are identical — two rows get rank=1, dedup fails to reduce to one
- row_number() always assigns exactly one rank=1 per partition, making canonical row selection deterministic
- In the tie case the chosen row is arbitrary but consistent within a single Glue run

## groupBy aggregate + join, not window collect_set, for source_apis (Chat 12)
- Window-based collect_set replicates the full set into every row before the rank filter — O(n × group_size) memory
- Doing groupBy on the original df (before rank filtering) then joining canonical rows to the aggregate is cheaper
- groupBy runs once; the join is narrow (two string key columns: dedup_key + snapshot_date)
- Parquet + Athena natively support ARRAY<STRING> columns (same SerDe as the existing tags column)

## source_count in gold, not source_apis array (Chat 12)
- Athena CTAS does not support ARRAY<STRING> as an output column type — the query fails at write time
- source_count (integer) passes through CTAS cleanly; it is sufficient to know "2 sources confirmed this job"
- source_apis array is kept in silver only, queryable via Athena SELECT on the silver external table
- If array is needed in gold later, it can be stored as a JSON string via array_join() and parsed at query time

## Cloudflare-first rule: test API from Lambda before writing ingestor (established Chat 11, confirmed Chat 12 research)
- Himalayas (Chat 4) and RemoteOK (Chat 11) both wasted effort — code written before discovering Cloudflare block
- Rule: curl-test from Lambda (or simulate datacenter IP) before writing any ingestor code
- Confirmed blocked from Lambda IPs: Himalayas, RemoteOK, Jooble, Japan Dev/TokyoDev, DoraHacks
- Confirmed working: Remotive, Arbeitnow, Greenhouse, Lever, HN Algolia, Devpost, Adzuna, USAJobs

## Batch Lambda per ATS, not per company (established Chat 12 research)
- Greenhouse/Lever/Ashby have thousands of company boards — one Lambda per company = 200+ Lambdas
- Pattern: one Lambda per ATS that reads a slug list from S3 or DynamoDB config, iterates companies, paginates each board
- Slug list stored as a JSON config file in S3, updatable without code deploy
- Step Functions Parallel state gets one branch per ATS (not per company)

## Cross-day dedup is a future chat (noted Chat 12)
- Chat 12 dedup handles within-snapshot (same job from two sources on same day)
- Cross-day: same job posting appears in silver on Day 1 and Day 7 = two rows (different snapshot_date)
- Current grain: one row per posting per snapshot_date — intentional for now
- Future: add a `canonical_job_id` that persists across snapshot_dates to track job posting lifetime

## Step Functions .sync integration for Glue (Chat 6)
- Used arn:aws:states:::glue:startJobRun.sync (optimized/synchronous integration)
- SF starts the Glue job, then internally polls glue:GetJobRun every ~20s until SUCCEEDED/FAILED
- No polling code written — AWS manages the wait natively
- Alternative: .waitForTaskToken — requires writing your own callback, much more complex
- Cost: a few extra state transitions per execution, well within 4K free tier/month