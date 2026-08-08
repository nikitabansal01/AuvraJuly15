"""Pure aggregation and sufficiency rules for progress and insights.

Nothing here is stored. Every number is recomputed from the canonical ledgers
on each request, because a stored aggregate is a second source of truth that
drifts from the facts it came from — the mistake v1 made with
``user_streak_data`` counters and ``user_rewards`` arrays.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


#: Below this many observations a correlation is noise, so the API says
#: "insufficient data" rather than reporting a number that looks meaningful.
MIN_OBSERVATIONS_FOR_PATTERN = 4

#: A bounded range keeps one request from scanning an unbounded history.
MAX_REPORT_DAYS = 400

WEEK = "week"
MONTH = "month"
ALL = "all"
PERIODS = frozenset({WEEK, MONTH, ALL})


@dataclass(frozen=True, slots=True)
class Bucket:
    """One reporting window and what happened inside it."""

    bucket_start: date
    bucket_end: date
    completed: int
    eligible: int
    days_earned: int
    days_frozen: int
    days_missed: int
    is_provisional: bool

    @property
    def adherence(self) -> float | None:
        """None, never zero, when nothing was eligible.

        Zero would claim the user failed on a day the app never asked her
        anything.
        """

        return self.completed / self.eligible if self.eligible else None


def iso_week_start(day: date) -> date:
    """The Monday owning this date.

    Uses the same arithmetic as the weekly check-in domain rather than
    date_trunc, so a progress week and a check-in week can never disagree.
    """

    return day.fromordinal(day.toordinal() - day.isoweekday() + 1)


def month_start(day: date) -> date:
    return day.replace(day=1)


def bucket_start_for(day: date, period: str) -> date:
    if period == WEEK:
        return iso_week_start(day)
    if period == MONTH:
        return month_start(day)
    return day


def bucket_end_for(start: date, period: str) -> date:
    if period == WEEK:
        return start + timedelta(days=6)
    if period == MONTH:
        next_month = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        return next_month - timedelta(days=1)
    return start


def range_error(*, start: date, end: date) -> str | None:
    """Return why this range is unusable, or None when it may be reported."""

    if end < start:
        return "The end of the range must not precede its start."
    if (end - start).days + 1 > MAX_REPORT_DAYS:
        return f"A report may cover at most {MAX_REPORT_DAYS} days."
    return None


def longest_run(days: list[date]) -> int:
    """Longest consecutive run among a set of qualifying local dates."""

    ordered = sorted(set(days))
    if not ordered:
        return 0
    best = run = 1
    for previous, current in zip(ordered, ordered[1:]):
        run = run + 1 if current - previous == timedelta(days=1) else 1
        best = max(best, run)
    return best


def is_sufficient(observation_count: int) -> bool:
    return observation_count >= MIN_OBSERVATIONS_FOR_PATTERN


def ratio(numerator: int, denominator: int) -> float | None:
    """None rather than zero when the denominator is empty."""

    return numerator / denominator if denominator else None
