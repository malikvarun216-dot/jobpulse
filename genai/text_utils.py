"""
Text preparation shared by the eval (eval/common.py re-exports it) and the weekly brief's retriever
(genai/brief_retrieval.py), so the brief embeds jobs exactly the way the eval measured them (Chat 32 RAG).
Moved here from eval/common.py unchanged.
"""

import html
import re

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
