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


_SECTION = re.compile(r"responsibilit|requirement|qualification|what you|who you|you will|you'll|about (us|the|you)"
                      r"|benefit|perks|we offer|nice to have|bonus|skills|compensation|salary|the role|the team", re.I)


def chunk_text(text: str, max_chars: int = 1500, min_chars: int = 300) -> list[str]:
    """
    Split a stripped JD into section-sized pieces (Chat 28 chunk-grain experiment). A new chunk starts at a short
    line that looks like a section heading ("Requirements", "What you'll do", "Benefits") once the current chunk
    has min_chars, or when adding a line would pass max_chars. A single over-long line is cut into windows.
    """
    chunks, cur = [], ""
    for line in (text or "").split("\n"):
        heading = len(line) < 80 and bool(_SECTION.search(line))
        if cur and ((heading and len(cur) >= min_chars) or len(cur) + len(line) + 1 > max_chars):
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
        while len(cur) > max_chars:
            chunks.append(cur[:max_chars])
            cur = cur[max_chars:]
    if cur.strip():
        chunks.append(cur)
    return chunks or [""]


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

    Order = ROUNDS: round 1 is 2 pairs (1 likely + 1 unlikely) for every query, then round 2, then round 3.
    Stopping after any round covers all queries evenly (option B in Chat 27: ~100 labels = round 1).
    The set of pairs is the same as before the reorder — only the order changed (2026-10-29).
    """
    best = pool[["bm25_rank", "vector_rank", "hybrid_rank"]].min(axis=1)
    picks = []
    for _, g in pool.assign(_top=best <= 5).groupby("query_id", sort=True):
        top, rest = g[g._top], g[~g._top]
        n_top = min(len(top), per_query // 2)
        t = top.sample(n_top, random_state=seed).assign(_order=lambda d: range(0, 2 * len(d), 2))  # 0, 2, 4
        r = rest.sample(min(len(rest), per_query - n_top), random_state=seed).assign(
            _order=lambda d: range(1, 2 * len(d), 2))                                              # 1, 3, 5
        picks.append(pd.concat([t, r]).sort_values("_order").assign(_pos=lambda d: range(len(d))))
    s = pd.concat(picks)
    s["round"] = s["_pos"] // 2 + 1
    return s.sort_values(["round", "query_id", "_pos"])[["query_id", "job_id", "round"]].reset_index(drop=True)


def filter_mask(corpus: pd.DataFrame, filters: dict | None) -> pd.Series:
    """True for rows that pass the query's filters (v1 only uses country)."""
    mask = pd.Series(True, index=corpus.index)
    for col, value in (filters or {}).items():
        mask &= corpus[col].astype(str) == str(value)
    return mask
