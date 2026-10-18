"""
eval/common.py
--------------
Small helpers shared by the eval scripts (pool, label, judge): load the frozen corpus and the queries,
strip HTML for reading, apply a query's metadata filter.
"""

import html
import re
from pathlib import Path

import pandas as pd
import yaml

DEFAULT_CACHE = Path(r"C:\Users\malik\jobpulse_lab_data")
EVAL_DIR = Path(__file__).parent

_DROP_BLOCKS = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|ul|ol|tr)>|<br\s*/?>", re.I)


def strip_html(text: str | None) -> str:
    """Greenhouse/Arbeitnow JDs are HTML. Keep the words and line breaks, drop markup and entities."""
    if not text:
        return ""
    t = html.unescape(text)  # some sources entity-encode the HTML itself (&lt;p&gt;)
    t = _DROP_BLOCKS.sub(" ", t)
    t = _BLOCK_END.sub("\n", t)
    t = html.unescape(_TAG.sub(" ", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def load_corpus(version: str = "v1", cache: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """The local copy written by freeze_corpus.py (same bytes as s3://jobpulse-gold-dev/eval/corpus_{version}/)."""
    return pd.read_parquet(cache / f"corpus_{version}" / "corpus.parquet")


def load_queries(version: str = "v1") -> list[dict]:
    return yaml.safe_load((EVAL_DIR / f"queries_{version}.yml").read_text(encoding="utf-8"))["queries"]


def human_sample(pool: pd.DataFrame, per_query: int = 6, seed: int = 27) -> pd.DataFrame:
    """
    The pairs Varun labels by hand (same set the judge is checked on). Per query: half from the top-5 of any
    method (likely relevant), half from the rest (likely not) — a purely random draw would be almost all 0s,
    and agreement on the 2s is the part that matters.
    """
    best = pool[["bm25_rank", "vector_rank", "hybrid_rank"]].min(axis=1)
    picks = []
    for _, g in pool.assign(_top=best <= 5).groupby("query_id", sort=True):
        top, rest = g[g._top], g[~g._top]
        n_top = min(len(top), per_query // 2)
        picks.append(top.sample(n_top, random_state=seed))
        picks.append(rest.sample(min(len(rest), per_query - n_top), random_state=seed))
    return pd.concat(picks)[["query_id", "job_id"]].reset_index(drop=True)


def filter_mask(corpus: pd.DataFrame, filters: dict | None) -> pd.Series:
    """True for rows that pass the query's filters (v1 only uses country)."""
    mask = pd.Series(True, index=corpus.index)
    for col, value in (filters or {}).items():
        mask &= corpus[col].astype(str) == str(value)
    return mask
