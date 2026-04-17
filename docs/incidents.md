# Incidents

## [2026-04-18] — s3.tf trailing space in filename
- What happened: terraform plan showed "No changes" despite 14 resources to create
- What I thought: credentials issue or provider misconfiguration
- Root cause: VS Code saved file as "s3.tf " (trailing space) — Terraform couldn't load it
- Fix: mv "s3.tf " s3.tf
- Prevention: always run `ls -la` to spot hidden filename issues
- Lesson: "No changes" with empty state = file not loading, not a credentials issue