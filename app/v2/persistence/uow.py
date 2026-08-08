"""The sole transaction boundary for v2 application operations."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.v2.persistence.database import get_async_session_maker
from app.v2.persistence.repositories import (
    ConsentRepository,
    IdempotencyRepository,
    JobRepository,
    OnboardingRepository,
    OutboxRepository,
    PlanRepository,
    ProfileRepository,
    UserRepository,
)
from app.v2.persistence.conversations import (
    ConversationRepository,
    WeeklyCheckinRepository,
)


class SqlAlchemyUnitOfWork:
    """Own one async session and its complete transaction lifecycle."""

    def __init__(
        self, session_factory: Callable[[], AsyncSession] | None = None
    ) -> None:
        self._session_factory = session_factory or get_async_session_maker()
        self.session: AsyncSession | None = None
        self._committed = False

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        await self.session.begin()
        self.users = UserRepository(self.session)
        self.profiles = ProfileRepository(self.session)
        self.consents = ConsentRepository(self.session)
        self.onboarding = OnboardingRepository(self.session)
        self.jobs = JobRepository(self.session)
        self.plans = PlanRepository(self.session)
        self.outbox = OutboxRepository(self.session)
        self.idempotency = IdempotencyRepository(self.session)
        self.conversations = ConversationRepository(self.session)
        self.weekly_checkins = WeeklyCheckinRepository(self.session)
        return self

    async def flush(self) -> None:
        self._require_session()
        await self.session.flush()

    async def commit(self) -> None:
        self._require_session()
        await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        self._require_session()
        await self.session.rollback()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        try:
            if exc is not None or not self._committed:
                await self.session.rollback()
        finally:
            await self.session.close()

    def _require_session(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork must be entered before use")
