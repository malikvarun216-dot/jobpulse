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

## Adzuna as third data source (Chat 13)
- Adzuna was chosen over other candidates because it returns `salary_min` and `salary_max` as integers — the only structured salary data among all tested sources
- Remotive has salary as an unstructured text field ("$120k-$150k"), Arbeitnow has no salary at all
- Structured salary makes the salary_fit scoring component meaningful for ~12 countries of data
- API requires a free-tier key (developer.adzuna.com) stored as Lambda env vars via Terraform variables

## Per-country error isolation in Adzuna ingestor (Chat 13)
- Adzuna covers 12 countries in one Lambda invocation — a single country's API failure should not abort the whole run
- `fetch_all_jobs()` wraps each country's `fetch_country_jobs()` call in try/except, logs the error, and continues
- Partial data (e.g. 11/12 countries) is still useful; a full abort produces nothing
- Trade-off: a silently failed country is hard to detect without monitoring — CloudWatch logs show "Skipping country=X" for investigation

## salary field name in bronze: "salary" not "salary_raw" (Chat 13)
- All ingestors write the key `salary` in their canonical bronze JSON (Remotive, Arbeitnow, Adzuna all use `salary`)
- The Spark job reads `job.salary` and aliases it to `salary_raw` in the silver schema
- Adzuna formats it as `"$80000-$120000"` — same parseable pattern as Remotive's text salary
- The MatchScorer's existing `_parse_salary` regex extracts the numbers correctly without any changes

## redirect_url resolved via COALESCE for Adzuna apply links (Chat 13)
- Adzuna's API uses `redirect_url` for the job application link (not `apply_url` or `url`)
- Added `F.col("job.redirect_url")` as the third fallback in the apply_url COALESCE in `build_silver_df()`
- Order: `job.apply_url` (canonical) → `job.url` (remotive/arbeitnow) → `job.redirect_url` (adzuna)
- No other sources use `redirect_url`, so the COALESCE is a no-op for existing sources

## Lambda credentials via env vars, not Secrets Manager (Chat 13)
- Adzuna app_id + app_key passed as Lambda env vars (ADZUNA_APP_ID, ADZUNA_APP_KEY) set in Terraform
- Secrets Manager is used for the Anthropic key (in Glue) because Glue has no native env var injection
- Lambda supports env vars natively; KMS encrypts them at rest automatically (no extra config)
- Trade-off: env vars are readable via `lambda:GetFunctionConfiguration` — acceptable for a free-tier API key, not for payment credentials or DB passwords
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

## SKILL_VOCAB as a whitelist, not a passthrough (Chat 18)
- LLM extraction output is filtered through SKILL_VOCAB before being stored. If a skill isn't in the vocab, it's dropped silently — both from the cache and from scoring.
- This was understating matches: JDs mentioning Kinesis, Delta Lake, Iceberg, Langchain scored 0 on those skills even when the user had them.
- Vocab expansion is a scoring fix, not just a coverage fix. Every new term added directly improves match accuracy for JDs that use that term.
- Trade-off: too broad a vocab = noise (unrelated terms match). Keep vocab to tools/technologies only, no soft skills.

## Seniority weight cut from 20 → 10 with gap softening (Chat 18)
- User has 2 YoE targeting senior roles (typically 4–6 YoE). Gap of 3 = 0 pts under the old hard cutoff — wipes out 20% of total score on every senior JD.
- Fix 1: gap == 3 → 25% partial credit. Senior roles still deprioritised but surface as stretch roles.
- Fix 2: cut seniority weight from 20 → 10. Even with partial credit, seniority shouldn't dominate when skills are the real filter.
- Combined effect: a senior DE role matching your Kafka+Spark+Python core now scores ~75 instead of ~55.

## skill_overlap weight raised from 40 → 50 (Chat 18)
- DE hiring is skills-first. "We need someone who knows Spark + Kafka + Airflow" is more specific than "we want a senior engineer."
- Extra 10 pts came from seniority_fit (cut to 10). Location and role family unchanged.
- With 30 skills in profile vs original 10, skill_overlap is also more meaningful — larger intersection is possible on real JDs.

## Voyage AI over AWS Bedrock for embeddings (Chat 15)
- Bedrock embedding models (Titan v2) are available but add IAM complexity and cost on top of the Glue job
- Voyage AI: `voyage-4-lite` = 200M tokens/month free after adding a payment method; rate limits unlocked
- Direct Python SDK (`voyageai.Client`) is simpler than a boto3 Bedrock call; no additional IAM policy needed
- Trade-off: external dependency (one more API vendor). Acceptable — embeddings are idempotent, failure just means stale embeddings, not broken pipeline

## In-memory cosine similarity over a managed vector DB (Chat 15)
- At 3,500–20,000 jobs, loading the full embedding matrix into a NumPy array and computing cosine similarity in-process takes <1s
- DynamoDB or OpenSearch (managed vector DB) adds per-read cost, latency, and infra complexity
- Embeddings are loaded once per Streamlit query, not on every page load (Streamlit session state caches results)
- Trade-off: scales to ~500K jobs before response time degrades. At that scale, switch to a FAISS index in S3 or a managed ANN service. Current scale is nowhere near that.

## Pure pyarrow over pandas for all Parquet I/O in Glue Python Shell (Chat 15/16)
- `pd.read_parquet()` and `pd.to_parquet()` require pandas to discover an engine (pyarrow or fastparquet) at runtime
- Glue 4.0 Python Shell's environment causes engine discovery to fail even when pyarrow is installed via `--additional-python-modules`
- Direct pyarrow API (`pq.read_table`, `pq.write_table`, `pa.table`) bypasses engine discovery entirely
- Applied to both embedding_agent.py and semantic_search.py. enrichment_runner uses pandas for CSV reads only (not Parquet), so unaffected.

## dedup_key = md5(source|job_id), cross_source_key separate (Chat 16)
- Original dedup_key = md5(company_name|title|country) caused 2,525→24 row collapse when Adzuna defaulted company_name to "Unknown" — identical hash for hundreds of unrelated jobs
- New design: dedup_key is per-source-unique (`md5(source|job_id)`) so no hash collision across jobs
- cross_source_key = md5(company|title|country) is used only for aggregation (source_apis, source_count) — it drives groupBy, not row selection
- source_count > 1 still identifies multi-confirmed jobs; dedup just no longer clobbers distinct jobs

## S3-stored user_profile.yml, separate from genai_package.zip (Chat 17)
- Previous design: profile was baked into `genai_package.zip` → updating skills required a new zip upload (`terraform apply` or manual `aws s3 cp`)
- New design: profile uploaded as `aws_s3_object.user_profile` to `s3://silver/config/user_profile.yml`; enrichment_runner downloads it fresh on every Glue run
- Fallback: if S3 download fails, use the bundled copy inside the zip — no single point of failure
- Impact: updating your profile is now a one-command operation, no Terraform cycle needed

## Tiered skill weights over flat Jaccard (Chat 17)
- Flat Jaccard (|intersection| / |union|) treats python and docker as equal signals — a job matching only docker (learning tier) would score the same as one matching python (core)
- New: core skills (python, sql, pyspark, aws, dbt) = 3x weight; secondary = 1.5x; learning = 1x
- Normalized against total user skill weight (not union) so score stays in [0, 1] regardless of how many skills a job lists
- Impact: jobs matching the core stack will consistently rank above jobs that only incidentally match a peripheral skill

## --force_rescore skips LLM entirely (Chat 17)
- When profile changes, old enrichment_scores are stale. Option 1: re-run LLM on all jobs (expensive). Option 2: re-score using existing cached extractions only.
- force_rescore=True uses rules fast path + S3 extraction cache; if neither hits, uses rules fallback — zero API calls
- This is safe because the cache stores the extraction (skills, seniority), not the score. Score is always recomputed from the current profile.
- Practical cost: rescoring 3,500 jobs with force_rescore takes ~3 min and $0.00

## Always run the linter against existing code before wiring it as a CI gate (Chat 19 incident)
- Ruff was added as a hard CI failure on day one without a prior local dry-run. 11 existing lint errors caused the first push to fail immediately.
- Rule: before adding any new CI check (linter, formatter, type checker), run it locally against the full codebase, fix all violations, then commit the fixes together with the CI config in the same PR.
- Trade-off: doing it in one PR is slightly more work to review. Not doing it guarantees a broken CI on first run, which undermines trust in the gate.
- Applied: in future chats, any new tool added to CI gets a local dry-run first — the CI config commit and the fix commit go in together.

## GE as a separate Glue job, not embedded in the Spark job (Chat 20)
- Option considered: run GE checks inside bronze_to_silver.py before the Parquet write
- Rejected: would require adding GE as a dependency to the Spark job (different container, different bootstrap), mixing concerns (transform vs validation)
- Chosen: separate Glue Python Shell job (ge_runner) inserted as its own Step Functions state
- Why: one job, one responsibility. If quality fails, Step Functions sees a FAILED Glue job and routes to PipelineFailure. Clean separation.

## GE ephemeral context over a persisted Data Docs setup (Chat 20)
- GE supports file-based, S3-backed, and in-memory ("ephemeral") contexts
- File/S3-backed: stores Data Docs (HTML reports), checkpoint configs, suite JSONs on disk or in S3. Good for teams sharing validation history.
- Ephemeral: everything in memory, nothing persisted. Suitable for Glue Python Shell where there's no writable filesystem and S3-backed Data Docs would add unnecessary cost and complexity.
- Decision: `gx.get_context(mode="ephemeral")` — validation result is printed to CloudWatch logs, which is sufficient for an unattended pipeline.

## ExpectColumnDistinctValuesToBeInSet for freshness, not ExpectColumnValuesToBeBetween (Chat 20)
- First attempt: `ExpectColumnValuesToBeBetween(min_value=snapshot_date, max_value=snapshot_date)` on a string column
- Problem: silently passed on valid data and gave an empty result object (no error, no pass)
- Root cause: GE's between-comparison on strings uses lexicographic ordering which behaves unexpectedly for date strings in some edge cases; the expectation was returning an empty validation result
- Correct approach: `ExpectColumnDistinctValuesToBeInSet(column="snapshot_date", value_set=[snapshot_date])` — explicitly checks that the only distinct value is today's date
- This is the semantically correct expectation for a freshness check: the set of all distinct dates must equal {today}

## ExpectTableRowCountToBeBetween instead of ExpectTableRowCountToBeGreaterThan (Chat 20)
- GE 1.x does not have `ExpectTableRowCountToBeGreaterThan` — calling it raises AttributeError
- `ExpectTableRowCountToBeBetween(min_value=100)` with no max_value is the correct API — max_value defaults to unbounded
- Lesson: always validate GE expectation class names against the version installed. GE APIs changed significantly between 0.x and 1.x.

## Partition column must be manually assigned when reading individual Parquet files (Chat 20)
- Hive-style partitioned Parquet: column values are stored in the directory path (`snapshot_date=2026-04-25/`), not in the file data
- When reading with `pq.read_table(io.BytesIO(body))` on individual files, pyarrow returns only data columns — the partition column is absent
- Dataset-level API (`pq.read_table(directory_path)`) can auto-populate partition columns, but requires filesystem access (unreliable in Glue Python Shell per Chat 15/16 incidents)
- Decision: read files individually via boto3 (reliable), then manually assign `df["snapshot_date"] = snapshot_date`

## EC2 t3.micro over ECS Fargate for dashboard hosting (Chat 21)
- Fargate: serverless containers, no SSH, auto-scales. But: cold starts on each request, no free tier, ~$15/mo minimum
- EC2 t3.micro: always-on, free 750 hrs/mo (6 months), SSH access for debugging, Docker runs natively
- Decision: t3.micro is the right call for a personal dashboard — low traffic, cost matters, debugging via SSH is useful
- Trade-off: requires instance management (patching, Docker restarts). Acceptable for a project of this scale.

## IAM instance profile over access keys on EC2 (Chat 21)
- Never store AWS credentials on the instance (in .env, ~/.aws/credentials). Keys can leak via `docker inspect`, logs, or repo accidents.
- Instance profile: IAM role attached to EC2. boto3 auto-fetches temp credentials from EC2 metadata service (169.254.169.254). Rotated every hour automatically.
- Zero credential management — no key generation, no rotation, no rotation tracking
- Least-privilege: the role only has permissions the dashboard actually needs (Athena queries, S3 gold/silver read, Glue catalog, Secrets Manager read)

## Elastic IP for stable dashboard endpoint (Chat 21)
- EC2 public IP changes on every stop/start. Without a fixed IP, users must look up the new address every time.
- Elastic IP: static, stays associated with the account even when instance is stopped. Re-attached after instance replacement (terraform apply -replace).
- Cost: free while associated with a running instance. $0.005/hr if unassociated — only matters if the instance is stopped long-term.
- Trade-off: not a domain name, but sufficient for a personal tool. Add Route53 + custom domain only if sharing publicly.

## Docker build context at repo root, not dashboard subdirectory (Chat 21)
- First design: `docker build .` from `dashboard/streamlit/` — only that directory's files are in the build context
- Problem: `from genai.semantic_search import search` inside app.py fails because `genai/` is outside the build context
- Fix: changed to `docker build -f dashboard/streamlit/Dockerfile .` from repo root — full repo is the build context
- Dockerfile explicitly copies `COPY dashboard/streamlit/ .` then `COPY genai/ genai/` — only the necessary directories
- Trade-off: slightly larger build context (whole repo sent to Docker daemon), but Docker layer caching means only changed files rebuild

## Adzuna country list restricted to English-speaking markets (Chat 21)
- Original list (12): gb, us, au, ca, de, fr, br, in, nz, pl, ru, za
- Removed: pl, ru (Adzuna.pl / Adzuna.ru geo-block non-local users — "Sorry, this job is not available in your region")
- Further removed: de, fr, br (German/French/Portuguese-language Adzuna sites have same geo-restriction behaviour)
- Final list (7): gb, us, au, ca, in, nz, za — all English-language markets, globally accessible apply links
- Trade-off: losing ~1,500 jobs/run from non-English markets. Acceptable — non-accessible apply links have zero value.

## numpy==1.26.4 as the safe pairing with pyarrow==14.0.2 (Chat 20)
- NumPy 2.0 removed `numpy.core` submodule. Any C extension compiled against numpy 1.x (including pyarrow 14.x) that imports `numpy.core.multiarray` will crash at load time.
- `numpy>=1.24.0` in `--additional-python-modules` resolves to numpy 2.x — crash guaranteed
- `numpy==1.26.4` is the last stable 1.x release and the correct pairing with `pyarrow==14.0.2`
- Rule: when pinning pyarrow to 14.x, always pin `numpy==1.26.4`. Document this pairing in glue.tf as a comment.

## loop variable `l` is ambiguous — always use descriptive names in comprehensions (Chat 19)
- `{l.lower() for l in locations}` — ruff E741 flags single-letter variables `l`, `O`, `I` as ambiguous (look like 1, 0, 1 in many fonts).
- Renamed to `loc`. Rule: comprehension variables should be short but descriptive — `loc`, `skill`, `tag`, not `l`, `s`, `t`.
- Not a functional bug, but a readability issue that causes real misreads during code review.

## CI/CD: Two workflows, not one (Chat 19)
- `ci.yml` runs on every push to every branch + every PR to dev. Purpose: fast feedback loop, catches regressions before code reaches dev.
- `deploy.yml` runs on push to dev only. Purpose: automated AWS deploy after merge.
- Alternative considered: one combined workflow with conditional deploy step (`if: github.ref == 'refs/heads/dev'`). Rejected — mixing test-only and deploy-capable workflows in one file makes it harder to reason about what runs when and obscures the deploy trigger in PR views.
- Two files = two clearly named workflow runs in GitHub Actions UI. `CI` appears on every branch. `Deploy` appears only on dev. Unambiguous at a glance.

## CI does not need real AWS credentials or API keys (Chat 19)
- All 192 unit tests mock every external call: S3 (boto3.client mocked), Anthropic API (mocked via `@patch`), Adzuna HTTP (urllib.request mocked), Voyage AI (mocked).
- Tests set their own env vars inline (`os.environ["BRONZE_BUCKET"] = "test-bronze-bucket"`) — no env setup in the workflow beyond `ANTHROPIC_API_KEY=sk-test-dummy`.
- `sk-test-dummy` prevents JDEnrichmentAgent from hitting Secrets Manager (it checks `os.environ.get("ANTHROPIC_API_KEY")` first — if set, skips AWS). Non-empty string is enough; the value is never sent to Anthropic in tests.
- Security benefit: CI job has zero AWS permissions. A compromised Actions runner cannot touch S3, Lambda, or Glue.

## Deploy re-runs tests instead of depending on ci.yml result (Chat 19)
- GitHub's `workflow_run` trigger can depend on another workflow completing, but it's async and doesn't guarantee the run was on the same commit. Race condition: ci.yml ran on commit A, but by the time deploy fires it might be on commit B.
- Simpler and safer: deploy.yml has its own `test` job as the first step (`needs: test` gates the `deploy` job). If tests fail, deploy never runs.
- Cost of re-running: <60 seconds. Cost of a failed deploy from a race condition: broken Lambda in production.

## Lambda zip contains only the handler .py — no deps bundled (Chat 19)
- All ingestors (Remotive, Arbeitnow, Adzuna) use only `urllib.request`, `gzip`, `json`, `hashlib` (stdlib) plus `boto3`.
- `boto3` is pre-installed in every AWS Lambda Python 3.12 runtime. Bundling it would increase zip size from ~3 KB to ~40 MB.
- Build command: `cd ingestion/sources/{source} && zip {source}.zip ingest_{source}.py` — one line, no build tool, reproducible anywhere.
- Contrast with genai_package.zip: that zip includes the full `genai/` directory (pure Python, no C extensions) because Glue doesn't auto-install it.

## genai_package.zip built in CI, not Terraform null_resource (Chat 19)
- Terraform's `null_resource` + `local-exec` built the zip during `terraform apply`. This coupled code deploys to infra applies — changing a genai/*.py file required either running `terraform apply` (which might also change infra) or manually building the zip.
- New pattern: CI builds the zip and uploads it on every push to dev. Terraform still owns the Glue job definition (name, timeout, IAM role, DPU) but the zip is out of its scope.
- Clean separation: Terraform = infra state. GitHub Actions = code artifacts. Never mix.
- Trade-off: if someone runs `terraform apply` locally and the null_resource triggers, it would upload an older copy of the zip. Mitigation: the null_resource is still in glue.tf as a fallback, but CI is the authoritative deploy path.

## ruff over flake8/pylint for linting (Chat 19)
- ruff is written in Rust, runs the full repo in <1s (vs 10–30s for flake8 on medium repos). CI feedback is faster.
- `--select E,F`: E (pycodestyle) catches syntax/indentation errors. F (pyflakes) catches undefined names and unused imports — the two categories most likely to cause runtime failures.
- `--ignore E501` (line length): existing codebase has many long lines, especially in SQL strings and dict literals. Enforcing this now would generate hundreds of warnings for zero functional benefit.
- `--ignore E402` (import not at top): all 6 ingestor/genai test files do `sys.path.insert(0, ...)` before `import ingest_X as sut`. This is the correct pattern when tests live outside the package they test — ruff's E402 doesn't understand this idiom.
- Not using W (pycodestyle warnings) or C (convention): too noisy on a codebase not designed with them from the start.

## Python 3.12 in CI matches Lambda runtime (Chat 19)
- All 4 Lambda functions use `runtime = "python3.12"` in Terraform.
- If CI used Python 3.9 or 3.11, a syntax or stdlib API difference could pass CI but fail in Lambda.
- Glue Python Shell jobs use Python 3.9 (Glue 4.0 limitation), but those aren't tested in CI — Glue job scripts are tested indirectly via the genai unit tests which run fine on 3.12.
- Trade-off: if Glue-only code uses a 3.9-only pattern, CI won't catch it. Acceptable — the genai tests cover extraction logic; the Glue bootstrap boilerplate (zip extraction, sys.path) is stable.

## GE runner as a separate Glue Python Shell job, not inside the Spark transform (Chat 20)
- Separation of concerns: the Spark job transforms; the GE job validates. Mixing them means a quality failure also aborts the transform — you can't distinguish "transform crashed" from "data was bad."
- Separate job = separate Step Functions state = separate CloudWatch log stream = pinpointed failure visibility.
- Cost: 0.0625 DPU Python Shell job, ~$0.004/run. Negligible for the observability gain.

## GE ephemeral in-memory context, not file-system context (Chat 20)
- Default GE setup writes a `great_expectations/` project directory and Data Docs HTML to disk.
- In a Glue Python Shell job, the working directory is ephemeral — no persistent file system, no S3 Data Docs needed.
- `gx.get_context(mode="ephemeral")` keeps everything in memory: no files written, no S3 side effects from the quality check itself.
- Trade-off: no Data Docs HTML report persisted between runs. Acceptable — failures are logged to CloudWatch; a Data Docs site would require hosting infrastructure.

## ExpectColumnDistinctValuesToBeInSet for freshness, not ExpectColumnValuesToBeBetween (Chat 20)
- `ExpectColumnValuesToBeBetween` on string columns in GE 1.x returns an empty result object (no error, just silent wrong behaviour). Caught by the unit test — `test_valid_df_passes` was raising ValueError on a valid DataFrame.
- `ExpectColumnDistinctValuesToBeInSet(column="snapshot_date", value_set=[today])` checks that the set of unique dates in the partition contains only today's date. A stale partition (yesterday's date) fails because the value is not in the allowed set.
- Rule: always test the happy path AND each failure path before trusting a GE expectation on a new column type.

## ExpectTableRowCountToBeBetween, not ExpectTableRowCountToBeGreaterThan (Chat 20)
- `ExpectTableRowCountToBeGreaterThan` does not exist in GE 1.x. The correct class is `ExpectTableRowCountToBeBetween(min_value=N)`.
- `min_value` is inclusive: `min_value=100` means "at least 100 rows."
- Lesson: always let tests find the real class name rather than guessing from documentation.

## RunDataQuality state inserted between RunGlueJob and RunDbtGold (Chat 20)
- Insertion point: after silver is written, before dbt reads it. This is the only point where bad silver data can be stopped before it propagates to gold.
- If GE fails before RunDbtGold: gold is untouched. dbt's CTAS only runs on valid data.
- If GE ran after RunDbtGold: bad data would already be in gold. The check would be post-mortem, not preventive.

## Lambda snapshot_date: IST (UTC+5:30) instead of UTC (Post-Chat-14 fix)
- All ingestors compute `snapshot_date = datetime.now(ist).strftime("%Y-%m-%d")`
- Reason: EventBridge cron fires at 2 AM IST = 8:30 PM UTC (previous day) → UTC computes wrong date
- Issue caught: Apr 23 2 AM IST pipeline computed Apr 22 (UTC) snapshot_date, wrote to wrong S3 partition
- Alternative: compute UTC, then offset by +5:30 hours → fragile, harder to explain
- Choice: use IST directly in Lambda — explicit, clear, matches business time zone
- Trade-off: Lambda is now IST-aware (not timezone-agnostic) — acceptable at project scale for single timezone
- Impact: snapshot_date now matches the day the pipeline ran (in India time); Apr 24+ pipelines produce correct dates
## EC2 t3.micro over ECS Fargate for dashboard (Chat 21)
- ECS Fargate: fully serverless, ~$30-50/mo, more complex, overkill for a personal dashboard
- EC2 t3.micro: free tier (6 months), SAA cert practice (EC2, SG, IAM instance profiles, EIP)
- Choice: t3.micro — zero cost for 6 months, covers SAA exam surface, simple to explain
- Trade-off: manual scaling if traffic grows (not expected for personal use)

## IAM instance profile over hardcoded keys for EC2 (Chat 21)
- Instance profile attaches an IAM role to EC2 — credentials auto-rotated by metadata service (169.254.169.254)
- Boto3 on EC2 picks up credentials automatically: no .env file, no key rotation work
- Alternative: IAM user keys in .env — credential leak risk, manual rotation
- Choice: instance profile — AWS best practice, SAA exam pattern, no secrets on disk

## scp via GitHub Actions over git clone in user data (Chat 21)
- git clone in user data requires GitHub credentials on EC2 — complex to manage securely
- scp via GitHub Actions: EC2 key pair in GitHub secrets, code pushed on every deploy
- Choice: scp — simpler, no credentials on EC2, leverages existing GitHub Actions SSH setup
- Trade-off: first deploy requires a code push; instance can't self-bootstrap from scratch

## Elastic IP over dynamic public IP (Chat 21)
- Dynamic public IP changes every stop/start — breaks bookmarks and resume links
- Elastic IP: static, persists across stop/start, billed only when unattached ($0.005/hr)
- Choice: Elastic IP — stable URL for resume/portfolio, negligible cost
- Trade-off: must remember to release EIP when project shuts down to avoid idle charges
