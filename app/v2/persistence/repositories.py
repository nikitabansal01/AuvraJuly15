"""Repository implementations for the first v2 vertical slice."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.v2.domain.enums import PlanStatus
from app.v2.persistence.models import (
    ActionPlan,
    ActionPlanItem,
    ActionPlanItemVariant,
    ConsentRecord,
    GenerationJob,
    IdempotencyRecord,
    OnboardingAssessment,
    OnboardingSession,
    OutboxEvent,
    User,
    UserProfile,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_subject(
        self, auth_provider: str, auth_subject: str, *, for_update: bool = False
    ) -> User | None:
        statement = select(User).where(
            User.auth_provider == auth_provider,
            User.auth_subject == auth_subject,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    def add(self, user: User) -> None:
        self._session.add(user)


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID, *, for_update: bool = False) -> UserProfile | None:
        statement = select(UserProfile).where(UserProfile.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    def add(self, profile: UserProfile) -> None:
        self._session.add(profile)


class ConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, record: ConsentRecord) -> None:
        self._session.add(record)


class OnboardingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_session(
        self, session_id: uuid.UUID, *, for_update: bool = False
    ) -> OnboardingSession | None:
        statement = select(OnboardingSession).where(OnboardingSession.id == session_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_current_assessment(
        self, session_id: uuid.UUID, *, for_update: bool = False
    ) -> OnboardingAssessment | None:
        statement = select(OnboardingAssessment).where(
            OnboardingAssessment.session_id == session_id,
            OnboardingAssessment.is_current.is_(True),
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_assessments(self, session_id: uuid.UUID) -> list[OnboardingAssessment]:
        result = await self._session.scalars(
            select(OnboardingAssessment).where(OnboardingAssessment.session_id == session_id)
        )
        return list(result)

    async def get_current_assessment_for_user(
        self, user_id: uuid.UUID
    ) -> OnboardingAssessment | None:
        return await self._session.scalar(
            select(OnboardingAssessment).where(
                OnboardingAssessment.user_id == user_id,
                OnboardingAssessment.is_current.is_(True),
            )
        )

    def add_session(self, onboarding_session: OnboardingSession) -> None:
        self._session.add(onboarding_session)

    def add_assessment(self, assessment: OnboardingAssessment) -> None:
        self._session.add(assessment)


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, job: GenerationJob) -> None:
        self._session.add(job)

    async def get_owned(self, job_id: uuid.UUID, user_id: uuid.UUID) -> GenerationJob | None:
        return await self._session.scalar(
            select(GenerationJob).where(
                GenerationJob.id == job_id,
                GenerationJob.user_id == user_id,
            )
        )

    async def get_latest_plan_generation(
        self, user_id: uuid.UUID, local_date: date
    ) -> GenerationJob | None:
        """Return the latest terminal or non-terminal generation job for one owner/day."""
        return await self._session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.user_id == user_id,
                GenerationJob.job_type == "plan_generation",
                GenerationJob.request_payload["local_date"].as_string() == local_date.isoformat(),
            )
            .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
            .limit(1)
        )


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current_ready(self, user_id: uuid.UUID, local_date: date) -> ActionPlan | None:
        statement = (
            select(ActionPlan)
            .where(
                ActionPlan.user_id == user_id,
                ActionPlan.local_date == local_date,
                ActionPlan.is_current.is_(True),
                ActionPlan.status == PlanStatus.READY.value,
            )
            .options(
                selectinload(ActionPlan.items).selectinload(ActionPlanItem.hero_asset),
                selectinload(ActionPlan.items).selectinload(ActionPlanItem.variants),
                selectinload(ActionPlan.items)
                .selectinload(ActionPlanItem.variants)
                .selectinload(ActionPlanItemVariant.asset),
            )
        )
        return await self._session.scalar(statement)


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: OutboxEvent) -> None:
        self._session.add(event)

    async def get(self, event_id: uuid.UUID) -> OutboxEvent | None:
        return await self._session.get(OutboxEvent, event_id)


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self,
        record: IdempotencyRecord,
        *,
        now: datetime,
    ) -> tuple[IdempotencyRecord, bool]:
        """Atomically reserve a key or take over an expired reservation.

        The conflicting row is locked before expiry is inspected or reset, so
        concurrent retries cannot both take over the same stale key.
        """

        statement = (
            insert(IdempotencyRecord)
            .values(
                id=record.id,
                scope=record.scope,
                subject=record.subject,
                idempotency_key=record.idempotency_key,
                request_hash=record.request_hash,
                state=record.state,
                expires_at=record.expires_at,
            )
            .on_conflict_do_nothing(index_elements=["scope", "subject", "idempotency_key"])
            .returning(IdempotencyRecord.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted_id is not None:
            stored = await self._session.get(IdempotencyRecord, inserted_id)
            if stored is None:  # defensive: RETURNING must make this row visible
                raise RuntimeError("idempotency reservation was not readable")
            return stored, True

        stored = await self._session.scalar(
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == record.scope,
                IdempotencyRecord.subject == record.subject,
                IdempotencyRecord.idempotency_key == record.idempotency_key,
            )
            .with_for_update()
        )
        if stored is None:
            raise RuntimeError("idempotency reservation disappeared")
        if stored.expires_at <= now:
            stored.request_hash = record.request_hash
            stored.state = record.state
            stored.response_status = None
            stored.response_body = None
            stored.expires_at = record.expires_at
            return stored, True
        return stored, False
