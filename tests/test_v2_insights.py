"""Pure aggregation rules for progress and insights."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.v2.domain.insights import (
    ALL,
    MAX_REPORT_DAYS,
    MIN_OBSERVATIONS_FOR_PATTERN,
    MONTH,
    WEEK,
    Bucket,
    bucket_end_for,
    bucket_start_for,
    is_sufficient,
    iso_week_start,
    longest_run,
    month_start,
    range_error,
    ratio,
)


def _bucket(completed: int, eligible: int) -> Bucket:
    return Bucket(
        bucket_start=date(2026, 8, 3),
        bucket_end=date(2026, 8, 9),
        completed=completed,
        eligible=eligible,
        days_earned=0,
        days_frozen=0,
        days_missed=0,
        is_provisional=False,
    )


def test_adherence_is_none_not_zero_when_nothing_was_eligible() -> None:
    """Zero would claim a failure on a day the app never asked anything."""

    assert _bucket(0, 0).adherence is None
    assert _bucket(0, 4).adherence == 0.0
    assert _bucket(2, 4).adherence == 0.5


def test_ratio_follows_the_same_rule() -> None:
    assert ratio(0, 0) is None
    assert ratio(1, 4) == 0.25


def test_week_start_is_monday_and_matches_the_checkin_domain() -> None:
    from app.v2.domain.conversations import iso_week_start as checkin_week_start
    from datetime import datetime, UTC

    for day in (date(2026, 8, 3), date(2026, 8, 9), date(2026, 1, 1)):
        assert iso_week_start(day).isoweekday() == 1
    # A progress week and a check-in week must never disagree.
    moment = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert iso_week_start(moment.date()) == checkin_week_start(moment, "UTC")


def test_week_start_spans_year_boundaries() -> None:
    assert iso_week_start(date(2026, 1, 1)) == date(2025, 12, 29)
    assert iso_week_start(date(2027, 1, 3)) == date(2026, 12, 28)


def test_month_bucket_covers_the_whole_month_including_december() -> None:
    assert month_start(date(2026, 8, 20)) == date(2026, 8, 1)
    assert bucket_end_for(date(2026, 8, 1), MONTH) == date(2026, 8, 31)
    assert bucket_end_for(date(2026, 2, 1), MONTH) == date(2026, 2, 28)
    assert bucket_end_for(date(2026, 12, 1), MONTH) == date(2026, 12, 31)


def test_week_bucket_is_seven_days() -> None:
    start = bucket_start_for(date(2026, 8, 6), WEEK)
    assert (bucket_end_for(start, WEEK) - start).days == 6


def test_all_grain_collapses_to_a_single_bucket_key() -> None:
    assert bucket_start_for(date(2026, 8, 6), ALL) == date(2026, 8, 6)


def test_an_inverted_or_oversized_range_is_rejected() -> None:
    assert range_error(start=date(2026, 8, 9), end=date(2026, 8, 1)) is not None
    ok = range_error(start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert ok is None
    too_long = range_error(
        start=date(2026, 1, 1), end=date(2026, 1, 1) + timedelta(days=MAX_REPORT_DAYS)
    )
    assert "at most" in too_long


def test_longest_run_finds_the_best_streak() -> None:
    assert longest_run([]) == 0
    days = [date(2026, 8, d) for d in (1, 2, 3, 6, 7)]
    assert longest_run(days) == 3
    assert longest_run(days + days) == 3


def test_sufficiency_threshold_is_explicit() -> None:
    assert not is_sufficient(MIN_OBSERVATIONS_FOR_PATTERN - 1)
    assert is_sufficient(MIN_OBSERVATIONS_FOR_PATTERN)
