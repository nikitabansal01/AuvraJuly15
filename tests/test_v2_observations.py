"""Observation catalog validation and derived body metrics."""

from __future__ import annotations

import pytest

from app.v2.domain.observation_catalog import (
    BODY_METRIC,
    CATALOG_VERSION,
    CODES,
    CYCLE_EVENT,
    MAX_CODE_CARDINALITY,
    NUMERIC,
    OBSERVATION_CATALOG,
    OBSERVATION_TYPES,
    PREFERENCE,
    SEVERITY_UNIT,
    SYMPTOM,
    bmi_band,
    body_mass_index,
    normalize_codes,
    observable_or_none,
    validation_error,
    waist_height_ratio,
)


def test_catalog_is_internally_consistent() -> None:
    codes = [o.code for o in OBSERVATION_CATALOG]
    assert len(codes) == len(set(codes))
    for observable in OBSERVATION_CATALOG:
        assert observable.observation_type in OBSERVATION_TYPES
        assert observable.value_kind in {NUMERIC, CODES, "text"}
        if observable.value_kind == NUMERIC:
            assert observable.unit, observable.code
            assert observable.minimum is not None and observable.maximum is not None
            assert observable.minimum < observable.maximum
        if observable.value_kind == CODES:
            assert observable.choices, observable.code
            assert len(observable.choices) == len(set(observable.choices))


def test_catalog_version_is_stamped() -> None:
    assert CATALOG_VERSION == "observations.v1"


def test_all_four_observation_types_are_represented() -> None:
    present = {o.observation_type for o in OBSERVATION_CATALOG}
    assert present == {SYMPTOM, BODY_METRIC, PREFERENCE, CYCLE_EVENT}


def test_unknown_code_is_rejected() -> None:
    assert "not a known observation" in validation_error(
        code="unicorns", observation_type=SYMPTOM, numeric=1, unit=SEVERITY_UNIT
    )


def test_code_declared_under_the_wrong_type_is_rejected() -> None:
    problem = validation_error(code="weight_kg", observation_type=SYMPTOM, numeric=60, unit="kg")
    assert "is a body_metric observation" in problem


def test_exactly_one_value_is_required() -> None:
    both = validation_error(
        code="weight_kg",
        observation_type=BODY_METRIC,
        numeric=60,
        unit="kg",
        codes=["x"],
    )
    assert both == "An observation records exactly one value."
    neither = validation_error(code="weight_kg", observation_type=BODY_METRIC)
    assert neither == "An observation records exactly one value."


@pytest.mark.parametrize(
    "value, unit, expected_ok",
    [
        (60.0, "kg", True),
        (19.0, "kg", False),  # below minimum
        (401.0, "kg", False),  # above maximum
        (60.0, "lb", False),  # wrong unit
    ],
)
def test_numeric_bounds_and_unit_are_enforced(value, unit, expected_ok) -> None:
    problem = validation_error(
        code="weight_kg", observation_type=BODY_METRIC, numeric=value, unit=unit
    )
    assert (problem is None) is expected_ok


def test_severity_must_be_a_whole_number() -> None:
    assert (
        validation_error(code="cramps", observation_type=SYMPTOM, numeric=7, unit=SEVERITY_UNIT)
        is None
    )
    assert "whole number" in validation_error(
        code="cramps", observation_type=SYMPTOM, numeric=7.5, unit=SEVERITY_UNIT
    )
    assert (
        validation_error(code="cramps", observation_type=SYMPTOM, numeric=11, unit=SEVERITY_UNIT)
        is not None
    )


def test_single_select_rejects_multiple_choices() -> None:
    assert (
        validation_error(code="diet_preference", observation_type=PREFERENCE, codes=["vegan"])
        is None
    )
    assert "single choice" in validation_error(
        code="diet_preference",
        observation_type=PREFERENCE,
        codes=["vegan", "jain"],
    )


def test_multi_select_accepts_several_but_rejects_unknown_choices() -> None:
    assert (
        validation_error(
            code="food_allergies",
            observation_type=PREFERENCE,
            codes=["dairy", "peanuts"],
        )
        is None
    )
    assert "is not a choice" in validation_error(
        code="food_allergies", observation_type=PREFERENCE, codes=["plutonium"]
    )


def test_choice_cardinality_is_bounded() -> None:
    problem = validation_error(
        code="food_allergies",
        observation_type=PREFERENCE,
        codes=[f"c{i}" for i in range(MAX_CODE_CARDINALITY + 1)],
    )
    assert "at most" in problem


def test_empty_choice_list_is_rejected() -> None:
    assert "at least one choice" in validation_error(
        code="food_allergies", observation_type=PREFERENCE, codes=[]
    )


def test_note_length_is_bounded() -> None:
    assert "note is too long" in validation_error(
        code="cramps",
        observation_type=SYMPTOM,
        numeric=5,
        unit=SEVERITY_UNIT,
        note="x" * 4001,
    )


def test_normalize_codes_sorts_and_deduplicates() -> None:
    assert normalize_codes(["soy", "dairy", "soy"]) == ["dairy", "soy"]


def test_period_start_is_a_cycle_event_not_a_symptom() -> None:
    observable = observable_or_none("period_start")
    assert observable is not None
    assert observable.observation_type == CYCLE_EVENT


def test_bmi_is_derived_and_never_invented_from_partial_data() -> None:
    assert body_mass_index(weight_kg=60, height_cm=165) == 22.0
    assert body_mass_index(weight_kg=None, height_cm=165) is None
    assert body_mass_index(weight_kg=60, height_cm=None) is None
    assert body_mass_index(weight_kg=60, height_cm=0) is None


def test_bmi_band_is_descriptive_and_absent_without_a_value() -> None:
    assert bmi_band(None) is None
    assert bmi_band(17.0) == "below_typical_range"
    assert bmi_band(22.0) == "typical_range"
    assert bmi_band(27.0) == "above_typical_range"
    assert bmi_band(35.0) == "well_above_typical_range"


def test_waist_height_ratio_needs_both_measurements() -> None:
    assert waist_height_ratio(waist_cm=70, height_cm=165) == 0.424
    assert waist_height_ratio(waist_cm=None, height_cm=165) is None
    assert waist_height_ratio(waist_cm=70, height_cm=None) is None
