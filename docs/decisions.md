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

## Least-privilege Lambda IAM policy (Chat 3)
- Lambda role scoped to bronze bucket only (PutObject + GetObject)
- Cannot touch silver/gold — blast radius limited if Lambda is compromised
- CloudWatch Logs added separately (required for Lambda to emit logs)