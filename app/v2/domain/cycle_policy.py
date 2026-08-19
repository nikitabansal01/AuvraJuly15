"""Deterministic menstrual-cycle phase derivation.

A cycle is not observed; it is inferred from observed period starts. That is
why there is no `menstrual_cycles` table and no cycle write route: a period
start is an observation like any other, and phase is a pure function over the
live ones. Storing an inferred cycle would mean recomputing or invalidating
every derived row whenever a user corrects a single period date, which is the
same class of bug as v1's stored BMI.

The phase boundaries are ported from the retained legacy service so users see
the same answer. The load-bearing detail is that ovulation is counted backward
from the *next* expected period rather than forward from the last one, so a
long cycle lengthens the follicular phase rather than shifting ovulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median


POLICY_VERSION = "cycle.v1"

MENSTRUAL = "menstrual"
FOLLICULAR = "follicular"
OVULATORY = "ovulatory"
LUTEAL = "luteal"

#: The luteal phase is approximately constant across cycle lengths; variation
#: lives in the follicular phase.
LUTEAL_LENGTH_DAYS = 14
MENSTRUAL_LENGTH_DAYS = 5
OVULATION_WINDOW_DAYS = 2

#: Below this many observed periods there are too few gaps to take a median,
#: so the user's declared bucket is used instead and labelled as such.
MIN_PERIODS_FOR_OBSERVED_LENGTH = 3

#: A cycle this long has almost certainly missed an observation, so the day
#: count stops being meaningful and confidence drops.
MAX_PLAUSIBLE_CYCLE_DAY = 90

DEFAULT_CYCLE_LENGTH_DAYS = 28
MIN_CYCLE_LENGTH_DAYS = 21
MAX_CYCLE_LENGTH_DAYS = 45

#: Midpoints of the assessment's buckets. "I'm not sure" and absent both mean
#: unknown rather than a guess.
DECLARED_CYCLE_LENGTHS: dict[str, int] = {
    "Less than 21 days": 20,
    "21-25 days": 23,
    "26-30 days": 28,
    "31-35 days": 33,
    "35+ days": 38,
}

OBSERVED = "observed"
DECLARED = "declared"
UNKNOWN = "unknown"

HIGH = "high"
LOW = "low"


@dataclass(frozen=True, slots=True)
class CycleEvaluation:
    """What the cycle looked like on one local date, and how sure we are."""

    policy_version: str
    as_of_local_date: date
    cycle_day: int | None
    phase: str | None
    phase_confidence: str
    cycle_length_days: int | None
    cycle_length_source: str
    last_period_start: date | None
    next_period_estimate: date | None

    def as_snapshot(self) -> dict[str, object]:
        """The memo stored on a published plan.

        Deliberately a record of a reproducible derivation rather than an
        independent source: replaying this policy against the observations that
        existed at publication must reproduce it exactly.
        """

        return {
            "policy_version": self.policy_version,
            "as_of_local_date": self.as_of_local_date.isoformat(),
            "cycle_day": self.cycle_day,
            "phase": self.phase,
            "phase_confidence": self.phase_confidence,
            "cycle_length_days": self.cycle_length_days,
            "cycle_length_source": self.cycle_length_source,
            "last_period_start": (
                self.last_period_start.isoformat() if self.last_period_start else None
            ),
        }


def declared_cycle_length(bucket: str | None) -> int | None:
    """Map an assessment bucket to a day count, or None when unknown."""

    if bucket is None:
        return None
    return DECLARED_CYCLE_LENGTHS.get(bucket)


def observed_cycle_length(period_starts: list[date]) -> int | None:
    """Median gap between consecutive observed period starts.

    The median rather than the mean, so one missed observation producing a
    double-length gap does not drag the estimate.
    """

    ordered = sorted(set(period_starts))
    if len(ordered) < MIN_PERIODS_FOR_OBSERVED_LENGTH:
        return None
    gaps = [
        (later - earlier).days
        for earlier, later in zip(ordered, ordered[1:])
        if MIN_CYCLE_LENGTH_DAYS <= (later - earlier).days <= MAX_CYCLE_LENGTH_DAYS
    ]
    if not gaps:
        return None
    return int(round(median(gaps)))


def resolve_cycle_length(
    *, period_starts: list[date], declared_bucket: str | None
) -> tuple[int | None, str]:
    """Prefer what the user's own history shows over what she once reported."""

    observed = observed_cycle_length(period_starts)
    if observed is not None:
        return observed, OBSERVED
    declared = declared_cycle_length(declared_bucket)
    if declared is not None:
        return declared, DECLARED
    return None, UNKNOWN


def phase_for_day(cycle_day: int, cycle_length_days: int) -> str:
    """Which phase a cycle day falls in.

    Ovulation is `cycle_length - LUTEAL_LENGTH_DAYS`, counted backward from the
    next expected period. A 35-day cycle therefore ovulates on day 21, not day
    14, and the extra days land in the follicular phase.
    """

    if cycle_day <= MENSTRUAL_LENGTH_DAYS:
        return MENSTRUAL
    ovulation_day = cycle_length_days - LUTEAL_LENGTH_DAYS
    half_window = OVULATION_WINDOW_DAYS // 2
    ovulation_start = max(MENSTRUAL_LENGTH_DAYS + 1, ovulation_day - half_window)
    ovulation_end = max(ovulation_start, ovulation_day + half_window)
    if cycle_day < ovulation_start:
        return FOLLICULAR
    if cycle_day <= ovulation_end:
        return OVULATORY
    return LUTEAL


def evaluate(
    *,
    period_starts: list[date],
    declared_bucket: str | None,
    as_of_local_date: date,
) -> CycleEvaluation:
    """Derive the cycle state on one local date from observed facts only."""

    past_starts = sorted({d for d in period_starts if d <= as_of_local_date})
    cycle_length_days, source = resolve_cycle_length(
        period_starts=past_starts, declared_bucket=declared_bucket
    )

    if not past_starts:
        return CycleEvaluation(
            policy_version=POLICY_VERSION,
            as_of_local_date=as_of_local_date,
            cycle_day=None,
            phase=None,
            phase_confidence=UNKNOWN,
            cycle_length_days=cycle_length_days,
            cycle_length_source=source,
            last_period_start=None,
            next_period_estimate=None,
        )

    last_start = past_starts[-1]
    cycle_day = (as_of_local_date - last_start).days + 1
    effective_length = cycle_length_days or DEFAULT_CYCLE_LENGTH_DAYS

    # Beyond a plausible cycle an observation is almost certainly missing, so
    # report the day count but decline to name a phase from it.
    if cycle_day > MAX_PLAUSIBLE_CYCLE_DAY:
        return CycleEvaluation(
            policy_version=POLICY_VERSION,
            as_of_local_date=as_of_local_date,
            cycle_day=cycle_day,
            phase=None,
            phase_confidence=UNKNOWN,
            cycle_length_days=cycle_length_days,
            cycle_length_source=source,
            last_period_start=last_start,
            next_period_estimate=None,
        )

    # A day past the expected length means the cycle is late, not that the user
    # has wrapped into a new one; the phase stays luteal and confidence drops.
    within_expected = cycle_day <= effective_length
    phase = phase_for_day(min(cycle_day, effective_length), effective_length)
    confidence = HIGH if source == OBSERVED and within_expected else LOW

    return CycleEvaluation(
        policy_version=POLICY_VERSION,
        as_of_local_date=as_of_local_date,
        cycle_day=cycle_day,
        phase=phase,
        phase_confidence=confidence,
        cycle_length_days=cycle_length_days,
        cycle_length_source=source,
        last_period_start=last_start,
        next_period_estimate=(
            last_start + timedelta(days=effective_length) if cycle_length_days is not None else None
        ),
    )
