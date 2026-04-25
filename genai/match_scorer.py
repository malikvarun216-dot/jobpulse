from __future__ import annotations

import re
from datetime import date
from typing import Any

from genai.guardrails import ExtractionResult

_SENIORITY_ORDER = ["junior", "mid", "senior", "lead", "staff", "principal"]


def _seniority_distance(job_level: str, user_level: str) -> int:
    try:
        return abs(_SENIORITY_ORDER.index(job_level) - _SENIORITY_ORDER.index(user_level))
    except ValueError:
        return 99


def _parse_salary_usd(salary_raw: str | None) -> float | None:
    if not salary_raw:
        return None
    lower = salary_raw.lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*k", lower)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"(\d{5,7})", lower)
    if m:
        return float(m.group(1))
    return None


class MatchScorer:
    """
    Computes a 0–100 match score for one job against the user profile.

    Usage:
        scorer = MatchScorer(profile)
        score, detail = scorer.score(extraction, job_row)
    """

    def __init__(self, profile: dict):
        self._user_skills    = {s.lower() for s in profile["skills"]}
        self._user_seniority = profile["seniority"].lower()
        self._user_yoe       = int(profile.get("yoe", 0))
        self._user_locations = {loc.lower() for loc in profile["preferred_locations"]}
        self._user_roles     = set(profile["preferred_role_families"])
        self._salary_min     = profile.get("salary_min_usd", 0)
        self._weights        = profile["weights"]

        # Build tiered weight map: core=3x, secondary=1.5x, learning=1x
        tiers = profile.get("skill_tiers", {})
        self._skill_weights: dict[str, float] = {s: 1.0 for s in self._user_skills}
        for s in tiers.get("core", []):
            self._skill_weights[s.lower()] = 3.0
        for s in tiers.get("secondary", []):
            self._skill_weights[s.lower()] = 1.5
        for s in tiers.get("learning", []):
            self._skill_weights[s.lower()] = 1.0
        self._max_skill_weight = sum(self._skill_weights.values()) or 1.0

    def score(self, extraction: ExtractionResult, job_row: dict[str, Any]) -> tuple[float, dict]:
        detail: dict[str, float] = {}

        # 1. Skill overlap — weighted by tier (core=3x, secondary=1.5x, learning=1x)
        job_skills = {s.lower() for s in extraction.skills}
        matched = job_skills & self._user_skills
        weighted_match = sum(self._skill_weights.get(s, 1.0) for s in matched)
        # Normalize against total user skill weight so score is always [0, 1]
        skill_score = min(weighted_match / self._max_skill_weight, 1.0)
        detail["skill_overlap"] = round(skill_score * self._weights["skill_overlap"], 2)

        # 2. Seniority fit — YoE-aware when available, title-based fallback
        w_sen = self._weights["seniority_fit"]
        if extraction.yoe_required is not None:
            gap = extraction.yoe_required - self._user_yoe
            if gap <= 0:
                seniority_pts = float(w_sen)
            elif gap == 1:
                seniority_pts = w_sen * 0.75
            elif gap == 2:
                seniority_pts = w_sen * 0.50
            elif gap == 3:
                seniority_pts = w_sen * 0.25  # stretch role — still surfaces it
            else:
                seniority_pts = 0.0
        else:
            dist = _seniority_distance(extraction.seniority, self._user_seniority)
            if dist == 0:
                seniority_pts = float(w_sen)
            elif dist == 1:
                seniority_pts = w_sen * 0.5
            else:
                seniority_pts = 0.0
        detail["seniority_fit"] = round(seniority_pts, 2)

        # 3. Location fit
        location_raw = (job_row.get("location_raw") or "").lower()
        job_type     = (job_row.get("job_type") or "").lower()
        country      = (job_row.get("country") or "").lower()
        location_hit = (
            "remote" in location_raw
            or "remote" in job_type
            or country in self._user_locations
            or any(loc in location_raw for loc in self._user_locations)
        )
        detail["location_fit"] = float(self._weights["location_fit"]) if location_hit else 0.0

        # 4. Role family fit
        role_family = job_row.get("role_family", "")
        detail["role_family_fit"] = (
            float(self._weights["role_family_fit"]) if role_family in self._user_roles else 0.0
        )

        # 5. Salary fit
        salary_usd = _parse_salary_usd(job_row.get("salary_raw"))
        if salary_usd is None or self._salary_min == 0 or salary_usd >= self._salary_min:
            detail["salary_fit"] = float(self._weights["salary_fit"])
        else:
            detail["salary_fit"] = 0.0

        # 6. Freshness
        pub_date_raw = job_row.get("publication_date")
        try:
            if isinstance(pub_date_raw, str):
                pub_date = date.fromisoformat(pub_date_raw[:10])
            elif isinstance(pub_date_raw, date):
                pub_date = pub_date_raw
            else:
                pub_date = None
            if pub_date:
                age_days = (date.today() - pub_date).days
                if age_days <= 7:
                    freshness_pts = float(self._weights["freshness"])
                elif age_days <= 14:
                    freshness_pts = self._weights["freshness"] * 0.6
                else:
                    freshness_pts = 0.0
            else:
                freshness_pts = self._weights["freshness"] * 0.5
        except (ValueError, TypeError):
            freshness_pts = 0.0
        detail["freshness"] = round(freshness_pts, 2)

        total = round(sum(detail.values()), 1)
        return min(total, 100.0), detail
