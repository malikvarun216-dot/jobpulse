# Incidents

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

## [2026-04-18] — s3.tf trailing space in filename
- What happened: terraform plan showed "No changes" despite 14 resources to create
- What I thought: credentials issue or provider misconfiguration
- Root cause: VS Code saved file as "s3.tf " (trailing space) — Terraform couldn't load it
- Fix: mv "s3.tf " s3.tf
- Prevention: always run `ls -la` to spot hidden filename issues
- Lesson: "No changes" with empty state = file not loading, not a credentials issue