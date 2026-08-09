"""The deterministic publication gate for user-visible plan content.

This module decides whether AI-generated text is allowed to reach a user of a
health product, and it had no tests at all. Every case below is a string a
language model could plausibly produce.

The gate is a blunt category filter, not clinical review, and these tests pin
that scope honestly: they prove it rejects the categories it claims to reject
and that it fails closed, not that its judgement is clinically sound.
"""

from __future__ import annotations

import pytest

from app.v2.domain.plan_safety import (
    PLAN_SAFETY_POLICY_VERSION,
    SafetyDecision,
    evaluate_user_visible_plan_fields,
)


def _check(text: str) -> SafetyDecision:
    return evaluate_user_visible_plan_fields({"title": text})


def test_policy_version_is_stamped_on_every_decision() -> None:
    """A decision must be attributable to the policy that produced it."""

    allowed = _check("Take a ten minute walk after lunch.")
    blocked = _check("Your prescription should be adjusted.")
    assert allowed.policy_version == PLAN_SAFETY_POLICY_VERSION
    assert blocked.policy_version == PLAN_SAFETY_POLICY_VERSION


def test_ordinary_wellness_content_is_allowed() -> None:
    for text in (
        "Take a ten minute walk after lunch.",
        "Add a palm-sized portion of protein to breakfast.",
        "Wind down with five minutes of slow breathing before bed.",
        "Swap your afternoon coffee for herbal tea.",
        "Stretch your hips for three minutes this evening.",
    ):
        decision = _check(text)
        assert decision.allowed, text
        assert decision.reason_code is None


@pytest.mark.parametrize(
    "text",
    [
        "This will help diagnose your condition.",
        "You have PCOS, so try this.",
        "This is the standard PCOS treatment.",
        "Based on your answers we diagnosed hypothyroidism.",
        "Endometriosis diagnosis is likely here.",
    ],
)
def test_diagnostic_claims_are_blocked(text) -> None:
    """The product must never appear to diagnose."""

    decision = _check(text)
    assert not decision.allowed
    assert decision.reason_code == "candidate_safety_diagnostic"


@pytest.mark.parametrize(
    "text",
    [
        "Take a magnesium supplement each night.",
        "Your doctor may prescribe metformin.",
        "Increase the dosage to twice daily.",
        "Swallow one tablet with water.",
        "This medication helps with cramps.",
        "Ask about a prescription for this.",
    ],
)
def test_medication_and_supplement_advice_is_blocked(text) -> None:
    """Dosing advice is out of scope for a wellness plan."""

    decision = _check(text)
    assert not decision.allowed
    assert decision.reason_code == "candidate_safety_medication"


@pytest.mark.parametrize(
    "text",
    [
        "If this happens, call 911 immediately.",
        "Go to the emergency room.",
        "Visit urgent care today.",
        "Call an ambulance if it worsens.",
    ],
)
def test_emergency_instructions_are_blocked(text) -> None:
    """Emergency routing is a clinical decision this product must not make."""

    decision = _check(text)
    assert not decision.allowed
    assert decision.reason_code == "candidate_safety_emergency"


@pytest.mark.parametrize(
    "text",
    [
        "Watch for heavy bleeding.",
        "Chest pain means something is wrong.",
        "If you experience shortness of breath, stop.",
        "Severe pain is a red flag.",
        "Thoughts of self-harm need attention.",
        "Fainting can occur.",
    ],
)
def test_clinical_red_flags_are_blocked(text) -> None:
    """Red-flag symptom guidance requires clinical ownership."""

    decision = _check(text)
    assert not decision.allowed
    assert decision.reason_code == "candidate_safety_red_flag"


@pytest.mark.parametrize(
    "text",
    [
        "This is contraindicated in pregnancy.",
        "Not safe for anyone with high blood pressure.",
        "Avoid this if you have joint pain.",
        "You should not perform this while fasting.",
        "Do not do this if you feel unwell.",
    ],
)
def test_contraindication_language_is_blocked(text) -> None:
    decision = _check(text)
    assert not decision.allowed
    assert decision.reason_code == "candidate_safety_contraindication"


def test_matching_is_case_insensitive() -> None:
    """A model may capitalise anything; the gate must not be evadable by case."""

    for text in ("PRESCRIPTION strength", "Prescription Strength", "prescription"):
        assert not _check(text).allowed


@pytest.mark.parametrize(
    "value",
    ["", "   ", "\n\t ", None, 42, [], {}],
)
def test_empty_or_non_text_content_fails_closed(value) -> None:
    """A missing or malformed field is refused, never treated as safe."""

    decision = evaluate_user_visible_plan_fields({"title": value})
    assert not decision.allowed
    assert decision.reason_code == "candidate_safety_invalid_visible_text"
    assert decision.field == "title"


def test_the_offending_field_is_named_but_its_text_is_never_returned() -> None:
    """Reason codes are safe to log; the untrusted text is not."""

    secret = "Your prescription for metformin is ready"
    decision = evaluate_user_visible_plan_fields({"instructions": secret})
    assert decision.field == "instructions"
    assert secret not in repr(decision)
    assert decision.reason_code == "candidate_safety_medication"


def test_every_field_is_inspected_not_just_the_first() -> None:
    """A safe title must not smuggle unsafe instructions past the gate."""

    decision = evaluate_user_visible_plan_fields(
        {
            "title": "Evening wind-down",
            "purpose": "Helps you relax",
            "instructions": "Take one tablet before bed.",
        }
    )
    assert not decision.allowed
    assert decision.reason_code == "candidate_safety_medication"
    assert decision.field == "instructions"


def test_an_empty_candidate_is_allowed_because_there_is_nothing_to_show() -> None:
    """No fields means no user-visible text; publication invariants cover the rest."""

    assert evaluate_user_visible_plan_fields({}).allowed


def test_a_fully_safe_multi_field_candidate_passes() -> None:
    decision = evaluate_user_visible_plan_fields(
        {
            "title": "Ten minute walk",
            "purpose": "Gentle movement after eating",
            "instructions": "Walk at an easy pace for ten minutes after lunch.",
        }
    )
    assert decision.allowed
    assert decision.reason_code is None
    assert decision.field is None
