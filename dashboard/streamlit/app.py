import sys
import os

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
from athena_client import run_query

st.set_page_config(page_title="JobPulse", layout="wide", page_icon="📊")

FLAT_JOIN_SQL = """
SELECT
    f.job_id,
    f.title,
    f.apply_url,
    f.job_type,
    f.salary_raw,
    f.tags,
    f.publication_date,
    f.snapshot_date,
    f.source,
    f.source_count,
    f.ingested_at,
    c.company_name,
    r.role_family,
    r.category                          AS role_category,
    co.country,
    COALESCE(e.match_score, -1)         AS match_score,
    COALESCE(e.seniority, 'unknown')    AS seniority,
    COALESCE(e.yoe_required, -1)        AS yoe_required
FROM jobpulse_gold_dev.fact_job_posting f
LEFT JOIN jobpulse_gold_dev.dim_company  c  ON f.company_key  = c.company_key
LEFT JOIN jobpulse_gold_dev.dim_role     r  ON f.role_key     = r.role_key
LEFT JOIN jobpulse_gold_dev.dim_country  co ON f.country_key  = co.country_key
LEFT JOIN jobpulse_gold_dev.enrichment_scores e
    ON f.job_id                              = e.job_id
    AND CAST(f.snapshot_date AS VARCHAR) = e.snapshot_date
ORDER BY match_score DESC, f.publication_date DESC
LIMIT 20000
"""


def parse_tags(raw: str) -> list:
    """
    Athena serializes array<string> in CSV as: [python, django, aws]
    (brackets, comma-separated, no inner quotes).
    """
    if not isinstance(raw, str) or raw.strip() in ("", "[]", "null"):
        return []
    inner = raw.strip().lstrip("[").rstrip("]")
    return [t.strip() for t in inner.split(",") if t.strip()]


@st.cache_data(ttl=3600)
def load_jobs() -> pd.DataFrame:
    df = run_query(FLAT_JOIN_SQL)
    df["publication_date"] = pd.to_datetime(df["publication_date"], errors="coerce")
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce")
    df["job_type"] = df["job_type"].fillna("unknown")
    df["country"] = df["country"].fillna("other")
    df["company_name"] = df["company_name"].fillna("unknown")
    df["role_family"] = df["role_family"].fillna("other")
    df["tags_parsed"] = df["tags"].apply(parse_tags)
    df["match_score"] = pd.to_numeric(df["match_score"], errors="coerce").fillna(-1)
    df["source_count"] = pd.to_numeric(df["source_count"], errors="coerce").fillna(1).astype(int)
    return df


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("JobPulse")
st.sidebar.caption("Job market intelligence dashboard")

if st.sidebar.button("Refresh data"):
    load_jobs.clear()
    st.rerun()

df_all = load_jobs()

title_search = st.sidebar.text_input("Search title", placeholder="e.g. Data Engineer")
roles = st.sidebar.multiselect(
    "Role Family", sorted(df_all["role_family"].dropna().unique())
)
countries = st.sidebar.multiselect(
    "Country", sorted(df_all["country"].dropna().unique())
)
job_types = st.sidebar.multiselect(
    "Job Type", sorted(df_all["job_type"].dropna().unique())
)
min_score = st.sidebar.slider("Min Match Score", min_value=0, max_value=100, value=0, step=5)
multi_source_only = st.sidebar.checkbox("2+ sources (higher confidence)", value=False)

mask = pd.Series(True, index=df_all.index)
if title_search:
    mask &= df_all["title"].str.contains(title_search, case=False, na=False)
if roles:
    mask &= df_all["role_family"].isin(roles)
if countries:
    mask &= df_all["country"].isin(countries)
if job_types:
    mask &= df_all["job_type"].isin(job_types)
if min_score > 0:
    mask &= df_all["match_score"] >= min_score
if multi_source_only:
    mask &= df_all["source_count"] >= 2

df = df_all[mask].copy()

st.sidebar.markdown(f"**{len(df)} jobs** shown")

# ── KPIs ─────────────────────────────────────────────────────────────────────

st.title("JobPulse — Job Market Intelligence")

k1, k2, k3 = st.columns(3)
k1.metric("Total Jobs", len(df))
k2.metric("Companies", df["company_name"].nunique())
k3.metric("Countries", df["country"].nunique())

st.divider()

# ── Results Table ─────────────────────────────────────────────────────────────

st.subheader("Job Listings")

display_cols = ["match_score", "title", "company_name", "role_family", "country",
                "job_type", "publication_date", "source_count", "salary_raw", "apply_url"]
display_df = df[display_cols].copy()

st.dataframe(
    display_df,
    column_config={
        "match_score": st.column_config.NumberColumn("Match %", format="%.1f"),
        "title": st.column_config.TextColumn("Title"),
        "company_name": st.column_config.TextColumn("Company"),
        "role_family": st.column_config.TextColumn("Role"),
        "country": st.column_config.TextColumn("Country"),
        "job_type": st.column_config.TextColumn("Type"),
        "publication_date": st.column_config.DateColumn("Posted"),
        "source_count": st.column_config.NumberColumn("Sources", format="%d"),
        "salary_raw": st.column_config.TextColumn("Salary"),
        "apply_url": st.column_config.LinkColumn("Apply", display_text="Apply →"),
    },
    use_container_width=True,
    hide_index=True,
)

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────

st.subheader("Market Breakdown")
c1, c2, c3 = st.columns(3)

with c1:
    country_counts = (
        df.groupby("country").size().reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    fig = px.bar(
        country_counts, x="country", y="count",
        title="Hiring by Country",
        color="count", color_continuous_scale="Blues",
        labels={"country": "Country", "count": "Jobs"},
    )
    fig.update_layout(coloraxis_showscale=False, margin=dict(t=40, b=0))
    st.plotly_chart(fig, use_container_width=True)

with c2:
    top_companies = (
        df.groupby("company_name").size().reset_index(name="count")
        .nlargest(10, "count").sort_values("count")
    )
    fig = px.bar(
        top_companies, x="count", y="company_name", orientation="h",
        title="Top 10 Companies",
        labels={"company_name": "", "count": "Jobs"},
    )
    fig.update_layout(margin=dict(t=40, b=0))
    st.plotly_chart(fig, use_container_width=True)

with c3:
    role_counts = df.groupby("role_family").size().reset_index(name="count")
    fig = px.pie(
        role_counts, names="role_family", values="count",
        title="Jobs by Role Family",
    )
    fig.update_layout(margin=dict(t=40, b=0))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── Tags / Skills ─────────────────────────────────────────────────────────────

st.subheader("Top Skills & Tags")

all_tags = [tag for tags_list in df["tags_parsed"] for tag in tags_list]
if all_tags:
    tag_counts = (
        pd.Series(all_tags).value_counts().head(20)
        .reset_index()
        .rename(columns={"index": "tag", "count": "count"})
    )
    # handle both pandas < and >= 2.0 column naming
    tag_counts.columns = ["tag", "count"]
    fig = px.bar(
        tag_counts, x="tag", y="count",
        title="Top 20 Tags / Skills",
        labels={"tag": "Tag", "count": "Occurrences"},
    )
    fig.update_layout(xaxis_tickangle=-35, margin=dict(t=40, b=60))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No tag data available for current filter selection.")
