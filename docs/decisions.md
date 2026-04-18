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