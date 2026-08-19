"""Deterministic safety policy for untrusted, user-visible plan content.

This is a publication gate, not clinical review.  It deliberately rejects a
candidate when a prohibited category appears in any rendered action field.
Clinical-owner approval is still required before this policy is enabled for a
production population.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


PLAN_SAFETY_POLICY_VERSION = "plan-safety.v2"


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """A stable, non-sensitive result of the deterministic publication gate."""

    allowed: bool
    reason_code: str | None
    policy_version: str = PLAN_SAFETY_POLICY_VERSION
    field: str | None = None


@dataclass(frozen=True, slots=True)
class _SafetyRule:
    reason_code: str
    expression: re.Pattern[str]


_RULES = (
    _SafetyRule(
        "candidate_safety_diagnostic",
        re.compile(
            r"\b(?:diagnos(?:e|es|ed|ing|is)|you\s+(?:have|has)\s+"
            r"(?:pcos|pcod|diabetes|endometriosis|hypothyroidism)|"
            r"(?:pcos|pcod|diabetes|endometriosis|hypothyroidism)\s+(?:diagnosis|treatment))\b",
            re.IGNORECASE,
        ),
    ),
    _SafetyRule(
        "candidate_safety_medication",
        re.compile(
            r"\b(?:prescrib(?:e|es|ed|ing)|prescription|medication|medicine|drug|"
            r"dosage|dose|supplement|capsule|tablet|pill|antibiotic)\b",
            re.IGNORECASE,
        ),
    ),
    _SafetyRule(
        "candidate_safety_emergency",
        re.compile(
            r"\b(?:emergency|call\s+(?:911|999|112)|ambulance|emergency\s+room|\ber\b|"
            r"urgent\s+care)\b",
            re.IGNORECASE,
        ),
    ),
    _SafetyRule(
        "candidate_safety_red_flag",
        re.compile(
            r"\b(?:red\s+flag|heavy\s+bleeding|chest\s+pain|shortness\s+of\s+breath|"
            r"faint(?:ing)?|loss\s+of\s+consciousness|self[- ]harm|suicid(?:e|al)|"
            r"severe\s+pain)\b",
            re.IGNORECASE,
        ),
    ),
    _SafetyRule(
        "candidate_safety_contraindication",
        re.compile(
            r"\b(?:contraindicat(?:ed|ion)|not\s+(?:safe|suitable)\s+for|"
            r"avoid\s+(?:this|it)\s+if|do\s+not\s+(?:do|perform|try)\s+this\s+if|"
            r"should\s+not\s+(?:do|perform|try))\b",
            re.IGNORECASE,
        ),
    ),
)


def evaluate_user_visible_plan_fields(fields: Mapping[str, str]) -> SafetyDecision:
    """Fail closed on the first prohibited category without retaining field text."""

    for field, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            return SafetyDecision(False, "candidate_safety_invalid_visible_text", field=field)
        for rule in _RULES:
            if rule.expression.search(value):
                return SafetyDecision(False, rule.reason_code, field=field)
    return SafetyDecision(True, None)
