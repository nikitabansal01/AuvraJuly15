"""Pure domain objects and safety checks for the v2 plan-generation pipeline.

This module deliberately contains no database or provider imports.  A plan is
not a persistence model while it is being generated: it is an untrusted
candidate which must meet this contract before any media is made durable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from app.v2.domain.plan_safety import (
    PLAN_SAFETY_POLICY_VERSION,
    SafetyDecision,
    evaluate_user_visible_plan_fields,
)


CANONICAL_VARIANT_TYPES = ("low_energy", "time_limited", "no_equipment")
MAX_TEXT_LENGTH = 500
MAX_INSTRUCTION_STEPS = 8


class PlanCandidateRejected(ValueError):
    """A terminal, non-retryable rejection with a non-sensitive stable code."""

    retryable = False

    def __init__(
        self,
        reason_code: str,
        *,
        policy_version: str | None = None,
    ) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,80}", reason_code) is None:
            raise ValueError("plan_candidate_rejection_code_invalid")
        self.reason_code = reason_code
        self.policy_version = policy_version
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """A verified external source available to the plan generator."""

    canonical_url: str
    title: str
    published_date: str | None = None


@dataclass(frozen=True, slots=True)
class PlanVariant:
    variant_type: str
    title: str
    instructions: tuple[str, ...]
    image_prompt: str


@dataclass(frozen=True, slots=True)
class PlanAction:
    slot: int
    category: str
    title: str
    purpose: str
    instructions: tuple[str, ...]
    image_prompt: str
    citation_urls: tuple[str, ...]
    variants: tuple[PlanVariant, ...]


@dataclass(frozen=True, slots=True)
class PlanCandidate:
    """Validated in-memory output, ready for media generation and publication."""

    actions: tuple[PlanAction, ...]


def candidate_from_payload(payload: Mapping[str, Any]) -> PlanCandidate:
    """Parse and validate the constrained JSON response returned by an AI provider."""

    actions_value = payload.get("actions")
    if not isinstance(actions_value, list) or len(actions_value) != 4:
        raise PlanCandidateRejected("candidate_shape_actions")
    actions = tuple(
        _action_from_payload(action, slot) for slot, action in enumerate(actions_value, 1)
    )
    return PlanCandidate(actions=actions)


def validate_candidate_evidence(
    candidate: PlanCandidate,
    sources: Sequence[EvidenceSource],
    evidence_queries: Sequence[str],
) -> None:
    """Check retrieved citation identity and lexical action/query relevance.

    This deliberately does not claim clinical entailment. A clinical owner must
    approve any deployment that relies on evidence-to-action interpretation.
    """

    sources_by_url = {source.canonical_url: source for source in sources}
    allowed_urls = set(sources_by_url)
    if not allowed_urls:
        raise PlanCandidateRejected("candidate_evidence_missing_sources")
    if not evidence_queries:
        raise PlanCandidateRejected("candidate_evidence_missing_queries")
    if any(not _https_url(source.canonical_url) or not source.title.strip() for source in sources):
        raise PlanCandidateRejected("candidate_evidence_invalid_retrieved_identity")
    for action in candidate.actions:
        if not action.citation_urls:
            raise PlanCandidateRejected("candidate_evidence_missing_citation")
        if not set(action.citation_urls).issubset(allowed_urls):
            raise PlanCandidateRejected("candidate_evidence_unretrieved_citation")
        if not _action_matches_queries(action, evidence_queries):
            raise PlanCandidateRejected("candidate_evidence_action_query_irrelevant")
        for citation_url in action.citation_urls:
            source = sources_by_url[citation_url]
            if not _source_matches_action_and_queries(source, action, evidence_queries):
                raise PlanCandidateRejected("candidate_evidence_citation_title_irrelevant")


def _action_from_payload(value: object, expected_slot: int) -> PlanAction:
    action = _mapping(value, "action")
    category = _text(action, "category")
    title = _text(action, "title")
    purpose = _safe_text(_text(action, "purpose"), "purpose")
    instructions = _instructions(action.get("instructions"), "instructions")
    image_prompt = _safe_text(_text(action, "image_prompt"), "image prompt")
    citations = _urls(action.get("citation_urls"), "citation URLs")
    variants_value = action.get("variants")
    if not isinstance(variants_value, list) or len(variants_value) != len(CANONICAL_VARIANT_TYPES):
        raise PlanCandidateRejected("candidate_shape_variants")
    variants = tuple(_variant_from_payload(item) for item in variants_value)
    if {variant.variant_type for variant in variants} != set(CANONICAL_VARIANT_TYPES):
        raise PlanCandidateRejected("candidate_shape_variant_types")
    action_value = PlanAction(
        slot=expected_slot,
        category=category,
        title=title,
        purpose=purpose,
        instructions=instructions,
        image_prompt=image_prompt,
        citation_urls=citations,
        variants=variants,
    )
    _require_safe_action(action_value)
    return action_value


def _variant_from_payload(value: object) -> PlanVariant:
    variant = _mapping(value, "variant")
    return PlanVariant(
        variant_type=_text(variant, "variant_type"),
        title=_text(variant, "title"),
        instructions=_instructions(variant.get("instructions"), "variant instructions"),
        image_prompt=_safe_text(_text(variant, "image_prompt"), "variant image prompt"),
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanCandidateRejected(f"candidate_shape_{label}")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip() or len(result.strip()) > MAX_TEXT_LENGTH:
        raise PlanCandidateRejected(f"candidate_shape_{key}")
    return result.strip()


def _safe_text(value: str, label: str) -> str:
    del label
    return value


def _instructions(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_INSTRUCTION_STEPS:
        raise PlanCandidateRejected(f"candidate_shape_{label.replace(' ', '_')}")
    steps = tuple(_safe_text(_text({"step": step}, "step"), label) for step in value)
    return steps


def _urls(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PlanCandidateRejected(f"candidate_shape_{label.replace(' ', '_')}")
    urls = tuple(_text({"url": url}, "url") for url in value)
    if any(_https_url(url) is False for url in urls):
        raise PlanCandidateRejected("candidate_shape_citation_urls")
    return urls


def _https_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _require_safe_action(action: PlanAction) -> None:
    decision = evaluate_user_visible_plan_fields(_user_visible_fields(action))
    if not decision.allowed:
        _raise_safety_rejection(decision)


def _raise_safety_rejection(decision: SafetyDecision) -> None:
    raise PlanCandidateRejected(
        decision.reason_code or "candidate_safety_rejected",
        policy_version=PLAN_SAFETY_POLICY_VERSION,
    )


def _user_visible_fields(action: PlanAction) -> dict[str, str]:
    fields = {
        "category": action.category,
        "title": action.title,
        "purpose": action.purpose,
        "image_prompt": action.image_prompt,
    }
    fields.update(
        {f"instruction_{index}": step for index, step in enumerate(action.instructions, 1)}
    )
    for variant in action.variants:
        prefix = f"variant_{variant.variant_type}"
        fields[f"{prefix}_title"] = variant.title
        fields[f"{prefix}_image_prompt"] = variant.image_prompt
        fields.update(
            {
                f"{prefix}_instruction_{index}": step
                for index, step in enumerate(variant.instructions, 1)
            }
        )
    return fields


def _action_matches_queries(action: PlanAction, queries: Sequence[str]) -> bool:
    query_terms = set().union(*(_relevance_terms(query) for query in queries))
    action_terms = set().union(
        *(_relevance_terms(value) for value in _user_visible_fields(action).values())
    )
    return bool(query_terms.intersection(action_terms))


def _source_matches_action_and_queries(
    source: EvidenceSource,
    action: PlanAction,
    queries: Sequence[str],
) -> bool:
    """Require title overlap with both the bounded retrieval query and cited action."""

    source_terms = _relevance_terms(source.title)
    query_terms = set().union(*(_relevance_terms(query) for query in queries))
    action_terms = set().union(
        *(_relevance_terms(value) for value in _user_visible_fields(action).values())
    )
    return bool(source_terms.intersection(query_terms)) and bool(
        source_terms.intersection(action_terms)
    )


def _relevance_terms(value: str) -> set[str]:
    ignored = {
        "adult",
        "adults",
        "action",
        "activity",
        "choose",
        "consistent",
        "for",
        "behavior",
        "evidence",
        "lifestyle",
        "of",
        "source",
        "study",
        "the",
        "to",
        "verified",
        "wellbeing",
    }
    return {token for token in re.findall(r"[a-z]{3,}", value.casefold()) if token not in ignored}
