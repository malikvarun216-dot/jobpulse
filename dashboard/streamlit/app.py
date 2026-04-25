import json
import os
import sys

import boto3
import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
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

GOLD_BUCKET = os.environ.get("GOLD_BUCKET", "jobpulse-gold-dev")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")


def _get_secret(env_var: str, secret_key: str) -> str:
    """Return env var value if set (local dev), else fetch from Secrets Manager."""
    if val := os.environ.get(env_var):
        return val
    try:
        client = boto3.client("secretsmanager", region_name=AWS_REGION)
        raw = client.get_secret_value(SecretId=f"jobpulse/{secret_key}")["SecretString"]
        parsed = json.loads(raw)
        return next(iter(parsed.values()))
    except Exception:
        return ""


VOYAGE_API_KEY = _get_secret("VOYAGE_API_KEY", "voyage_key_dev")
ANTHROPIC_API_KEY = _get_secret("ANTHROPIC_API_KEY", "anthropic_key_dev")


def parse_tags(raw: str) -> list:
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

# ── Header ───────────────────────────────────────────────────────────────────

st.title("JobPulse — Job Market Intelligence")

tab_browse, tab_semantic = st.tabs(["Browse Jobs", "Semantic Search"])

# ── Tab 1: Browse Jobs ────────────────────────────────────────────────────────

with tab_browse:
    k1, k2, k3 = st.columns(3)
    k1.metric("Total Jobs", len(df))
    k2.metric("Companies", df["company_name"].nunique())
    k3.metric("Countries", df["country"].nunique())

    st.divider()

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

    st.subheader("Top Skills & Tags")
    all_tags = [tag for tags_list in df["tags_parsed"] for tag in tags_list]
    if all_tags:
        tag_counts = (
            pd.Series(all_tags).value_counts().head(20)
            .reset_index()
            .rename(columns={"index": "tag", "count": "count"})
        )
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

# ── Tab 2: Semantic Search ────────────────────────────────────────────────────

with tab_semantic:
    if not VOYAGE_API_KEY:
        st.warning(
            "VOYAGE_API_KEY not set. Set it as an environment variable to enable semantic search.\n\n"
            "Get a free key at voyageai.com (200M tokens/month free)."
        )
        st.stop()

    st.subheader("Semantic Search")
    st.caption(
        "Describe the role you want in plain English. "
        "Finds semantically similar jobs regardless of exact title words."
    )

    query = st.text_area(
        "Describe the role you're looking for",
        placeholder="e.g. cloud data pipeline engineer using Spark and Airflow, based in India or remote",
        height=80,
    )
    search_btn = st.button("Search", type="primary")

    if "sem_results" not in st.session_state:
        st.session_state.sem_results = []
        st.session_state.sem_query = ""

    if search_btn and query.strip():
        try:
            import boto3 as _boto3
            import voyageai as _voyageai
            from genai.semantic_search import search as _semantic_search  # noqa: E402

            _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if _repo_root not in sys.path:
                sys.path.insert(0, _repo_root)

            with st.spinner("Embedding query and searching..."):
                _vo = _voyageai.Client(api_key=VOYAGE_API_KEY)
                _s3 = _boto3.client("s3", region_name=AWS_REGION)
                results = _semantic_search(query.strip(), _vo, _s3, GOLD_BUCKET, k=50)

            st.session_state.sem_results = results
            st.session_state.sem_query = query.strip()

        except Exception as exc:
            st.error(f"Search failed: {exc}")

    if st.session_state.sem_results:
        result_ids = [r[0] for r in st.session_state.sem_results]
        score_map = {r[0]: r[1] for r in st.session_state.sem_results}

        df_sem = df_all[df_all["job_id"].isin(result_ids)].copy()

        if df_sem.empty:
            st.info(
                "Embeddings exist but job IDs don't match the current gold snapshot. "
                "Run the pipeline once to generate fresh embeddings."
            )
        else:
            df_sem["similarity"] = (df_sem["job_id"].map(score_map) * 100).round(1)

            # Hybrid score: semantic relevance + profile match (equal weight)
            # Clips match_score at 0 so -1 (no enrichment) doesn't help irrelevant jobs
            df_sem["combined_score"] = (
                df_sem["similarity"] * 0.5
                + df_sem["match_score"].clip(lower=0) * 0.5
            ).round(1)
            df_sem = df_sem.sort_values("combined_score", ascending=False)

            # Drop jobs with no profile overlap at all (match_score == -1 means no enrichment ran)
            df_sem = df_sem[df_sem["match_score"] > 0]

            st.markdown(
                f"**{len(df_sem)} results** for: *{st.session_state.sem_query}*  \n"
                f"💡 **Score** = semantic relevance × 50% + profile match × 50%.  "
                f"Jobs with zero profile match are filtered out."
            )

            sem_display_cols = [
                "combined_score", "similarity", "match_score", "title", "company_name",
                "role_family", "country", "job_type", "publication_date", "salary_raw", "apply_url",
            ]
            st.dataframe(
                df_sem[sem_display_cols],
                column_config={
                    "combined_score": st.column_config.NumberColumn("Score", format="%.1f"),
                    "similarity": st.column_config.NumberColumn("Semantic %", format="%.1f"),
                    "match_score": st.column_config.NumberColumn("Profile Match %", format="%.1f"),
                    "title": st.column_config.TextColumn("Title"),
                    "company_name": st.column_config.TextColumn("Company"),
                    "role_family": st.column_config.TextColumn("Role"),
                    "country": st.column_config.TextColumn("Country"),
                    "job_type": st.column_config.TextColumn("Type"),
                    "publication_date": st.column_config.DateColumn("Posted"),
                    "salary_raw": st.column_config.TextColumn("Salary"),
                    "apply_url": st.column_config.LinkColumn("Apply", display_text="Apply →"),
                },
                use_container_width=True,
                hide_index=True,
            )

            # "Why this match?" — Claude Haiku explains top 3 results
            if ANTHROPIC_API_KEY:
                st.divider()
                st.subheader("Why these match?")
                st.caption("Claude Haiku explains the top 3 results.")

                try:
                    import anthropic as _anthropic
                    _client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

                    for _, row in df_sem.head(3).iterrows():
                        label = f"{row['title']} at {row['company_name']} ({row['country']})"
                        with st.expander(label):
                            desc_snippet = ""
                            if "description" in df_all.columns:
                                raw_desc = df_all.loc[
                                    df_all["job_id"] == row["job_id"], "description"
                                ].values
                                if len(raw_desc) and raw_desc[0]:
                                    desc_snippet = str(raw_desc[0])[:500]

                            prompt = (
                                f"Query: {st.session_state.sem_query}\n\n"
                                f"Job: {row['title']} at {row['company_name']}, {row['country']}\n"
                                f"Role: {row['role_family']} | Salary: {row.get('salary_raw', 'N/A')}\n"
                                f"Description excerpt: {desc_snippet}"
                            )
                            with st.spinner("Asking Claude..."):
                                resp = _client.messages.create(
                                    model="claude-haiku-4-5-20251001",
                                    max_tokens=150,
                                    system="Explain in 2 sentences why this job matches the search query. Be specific about skills and location.",
                                    messages=[{"role": "user", "content": prompt}],
                                    timeout=10.0,
                                )
                            st.write(resp.content[0].text)

                except Exception as exc:
                    st.warning(f"Could not generate explanation: {exc}")
