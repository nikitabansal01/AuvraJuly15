"""Pure rules for canonical conversation and weekly check-in facts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.v2.application.errors import unprocessable_content

WEEKLY_CHECKIN_DEFINITION_VERSION = "weekly-checkin.v1"
THREAD_TYPES = {
    "general",
    "care_plan",
    "weekly_checkin",
    "symptom_checkin",
    "support",
}
ANSWER_TYPES = {"scale", "choice", "text", "boolean", "multi_select"}


def iso_week_start(now: datetime, timezone: str) -> date:
    """Return the immutable Monday that owns this instant for the supplied IANA zone."""
    local_date = now.astimezone(ZoneInfo(timezone)).date()
    return local_date.fromordinal(local_date.toordinal() - local_date.isoweekday() + 1)


def validate_weekly_answer(
    answer: dict[str, Any], *, answer_type: str, answer_schema: dict[str, Any]
) -> None:
    """Validate the small, versioned answer vocabulary without accepting arbitrary JSON."""
    if answer_type not in ANSWER_TYPES:
        raise unprocessable_content("unknown_weekly_answer_type", "Question definition is invalid.")
    if set(answer) != {"value"}:
        raise unprocessable_content(
            "invalid_weekly_answer",
            "A weekly check-in answer must contain exactly one value field.",
        )
    value = answer["value"]
    if answer_type == "scale":
        _validate_scale(value, answer_schema)
    elif answer_type == "choice":
        _validate_choice(value, answer_schema)
    elif answer_type == "text":
        _validate_text(value, answer_schema)
    elif answer_type == "multi_select":
        _validate_multi_select(value, answer_schema)
    else:
        _validate_boolean(value)


def _validate_scale(value: Any, schema: dict[str, Any]) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if type(value) is not int or type(minimum) is not int or type(maximum) is not int:
        raise unprocessable_content("invalid_weekly_answer", "A scale answer must be an integer.")
    if value < minimum or value > maximum:
        raise unprocessable_content("invalid_weekly_answer", "Scale answer is outside its range.")


def _validate_choice(value: Any, schema: dict[str, Any]) -> None:
    choices = schema.get("choices")
    if not isinstance(value, str) or not isinstance(choices, list) or value not in choices:
        raise unprocessable_content(
            "invalid_weekly_answer", "Choice answer is not in its definition."
        )


def _validate_text(value: Any, schema: dict[str, Any]) -> None:
    maximum = schema.get("max_length")
    if not isinstance(value, str) or not isinstance(maximum, int):
        raise unprocessable_content("invalid_weekly_answer", "Text answer is invalid.")
    if not value.strip() or len(value) > maximum:
        raise unprocessable_content("invalid_weekly_answer", "Text answer violates its definition.")


def _validate_boolean(value: Any) -> None:
    if type(value) is not bool:
        raise unprocessable_content("invalid_weekly_answer", "Boolean answer is invalid.")


def _validate_multi_select(value: Any, schema: dict[str, Any]) -> None:
    choices = schema.get("choices")
    maximum = schema.get("max_selections")
    if not isinstance(value, list) or not isinstance(choices, list) or not isinstance(maximum, int):
        raise unprocessable_content("invalid_weekly_answer", "Multi-select answer is invalid.")
    if not value or len(value) > maximum or len(value) != len(set(value)):
        raise unprocessable_content("invalid_weekly_answer", "Multi-select values are invalid.")
    if not all(isinstance(item, str) and item in choices for item in value):
        raise unprocessable_content(
            "invalid_weekly_answer", "Multi-select answer is not in its definition."
        )
