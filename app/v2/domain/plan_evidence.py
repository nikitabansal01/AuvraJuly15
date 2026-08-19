"""Typed, minimized context and deterministic queries for plan evidence retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


_FOCUSES = frozenset({"eat", "move", "pause"})
_ACTIVITY_LEVELS = {
    "Low": "low",
    "Moderate": "moderate",
    "High": "high",
    "I'm yet to start": "starting",
}
_SLEEP_BANDS = {
    "<6 hours": "under_6",
    "6-7 hours": "six_to_seven",
    "7-8 hours": "seven_to_eight",
    "8+ hours": "eight_plus",
}
_STRESS_LEVELS = frozenset({"Low", "Moderate", "High"})


@dataclass(frozen=True, slots=True)
class PlanAssessmentContext:
    """Non-identifying assessment projection; excludes every free-text answer."""

    lifestyle_focus: tuple[str, ...]
    activity_level: str | None
    sleep_band: str | None
    stress_level: str | None
    has_period_concern: bool
    has_mental_wellbeing_concern: bool

    def provider_context(self, *, timezone: str, local_date: str) -> dict[str, object]:
        """Return only safe categorical data for the structured-plan provider."""

        return {
            "timezone": timezone,
            "local_date": local_date,
            "lifestyle_focus": list(self.lifestyle_focus),
            "activity_level": self.activity_level,
            "sleep_band": self.sleep_band,
            "stress_level": self.stress_level,
            "has_period_concern": self.has_period_concern,
            "has_mental_wellbeing_concern": self.has_mental_wellbeing_concern,
        }


def assessment_context_from_answers(answers: Mapping[str, object]) -> PlanAssessmentContext:
    """Project validation-controlled enum answers; ignore custom and identifying text."""

    focus_values = answers.get("lifestyle_focus")
    focus = (
        tuple(item for item in focus_values if isinstance(item, str) and item in _FOCUSES)
        if isinstance(focus_values, (list, tuple))
        else ()
    )
    activity = _ACTIVITY_LEVELS.get(answers.get("workout_intensity"))
    sleep_band = _SLEEP_BANDS.get(answers.get("sleep_duration"))
    stress = answers.get("stress_level")
    stress_level = stress if isinstance(stress, str) and stress in _STRESS_LEVELS else None
    return PlanAssessmentContext(
        lifestyle_focus=tuple(sorted(set(focus))),
        activity_level=activity,
        sleep_band=sleep_band,
        stress_level=stress_level,
        has_period_concern=_nonempty_list(answers.get("period_concerns")),
        has_mental_wellbeing_concern=_nonempty_list(answers.get("mental_health_concerns")),
    )


def evidence_queries_for(context: PlanAssessmentContext) -> tuple[str, ...]:
    """Derive bounded PubMed queries from categorical context, never raw answers."""

    queries: list[str] = []
    focus_queries = {
        "eat": "nutrition dietary patterns adult wellbeing",
        "move": "physical activity adult wellbeing",
        "pause": "stress management adult wellbeing",
    }
    queries.extend(focus_queries[focus] for focus in context.lifestyle_focus)
    if context.activity_level in {"low", "starting"}:
        queries.append("physical activity beginner adult wellbeing")
    if context.sleep_band == "under_6":
        queries.append("sleep hygiene adult wellbeing")
    if context.stress_level == "High" or context.has_mental_wellbeing_concern:
        queries.append("stress management adult wellbeing")
    if context.has_period_concern:
        queries.append("menstrual wellbeing lifestyle adults")
    if not queries:
        queries.append("daily routine habit formation adults")
    return tuple(dict.fromkeys(queries))[:4]


def _nonempty_list(value: object) -> bool:
    return isinstance(value, (list, tuple)) and bool(value)
