"""
eval/review_app.py
------------------
Second pass over the pairs where Varun and the judge (Sonnet 5.5) disagree — "adjudication" in labeling terms.
Shows the rule, the job, YOUR label and BOTH judges' labels + reasons; you keep yours or change it.

Biggest disagreements first (0 vs 2), then the 1-step ones. Every decision — kept or changed — is logged to
{cache}/review_log_v1.csv, so we can report honestly how many labels moved. A changed label is appended to
human_labels_v1.csv (last label per pair wins), so agreement.py picks it up with no other step.

Caveat (say it in interviews): seeing the judge's answer can pull you toward it (anchoring). So κ AFTER review is
reported next to κ BEFORE review, never instead of it.

Run:  .\\.venv\\Scripts\\streamlit.exe run eval\\review_app.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import DEFAULT_CACHE, load_corpus, load_queries, strip_html  # noqa: E402

VERSION = "v1"
LABELS = DEFAULT_CACHE / f"human_labels_{VERSION}.csv"
LOG = DEFAULT_CACHE / f"review_log_{VERSION}.csv"
PRIMARY, SECOND = "claude-sonnet-5-5", "claude-haiku-5-5"
NAMES = {0: "0 · not relevant", 1: "1 · partly", 2: "2 · clearly relevant"}


@st.cache_data
def load_static():
    corpus = load_corpus(VERSION).set_index("job_id")
    queries = {q["id"]: q for q in load_queries(VERSION)}
    j1 = pd.read_parquet(DEFAULT_CACHE / f"judge_{VERSION}_labeled_{PRIMARY}.parquet")
    j2 = pd.read_parquet(DEFAULT_CACHE / f"judge_{VERSION}_labeled_{SECOND}.parquet")
    judges = j1[["query_id", "job_id", "label", "reason"]].merge(
        j2[["query_id", "job_id", "label", "reason"]], on=["query_id", "job_id"], suffixes=("_s", "_h"))
    return corpus, queries, judges


def queue(judges: pd.DataFrame) -> pd.DataFrame:
    """Disagreements as of the ORIGINAL labels (before any review), biggest gap first; reviewed ones drop out."""
    raw = pd.read_csv(LABELS, dtype={"job_id": str})
    first = raw.drop_duplicates(["query_id", "job_id"], keep="first")  # the label given before this review
    m = first.merge(judges, on=["query_id", "job_id"]).dropna(subset=["label_s"])
    m = m[m["label"] != m["label_s"]].assign(gap=lambda d: (d["label"] - d["label_s"]).abs())
    if LOG.exists():
        seen = pd.read_csv(LOG, dtype={"job_id": str})
        m = m.merge(seen[["query_id", "job_id"]].assign(_seen=1), on=["query_id", "job_id"], how="left")
        total = len(m)
        m = m[m["_seen"].isna()]
    else:
        total = len(m)
    return m.sort_values(["gap", "query_id"], ascending=[False, True]), total


def decide(row, final: int) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pd.DataFrame([{"query_id": row.query_id, "job_id": row.job_id, "before": int(row.label), "after": final,
                   "judge": int(row.label_s), "reviewed_at": now}]).to_csv(LOG, mode="a", header=not LOG.exists(), index=False)
    if final != int(row.label):
        pd.DataFrame([{"query_id": row.query_id, "job_id": row.job_id, "label": final, "labeled_at": now}]).to_csv(
            LABELS, mode="a", header=False, index=False)


st.set_page_config(page_title="JobPulse review", layout="wide")
corpus, queries, judges = load_static()
todo, total = queue(judges)
done = total - len(todo)
st.sidebar.progress(done / max(total, 1), text=f"{done} / {total} disagreements reviewed")
if LOG.exists():
    log = pd.read_csv(LOG)
    st.sidebar.caption(f"So far: kept yours {int((log.before == log.after).sum())} · changed {int((log.before != log.after).sum())}")
st.sidebar.caption("Re-read the 'Relevant if' rule, then decide. The judge can be wrong — keep your label when the rule "
                   "supports you. Years/seniority only count for q26, q49. A filter that doesn't match → 0.")
if todo.empty:
    st.success(f"All {total} disagreements reviewed → tell Claude 'review done'.")
    st.stop()

r = todo.iloc[0]
q, job = queries[r.query_id], corpus.loc[r.job_id]
st.caption(f"{'Big gap (0 vs 2)' if r.gap == 2 else 'One step apart'} · {len(todo)} left")
st.markdown(f"### `{r.query_id}` · {q['text']}")
st.markdown(f"**Relevant if:** {q['relevant_if']}" + (f"  \n**Filter:** {q['filters']}" if q.get("filters") else ""))

c1, c2, c3 = st.columns(3)
c1.metric("Your label", NAMES[int(r.label)])
c2.metric("Sonnet 5.5", NAMES[int(r.label_s)])
c3.metric("Haiku 5.5", NAMES[int(r.label_h)] if pd.notna(r.label_h) else "—")
st.info(f"**Sonnet's reason:** {r.reason_s}")
if pd.notna(r.reason_h):
    st.caption(f"Haiku's reason: {r.reason_h}")

cols = st.columns(4)
if cols[0].button(f"Keep mine ({int(r.label)})", width="stretch", type="primary"):
    decide(r, int(r.label))
    st.rerun()
for col, v in zip(cols[1:], (0, 1, 2)):
    if v != int(r.label) and col.button(f"Change to {NAMES[v]}", width="stretch"):
        decide(r, v)
        st.rerun()

st.divider()
st.markdown(f"#### {job['title']}")
st.caption(f"{job['company_name']} · country={job['country']} · {job['location_raw'] or 'no location'} · "
           f"source={job['source']} · [apply link]({job['apply_url']})")
with st.container(height=420):
    st.text(strip_html(job["description"]))
