"""The versioned catalog of things a user can assert about herself.

Preferences, body metrics, symptoms and period dates were four subsystems in
v1 — three tables plus a free-form ``user_profiles.chatbot_memory`` JSON blob.
They are one row grain: *the user asserted that a named observable held a value
at an instant*. The difference between them lives entirely in the read (latest
value versus full series), not in the storage.

The obvious way to unify them would be a ``value JSONB`` column, which is the
same sin as ``chatbot_memory`` wearing a new table name. Instead each code
declares its value kind here, and the database enforces exactly one typed value
column per row. Nothing in this file is stored in the database; it is policy,
and every observation row stamps :data:`CATALOG_VERSION` so a future revision
can be reasoned about without rewriting history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


CATALOG_VERSION = "observations.v1"

# Row grain discriminator.
SYMPTOM = "symptom"
BODY_METRIC = "body_metric"
PREFERENCE = "preference"
CYCLE_EVENT = "cycle_event"

OBSERVATION_TYPES = frozenset({SYMPTOM, BODY_METRIC, PREFERENCE, CYCLE_EVENT})

# Which typed column carries the value.
NUMERIC = "numeric"
CODES = "codes"
TEXT = "text"

MAX_CODE_CARDINALITY = 24
MAX_NOTE_LENGTH = 4000


@dataclass(frozen=True, slots=True)
class Observable:
    """One thing a user can assert, and the shape of a valid assertion."""

    code: str
    observation_type: str
    value_kind: str
    label: str
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    multi_select: bool = False

    @property
    def is_multi(self) -> bool:
        return self.value_kind == CODES and self.multi_select


def _numeric(code, observation_type, label, unit, minimum, maximum) -> Observable:
    return Observable(
        code=code,
        observation_type=observation_type,
        value_kind=NUMERIC,
        label=label,
        unit=unit,
        minimum=minimum,
        maximum=maximum,
    )


def _choice(code, observation_type, label, choices, *, multi=False) -> Observable:
    return Observable(
        code=code,
        observation_type=observation_type,
        value_kind=CODES,
        label=label,
        choices=tuple(choices),
        multi_select=multi,
    )


#: Symptom severity reuses v1's 0-10 scale so historical rows migrate without
#: rescaling. `present` records an occurrence with no severity given.
SEVERITY_UNIT = "score_0_10"

OBSERVATION_CATALOG: tuple[Observable, ...] = (
    # --- body metrics: numeric, time series by nature ---------------------
    _numeric("weight_kg", BODY_METRIC, "Weight", "kg", 20, 400),
    _numeric("height_cm", BODY_METRIC, "Height", "cm", 80, 250),
    _numeric("waist_cm", BODY_METRIC, "Waist", "cm", 30, 250),
    # --- preferences: current state, but corrigible over time -------------
    _choice(
        "diet_preference",
        PREFERENCE,
        "Diet",
        ("omnivore", "vegetarian", "vegan", "pescatarian", "eggetarian", "jain"),
    ),
    _choice(
        "food_allergies",
        PREFERENCE,
        "Food allergies",
        (
            "dairy", "eggs", "fish", "gluten", "peanuts", "sesame",
            "shellfish", "soy", "tree_nuts", "none",
        ),
        multi=True,
    ),
    _choice(
        "cuisine_preference",
        PREFERENCE,
        "Cuisines",
        (
            "south_indian", "north_indian", "chinese", "continental",
            "japanese", "mediterranean", "mexican", "thai",
        ),
        multi=True,
    ),
    _choice(
        "dine_out_frequency",
        PREFERENCE,
        "Dining out",
        ("rarely", "weekly", "few_times_a_week", "daily"),
    ),
    _choice(
        "cultural_background",
        PREFERENCE,
        "Cultural background",
        ("south_asian", "east_asian", "african", "european", "latin", "other"),
    ),
    _choice(
        "cravings",
        PREFERENCE,
        "Cravings",
        ("sweet", "salty", "fried", "carbs", "chocolate", "caffeine"),
        multi=True,
    ),
    # --- cycle events: the observed facts phase is derived from -----------
    _choice("period_start", CYCLE_EVENT, "Period started", ("period_start",)),
    _numeric("cycle_length_declared", CYCLE_EVENT, "Usual cycle length", "days", 15, 90),
    # --- symptoms ---------------------------------------------------------
    _numeric("cramps", SYMPTOM, "Cramps", SEVERITY_UNIT, 0, 10),
    _numeric("bloating", SYMPTOM, "Bloating", SEVERITY_UNIT, 0, 10),
    _numeric("acne", SYMPTOM, "Acne", SEVERITY_UNIT, 0, 10),
    _numeric("fatigue", SYMPTOM, "Fatigue", SEVERITY_UNIT, 0, 10),
    _numeric("mood_swings", SYMPTOM, "Mood swings", SEVERITY_UNIT, 0, 10),
    _numeric("headache", SYMPTOM, "Headache", SEVERITY_UNIT, 0, 10),
    _numeric("hair_loss", SYMPTOM, "Hair loss", SEVERITY_UNIT, 0, 10),
    _numeric("sleep_quality", SYMPTOM, "Sleep quality", SEVERITY_UNIT, 0, 10),
)

BY_CODE: Mapping[str, Observable] = {o.code: o for o in OBSERVATION_CATALOG}


def observable_or_none(code: str) -> Observable | None:
    return BY_CODE.get(code)


def normalize_codes(values: list[str]) -> list[str]:
    """Sorted and deduplicated, matching the database's normalization check."""

    return sorted(set(values))


def _numeric_error(observable: Observable, value: float | None, unit: str | None) -> str | None:
    if value is None:
        return f"'{observable.code}' records a number."
    if unit != observable.unit:
        return f"'{observable.code}' is measured in {observable.unit}."
    if observable.minimum is not None and value < observable.minimum:
        return f"{observable.label} must be at least {observable.minimum:g}."
    if observable.maximum is not None and value > observable.maximum:
        return f"{observable.label} must be at most {observable.maximum:g}."
    if observable.unit == SEVERITY_UNIT and value != int(value):
        return "Severity must be a whole number from 0 to 10."
    return None


def _codes_error(observable: Observable, values: list[str] | None) -> str | None:
    if values is None:
        return f"'{observable.code}' records one or more choices."
    if not values:
        return f"'{observable.code}' needs at least one choice."
    if not observable.multi_select and len(values) > 1:
        return f"'{observable.code}' takes a single choice."
    if len(values) > MAX_CODE_CARDINALITY:
        return f"'{observable.code}' takes at most {MAX_CODE_CARDINALITY} choices."
    unknown = sorted(set(values) - set(observable.choices))
    if unknown:
        return f"'{unknown[0]}' is not a choice for '{observable.code}'."
    return None


_VALUE_VALIDATORS = {
    NUMERIC: lambda o, n, u, c, t: _numeric_error(o, n, u),
    CODES: lambda o, n, u, c, t: _codes_error(o, c),
    TEXT: lambda o, n, u, c, t: (
        None if t is not None else f"'{o.code}' records text."
    ),
}


def validation_error(
    *,
    code: str,
    observation_type: str,
    numeric: float | None = None,
    unit: str | None = None,
    codes: list[str] | None = None,
    text: str | None = None,
    note: str | None = None,
) -> str | None:
    """Return why this assertion is invalid, or None when it may be written.

    This mirrors the database's constraints so a bad request fails as a 422
    with a useful message rather than as a constraint violation.
    """

    observable = BY_CODE.get(code)
    if observable is None:
        return f"'{code}' is not a known observation."
    if observable.observation_type != observation_type:
        return (
            f"'{code}' is a {observable.observation_type} observation, "
            f"not {observation_type}."
        )
    if len([v for v in (numeric, codes, text) if v is not None]) != 1:
        return "An observation records exactly one value."
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        return "Your note is too long."
    return _VALUE_VALIDATORS[observable.value_kind](
        observable, numeric, unit, codes, text
    )


def body_mass_index(*, weight_kg: float | None, height_cm: float | None) -> float | None:
    """BMI is a pure function of the latest live weight and height.

    v1 stored `bmi` and `bmi_category` alongside the metrics, so both went
    stale the moment a new weight arrived. Nothing derived is persisted here.
    """

    if not weight_kg or not height_cm:
        return None
    metres = height_cm / 100
    return round(weight_kg / (metres * metres), 1)


def bmi_band(bmi: float | None) -> str | None:
    """Descriptive band only. This is not a diagnosis and implies no advice."""

    if bmi is None:
        return None
    if bmi < 18.5:
        return "below_typical_range"
    if bmi < 25:
        return "typical_range"
    if bmi < 30:
        return "above_typical_range"
    return "well_above_typical_range"


def waist_height_ratio(
    *, waist_cm: float | None, height_cm: float | None
) -> float | None:
    if not waist_cm or not height_cm:
        return None
    return round(waist_cm / height_cm, 3)
