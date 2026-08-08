"""Cycle phase derivation as a pure, replayable function."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.v2.domain.cycle_policy import (
    DECLARED,
    DEFAULT_CYCLE_LENGTH_DAYS,
    FOLLICULAR,
    HIGH,
    LOW,
    LUTEAL,
    LUTEAL_LENGTH_DAYS,
    MAX_PLAUSIBLE_CYCLE_DAY,
    MENSTRUAL,
    MENSTRUAL_LENGTH_DAYS,
    OBSERVED,
    OVULATORY,
    POLICY_VERSION,
    UNKNOWN,
    declared_cycle_length,
    evaluate,
    observed_cycle_length,
    phase_for_day,
    resolve_cycle_length,
)


def _starts(anchor: date, gaps: list[int]) -> list[date]:
    days, cursor = [anchor], anchor
    for gap in gaps:
        cursor = cursor + timedelta(days=gap)
        days.append(cursor)
    return days


def test_policy_version_is_stamped() -> None:
    assert POLICY_VERSION == "cycle.v1"


@pytest.mark.parametrize("day", range(1, MENSTRUAL_LENGTH_DAYS + 1))
def test_the_first_days_are_menstrual(day) -> None:
    assert phase_for_day(day, 28) == MENSTRUAL


def test_twenty_eight_day_cycle_boundaries() -> None:
    assert phase_for_day(5, 28) == MENSTRUAL
    assert phase_for_day(6, 28) == FOLLICULAR
    assert phase_for_day(13, 28) == OVULATORY
    assert phase_for_day(14, 28) == OVULATORY
    assert phase_for_day(15, 28) == OVULATORY
    assert phase_for_day(16, 28) == LUTEAL
    assert phase_for_day(28, 28) == LUTEAL


def test_a_long_cycle_lengthens_follicular_rather_than_moving_luteal() -> None:
    """Ovulation is counted backward from the next period, not forward."""

    # Ovulation day is 35 - 14 = 21, with a window of 20-22.
    assert phase_for_day(19, 35) == FOLLICULAR
    assert phase_for_day(20, 35) == OVULATORY
    assert phase_for_day(21, 35) == OVULATORY
    assert phase_for_day(22, 35) == OVULATORY
    assert phase_for_day(23, 35) == LUTEAL
    # The luteal phase stays the same length regardless of cycle length.
    for length in (21, 28, 35, 40):
        luteal_days = sum(
            1
            for day in range(1, length + 1)
            if phase_for_day(day, length) == LUTEAL
        )
        assert luteal_days == LUTEAL_LENGTH_DAYS - 1, length


def test_a_short_cycle_never_puts_ovulation_inside_menstruation() -> None:
    for day in range(1, MENSTRUAL_LENGTH_DAYS + 1):
        assert phase_for_day(day, 21) == MENSTRUAL
    assert phase_for_day(MENSTRUAL_LENGTH_DAYS + 1, 21) != MENSTRUAL


def test_declared_bucket_maps_to_a_length_and_unknown_stays_unknown() -> None:
    assert declared_cycle_length("26-30 days") == 28
    assert declared_cycle_length("I'm not sure") is None
    assert declared_cycle_length(None) is None


def test_observed_length_needs_three_periods_and_uses_the_median() -> None:
    anchor = date(2026, 1, 1)
    assert observed_cycle_length(_starts(anchor, [])) is None
    assert observed_cycle_length(_starts(anchor, [28])) is None
    assert observed_cycle_length(_starts(anchor, [28, 30])) == 29
    assert observed_cycle_length(_starts(anchor, [28, 29, 30])) == 29
    # A missed observation produces an implausible 56-day gap. It is discarded
    # rather than averaged in, so the estimate stays at the real cycle length.
    assert observed_cycle_length(_starts(anchor, [28, 56, 29, 30])) == 29


def test_history_beats_a_reported_bucket() -> None:
    anchor = date(2026, 1, 1)
    length, source = resolve_cycle_length(
        period_starts=_starts(anchor, [30, 30, 30]), declared_bucket="21-25 days"
    )
    assert (length, source) == (30, OBSERVED)

    length, source = resolve_cycle_length(
        period_starts=[anchor], declared_bucket="21-25 days"
    )
    assert (length, source) == (23, DECLARED)

    length, source = resolve_cycle_length(period_starts=[], declared_bucket=None)
    assert (length, source) == (None, UNKNOWN)


def test_no_observations_yields_no_phase_rather_than_a_guess() -> None:
    result = evaluate(
        period_starts=[], declared_bucket=None, as_of_local_date=date(2026, 8, 8)
    )
    assert result.phase is None
    assert result.cycle_day is None
    assert result.phase_confidence == UNKNOWN
    assert result.next_period_estimate is None


def test_a_future_period_start_is_ignored() -> None:
    """An observation about tomorrow must not produce a negative cycle day."""

    result = evaluate(
        period_starts=[date(2026, 9, 1)],
        declared_bucket="26-30 days",
        as_of_local_date=date(2026, 8, 8),
    )
    assert result.cycle_day is None
    assert result.last_period_start is None


def test_cycle_day_counts_from_the_period_start_inclusive() -> None:
    result = evaluate(
        period_starts=[date(2026, 8, 1)],
        declared_bucket="26-30 days",
        as_of_local_date=date(2026, 8, 1),
    )
    assert result.cycle_day == 1
    assert result.phase == MENSTRUAL


def test_confidence_is_high_only_with_observed_history_inside_the_cycle() -> None:
    anchor = date(2026, 5, 1)
    starts = _starts(anchor, [28, 28, 28])
    inside = evaluate(
        period_starts=starts,
        declared_bucket=None,
        as_of_local_date=starts[-1] + timedelta(days=10),
    )
    assert inside.phase_confidence == HIGH
    assert inside.cycle_length_source == OBSERVED

    declared_only = evaluate(
        period_starts=[anchor],
        declared_bucket="26-30 days",
        as_of_local_date=anchor + timedelta(days=10),
    )
    assert declared_only.phase_confidence == LOW


def test_a_late_cycle_stays_luteal_instead_of_wrapping() -> None:
    anchor = date(2026, 5, 1)
    starts = _starts(anchor, [28, 28, 28])
    late = evaluate(
        period_starts=starts,
        declared_bucket=None,
        as_of_local_date=starts[-1] + timedelta(days=40),
    )
    assert late.cycle_day == 41
    assert late.phase == LUTEAL
    assert late.phase_confidence == LOW


def test_an_implausibly_long_gap_declines_to_name_a_phase() -> None:
    anchor = date(2026, 1, 1)
    result = evaluate(
        period_starts=[anchor],
        declared_bucket="26-30 days",
        as_of_local_date=anchor + timedelta(days=MAX_PLAUSIBLE_CYCLE_DAY + 1),
    )
    assert result.cycle_day == MAX_PLAUSIBLE_CYCLE_DAY + 2
    assert result.phase is None
    assert result.phase_confidence == UNKNOWN


def test_correcting_a_period_date_changes_the_answer() -> None:
    """Corrections are just a different observation set; nothing is recomputed."""

    as_of = date(2026, 8, 20)
    before = evaluate(
        period_starts=[date(2026, 8, 1)],
        declared_bucket="26-30 days",
        as_of_local_date=as_of,
    )
    after = evaluate(
        period_starts=[date(2026, 8, 10)],
        declared_bucket="26-30 days",
        as_of_local_date=as_of,
    )
    assert before.cycle_day == 20
    assert after.cycle_day == 11
    assert before.phase != after.phase


def test_evaluation_is_deterministic_and_order_independent() -> None:
    anchor = date(2026, 3, 1)
    starts = _starts(anchor, [29, 28, 30])
    as_of = starts[-1] + timedelta(days=5)
    first = evaluate(
        period_starts=starts, declared_bucket=None, as_of_local_date=as_of
    )
    shuffled = evaluate(
        period_starts=list(reversed(starts)) + starts,
        declared_bucket=None,
        as_of_local_date=as_of,
    )
    assert first == shuffled


def test_the_snapshot_is_a_reproducible_memo() -> None:
    anchor = date(2026, 4, 1)
    starts = _starts(anchor, [28, 28, 28])
    as_of = starts[-1] + timedelta(days=7)
    evaluation = evaluate(
        period_starts=starts, declared_bucket=None, as_of_local_date=as_of
    )
    snapshot = evaluation.as_snapshot()

    replay = evaluate(
        period_starts=starts, declared_bucket=None, as_of_local_date=as_of
    ).as_snapshot()
    assert snapshot == replay
    assert snapshot["policy_version"] == POLICY_VERSION
    assert set(snapshot) == {
        "policy_version",
        "as_of_local_date",
        "cycle_day",
        "phase",
        "phase_confidence",
        "cycle_length_days",
        "cycle_length_source",
        "last_period_start",
    }


def test_dst_transitions_do_not_shift_the_cycle_day() -> None:
    """Local dates are stored, so a clock change cannot move a day boundary."""

    # 2026-03-29 is a European DST transition; 2026-11-01 a US one.
    for transition in (date(2026, 3, 29), date(2026, 11, 1)):
        start = transition - timedelta(days=10)
        result = evaluate(
            period_starts=[start],
            declared_bucket="26-30 days",
            as_of_local_date=transition,
        )
        assert result.cycle_day == 11


def test_default_length_is_used_for_phase_when_length_is_unknown() -> None:
    result = evaluate(
        period_starts=[date(2026, 8, 1)],
        declared_bucket=None,
        as_of_local_date=date(2026, 8, 15),
    )
    assert result.cycle_length_days is None
    assert result.cycle_length_source == UNKNOWN
    # A phase is still named, using the default, and flagged low confidence.
    assert result.phase == phase_for_day(15, DEFAULT_CYCLE_LENGTH_DAYS)
    assert result.phase_confidence == LOW
