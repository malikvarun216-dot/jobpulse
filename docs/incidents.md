# Incidents

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

## [2026-04-18] — s3.tf trailing space in filename
- What happened: terraform plan showed "No changes" despite 14 resources to create
- What I thought: credentials issue or provider misconfiguration
- Root cause: VS Code saved file as "s3.tf " (trailing space) — Terraform couldn't load it
- Fix: mv "s3.tf " s3.tf
- Prevention: always run `ls -la` to spot hidden filename issues
- Lesson: "No changes" with empty state = file not loading, not a credentials issue