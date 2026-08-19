"""SQLAlchemy repositories for the conversation and weekly check-in boundary."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.v2.persistence.models_engagement import (
    Conversation,
    ConversationMessage,
    WeeklyCheckin,
    WeeklyQuestion,
    WeeklyResponse,
)


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, conversation: Conversation) -> None:
        self._session.add(conversation)

    def add_message(self, message: ConversationMessage) -> None:
        self._session.add(message)

    async def get_owned(
        self,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_owned(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        before_updated_at: datetime | None = None,
        before_id: uuid.UUID | None = None,
    ) -> list[Conversation]:
        statement = select(Conversation).where(Conversation.user_id == user_id)
        if before_updated_at is not None and before_id is not None:
            statement = statement.where(
                or_(
                    Conversation.updated_at < before_updated_at,
                    and_(
                        Conversation.updated_at == before_updated_at,
                        Conversation.id < before_id,
                    ),
                )
            )
        rows = await self._session.scalars(
            statement.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit)
        )
        return list(rows)

    async def list_messages(
        self, conversation_id: uuid.UUID, *, before_sequence: int | None, limit: int
    ) -> list[ConversationMessage]:
        statement = select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id
        )
        if before_sequence is not None:
            statement = statement.where(ConversationMessage.sequence < before_sequence)
        rows = await self._session.scalars(
            statement.order_by(ConversationMessage.sequence.desc()).limit(limit)
        )
        return list(reversed(list(rows)))

    async def get_by_client_message(
        self, conversation_id: uuid.UUID, client_message_id: uuid.UUID
    ) -> ConversationMessage | None:
        return await self._session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.client_message_id == client_message_id,
            )
        )

    async def next_sequence(self, conversation_id: uuid.UUID) -> int:
        """Caller must hold the parent conversation row lock before calling this."""
        last_message = await self._session.scalar(
            select(ConversationMessage.sequence)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sequence.desc())
            .limit(1)
        )
        return (last_message or 0) + 1


class WeeklyCheckinRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, checkin: WeeklyCheckin) -> None:
        self._session.add(checkin)

    def add_response(self, response: WeeklyResponse) -> None:
        self._session.add(response)

    async def get_owned(
        self, checkin_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> WeeklyCheckin | None:
        statement = select(WeeklyCheckin).where(
            WeeklyCheckin.id == checkin_id, WeeklyCheckin.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_for_week(
        self, user_id: uuid.UUID, week_start, *, for_update: bool = False
    ) -> WeeklyCheckin | None:
        statement = select(WeeklyCheckin).where(
            WeeklyCheckin.user_id == user_id, WeeklyCheckin.week_start == week_start
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_questions(self, version: str) -> list[WeeklyQuestion]:
        rows = await self._session.scalars(
            select(WeeklyQuestion)
            .where(WeeklyQuestion.version == version)
            .order_by(WeeklyQuestion.ordinal)
        )
        return list(rows)

    async def get_question(self, question_id: uuid.UUID) -> WeeklyQuestion | None:
        return await self._session.get(WeeklyQuestion, question_id)

    async def get_response(
        self, checkin_id: uuid.UUID, question_id: uuid.UUID
    ) -> WeeklyResponse | None:
        return await self._session.scalar(
            select(WeeklyResponse).where(
                WeeklyResponse.weekly_checkin_id == checkin_id,
                WeeklyResponse.question_id == question_id,
            )
        )

    async def list_responses(self, checkin_id: uuid.UUID) -> list[WeeklyResponse]:
        rows = await self._session.scalars(
            select(WeeklyResponse)
            .where(WeeklyResponse.weekly_checkin_id == checkin_id)
            .order_by(WeeklyResponse.answered_at, WeeklyResponse.id)
        )
        return list(rows)

    async def count_required_answered(self, checkin_id: uuid.UUID, version: str) -> tuple[int, int]:
        questions = await self.list_questions(version)
        required_ids = {question.id for question in questions if question.required}
        if not required_ids:
            return 0, 0
        answers = await self._session.scalars(
            select(WeeklyResponse.question_id).where(
                WeeklyResponse.weekly_checkin_id == checkin_id,
                WeeklyResponse.question_id.in_(required_ids),
            )
        )
        return len(required_ids), len(set(answers))
