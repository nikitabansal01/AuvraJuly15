"""Compare saved survey facts without treating array order as a new assessment."""
from datetime import datetime, timezone
from app.utils.data_sanitization import sanitize_list_field

ASSESSMENT_FIELDS = ("age", "period_description", "birth_control", "last_period_date_utc", "cycle_length",
    "period_concerns", "body_concerns", "skin_hair_concerns", "mental_health_concerns", "other_concerns",
    "top_concern", "diagnosed_conditions", "family_history", "workout_intensity", "sleep_duration", "stress_level", "survey_timezone")
ARRAY_FIELDS = {"birth_control", "period_concerns", "body_concerns", "skin_hair_concerns", "mental_health_concerns",
                "other_concerns", "diagnosed_conditions", "family_history"}

def assessment_facts(record):
    result = {}
    for field in ASSESSMENT_FIELDS:
        value = getattr(record, field, None)
        if field in ARRAY_FIELDS:
            value = sorted(sanitize_list_field(value, field))
        elif isinstance(value, datetime):
            value = (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()
        result[field] = value
    return result

def has_core_answers(record):
    return record is not None and (getattr(record, "age", None) is not None or getattr(record, "period_description", None) is not None)

class SavedSetupConflict(ValueError):
    pass
