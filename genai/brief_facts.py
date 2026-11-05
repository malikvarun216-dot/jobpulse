"""
Facts for the weekly brief (Chat 32): SQL on silver + enrichment → one JSON dict.

The LLM never sees a table — only this dict. Every number the brief may print is computed here, so a
number that is not in the dict is by definition invented (weekly_brief.check_faithfulness).

Week = the 7 snapshots ending at week_end; "previous week" = the 7 before. Postings are a daily stock
(what the APIs list that day), so "open now" compares two snapshots; "new" = job_ids seen this week
and not in the previous week.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd

from genai.text_utils import strip_html

SILVER = "jobpulse_silver_dev.silver_jobs"
ENRICH = "jobpulse_gold_dev.enrichment_scores"   # snapshot_date is varchar here, date in silver

# Snapshots from which a number means something different. A week-over-week comparison that spans
# one is a pipeline artifact, not the market — the brief must not report it as a trend.
KNOWN_BREAKS = [
    ("2026-10-24", "role_family", ("role_family rules changed (title first, Chat 28); the Oct 30 deploy "
                                   "relabelled snapshots from 2026-10-24 on")),
]
# Sources that list a company's whole board every day. Adzuna (fixed pages per country) and Arbeitnow
# (latest 300) rotate: on 2026-11-03 they had 9,895 + 1,831 "new" job_ids in a week from ~1,800 rows a day.
COMPLETE_SOURCES = ("greenhouse", "remotive")
# The rule extractor matches these as English words ("go" hit 1,483 jobs at 64 companies on 2026-11-03).
AMBIGUOUS_SKILLS = {"go", "r"}
STATIC_NOTES = [
    ("Adzuna and Arbeitnow return a rotating slice of listings, so their open counts reflect pages fetched, "
     "not market size. New-job counts and skills use only complete company boards (Greenhouse, Remotive)."),
    ("Skills come from the rule-based extractor (LLM extraction is off), counted per distinct job; "
     "'go' and 'r' are left out because the rules match the English words."),
    ("A skill whose jobs mostly come from one company (top_company_jobs close to jobs) reflects that "
     "company's job-ad template, not the market."),
]
MIN_SKILL_JOBS = 30      # a skill needs this many jobs this week to be called rising / falling
# ...and a share move of at least this many percentage points. With ~4,800 jobs a week, a 20% share
# has a sampling wobble of about ±0.8 pp week to week (2 × standard error), so smaller moves are noise.
MIN_MOVE_PP = 2.0
EXCERPT_CHARS = 600
TOP_K = 5


def week_bounds(week_end: date) -> dict[str, date]:
    return {"start": week_end - timedelta(days=6), "end": week_end,
            "prev_start": week_end - timedelta(days=13), "prev_end": week_end - timedelta(days=7)}


def comparable(field: str, a: date, b: date) -> bool:
    """False when a known break for `field` lies in (a, b] — the two snapshots use different rules."""
    return not any(f == field and a < date.fromisoformat(d) <= b for d, f, _ in KNOWN_BREAKS)


def pct_change(new: int, old: int) -> float | None:
    return round(100.0 * (new - old) / old, 1) if old else None


def skill_movers(rows: pd.DataFrame, top_n: int = 5) -> dict:
    """rows: wk ('this'/'prev'), skill, company, n, total → share of jobs per skill and the biggest moves.

    n is distinct jobs per (week, skill, company); a job has one company, so summing over companies
    gives distinct jobs per skill. The top company per skill shows when one employer's template drives it.
    """
    rows = rows[~rows["skill"].isin(AMBIGUOUS_SKILLS)]
    totals = rows.groupby("wk")["total"].first().to_dict()
    wide = rows.pivot_table(index="skill", columns="wk", values="n", aggfunc="sum", fill_value=0)
    wide = wide.reindex(columns=["this", "prev"], fill_value=0)
    wide["share"] = (100.0 * wide["this"] / totals.get("this", 1)).round(1)
    wide["prev_share"] = (100.0 * wide["prev"] / totals.get("prev", 1)).round(1)
    wide["change_pp"] = (wide["share"] - wide["prev_share"]).round(1)
    this = rows[rows["wk"] == "this"].sort_values(["n", "company"], ascending=[False, True])
    top_co = this.drop_duplicates("skill").set_index("skill")
    big = wide[wide["this"] >= MIN_SKILL_JOBS]

    def rec(df: pd.DataFrame) -> list[dict]:
        return [{"skill": s, "jobs": int(r["this"]), "share_pct": float(r["share"]),
                 "prev_share_pct": float(r["prev_share"]), "change_pp": float(r["change_pp"]),
                 "top_company": top_co["company"].get(s), "top_company_jobs": int(top_co["n"].get(s, 0))}
                for s, r in df.iterrows()]

    return {"jobs_this_week": int(totals.get("this", 0)), "jobs_prev_week": int(totals.get("prev", 0)),
            "top": rec(wide.sort_values("this", ascending=False).head(10)),
            "min_move_pp": MIN_MOVE_PP,
            "rising": rec(big[big["change_pp"] >= MIN_MOVE_PP].sort_values("change_pp", ascending=False).head(top_n)),
            "falling": rec(big[big["change_pp"] <= -MIN_MOVE_PP].sort_values("change_pp").head(top_n))}


def _sql(w: dict[str, date]) -> dict[str, str]:
    d = {k: f"DATE '{v}'" for k, v in w.items()}
    s = {k: f"'{v}'" for k, v in w.items()}
    complete = ", ".join(f"'{x}'" for x in COMPLETE_SOURCES)
    return {
        "open": f"""SELECT CAST(snapshot_date AS varchar) AS d, source, count(DISTINCT job_id) AS jobs FROM {SILVER}
                    WHERE snapshot_date IN ({d['end']}, {d['prev_end']}) GROUP BY 1, 2""",
        "companies": f"""SELECT count(DISTINCT company_name) AS companies FROM {SILVER}
                         WHERE snapshot_date = {d['end']}""",
        "new": f"""WITH t AS (SELECT job_id, min(source) AS source FROM {SILVER}
                      WHERE snapshot_date BETWEEN {d['start']} AND {d['end']} GROUP BY 1),
                    p AS (SELECT DISTINCT job_id FROM {SILVER}
                      WHERE snapshot_date BETWEEN {d['prev_start']} AND {d['prev_end']})
                    SELECT t.source, count(*) AS new_jobs FROM t LEFT JOIN p ON t.job_id = p.job_id
                    WHERE p.job_id IS NULL GROUP BY 1""",
        "roles": f"""SELECT CAST(snapshot_date AS varchar) AS d, role_family, count(DISTINCT job_id) AS jobs
                     FROM {SILVER} WHERE snapshot_date IN ({d['end']}, {d['prev_end']}) GROUP BY 1, 2""",
        "skills": f"""WITH j AS (SELECT DISTINCT job_id, company_name FROM {SILVER}
                        WHERE snapshot_date BETWEEN {d['prev_start']} AND {d['end']} AND source IN ({complete})),
                      e AS (SELECT e.job_id, e.skills, j.company_name,
                        CASE WHEN e.snapshot_date >= {s['start']} THEN 'this' ELSE 'prev' END AS wk
                        FROM {ENRICH} e JOIN j ON j.job_id = e.job_id
                        WHERE e.snapshot_date BETWEEN {s['prev_start']} AND {s['end']}),
                      tot AS (SELECT wk, count(DISTINCT job_id) AS total FROM e GROUP BY 1)
                      SELECT e.wk, sk AS skill, e.company_name AS company, count(DISTINCT e.job_id) AS n,
                        max(tot.total) AS total
                      FROM e CROSS JOIN UNNEST(e.skills) AS u(sk) JOIN tot ON e.wk = tot.wk GROUP BY 1, 2, 3""",
        "data_companies": f"""SELECT company_name AS company, count(DISTINCT job_id) AS data_jobs FROM {SILVER}
                         WHERE snapshot_date = {d['end']} AND role_family = 'DATA'
                           AND company_name IS NOT NULL AND lower(company_name) <> 'unknown'
                         GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 8""",
        # Candidates for "Worth a look": still open on the last day, not seen the week before, complete boards only
        # (Adzuna/Arbeitnow rotate, and their descriptions are short snippets). Ranked in Python: by the retriever
        # (genai/brief_retrieval.py, RAG) or, without one, by match_score (the pre-RAG pick).
        "candidates": f"""WITH p AS (SELECT DISTINCT job_id FROM {SILVER}
                         WHERE snapshot_date BETWEEN {d['prev_start']} AND {d['prev_end']})
                       SELECT s.job_id, s.title, s.company_name AS company, s.country, s.apply_url, s.description,
                         CAST(round(e.match_score) AS integer) AS match_score
                       FROM {SILVER} s
                       LEFT JOIN p ON p.job_id = s.job_id
                       LEFT JOIN {ENRICH} e ON e.job_id = s.job_id AND e.snapshot_date = {s['end']}
                       WHERE s.snapshot_date = {d['end']} AND p.job_id IS NULL AND s.source IN ({complete})
                         AND s.description IS NOT NULL""",
    }


def pick_by_match_score(cand: pd.DataFrame, k: int = TOP_K) -> pd.DataFrame:
    """The pre-RAG pick: top K by match_score (the same for every query), opening EXCERPT_CHARS of the JD."""
    top = cand.sort_values(["match_score", "job_id"], ascending=[False, True], na_position="last").head(k)
    return top.assign(excerpt=[strip_html(x)[:EXCERPT_CHARS] for x in top["description"]])


def build_facts(run_query: Callable[[str], pd.DataFrame], week_end: date,
                retriever=None) -> tuple[dict, list[dict]]:
    """Returns (facts, job_texts). job_texts holds third-party JD excerpts — untrusted, kept out of facts.
    retriever: genai.brief_retrieval.Retriever (RAG) or None (top K by match_score)."""
    w = week_bounds(week_end)
    q = {name: run_query(sql) for name, sql in _sql(w).items()}

    now, ago = str(w["end"]), str(w["prev_end"])
    by_src = q["open"].pivot_table(index="source", columns="d", values="jobs", fill_value=0)
    by_src = by_src.reindex(columns=[now, ago], fill_value=0)
    new = {r.source: int(r.new_jobs) for r in q["new"].itertuples()}
    full = by_src[by_src.index.isin(COMPLETE_SOURCES)]
    postings = {"complete_boards_open_now": int(full[now].sum()), "complete_boards_open_week_ago": int(full[ago].sum()),
                "complete_boards_change_pct": pct_change(int(full[now].sum()), int(full[ago].sum())),
                "all_sources_open_now": int(by_src[now].sum()), "all_sources_open_week_ago": int(by_src[ago].sum()),
                "companies_now": int(q["companies"]["companies"][0]),
                "new_this_week_complete_boards": sum(new.get(x, 0) for x in COMPLETE_SOURCES),
                "by_source": [{"source": src, "complete_board": src in COMPLETE_SOURCES,
                               "open_now": int(r[now]), "open_week_ago": int(r[ago]), "new_this_week": new.get(src, 0)}
                              for src, r in by_src.sort_values(now, ascending=False).iterrows()]}

    roles_ok = comparable("role_family", w["prev_end"], w["end"])
    r = q["roles"].pivot_table(index="role_family", columns="d", values="jobs", fill_value=0)
    roles = [{"role_family": fam, "jobs_now": int(row.get(now, 0)),
              **({"jobs_week_ago": int(row.get(ago, 0)),
                  "change_pct": pct_change(int(row.get(now, 0)), int(row.get(ago, 0)))} if roles_ok else {})}
             for fam, row in r.sort_values(now, ascending=False).iterrows()]

    cand = q["candidates"].drop_duplicates("job_id")
    picked = retriever(cand) if retriever is not None else pick_by_match_score(cand)
    keep = ["job_id", "title", "company", "country", "apply_url", "match_score"] + (
        ["similarity"] if "similarity" in picked else [])
    m = picked[keep + ["excerpt"]].astype(object).where(picked[keep + ["excerpt"]].notna(), None)  # NaN → null
    facts = {
        "week": {k: str(v) for k, v in w.items()},
        "postings": postings,
        "roles": {"week_over_week_comparable": roles_ok, "rows": roles},
        "skills": skill_movers(q["skills"]),
        "top_data_companies": q["data_companies"].astype({"data_jobs": int}).to_dict("records"),
        "top_new_matches": [{**r, "match_score": None if pd.isna(r["match_score"]) else int(r["match_score"])}
                            for r in m.drop(columns=["excerpt"]).to_dict("records")],   # CSV gives 61.0 / NaN
        "top_new_matches_how": {"picked_by": getattr(retriever, "method", "match_score"),
                                "query": getattr(retriever, "query", None), "candidates": int(len(cand))},
        "data_notes": STATIC_NOTES + [note for d, f, note in KNOWN_BREAKS
                                      if w["prev_start"] < date.fromisoformat(d) <= w["end"]],
    }
    job_texts = [{"job_id": j, "excerpt": " ".join(str(x).split())} for j, x in zip(m["job_id"], m["excerpt"])]
    return facts, job_texts
