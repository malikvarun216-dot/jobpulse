"""
eval/label_app.py
-----------------
Tiny local labeling page: shows one (query, job) pair at a time from the human sample (~300 pairs, see
common.human_sample) and saves your 0 / 1 / 2 to {cache}/human_labels_v1.csv. Close it any time — it
continues where you stopped. Method ranks are hidden on purpose (blind labeling: knowing "BM25 found this"
would bias you).

Run:  .\\.venv\\Scripts\\streamlit.exe run eval\\label_app.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import DEFAULT_CACHE, human_sample, load_corpus, load_queries, strip_html  # noqa: E402

VERSION = "v1"
LABELS = DEFAULT_CACHE / f"human_labels_{VERSION}.csv"
SCALE = {0: "0 · not relevant", 1: "1 · partly (right field, wrong level/stack/place)", 2: "2 · clearly relevant"}


@st.cache_data
def load():
    corpus = load_corpus(VERSION).set_index("job_id")
    queries = {q["id"]: q for q in load_queries(VERSION)}
    sample = human_sample(pd.read_parquet(DEFAULT_CACHE / f"pool_{VERSION}.parquet"))
    return corpus, queries, sample


def read_labels() -> pd.DataFrame:
    if not LABELS.exists():
        return pd.DataFrame(columns=["query_id", "job_id", "label", "labeled_at"])
    return pd.read_csv(LABELS, dtype={"job_id": str}).drop_duplicates(["query_id", "job_id"], keep="last")


def save(query_id: str, job_id: str, label: int) -> None:
    row = pd.DataFrame([{"query_id": query_id, "job_id": job_id, "label": label,
                         "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])
    row.to_csv(LABELS, mode="a", header=not LABELS.exists(), index=False)


def undo_last() -> None:
    lines = LABELS.read_text(encoding="utf-8").splitlines(keepends=True) if LABELS.exists() else []
    if len(lines) > 1:
        LABELS.write_text("".join(lines[:-1]), encoding="utf-8")


st.set_page_config(page_title="JobPulse labeling", layout="wide")
corpus, queries, sample = load()
labels = read_labels()
done = set(zip(labels["query_id"], labels["job_id"]))
todo = [(q, j) for q, j in zip(sample["query_id"], sample["job_id"]) if (q, j) not in done]

n_done = len(sample) - len(todo)
GOAL = 100  # option B (Chat 27): round 1 ≈ 100 pairs covers every search; rounds 2–3 are optional extra precision
st.sidebar.progress(min(n_done / GOAL, 1.0), text=f"{n_done} / {GOAL} labeled (goal) · {len(sample)} available")
if n_done >= GOAL:
    st.sidebar.success("Goal reached — tell Claude 'done'. More labels only make κ more precise.")
st.sidebar.markdown("\n".join(f"- **{v}**" for v in SCALE.values()))
st.sidebar.caption("Judge the job against the query's *relevant if* line, not 'would I apply'. The title alone is "
                   "not enough — read for the actual work. Years/seniority count only if the query names a level "
                   "(q26, q49). A filter (country) that doesn't match → 0.")
if st.sidebar.button("↩ Undo last label"):
    undo_last()
    st.rerun()

if not todo:
    st.success(f"All {len(sample)} pairs labeled → {LABELS}")
    st.stop()

qid, job_id = todo[0]
q, job = queries[qid], corpus.loc[job_id]

rnd = int(sample.loc[(sample.query_id == qid) & (sample.job_id == job_id), "round"].iloc[0])
st.caption(f"Round {rnd} of {sample['round'].max()} · search {sorted(queries).index(qid) + 1} of {len(queries)} — "
           f"each round gives every search 2 jobs, so stopping after a round still covers all {len(queries)} searches")
st.markdown(f"### `{qid}` · {q['text']}")
st.markdown(f"**Relevant if:** {q['relevant_if']}" + (f"  \n**Filter:** {q['filters']}" if q.get("filters") else ""))
st.divider()
st.markdown(f"#### {job['title']}")
st.caption(f"{job['company_name']} · country={job['country']} · {job['location_raw'] or 'no location'} · "
           f"source={job['source']} · [apply link]({job['apply_url']})")
with st.container(height=480):
    st.text(strip_html(job["description"]))

cols = st.columns(3)
for col, (value, text) in zip(cols, SCALE.items()):
    if col.button(text, key=f"b{value}", width="stretch"):
        save(qid, job_id, value)
        st.rerun()
