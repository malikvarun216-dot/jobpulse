# JobPulse Roadmap — Data + AI track (Chat 24 onward)

Last updated: **Chat 27 part 2 (2026-10-18)**. Update this file at the end of every chat: tick the chat, adjust the next one.

## Why this roadmap exists

JobPulse was built in Chats 1–22 (Apr 16–26, 2026) and ran unattended until it started failing in August
(see `progress.md` → "Production run"). The goal now is to turn it into an **AI Data Engineer** project:
embeddings, vector stores, retrieval evaluation, LLM extraction quality, grounded generation — all measured.

Ground rules for every chat:
- **Measure before adding complexity.** Every AI change must move a number on the eval set. No number, no claim.
- **Stay on AWS (paid account, shared with ledgerline).** Account-wide budgets: $20/month, $2/day spike.
- **Dashboard runs locally on demand** (`streamlit run`), the pipeline stays laptop-independent.
- **Learning notes + interview guide are updated every chat** (`docs/learning.md`, `docs/interview_guide.md` — local only, not in git).
- Ledgerline covers lakehouse / streaming / CDC / governance / point-in-time ML. JobPulse owns **AI-for-data**. No overlap.

## Status board

| Chat | Theme | Status |
|---|---|---|
| 23 | Recon, AWS decision, docs overhaul, interview guide | ✅ done (2026-10-06) |
| 24 | Stabilize the account and repo (must finish before ~Oct 16) | ✅ done (2026-10-07) |
| 25 | Fix the failures, go live again | ✅ live 2026-10-08 — nights 1–3 (Oct 9–11) green; closes after the alarm test |
| 26 | AI concepts lab (learning session) | ✅ built + committed `79aa6ba` (2026-10-10); 90-s drill parked (Varun) |
| 27 | Evaluation foundations: freeze a corpus, label it | 🟡 part 2 (2026-10-18): `corpus_v1` frozen (9,266 jobs), pool + label page + judge + κ built · waiting on real queries + 300 human labels |
| 28 | Baseline + better retrieval (hybrid, chunk grain) | |
| 29 | Vector stores on AWS: pgvector (RDS) vs S3 Vectors, by eval | 🟡 part 1 local (2026-10-29): Docker pgvector loaded + benchmarked vs NumPy · RDS / S3 Vectors after labels |
| 30 | Embeddings as a real pipeline (incremental, versioned, freshness) | |
| 31 | Evaluate the LLM extraction (is the LLM worth it?) | |
| 32 | Grounded generation: weekly AI brief | |
| 33 | Narrative + resume | |

---

## Before Chat 24 — things only Varun can do

1. ~~Upgrade the AWS account to the paid plan~~ — ✅ **already PAID** (checked 2026-10-06 via `aws freetier get-account-plan-state`: `PAID / ACTIVE`, $68.13 credits left). The account is **shared with the ledgerline project**.
2. ~~AWS Budgets~~ — ✅ already covered: three **account-wide** budgets (no filters) cover JobPulse too — `ledgerline-monthly` $20 (alerts at 50/80/100% actual + 100% forecast), `ledgerline-daily-spike` $2/day, `My Zero-Spend Budget` $0.01. ⚠️ The $20/month is shared by both projects — RDS in Chat 29 (~$13–18 while running) needs it raised or the instance stopped between sessions.
3. **Sync the main checkout** — local `dev` was stuck at Chat 9 (`ccf0cc4`):
   ```bash
   git pull --ff-only origin dev
   ```
   (The uncommitted `eventbridge.tf` change survives — that file did not change upstream.) Then commit it:
   `fix: disable nightly schedule while pipeline is paused`.
4. **Rotate the Adzuna key** at developer.adzuna.com — the old one is committed in `terraform/envs/dev/terraform.tfvars` on GitHub.
5. **Never run `terraform apply` from an out-of-date checkout** — it plans to destroy everything built after it.

## Chat 24 — Stabilize (before ~Oct 16)

- Move `adzuna_app_id` / `adzuna_app_key` out of `terraform.tfvars` → `TF_VAR_` env vars / GitHub secret; gitignore `terraform.tfvars`.
- Remove the EC2 dashboard resources from `ec2.tf` and the EC2 job from `deploy.yml` (instance + EIP are already gone in AWS; Terraform still declares them → drift).
- Cost hygiene in Terraform: lifecycle rule on `gold/athena-results/` (expire 7 days — 1.5 GB of old query CSVs today); CloudWatch log retention 14 days; pin `anthropic`, `voyageai`, `great-expectations` versions in `glue.tf`.
- Local backup: `aws s3 sync` silver + `embeddings/` + `enrichment-*` + gold tables (~0.9 GB).
- `terraform plan` from the synced tree → only the intended changes.
- Activate the `project` cost-allocation tag (Billing → Cost allocation tags) so Cost Explorer can split JobPulse vs ledgerline spend on the shared account.
- Docs: runbook entries for anything new; interview guide → AWS section (cost hygiene, drift).

**Done when:** plan is clean, key rotated, backup file count matches S3, Budgets active.

**Result (Chat 24):** ✅ secrets → `TF_VAR_`, EC2 out of IaC + CI, 14-day log retention (import blocks), Glue pins,
`.gitattributes` LF, backup 943 MB (counts match). ✅ Budgets (already active). ✅ Applied, key rotated + verified, tfvars removed,
cost allocation tags `project` + `layer` active. ❌ Moved to Chat 25: `athena-results/` expiry (gold tables live there). Plan has a known benign diff
(CI + Terraform both deploy code).

## Chat 25 — Fix the failures, go live

- **Enrichment timeout:** add per-stage timing logs (`genai/enrichment_runner.py`, `genai/jd_enrichment_agent.py`) → find where the ~50 min goes. Make enrichment incremental (only JDs not already scored, keyed by content hash across days). Right-size DPU. Alarm when Glue duration > 70% of timeout.
- **`tags` drift:** read the Aug 1–9 Glue error logs; normalize `tags` to `array<string>` from string *or* array in `spark/jobs/bronze_to_silver.py`; explicit schema instead of inference; unit test with both shapes; GE expectation on the type.
- **Alarm:** replace the self-resetting failure alarm with an **absence-of-success** alarm (`ExecutionsSucceeded < 1` per day, missing data = breaching).
- **Real gates:** `dbt_runner.py` → `dbt build` (21 tests start gating); GE checks `snapshot_date` from the data, not an injected value; fix/delete the 2 contradictory PySpark tests.
- **Empty-context bug:** pass `description` to the "Why these match?" call (`dashboard/streamlit/app.py`).
- **Gold tables out of `athena-results/`:** make dbt write to `gold/models/` (workgroup enforcement currently drops
  `s3_data_dir`), run once, verify catalog locations, **then** add the 7-day expiry on query results. Clean orphaned
  `athena-results/tables/*` (only the 4 live folders are referenced).
- **One owner for code deploys:** CI or Terraform, not both (`lifecycle { ignore_changes }` or drop CI uploads).
- Use the exported Glue logs (`jobpulse_backup/2026-10-06/logs/`) to find which source changed `tags`.
- Re-enable EventBridge.

**Done when:** 3 green nights in a row; a deliberate failure fires the new alarm once and does not auto-reset.

**Result (Chat 25, code):** root causes measured, not guessed —
- Enrichment hour = every Claude call failing since 2026-04-21, each retried with 7 s of sleeps; empty Greenhouse JDs sent
  ~all jobs down that path; buffered stdout hid it. Rules for 5,729 JDs take 4 s → **incremental enrichment not built**
  (would save 4 s). Fixed: flushed logs + timings, no own retry loop, circuit breaker, LLM off (`--use_llm false`),
  timeout 60 → 20. Agent on real data: 4.4 s / 14 s (LLM failing) vs ~60 min.
- `tags`: explicit bronze schema (strings) + `parse_tags()`; source not identifiable (bronze expired).
- Gates: `dbt build` (first run vs live gold found 2 failing tests → `dim_company` fan-out +75%, fixed), GE freshness from
  `ingested_at`, `tags` type check, PySpark tests running in CI for the first time (9 of 14 had been failing).
- Alarms: absence-of-success (26 × 1 h) + duration > 70% of timeout. Gold tables → `gold/models/`. CI owns code deploys.
- Go-live 2026-10-08: applied (4/4/3), manual run green in 9 min 36 s (enrichment 23 s, dbt 26/26), tables in `gold/models/`,
  then schedule + expiry enabled. Found during go-live: Arbeitnow `KeyError: 0` (PHP object instead of list) caused the
  sub-second August failures → `as_list()`; the fact table itself was ~55% inflated by the `dim_company` bug.
- Open: 3 green nights; why Claude calls fail (key or credits); Greenhouse descriptions (`?content=true`) — decision;
  999 silver rows share source + job_id + day (dedup, Chat 30).

## Chat 26 — AI concepts lab (no AWS changes)

> First 5 minutes: Chat 25 night check — last 3 Step Functions executions green? enrichment `[timing] total` < 60 s?
> `jobpulse-sfn-no-success-26h-dev` left "Unchecked"? (if not → metric-math `FILL(m1, 0)` rebuild).

Notebook on ~20 real JDs from the exported silver data:
1. Embed 5 JDs, look at the 512 numbers, print the similarity matrix.
2. 3 queries where keyword misses and vector finds; 3 where the reverse happens (exact tool names).
3. Split one long JD into sections; see which section matches a query; see what the 4,000-char cut throws away.
4. Mini-RAG: "why does this job match me?" **without** the JD text, then **with** it. Compare.
5. Label top-10 for 2 queries by hand, compute precision@10 on paper.

**Done when:** each concept explained aloud in 90 seconds with a JobPulse example.

> ✅ Result (Chat 26): `notebooks/chat26_ai_concepts_lab.ipynb`, run on Oct 9 live data (1,544 vectors over the full
> embedded set, not 20 JDs). Side findings: vectors are 1024-d; Arbeitnow JDs are raw HTML; the Claude 401 root cause.

## Chat 27 — Evaluation foundations

> Prerequisite from Chat 25: ~70% of jobs (Greenhouse) have **no description** and Adzuna's are 500-char snippets.
> A corpus frozen from today's data would be mostly titles. Decide on Greenhouse `?content=true` first and let a few
> weeks of full JDs accumulate (pin those dates).

- Frozen corpus: distinct jobs from pinned dates (e.g. Jul 1–31) → `s3://jobpulse-gold-dev/eval/corpus_v1/` + manifest (dates, row count, content hash). Never changes; new versions = new folders.
- `eval/queries_v1.yml`: ~50 queries (your real searches, keyword-heavy, paraphrase, filtered, edge cases).
- Pooling: union of top-20 from BM25, NumPy vector, current hybrid → ~2,500 query–job pairs.
- Labels 0/1/2: hand-label ~300 pairs in a tiny local Streamlit page; Claude judge with a written rubric labels all; Cohen's κ between judge and you (≥ ~0.6 → trust the judge). Output `eval/qrels_v1.parquet`.

> ✅ Part 1 (2026-10-11): `?content=true` **on** (4,466 full JDs/night; Lambda 1024 MB). `eval/freeze_corpus.py` (immutable
> corpus + manifest with content hash) and `eval/queries_v1.yml` (50 drafts with `relevant_if`). Pinned dates:
> **Oct 12–14** (Greenhouse returns every open job nightly — no need to wait weeks).
>
> Part 2 (on/after Oct 14): check the Oct 12–14 silver has Greenhouse JDs → `freeze_corpus.py --dates 2026-10-12 2026-10-13 2026-10-14`
> → Varun swaps draft queries for real ones → pooling (BM25 + NumPy vector + current hybrid, top-20 each) → labeling page → judge → κ.
> ✅ Part 2 (2026-10-18): `corpus_v1` frozen (9,266 jobs, all with a JD, hash `f8383eff…`). `eval/pool.py` → 2,246 pairs on
> the drafts (BM25 and vector agree on only 94). `label_app.py`, `judge.py` (Batches, JSON schema), `agreement.py` (κ → qrels).
> 2026-10-30: 108 human labels; κ vs Sonnet 0.29 → 0.69 after reviewing 52 disagreements (Haiku 0.47). Judge-all (≈ $5)
> **paused** by choice — resume command in progress.md. Queries rewritten from the resume the same day (2,302 pairs). **Left:** Varun reviews queries → re-pool → label 300 → judge human sample → κ → judge all →
> `eval/qrels_v1.parquet`. Judge cost (batch): Haiku 5.5 $0.04 / Sonnet 5.5 $0.83 on the 300; all pairs $0.32 / $6.33.
>
> **Keyword lift (2026-10-19, corpus_v1):** share of jobs mentioning a skill that have a data title vs overall — dbt 22×,
> Airflow 22×, BigQuery 18×, ETL/ELT 10×, warehouse 9×, Snowflake 9×, Hadoop 8× (strong DE); Kafka 6×, SQL 5×, Spark 4×,
> Python 4× (shared); Docker 1.7×, AWS 1.4×, Terraform 0.9× (SDE/DevOps). Basis for the `role_family` fix: title says data
> OR ≥ 2 strong-DE keywords → DATA. Spark is inflated by Databricks boilerplate ("creators of Apache Spark").
>
> Found in part 1, for later chats: `role_family = Other` on 93% of silver rows (the role filter is useless — fix before
> the dashboard/brief rely on it); 11 / 30 Greenhouse slugs 404; 8 Adzuna duplicate rows within one snapshot.

## Chat 28 — Baseline + better retrieval

> 🟡 Harness built early (2026-10-18): `eval/search.py` (bm25, vector, dashboard, rrf) + `eval/run_eval.py` (metrics,
> judged@10, latency, results file). Waiting only on `qrels_v1`. Still to add here: chunk grain, HTML strip, Voyage
> `input_type`, reranker, match_score normalization fix.
>
> 🟡 Part 1 (2026-10-30): provisional run on the 108 human labels (judged@10 only 2–8% → not a ranking; harness smoke test).
> Built, not yet measured: `eval/embed_variants.py` (variants `strip`, `strip_doc`, `strip_doc_title`, `chunks`; lab-only)
> + `run_eval.py --variants` and `dashboard_fixed`; `MatchScorer` `skill_full_at` (off unless set in the profile; at 12 →
> 404 corpus jobs reach 70, was 0). Dry-run: 50% of jobs lose text at the 4,000 cut after stripping; chunks = 36,115 texts
> (3.9 / job). Embedded (19.7M Voyage tokens): provisional nDCG@10 vector 0.225 → `strip_doc` 0.268; `input_type` fixes bare
> keywords (`spark` no longer → DRIVER); chunks' top 10 is 42% outside the pool. **Left:** add variants to the pool → qrels →
> score → pick by numbers → ship the winner to `embedding_agent.py` / the profile. Reranker not started.

- `eval/run_eval.py`: one interface `search(query, k) -> [job_id]`; writes metrics + latency p50/p95 + cost per run to `eval/results.parquet`.
- Compare: BM25, NumPy vector (exact), **today's 0.5/0.5 hybrid (baseline)**, RRF hybrid, optional reranker on top-50, optional query rewriting.
- Chunk grain experiment: one vector per JD (4,000-char cut) vs section chunks (responsibilities / requirements / benefits, max-score roll-up). Report truncation rate.
- From Chat 26: strip HTML before embedding (Arbeitnow window is ~25% markup; 79% of Arbeitnow JDs cut at 4,000) — a cheap
  candidate to measure first. Query set must mix descriptive queries *and* bare tool names (vector loses those: Snowflake, Airflow, dbt).
- From Chat 27b: vector search fails bare keywords badly (`snowflake` → sales associate, `spark` → "DRIVER"). Markup is only
  ~12% of the 4,000-char window, so test also: Voyage `input_type="query"/"document"`, and skipping company boilerplate.
  The dashboard "hybrid" isn't keyword + vector — it blends in the query-independent match_score.
- From Chat 27b (profile rebuild): **match_score barely uses skills** — skill overlap averages 1.6 / 50 points because it is
  divided by the total weight of all profile skills; no corpus job reaches 70. Fix the normalization (e.g. against the job's
  own skills or the top-N matched) and check it on the eval set before shipping — it also changes the dashboard hybrid.
- Pick fusion, k and grain **by the numbers**.

## Chat 29 — Vector stores on AWS, compared by eval

> 🟡 Part 1 local (2026-10-29): `vectorstore/` — Docker Postgres 17 + pgvector 0.8, corpus_v1 loaded, vector / keyword /
> hybrid-RRF in SQL. Recall@10 vs exact: default HNSW (ef 40) **0.83**, ef 100 0.92, ef 200 0.97; m 32 → 0.97 at ef 100.
> Filtered HNSW without iterative scan can return short. Planner skips HNSW at this size (seq scan, 33 ms). Left: score pg
> methods in `run_eval.py` on qrels, then one RDS + S3 Vectors session, decision in decisions.md.

- **pgvector on RDS Postgres** (db.t4g.micro, Terraform). Loader Lambda inside the VPC + **S3 gateway endpoint** (free) + **IAM DB auth** → no NAT gateway (~$30+/mo trap). Laptop access: SG allows only your IP /32, SSL required. HNSW index + `tsvector` hybrid in one SQL query.
- **Amazon S3 Vectors** (GA in ap-south-1): vector bucket + index (**1024-d** — measured Chat 26; 512 only if Chat 30 re-embeds with `output_dimension=512`, cosine) with metadata (country, seniority, role_family, snapshot_date). No VPC.
- Measure both vs **exact NumPy as ground truth**: recall@10, nDCG@10 on qrels, p95 latency, $/month, HNSW `ef_search` sweep.
- Decision with numbers in `decisions.md`; wire winner into dashboard; stop/tear down the other (RDS can be stopped between sessions).

## Chat 30 — Embeddings as a real pipeline

- Incremental + idempotent: key = `sha256(normalized JD) + model_id`, upsert. Removes the daily re-embed of ~7.8K jobs.
- The embedding query reads `description` from the **unpartitioned** `fact_job_posting` (full-column scan nightly) — read from silver by `snapshot_date` instead.
- Model versioning: new model → new index version → backfill → **eval gate** → switch (blue/green). Challenger: **Bedrock Titan Text Embeddings v2** (check ap-south-1 availability).
- Freshness: `last_seen_date`; a posting unseen for N days = closed; search only open postings.
- Near-duplicate dedup: cosine threshold chosen by labeling ~200 pairs (precision/recall of the dedup decision).
- RAG health metrics table + dashboard tab: ingested/day, parse failures, truncation %, embeddings/day and $, retrieval p50/p99, eval hit rate over time, generation $.

## Chat 31 — Evaluate the LLM extraction

> From Chat 25: LLM extraction is **off** (`--use_llm false`). Chat 26 found why no call succeeded: the JSON secret was
> sent raw (401) — fixed with `parse_api_key()`. Turning the LLM back on now **really spends** (cap $0.50/day). The code
> still pins `claude-haiku-4-5-20251001` ($1/$5 per M tokens); `claude-haiku-5-5` is $0.10/$0.50 — compare both in the eval.
> Add a startup ping call so a 401 fails the run once, not per job.

- Hand-label skills / seniority / yoe for 150 JDs (stratified by source and rules-vs-LLM path).
- Per-field precision / recall / F1: rules-only vs rules + Haiku; % JDs sent to the LLM; $ per 1K JDs.
- Fix: budget tracker check-then-reserve race; price constants (Haiku 3.5 numbers in code); `cache_control` on a prompt below the minimum cacheable size; Haiku may wrap JSON in ``` fences (json.loads fails); Message Batches API for the nightly run (measure the saving).
- CI regression with recorded LLM responses (no API calls in CI).

## Chat 32 — Weekly AI brief (grounded generation)

- SQL → facts JSON (week-over-week counts, rising skills, top companies). Claude writes only from the facts, cites `job_id`s.
- Automated faithfulness check: every number in the brief must match the facts JSON.
- JD text treated as untrusted (prompt-injection test JD).
- Every LLM call logged: prompt version, retrieved ids, response, tokens, latency, $.

## Chat 33 — Narrative + resume

- Final pass on `docs/interview_guide.md`.
- Resume bullets with the measured numbers (templates in the interview guide, section 13).
- Claim GE + dbt tests only after Chat 25 makes them real gates. **Never** claim an orchestrator/sub-agent pattern.

## Rough monthly cost (approximate — check the AWS Pricing Calculator)

| Item | Estimate |
|---|---|
| Pipeline (Glue, Lambda, Step Functions, S3, Athena, Secrets Manager) | ~$3–6 |
| RDS t4g.micro (Chat 29+, while running) | ~$13–18 (stop between sessions) |
| S3 Vectors | cents |
| Voyage embeddings | a few $, falls after Chat 30 |
| Claude | ~$1–5 one-time for judge labelling, then low |
