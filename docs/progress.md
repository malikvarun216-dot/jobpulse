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

