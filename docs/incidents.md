# Incidents

## [2026-04-21] — Enrichment silently hangs at scale (3,400 jobs → 60-min TIMEOUT)
- What happened: enrichment Glue job ran 60 min with zero log output after "Starting script execution", then hit TIMEOUT. Had worked fine at 121 jobs (Remotive + Arbeitnow only).
- What I thought: Claude API weekly budget exhausted or API key missing from Glue env
- Root cause: pure scale — sequential Claude API batches for 3,421 jobs (3,300 Adzuna added) exceed 60-min limit. No per-call timeout means one slow API response hangs the entire job indefinitely.
- Fix (partial): bumped Glue timeout 20→60 min. Real fix pending: per-call timeout + parallel batch processing.
- Prevention: set `timeout=30` on every Claude API call; wrap each batch in try/except so one failure skips the batch, not kills the job.
- Lesson: always test at production volume before calling a job "working". 121 jobs ≠ 3,400 jobs.

## [2026-04-21] — dbt COLUMN_NOT_FOUND: source_count missing from stg_silver_jobs
- What happened: dbt run failed with `COLUMN_NOT_FOUND: Column 'j.source_count' cannot be resolved` on `fact_job_posting` model. dim_* models all passed (PASS=4), only fact failed.
- What I thought: external table schema was missing source_count — checked athena.tf which correctly defined it
- Root cause: `stg_silver_jobs.sql` had an explicit column list that excluded `source_count` and `source_apis` (columns added by `deduplicate_silver_df()` in the Glue job). `fact_job_posting` referenced `j.source_count` via the staging view — column was filtered out before it got there.
- Fix: added `source_count` and `source_apis` to the explicit select in `stg_silver_jobs.sql`.
- Prevention: when adding new columns to the Spark output, always check downstream dbt staging models for explicit column lists — `select *` passes everything through, explicit lists silently drop new columns.
- Lesson: explicit column lists in staging models are a silent filter. Either use `select *` or maintain the list whenever the upstream schema changes.

## [2026-04-21] — dbt_runner.py swallowed all error output
- What happened: dbt Glue job showed generic "RuntimeError: dbt run failed" with no detail. Could not diagnose the actual dbt error from Glue API or CloudWatch.
- What I thought: log group didn't exist; tried `/aws-glue/python-jobs` which wasn't accessible
- Root cause: `dbtRunner().invoke()` captures all dbt output internally — nothing streams to stdout/CloudWatch. Also uploaded dbt_project.zip to bronze bucket but dbt_runner downloads from silver bucket (`s3://{silver_bucket}/dbt-project/dbt_project.zip`).
- Fix: switched to `subprocess.run(["dbt", "run"])` without `capture_output=True` — stdout/stderr stream directly to CloudWatch. Fixed zip upload to silver bucket.
- Prevention: never use `dbtRunner().invoke()` in Glue — it swallows logs. Always use subprocess so output appears in CloudWatch. Verify S3 path matches what the script actually reads.
- Lesson: Glue Python Shell log group is `/aws-glue/python-jobs/output`, stream name = job run ID.

## [2026-04-19] — Glue 5.1 Python Shell boto3 vendoring breaks all pip installs
- What happened: Both dbt_runner and enrichment_runner Glue jobs failed with pip dependency conflicts on every run
- What I thought: pinning specific package versions (anthropic==0.28.0, dbt-core==1.7.14 etc.) would fix it
- Root cause: Glue 5.1 Python Shell pre-installs awscli 1.23.5 + aiobotocore 2.2.0 which require botocore==1.25.5. Any modern package (dbt-core, anthropic, pydantic) pulls botocore 1.42.x → hard conflict. Version pinning cannot fix this — the conflict is in Glue's vendored environment, not the packages themselves.
- Fix (planned Chat 10): add `glue_version = "4.0"` to both Python Shell jobs in glue.tf — Glue 4.0 uses a cleaner environment compatible with modern packages
- Prevention: always set explicit glue_version on Python Shell jobs; never rely on Glue default (which picks latest = 5.1)
- Lesson: Glue version controls the entire execution environment, not just the runtime. Default version = latest = most restrictive vendoring.

## [2026-04-18] — Himalayas API blocked by Cloudflare
- What happened: Lambda invoked, got HTTP 403 Forbidden from Himalayas API
- What I thought: Lambda IP block or User-Agent issue
- Root cause: Himalayas added Cloudflare bot protection (cf-mitigated: challenge) — requires browser JS execution, blocks all programmatic clients
- Fix: Added Remotive as replacement live source; Himalayas Lambda kept for future
- Prevention: test API accessibility with curl before building ingestor
- Lesson: "Free public API" doesn't guarantee programmatic access — always curl-test before coding

## [2026-04-18/19] — Terraform worktree tfstate mismatch (recurring, Chat 4 + Chat 5)
- What happened: terraform apply tried to recreate all existing AWS resources, failing with EntityAlreadyExists / BucketAlreadyOwnedByYou (Chat 4). In Chat 5, Lambda functions weren't in any tfstate at all — required terraform import before apply could run cleanly
- What I thought: IAM/S3 conflict or credentials issue
- Root cause: Each git worktree is an independent directory — tfstate lives on disk as a file, so worktrees start with no state and don't know what already exists in AWS. Chat 4's tfstate was also lost when that worktree was cleaned up, leaving Chat 5 with only Chat 3's resources in state
- Fix (short-term): Copied tfstate from main dev dir + ran terraform import for missing Lambda resources
- Fix (permanent): Migrated backend from local file → S3 (jobpulse-tfstate-dev bucket, versioned + AES256 encrypted) with DynamoDB state locking (jobpulse-tfstate-lock table, PAY_PER_REQUEST). Now any worktree runs terraform init and pulls real state from S3 automatically — no copying ever again
- Prevention: S3 backend is now configured — this incident cannot recur. Bootstrap bucket created via AWS CLI (Terraform can't manage its own backend bucket)
- Lesson: Local tfstate + git worktrees = guaranteed drift. Remote state backend (S3/GCS/Terraform Cloud) is non-negotiable in any multi-environment or multi-machine setup

## [2026-04-18] — AWS_REGION is a reserved Lambda environment variable
- What happened: terraform apply failed with InvalidParameterValueException — reserved key AWS_REGION
- What I thought: permission or variable format issue
- Root cause: Lambda runtime sets AWS_REGION automatically; setting it manually is blocked
- Fix: Removed AWS_REGION from lambda.tf environment block; ingestor already has ap-south-1 as default
- Prevention: never set AWS_ prefixed vars in Lambda env — all reserved
- Lesson: Lambda reserves all AWS_ prefix vars; use custom names for any region config you need to override

## [2026-04-18] — AccessDenied on s3:PutObjectTagging
- What happened: Live Lambda invoke failed with AccessDenied on PutObject call
- What I thought: PutObject permission was sufficient for put_object with Tagging
- Root cause: S3 Tagging param in put_object requires s3:PutObjectTagging as a separate IAM action — it is not bundled with s3:PutObject
- Fix: Added s3:PutObjectTagging to IAM policy in iam.tf, ran terraform apply
- Prevention: whenever using put_object with Tagging, add PutObjectTagging to IAM policy
- Lesson: S3 IAM is granular — always check AWS docs for exact action names when using advanced put_object params

## [2026-04-19] — pytest.importorskip at module level skips entire test file
- What happened: ran pytest on test_bronze_to_silver_remotive.py, got "0 collected / 1 skipped" — no tests ran at all
- What I thought: pyspark skip marker was working correctly, tests would be selectively skipped
- Root cause: pytest.importorskip() called at module level raises a skip exception before any test is collected — skips the entire file, not just the pyspark-dependent tests
- Fix: replaced with try/except ImportError to set _HAS_PYSPARK flag, then @pytest.mark.skipif(not _HAS_PYSPARK) decorator on each Spark test individually
- Prevention: never use pytest.importorskip at module level when the file has mixed pure/Spark tests
- Lesson: put optional-dependency guards at the test function level, not module level — pure functions should always be testable without heavy dependencies

## [2026-04-19] — md5(varchar) fails in Athena — must use to_hex(md5(to_utf8(...)))
- What happened: dbt run failed with `FUNCTION_NOT_FOUND: Unexpected parameters (varchar) for function md5. Expected: md5(varbinary)`
- What I thought: md5() would accept a string like in most SQL dialects
- Root cause: Athena runs on Trino/Presto — md5() requires varbinary, not varchar. Standard SQL habit doesn't apply here.
- Fix: replaced `md5(cast(col as varchar))` with `to_hex(md5(to_utf8(col)))` in all dim models and fact joins
- Prevention: always check Trino function signatures when writing Athena SQL
- Lesson: Athena ≠ standard SQL. md5/sha256/etc. operate on varbinary. Use to_utf8() to convert strings first.

## [2026-04-19] — current_timestamp produces timestamp with time zone, breaks Parquet CTAS
- What happened: dbt table models failed with `NOT_SUPPORTED: Unsupported Hive type: timestamp(3) with time zone`
- What I thought: current_timestamp is a safe generic SQL expression
- Root cause: Athena's current_timestamp returns `timestamp(3) with time zone`. Parquet CTAS via Athena does not support timezone-aware timestamps — it only accepts plain `timestamp`.
- Fix: replaced `current_timestamp` with `localtimestamp` in all dim models (returns `timestamp(3)` without timezone)
- Prevention: in Athena CTAS/dbt-athena: always use `localtimestamp` or `cast(current_timestamp as timestamp)` for created_at columns
- Lesson: Parquet type compatibility in Athena CTAS is strict. timezone-aware timestamps must be stripped before writing to Parquet.

## [2026-04-19] — dim_role surrogate key not unique due to composite grain
- What happened: `dbt test` reported `unique_dim_role_role_key` failure — 1 duplicate detected
- What I thought: role_family alone would produce unique rows in the dim
- Root cause: dim_role has grain `(role_family, category)` not `role_family`. The same role_family (e.g., SDE) appeared with two different categories, giving two rows with the same `role_key` (keyed only on role_family)
- Fix: changed surrogate key to `to_hex(md5(to_utf8(concat(role_family, '|', coalesce(category, '')))))` — composite key matches the actual grain
- Prevention: always define surrogate key on the same columns as the SELECT DISTINCT — key grain = row grain
- Lesson: if your dim SELECT DISTINCT has N columns, your surrogate key must cover all N columns that define uniqueness

## [2026-04-19] — test boundary: assertLess failed on exact equality (zero skill overlap)
- What happened: `test_zero_skill_overlap_reduces_score` failed with `60.0 is not less than 60.0`
- What I thought: zero skill overlap would produce a clearly low score, safely below 60
- Root cause: with zero skill overlap (0/40 pts) but a perfect profile match on everything else — mid seniority, remote location, DATA role, salary unknown (full pts), fresh job — the remaining components sum to exactly 60: seniority(20) + location(15) + role(15) + salary(5) + freshness(5) = 60. The assertion used `assertLess` (strict), not `assertLessEqual`
- Fix: changed `assertLess(score, 60.0)` to `assertLessEqual(score, 60.0)` — the boundary case is valid and expected
- Prevention: when testing score ceilings, think through what the other components contribute; use `assertLessEqual` unless you have a strict reason to exclude the boundary
- Lesson: scoring tests need to account for every weight component, not just the one being isolated

## [2026-04-19] — bash heredoc broke on Python single-quoted dict keys
- What happened: `Bash` tool with heredoc payload containing `job["job_id"]` caused the shell to mis-parse the heredoc boundary, writing a truncated or malformed file
- What I thought: double-quoted heredoc delimiter (`<<"EOF"`) would suppress all expansion
- Root cause: the Python source code contained single-quoted string keys (e.g. `job['job_id']`, `{'status': 'OK'}`). When pasted inside a bash heredoc the shell interprets single quotes contextually depending on the surrounding quoting mode, causing the content boundary to be misread in some terminal environments
- Fix: switched to the `Write` tool for all multi-line Python files with dict literals — bypasses the shell entirely
- Prevention: always use the `Write` tool (not bash heredoc) for Python files containing `{}` dict literals, f-strings, or nested quotes
- Lesson: heredocs are fine for simple config; for code files with mixed quoting, Write tool is safer and avoids shell interpretation entirely

## [2026-04-19] — `python3` not found on Windows; pydantic/anthropic not installed
- What happened: `python3 -m pytest tests/test_genai.py` returned `command not found`. Then running tests with the correct Python path failed with `ModuleNotFoundError: No module named 'pydantic'`
- What I thought: Python was available as `python3` (Unix convention), and the project virtualenv had all dependencies
- Root cause: On Windows, Python is registered as `python` or by full path, not `python3`. The project has no virtualenv — dependencies (pydantic, anthropic, boto3, pyyaml) were not installed in the system Python
- Fix: used full path `/c/Program\ Files/Python311/python.exe` and ran `pip install pydantic anthropic boto3 pyyaml` first
- Prevention: on Windows always use `python` or the full path; keep a `requirements-dev.txt` (or pyproject.toml) so anyone can `pip install -r requirements-dev.txt` before running tests
- Lesson: cross-platform dev needs explicit Python invocation conventions; don't assume `python3` exists on Windows

## [2026-04-19] — terraform not in PATH on second machine
- What happened: user ran `terraform plan` in CMD on a different laptop — `terraform` command not found
- What I thought: terraform was already installed from earlier chats
- Root cause: earlier chats ran on a different machine. This laptop had no terraform installed
- Fix: `winget install HashiCorp.Terraform` installed v1.14.8; used full winget path for the CLI session since PATH hadn't refreshed
- Prevention: document terraform version in README or `terraform/.terraform-version`; use tfenv or asdf for consistent versions across machines
- Lesson: treat CLI tools as project dependencies — pin the version, document the install method

## [2026-04-19] — terraform import ID format wrong for Glue catalog table
- What happened: `terraform import aws_glue_catalog_table.enrichment_scores jobpulse_gold_dev/enrichment_scores` failed with "expected ID in format catalog-id:database-name:table-name"
- What I thought: database/table slash format was correct (same as the AWS console path)
- Root cause: Terraform requires the AWS account ID as the catalog ID prefix: `{account_id}:{database}:{table}`
- Fix: `terraform import aws_glue_catalog_table.enrichment_scores 240939827246:jobpulse_gold_dev:enrichment_scores`
- Prevention: check `terraform import` docs for resource-specific ID format before running; Glue table always needs account_id prefix
- Lesson: Terraform import IDs are not URL paths — they encode the full resource address including account context

## [2026-04-19] — dbt-core ≥1.10 requires Python ≥3.10; Glue 4.0 is Python 3.9
- What happened: Glue dbt_runner job failed during pip install: `dbt-core could not be installed — requires Python >=3.10`
- What I thought: dbt-core 1.9.10 was safely within the Python 3.9 range
- Root cause: We had initially set `dbt-core==1.11.8` in glue.tf (latest at time of writing Chat 7). dbt-core 1.10 raised the minimum Python version to 3.10. Glue 4.0 Python Shell runs Python 3.9.
- Fix: downgraded to `dbt-core==1.9.10,dbt-athena-community==1.9.5` — last series that supports Python 3.9; verified with `python3 -c "import importlib.metadata; m=importlib.metadata.metadata('dbt-core'); print(m['Requires-Python'])"`
- Prevention: when choosing versions for a constrained runtime (Glue, Lambda), always check `Requires-Python` in package metadata before pinning
- Lesson: "latest" is not safe in runtimes with frozen Python versions — always verify runtime Python version first, then find the last compatible package version

## [2026-04-19] — dbt zip path is one level deeper than expected
- What happened: dbt_runner.py set `--project-dir /tmp/dbt_project` but dbt errored: `No dbt_project.yml found at expected path /tmp/dbt_project/dbt_project.yml`
- What I thought: extracting `dbt_project.zip` to `/tmp/dbt_project` would put `dbt_project.yml` directly in that directory
- Root cause: the zip was created with `zip -r /tmp/dbt_project.zip dbt_project/` from the repo root. This puts every file under a `dbt_project/` prefix inside the zip. Extracting to `/tmp/dbt_project` produces `/tmp/dbt_project/dbt_project/dbt_project.yml` — one extra level.
- Fix: added `DBT_PROJECT_DIR = os.path.join(DBT_LOCAL_DIR, "dbt_project")` and used it for `--project-dir`
- Prevention: when creating zips for remote extraction, either use `cd dbt_project && zip -r ../archive.zip .` (no prefix) or always extract and add the folder name suffix in the extraction path
- Lesson: zip -r from parent dir always preserves the source folder name as a prefix — the extraction path must account for it

## [2026-04-19] — dbt schema.yml `arguments:` wrapper removed in dbt 1.8+
- What happened: dbt run passed but `dbt test` failed: `macro 'dbt_macro__test_accepted_values' takes no keyword argument 'arguments'`
- What I thought: the schema.yml test syntax with `arguments:` was standard dbt
- Root cause: dbt 1.8 removed the `arguments:` wrapper for built-in tests (`accepted_values`, `relationships`, `not_null`, `unique`). The old dbt-utils syntax nested test params under `arguments:` but dbt adopted these tests natively with direct param placement.
- Fix: removed `arguments:` from all test entries in schema.yml; placed `values:`, `to:`, `field:` directly under the test name
- Prevention: when upgrading dbt versions, read the migration guide; built-in test syntax changed in 1.8
- Lesson: schema.yml test syntax differs between dbt versions — always match syntax to the dbt version actually being used

## [2026-04-19] — pyarrow ≥15 has no Python 3.9 wheels on Amazon Linux
- What happened: enrichment_runner Glue job failed during pip install: `pyarrow could not be installed — no pre-built wheel available for Python 3.9`
- What I thought: `pyarrow>=15.0.0` would resolve to a compatible version
- Root cause: pyarrow dropped Python 3.9 manylinux wheels starting from version 15.0. Glue 4.0 Python Shell runs Python 3.9 on Amazon Linux 2. Without a pre-built wheel, pip tries to build from source which requires Arrow C++ dev headers — not available in Glue's environment.
- Fix: pinned to `pyarrow==14.0.2` — the last release with Python 3.9 manylinux2014_x86_64 wheels
- Prevention: check PyPI "Download files" tab for a given version before pinning; look for `cp39-cp39-manylinux` wheel
- Lesson: "available for Python 3.9" and "has a pre-built wheel for Python 3.9 on Linux" are different things — always verify wheel availability for the target platform

## [2026-04-19] — Glue 4.0 Python Shell does not add --extra-py-files to sys.path
- What happened: enrichment_runner.py imported `from genai.jd_enrichment_agent import ...` and failed with `ModuleNotFoundError: No module named 'genai'` even though `--extra-py-files` was set to the genai_package.zip S3 path
- What I thought: --extra-py-files would work like --py-files in Spark: download and auto-add to sys.path
- Root cause: In Glue 4.0 Python Shell, `--extra-py-files` downloads the zip to `/tmp/glue-python-libs-.../` but does NOT automatically add it to `sys.path`. The script sees the zip file exists on disk but Python cannot import from it. (Glue 2.0 Python Shell did auto-add; behavior changed in 4.0.)
- Fix: bootstrap block at the top of enrichment_runner.py that detects the Glue environment (no `genai/` in the local repo root), downloads genai_package.zip from S3, extracts it to `/tmp/genai_pkg`, and calls `sys.path.insert(0, _GENAI_EXTRACT_DIR)` explicitly
- Prevention: for Glue Python Shell 4.0, never rely on --extra-py-files for imports — always write a bootstrap that downloads + extracts + inserts into sys.path
- Lesson: --extra-py-files semantics differ across Glue versions and job types (Spark vs Python Shell). When in doubt, do the sys.path management yourself.

## [2026-04-19] — pandas reads Athena CSV nulls as float NaN, not None
- What happened: enrichment_runner failed with `AttributeError: 'float' object has no attribute 'lower'` inside MatchScorer when calling `.lower()` on a field
- What I thought: empty/null columns in the Athena CSV would come through as None (Python null)
- Root cause: `pd.read_csv()` converts empty/NA cells to `float("nan")`. In Python, `nan or ""` evaluates to `nan` (NaN is truthy), so the guard `(val or "")` does not replace it with an empty string. `.lower()` on a float then raises AttributeError.
- Fix: added `df = df.where(pd.notnull(df), None)` immediately after `pd.read_csv()` in `_run_athena_query()` to replace all NaN with Python None — downstream code can then safely use `(val or "")` guards
- Prevention: always normalize NaN→None when passing pandas DataFrames to non-pandas code; `df.where(pd.notnull(df), None)` is the standard pattern
- Lesson: pandas NaN ≠ Python None. They behave differently in boolean contexts. Normalize at the boundary.

## [2026-04-19] — Pydantic v2 rejects int for str-typed field
- What happened: enrichment_runner failed with `pydantic.ValidationError: 1 validation error for EnrichmentRecord — job_id: string_type`
- What I thought: Pydantic would auto-coerce int → str for a `str` field (Pydantic v1 behavior)
- Root cause: Pydantic v2 uses strict type validation by default. `job_id` in the CSV is an unquoted integer (e.g., `12345`) which pandas reads as int64. Passing an int to a Pydantic v2 `str` field raises a validation error.
- Fix: `job_id=str(job["job_id"])` in both EnrichmentRecord instantiation sites in jd_enrichment_agent.py
- Prevention: when migrating to Pydantic v2, audit all model instantiation sites where int/float might be passed to str fields; explicit str() casts are safer than relying on coercion
- Lesson: Pydantic v2 is stricter than v1 — coercions that worked implicitly before now require explicit casts

## [2026-04-19] — Athena won't implicitly cast date to varchar in JOIN conditions
- What happened: the flat JOIN query in app.py ran without error but returned 0 matches in the enrichment_scores join — every match_score was -1 (the COALESCE fallback)
- What I thought: Athena would implicitly compare `date '2026-04-19'` to `varchar '2026-04-19'`
- Root cause: `fact_job_posting.snapshot_date` is a `date` type; `enrichment_scores.snapshot_date` is a partition key of type `string`. Athena (Trino) does not allow implicit date→varchar casting in JOIN conditions — the types must match exactly.
- Fix: changed JOIN condition to `CAST(f.snapshot_date AS VARCHAR) = e.snapshot_date`
- Prevention: when joining across tables with different snapshot_date types (dbt CTAS produces date; external Parquet tables have string partition keys), always cast explicitly
- Lesson: Athena JOIN conditions are type-strict. Mixed-type joins silently produce 0 matches rather than an error — always verify row counts when enrichment scores look wrong.

## [2026-04-20] — RemoteOK blocked by Cloudflare from Lambda (same as Himalayas)
- What happened: RemoteOK Lambda test returned 403 with `server: cloudflare` in response headers
- What I thought: RemoteOK had a free public JSON endpoint with no bot protection (documented as such)
- Root cause: RemoteOK uses Cloudflare bot protection that blocks AWS datacenter IPs. Lambda runs in AWS datacenters — IPs are well-known and fingerprinted by Cloudflare's bot detection. Same root cause as Himalayas (Chat 4).
- Fix: pivoted to Arbeitnow (tested from Lambda first, confirmed 200 OK with real data)
- Prevention: before writing a full ingestor, make a single test HTTP call from Lambda to confirm the API responds. Never trust "no key needed" docs without live verification from a Lambda IP.
- Lesson: "no API key needed" ≠ "accessible from Lambda". Cloudflare blocks datacenter IPs regardless of API key requirements. Test from the actual execution environment first.

## [2026-04-20] — EmptyDataError: Athena empty result is a 0-byte file, not a 0-row CSV
- What happened: `enrichment_runner.py` crashed with `EmptyDataError: No columns to parse from file` when the enrichment query for `snapshot_date=2026-04-19` returned 0 rows
- What I thought: an empty Athena query result would produce a CSV with just a header row
- Root cause: Athena writes a completely empty file (0 bytes) when a query returns no rows — not even a header. `pd.read_csv()` raises `EmptyDataError` on a 0-byte file. The `if not jobs:` guard was never reached because the crash happened inside `_run_athena_query()`.
- Fix: read raw bytes first: `content = obj["Body"].read()`. Check `if not content.strip(): return []` before calling `pd.read_csv()`.
- Prevention: any code calling `pd.read_csv()` on Athena output must guard for 0-byte case. Athena "no results" ≠ empty result set — it's an empty file.
- Lesson: Athena empty result = empty file, not empty table. Always check `content.strip()` before parsing Athena CSV output.

## [2026-04-20] — Step Functions Redrive ran old Glue script (race with S3 upload)
- What happened: uploaded fix to S3, then attempted Redrive of failed 2AM execution — still failed with same EmptyDataError
- What I thought: Glue would fetch the updated script from S3 on the next run
- Root cause: Redrive ran nearly simultaneously with the S3 upload. Glue fetched the script before the upload completed. The 2AM execution also had `snapshot_date=2026-04-19` — gold tables were since rebuilt for Apr 20, so any retry would return EMPTY anyway.
- Fix: the Apr 20 8:50 AM manual run used the fixed script and succeeded (121 jobs enriched). Redrive for Apr 19 is now moot.
- Prevention: after uploading a script fix to S3, confirm the object's LastModified timestamp before triggering a Redrive. For stale snapshot_dates, don't Redrive — just run the pipeline fresh for today's date.
- Lesson: Glue fetches script from S3 at job start — race condition possible if upload and Redrive happen within seconds. Confirm S3 object is updated before retrying.

## [2026-04-21] — COALESCE references field that normalize_jobs already mapped away
- What happened: Glue bronze-to-silver job failed with `AnalysisException: No such struct field redirect_url in apply_url, candidate_required_location, ..., url`
- What I thought: Adzuna's `redirect_url` field would appear in the bronze JSON, requiring a COALESCE fallback in the Spark job
- Root cause: `normalize_jobs()` in `ingest_adzuna.py` already maps `redirect_url → apply_url` before writing to bronze. So `redirect_url` never exists in the schema. The COALESCE `F.col("job.redirect_url")` referenced a field that no source ever wrote — Spark's schema analysis caught it as a hard error.
- Fix: removed `F.col("job.redirect_url")` from the apply_url COALESCE in `build_silver_df()`. The two-way COALESCE (`apply_url` → `url`) is sufficient: Adzuna writes `apply_url`, Remotive writes `url`.
- Prevention: when adding a new COALESCE column, check what keys actually land in the bronze JSON, not what the raw API returns. The ingestor's `normalize_jobs()` is the authoritative schema — COALESCE covers field names that differ between ingestors, not pre-normalization raw API fields.
- Lesson: COALESCE in Spark is for different ingestors writing different field names. If normalize_jobs standardises the name, no COALESCE needed on the Spark side.

## [2026-04-18] — s3.tf trailing space in filename
- What happened: terraform plan showed "No changes" despite 14 resources to create
- What I thought: credentials issue or provider misconfiguration
- Root cause: VS Code saved file as "s3.tf " (trailing space) — Terraform couldn't load it
- Fix: mv "s3.tf " s3.tf
- Prevention: always run `ls -la` to spot hidden filename issues
- Lesson: "No changes" with empty state = file not loading, not a credentials issue