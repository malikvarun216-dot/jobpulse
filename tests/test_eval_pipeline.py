"""
tests/test_eval_pipeline.py
---------------------------
Pooling, sampling, judge prompt and kappa (Chat 27 part 2). No AWS / API calls.
Run locally: pytest tests/test_eval_pipeline.py -v
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from eval import agreement, common, judge, pool  # noqa: E402


def test_strip_html_keeps_words_and_breaks():
    raw = "<div><p>Build <b>Airflow</b> DAGs&nbsp;&amp; dbt</p><script>x=1</script><ul><li>SQL</li></ul></div>"
    assert common.strip_html(raw) == "Build Airflow DAGs & dbt\nSQL"
    assert common.strip_html("&lt;p&gt;entity &amp;amp; encoded&lt;/p&gt;") == "entity & encoded"
    assert common.strip_html(None) == ""


def test_filter_mask_by_country():
    corpus = pd.DataFrame({"country": ["IN", "US", "IN"]})
    assert common.filter_mask(corpus, {"country": "IN"}).tolist() == [True, False, True]
    assert common.filter_mask(corpus, None).all()


def test_top_k_respects_mask_and_order():
    scores = np.array([0.9, 0.1, 0.5, 0.7])
    mask = np.array([False, True, True, True])
    assert pool.top_k(scores, mask, 2).tolist() == [3, 2]
    assert pool.top_k(scores, np.zeros(4, bool), 2).tolist() == []


def _toy():
    corpus = pd.DataFrame({"job_id": ["a", "b", "c", "d"], "country": ["IN", "IN", "US", "IN"]})
    vecs = np.eye(4, dtype=np.float32)  # job i points along axis i
    bm25 = {"kafka": np.array([0.0, 2.0, 5.0, 1.0])}
    return corpus, vecs, bm25


def test_build_pool_unions_methods_and_applies_filter():
    corpus, vecs, bm25 = _toy()
    queries = [{"id": "q1", "text": "kafka", "filters": {"country": "IN"}}]
    qvecs = {"kafka": np.array([1.0, 0.0, 0.0, 0.5], dtype=np.float32)}  # closest to a, then d
    match = np.array([50.0, 10.0, 90.0, -1.0])  # d has no enrichment → dropped from hybrid
    p = pool.build_pool(corpus, queries, lambda t: bm25[t], vecs, qvecs, match, depth=2, hybrid_candidates=3)
    got = p.set_index("job_id")
    assert "c" not in got.index  # US job filtered out of every method
    assert got.loc["b", "bm25_rank"] == 1 and got.loc["d", "bm25_rank"] == 2  # bm25 > 0 only, "a" scored 0
    assert got.loc["a", "vector_rank"] == 1 and got.loc["d", "vector_rank"] == 2
    assert got.loc["a", "hybrid_rank"] == 1 and pd.isna(got.loc["d", "hybrid_rank"])
    assert p.job_id.is_unique


def test_human_sample_is_deterministic_and_mixes_top_and_rest():
    rows = [{"query_id": q, "job_id": f"{q}{i}", "bm25_rank": i + 1, "vector_rank": None, "hybrid_rank": None}
            for q in ["q1", "q2"] for i in range(20)]
    pl = pd.DataFrame(rows).astype({"bm25_rank": "Int64", "vector_rank": "Int64", "hybrid_rank": "Int64"})
    s1, s2 = common.human_sample(pl), common.human_sample(pl)
    assert s1.equals(s2)
    assert len(s1) == 12
    ranks = s1.merge(pl, on=["query_id", "job_id"]).bm25_rank
    assert (ranks <= 5).sum() == 6  # 3 per query from the top-5


def test_cohen_kappa_known_values():
    assert agreement.cohen_kappa([0, 1, 2, 2], [0, 1, 2, 2]) == pytest.approx(1.0)
    # observed agreement 0.75, chance agreement 0.5 → (0.75 - 0.5) / (1 - 0.5)
    assert agreement.cohen_kappa([0, 0, 1, 1], [0, 1, 1, 1]) == pytest.approx(0.5)
    # weighted: a 0-vs-2 miss costs 4x a 0-vs-1 miss
    assert agreement.cohen_kappa([0, 1, 2], [1, 1, 2], "quadratic") > agreement.cohen_kappa([0, 1, 2], [2, 1, 2], "quadratic")


def test_judge_prompt_flags_cut_postings_and_shows_filter():
    job = pd.Series({"title": "DE", "company_name": "X", "country": "IN", "location_raw": None,
                     "description": "<p>" + "a" * (judge.MAX_DESC_CHARS + 10) + "</p>"})
    text, cut = judge.pair_prompt({"text": "data engineer", "relevant_if": "DE role", "filters": {"country": "IN"}}, job)
    assert cut and "[... posting cut here]" in text
    assert 'Filter: {"country": "IN"}' in text and "<p>" not in text


def test_rrf_rewards_agreement_between_methods():
    from eval.search import rrf_rank
    bm25 = np.array([0, 1, 2])
    vec = np.array([3, 1, 4])
    fused = rrf_rank([bm25, vec], k=5).tolist()
    assert fused[0] == 1  # rank 2 in both beats rank 1 in only one
    assert set(fused) == {0, 1, 2, 3, 4}


def test_query_metrics_match_the_learning_doc_example():
    # learning.md Part C.4: results #1, #3, #4 relevant (grade 2), 5 relevant jobs exist in the qrels
    from eval.run_eval import query_metrics
    ranked = [f"j{i}" for i in range(1, 11)]
    labels = {"j1": 2, "j3": 2, "j4": 2, "x1": 2, "x2": 2, "j2": 0}
    m = query_metrics(ranked, labels)
    assert m["precision@10"] == pytest.approx(0.3)
    assert m["recall@50"] == pytest.approx(0.6)
    assert m["mrr"] == pytest.approx(1.0)
    assert m["judged@10"] == pytest.approx(0.4)  # j1, j2, j3, j4 have labels
    assert 0 < m["ndcg@10"] < 1


def test_ndcg_is_one_for_perfect_order_and_nan_without_relevant():
    from eval.run_eval import ndcg_at, query_metrics
    assert ndcg_at([2, 1, 0], [0, 1, 2]) == pytest.approx(1.0)
    assert ndcg_at([0, 1, 2], [0, 1, 2]) < 1.0
    m = query_metrics(["a", "b"], {"a": 1})
    assert np.isnan(m["recall@50"]) and np.isnan(m["mrr"])  # no grade-2 job → undefined, left out of means
