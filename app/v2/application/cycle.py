"""Cycle state derived from observed period starts.

There is no cycle write path. Recording a period is
``POST /me/observations`` with ``code='period_start'``, so there is exactly one
way for that fact to enter the system.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.v2.application.contracts import CycleStateResponse
from app.v2.application.errors import not_found
from app.v2.domain.cycle_policy import CycleEvaluation, evaluate
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.models_observations import UserObservation
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


PERIOD_START_CODE = "period_start"
DECLARED_LENGTH_CODE = "cycle_length_declared"


async def _live_period_starts(
    session, user_id: uuid.UUID, *, as_of_recorded: datetime | None = None
) -> list[date]:
    """Local dates of period starts nothing supersedes.

    ``as_of_recorded`` replays the observation set as it stood at a past
    instant, which is what lets a published plan's cycle snapshot be
    reconciled against the facts that existed when it was published.
    """

    superseding = select(UserObservation.supersedes_id).where(
        UserObservation.supersedes_id.is_not(None)
    )
    if as_of_recorded is not None:
        superseding = superseding.where(UserObservation.recorded_at <= as_of_recorded)

    statement = select(UserObservation.observed_local_date).where(
        UserObservation.user_id == user_id,
        UserObservation.code == PERIOD_START_CODE,
        UserObservation.id.not_in(superseding),
    )
    if as_of_recorded is not None:
        statement = statement.where(UserObservation.recorded_at <= as_of_recorded)
    return list((await session.scalars(statement)).all())


async def _declared_bucket(session, user_id: uuid.UUID, uow) -> str | None:
    """The cycle-length bucket the user reported during onboarding."""

    assessment = await uow.onboarding.get_current_assessment_for_user(user_id)
    if assessment is None:
        return None
    answers = assessment.answers or {}
    value = answers.get("cycle_length")
    return value if isinstance(value, str) else None


async def evaluate_for_user(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: uuid.UUID,
    timezone: str,
    as_of_local_date: date,
    as_of_recorded: datetime | None = None,
) -> CycleEvaluation:
    """Shared derivation used by both the route and plan generation."""

    starts = await _live_period_starts(uow.session, user_id, as_of_recorded=as_of_recorded)
    return evaluate(
        period_starts=starts,
        declared_bucket=await _declared_bucket(uow.session, user_id, uow),
        as_of_local_date=as_of_local_date,
    )


async def cycle_state(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    as_of: date | None = None,
    now: datetime | None = None,
) -> CycleStateResponse:
    now = now or datetime.now(UTC)
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != "active":
        raise not_found("User")
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Profile")

    local = as_of or now.astimezone(ZoneInfo(profile.timezone)).date()
    evaluation = await evaluate_for_user(
        uow, user_id=user.id, timezone=profile.timezone, as_of_local_date=local
    )
    return CycleStateResponse(
        as_of_local_date=evaluation.as_of_local_date,
        timezone=profile.timezone,
        policy_version=evaluation.policy_version,
        cycle_day=evaluation.cycle_day,
        phase=evaluation.phase,
        phase_confidence=evaluation.phase_confidence,
        cycle_length_days=evaluation.cycle_length_days,
        cycle_length_source=evaluation.cycle_length_source,
        last_period_start=evaluation.last_period_start,
        next_period_estimate=evaluation.next_period_estimate,
    )
