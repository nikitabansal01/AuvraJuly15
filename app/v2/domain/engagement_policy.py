"""Deterministic, replay-safe engagement adjudication policy.

The retained flow awards one point only for a closed daily plan whose immutable
Daily Review records every active item as completed.  A freeze is a qualifying
streak state, but no client-facing freeze allocation or redemption command is
approved in this slice.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


DAILY_REVIEW_REWARD_POINTS = 1
QUALIFYING_OUTCOME = "completed"
QUALIFYING_STREAK_STATES = frozenset(("earned", "frozen"))


def is_closed_local_day(*, plan_date: date, current_local_date: date) -> bool:
    """A local date is final only once that user's next local date has begun."""

    return plan_date < current_local_date


def daily_review_state(*, completed_count: int, total_count: int) -> str:
    """Return the immutable daily adjudication from normalized review answers."""

    if total_count <= 0:
        raise ValueError("a Daily Review needs at least one item")
    return "earned" if completed_count == total_count else "missed"


def reward_points_for_streak_state(state: str) -> int:
    """Return the approved deterministic grant for one finalized streak fact."""

    return DAILY_REVIEW_REWARD_POINTS if state == "earned" else 0


def closed_streak_length(qualifying_days: Iterable[date], *, current_local_date: date) -> int:
    """Derive the current streak from finalized earned/frozen local dates."""

    days = set(qualifying_days)
    cursor = current_local_date - timedelta(days=1)
    length = 0
    while cursor in days:
        length += 1
        cursor -= timedelta(days=1)
    return length
