from __future__ import annotations

import json
import re
import time

import anthropic

from genai.guardrails import SKILL_VOCAB, BudgetTracker, ExtractionResult

# Seniority patterns — most specific first
SENIORITY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(staff|principal)\b",    "staff"),
    (r"\blead\b",                  "lead"),
    (r"\bsenior\b|\bsr[\.\s]",     "senior"),
    (r"\bjunior\b|\bjr[\.\s]",     "junior"),
    (r"\b(mid|intermediate)\b",    "mid"),
]

# YoE pattern — captures the first number followed by optional + and "year(s)"
YOE_PATTERN = re.compile(r"(\d+)\s*\+?\s*years?\s*(of\s+)?(experience|exp)", re.IGNORECASE)

RULES_MIN_SKILLS = 5

SYSTEM_PROMPT = (
    "You are a precise job description parser. "
    "Extract technical skills, seniority level, and minimum years of experience from the job description. "
    "Respond ONLY with a JSON object — no markdown, no explanation — matching this schema:\n"
    '{"skills": ["python", "sql", ...], "seniority": "mid", "yoe_required": 3}\n'
    "skills: lowercase canonical names. "
    "seniority: junior | mid | senior | lead | staff | principal | unknown. "
    "yoe_required: integer minimum years required, or null if not specified."
)


def _rule_based_extract(description: str) -> ExtractionResult:
    lower = description.lower()

    found_skills = sorted(
        skill for skill in SKILL_VOCAB
        if re.search(r"\b" + re.escape(skill) + r"\b", lower)
    )

    seniority = "unknown"
    for pattern, level in SENIORITY_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            seniority = level
            break

    yoe_required: int | None = None
    m = YOE_PATTERN.search(description)
    if m:
        yoe_required = int(m.group(1))

    return ExtractionResult(skills=found_skills, seniority=seniority, yoe_required=yoe_required)


def _llm_extract(
    description: str,
    client: anthropic.Anthropic,
    budget: BudgetTracker,
) -> ExtractionResult:
    estimated_input  = len(SYSTEM_PROMPT) // 4 + len(description) // 4 + 50
    estimated_output = 200

    budget.check_and_increment(estimated_input, estimated_output)

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": description[:4000]}],
            )
            usage = response.usage
            budget.record_actual_usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
            )
            raw = response.content[0].text.strip()
            data = json.loads(raw)
            return ExtractionResult(**data)
        except (anthropic.APIError, anthropic.APIConnectionError) as e:
            last_error = e
            time.sleep(2 ** attempt)
        except (json.JSONDecodeError, ValueError):
            break

    raise RuntimeError(f"LLM extraction failed: {last_error}")


class SkillExtractor:
    """
    Public interface consumed by JDEnrichmentAgent.
    Returns (ExtractionResult, source) where source is "rules" or "llm".
    """

    def __init__(self, client: anthropic.Anthropic, budget: BudgetTracker):
        self._client = client
        self._budget = budget

    def extract(self, description: str) -> tuple[ExtractionResult, str]:
        rules_result = _rule_based_extract(description)

        if len(rules_result.skills) >= RULES_MIN_SKILLS and rules_result.seniority != "unknown":
            return rules_result, "rules"

        try:
            llm_result = _llm_extract(description, self._client, self._budget)
            return llm_result, "llm"
        except Exception:
            return rules_result, "rules"
