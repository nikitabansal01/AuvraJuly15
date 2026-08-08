"""Weekly check-in history and the care-plan check-in thread.

These close two real gaps rather than adding features. A client that answered
two of five questions and backgrounded the app could not re-read its own
check-in, because `/due` was the only way to reach one. And a care-plan
conversation had no way to say which plan it was about.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.v2.application.contracts import (
    ConversationResponse,
    WeeklyCheckinPageResponse,
    WeeklyCheckinResponse,
)
from app.v2.application.conversations import (
    _active_user,
    _checkin_response,
    _conversation_response,
)
from app.v2.application.errors import conflict, not_found
from app.v2.application.services import _begin_idempotent, _complete_idempotent
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.models import ActionPlan
from app.v2.persistence.models_engagement import (
    Conversation,
    WeeklyCheckin,
    WeeklyQuestion,
    WeeklyResponse,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


CARE_PLAN_SUBJECT = "action_plan"
MAX_PAGE_SIZE = 100


async def _owned_checkin(
    uow: SqlAlchemyUnitOfWork, user_id: uuid.UUID, checkin_id: uuid.UUID
) -> WeeklyCheckin:
    checkin = await uow.session.get(WeeklyCheckin, checkin_id)
    # Another user's check-in is reported as absent, never as forbidden, so the
    # response cannot confirm that an id exists.
    if checkin is None or checkin.user_id != user_id:
        raise not_found("Weekly check-in")
    return checkin


async def _definition(uow: SqlAlchemyUnitOfWork, checkin: WeeklyCheckin):
    questions = list(
        (
            await uow.session.scalars(
                select(WeeklyQuestion)
                .where(WeeklyQuestion.version == checkin.definition_version)
                .order_by(WeeklyQuestion.ordinal)
            )
        ).all()
    )
    responses = list(
        (
            await uow.session.scalars(
                select(WeeklyResponse).where(
                    WeeklyResponse.weekly_checkin_id == checkin.id
                )
            )
        ).all()
    )
    return questions, responses


async def get_weekly_checkin(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    checkin_id: uuid.UUID,
) -> WeeklyCheckinResponse:
    user = await _active_user(uow, principal)
    checkin = await _owned_checkin(uow, user.id, checkin_id)
    questions, responses = await _definition(uow, checkin)
    return _checkin_response(checkin, questions, responses)


async def list_weekly_checkins(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    limit: int = 20,
    cursor: uuid.UUID | None = None,
) -> WeeklyCheckinPageResponse:
    user = await _active_user(uow, principal)
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    statement = select(WeeklyCheckin).where(WeeklyCheckin.user_id == user.id)
    if cursor is not None:
        anchor = await _owned_checkin(uow, user.id, cursor)
        statement = statement.where(
            (WeeklyCheckin.week_start, WeeklyCheckin.id) < (anchor.week_start, anchor.id)
        )
    rows = (
        await uow.session.scalars(
            statement.order_by(
                WeeklyCheckin.week_start.desc(), WeeklyCheckin.id.desc()
            ).limit(limit + 1)
        )
    ).all()

    has_more = len(rows) > limit
    page = list(rows[:limit])
    checkins = []
    for checkin in page:
        questions, responses = await _definition(uow, checkin)
        checkins.append(_checkin_response(checkin, questions, responses))
    return WeeklyCheckinPageResponse(
        checkins=checkins, next_cursor=page[-1].id if has_more and page else None
    )


async def open_plan_checkin(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    plan_id: uuid.UUID,
    revision: int,
    key: str,
    now: datetime | None = None,
) -> ConversationResponse:
    """Open, or return, the single care-plan thread for one plan."""

    now = now or datetime.now(UTC)
    user = await _active_user(uow, principal)

    plan = await uow.session.get(ActionPlan, plan_id)
    if plan is None or plan.user_id != user.id:
        raise not_found("Plan")
    if plan.revision != revision:
        raise conflict(
            "plan_revision_conflict",
            "This plan changed; reload it before starting a check-in.",
        )

    decision = await _begin_idempotent(
        uow,
        scope="conversation.plan_checkin",
        subject=str(user.id),
        key=key,
        payload={"plan_id": str(plan_id), "revision": revision},
        now=now,
    )
    if decision.replay_body is not None:
        return ConversationResponse.model_validate(decision.replay_body)

    # uq_conversations_subject guarantees one thread per plan; returning the
    # existing one keeps a repeated tap idempotent rather than a conflict.
    existing = await uow.session.scalar(
        select(Conversation).where(
            Conversation.user_id == user.id,
            Conversation.subject_type == CARE_PLAN_SUBJECT,
            Conversation.subject_id == plan_id,
        )
    )
    conversation = existing or Conversation(
        id=uuid.uuid4(),
        user_id=user.id,
        thread_type="care_plan",
        status="active",
        subject_type=CARE_PLAN_SUBJECT,
        subject_id=plan_id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    if existing is None:
        uow.conversations.add(conversation)

    result = _conversation_response(conversation)
    _complete_idempotent(
        decision, response_status=201, response_body=result.model_dump(mode="json")
    )
    await uow.commit()
    return result
