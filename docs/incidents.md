# Incidents

## [2026-04-18] — Himalayas API blocked by Cloudflare
- What happened: Lambda invoked, got HTTP 403 Forbidden from Himalayas API
- What I thought: Lambda IP block or User-Agent issue
- Root cause: Himalayas added Cloudflare bot protection (cf-mitigated: challenge) — requires browser JS execution, blocks all programmatic clients
- Fix: Added Remotive as replacement live source; Himalayas Lambda kept for future
- Prevention: test API accessibility with curl before building ingestor
- Lesson: "Free public API" doesn't guarantee programmatic access — always curl-test before coding

## [2026-04-18] — Terraform apply failed: empty worktree tfstate
- What happened: terraform apply tried to create all 14 resources, failing with EntityAlreadyExists / BucketAlreadyOwnedByYou
- What I thought: IAM/S3 conflict from a previous partial apply
- Root cause: Worktree had an empty tfstate — Terraform didn't know resources already existed in AWS from Chat 3's apply
- Fix: Copied terraform.tfstate from the main dev directory into the worktree
- Prevention: when working in a worktree, always copy existing tfstate before apply
- Lesson: Terraform state is the source of truth — empty state = Terraform thinks nothing exists

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