"""Conversation and weekly check-in commands with one durable source of truth."""

from __future__ import annotations

import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime

from app.v2.application.contracts import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationMessageAcceptedResponse,
    ConversationMessageCreateRequest,
    ConversationMessageResponse,
    ConversationPageResponse,
    ConversationResponse,
    WeeklyCheckinAnswerRequest,
    WeeklyCheckinAnswerResponse,
    WeeklyCheckinSavedAnswerResponse,
    WeeklyCheckinDueResponse,
    WeeklyCheckinQuestionResponse,
    WeeklyCheckinResponse,
)
from app.v2.application.errors import conflict, not_found, precondition_failed
from app.v2.application.services import _begin_idempotent, _complete_idempotent
from app.v2.domain.conversations import (
    WEEKLY_CHECKIN_DEFINITION_VERSION,
    iso_week_start,
    validate_weekly_answer,
)
from app.v2.domain.enums import JobState, UserStatus
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.models import GenerationJob, OutboxEvent, User
from app.v2.persistence.models_engagement import (
    Conversation,
    ConversationMessage,
    WeeklyCheckin,
    WeeklyQuestion,
    WeeklyResponse,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


async def _active_user(uow: SqlAlchemyUnitOfWork, principal: VerifiedPrincipal) -> User:
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise not_found("Resource")
    return user


def _conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=conversation.id,
        thread_type=conversation.thread_type,
        status=conversation.status,
        revision=conversation.revision,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_response(message: ConversationMessage) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        message_id=message.id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )


def _question_response(question: WeeklyQuestion) -> WeeklyCheckinQuestionResponse:
    return WeeklyCheckinQuestionResponse(
        question_id=question.id,
        ordinal=question.ordinal,
        prompt=question.prompt,
        answer_type=question.answer_type,
        answer_schema=question.answer_schema,
        required=question.required,
    )


def _saved_answer_response(
    response: WeeklyResponse,
) -> WeeklyCheckinSavedAnswerResponse:
    return WeeklyCheckinSavedAnswerResponse(
        question_id=response.question_id,
        answer=response.answer,
        answered_at=response.answered_at,
    )


def _checkin_response(
    checkin: WeeklyCheckin,
    questions: list[WeeklyQuestion],
    responses: list[WeeklyResponse],
) -> WeeklyCheckinResponse:
    return WeeklyCheckinResponse(
        checkin_id=checkin.id,
        conversation_id=checkin.conversation_id,
        week_start=checkin.week_start,
        definition_version=checkin.definition_version,
        timezone=checkin.timezone,
        revision=checkin.revision,
        completed_at=checkin.completed_at,
        questions=[_question_response(question) for question in questions],
        answers=[_saved_answer_response(response) for response in responses],
    )


def _encode_conversation_cursor(conversation: Conversation) -> str:
    value = f"{conversation.updated_at.isoformat()}|{conversation.id}".encode()
    return urlsafe_b64encode(value).decode().rstrip("=")


def _decode_conversation_cursor(
    cursor: str | None,
) -> tuple[datetime | None, uuid.UUID | None]:
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        timestamp, identifier = urlsafe_b64decode(padded).decode().split("|", 1)
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp is naive")
        return parsed, uuid.UUID(identifier)
    except (UnicodeDecodeError, ValueError) as exc:
        raise conflict("invalid_cursor", "Conversation cursor is invalid.") from exc


async def create_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    key: str,
    request: ConversationCreateRequest,
    now: datetime | None = None,
) -> ConversationResponse:
    now = now or datetime.now(UTC)
    user = await _active_user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="conversation.create",
        subject=str(user.id),
        key=key,
        payload=request.model_dump(mode="json"),
        now=now,
    )
    if decision.replay_body is not None:
        return ConversationResponse.model_validate(decision.replay_body)
    conversation = Conversation(
        id=uuid.uuid4(),
        user_id=user.id,
        thread_type=request.thread_type,
        status="active",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    uow.conversations.add(conversation)
    result = _conversation_response(conversation)
    _complete_idempotent(
        decision, response_status=201, response_body=result.model_dump(mode="json")
    )
    await uow.commit()
    return result


async def list_conversations(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    limit: int,
    cursor: str | None,
) -> ConversationPageResponse:
    user = await _active_user(uow, principal)
    before_updated_at, before_id = _decode_conversation_cursor(cursor)
    rows = await uow.conversations.list_owned(
        user.id,
        limit=limit + 1,
        before_updated_at=before_updated_at,
        before_id=before_id,
    )
    page = rows[:limit]
    next_cursor = _encode_conversation_cursor(page[-1]) if len(rows) > limit else None
    return ConversationPageResponse(
        conversations=[_conversation_response(row) for row in page],
        next_cursor=next_cursor,
    )


async def get_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    conversation_id: uuid.UUID,
    message_limit: int,
    message_cursor: str | None,
) -> ConversationDetailResponse:
    user = await _active_user(uow, principal)
    conversation = await uow.conversations.get_owned(conversation_id, user.id)
    if conversation is None:
        raise not_found("Conversation")
    before_sequence = _decode_message_cursor(message_cursor)
    messages = await uow.conversations.list_messages(
        conversation.id, before_sequence=before_sequence, limit=message_limit + 1
    )
    page = messages[-message_limit:]
    next_cursor = (
        _encode_message_cursor(page[0].sequence)
        if len(messages) > message_limit
        else None
    )
    return ConversationDetailResponse(
        **_conversation_response(conversation).model_dump(),
        messages=[_message_response(message) for message in page],
        next_message_cursor=next_cursor,
    )


def _encode_message_cursor(sequence: int) -> str:
    return urlsafe_b64encode(str(sequence).encode()).decode().rstrip("=")


def _decode_message_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        sequence = int(urlsafe_b64decode(padded).decode())
        if sequence <= 0:
            raise ValueError("nonpositive sequence")
        return sequence
    except (UnicodeDecodeError, ValueError) as exc:
        raise conflict("invalid_cursor", "Message cursor is invalid.") from exc


async def create_conversation_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    conversation_id: uuid.UUID,
    expected_revision: int,
    key: str,
    request: ConversationMessageCreateRequest,
    now: datetime | None = None,
) -> ConversationMessageAcceptedResponse:
    now = now or datetime.now(UTC)
    user = await _active_user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="conversation.message.create",
        subject=str(user.id),
        key=key,
        payload={
            "conversation_id": str(conversation_id),
            **request.model_dump(mode="json"),
        },
        now=now,
    )
    if decision.replay_body is not None:
        return ConversationMessageAcceptedResponse.model_validate(decision.replay_body)
    conversation = await uow.conversations.get_owned(
        conversation_id, user.id, for_update=True
    )
    if conversation is None:
        raise not_found("Conversation")
    if conversation.status != "active":
        raise conflict(
            "conversation_closed", "Messages cannot be added to a closed conversation."
        )
    if conversation.revision != expected_revision:
        raise precondition_failed(
            "The conversation has changed; fetch its current ETag."
        )
    existing = await uow.conversations.get_by_client_message(
        conversation.id, request.client_message_id
    )
    if existing is not None:
        raise conflict(
            "client_message_id_reused",
            "The client message identifier already belongs to this conversation.",
        )
    sequence = await uow.conversations.next_sequence(conversation.id)
    message = ConversationMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sequence=sequence,
        client_message_id=request.client_message_id,
        role="user",
        content=request.content,
        metadata_json={},
        created_at=now,
    )
    job = GenerationJob(
        id=uuid.uuid4(),
        user_id=user.id,
        job_type="conversation_response.v1",
        state=JobState.QUEUED.value,
        progress=0,
        phase="queued",
        request_payload={
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
        },
        available_at=now,
    )
    event = OutboxEvent(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        aggregate_type="conversation",
        aggregate_id=conversation.id,
        event_type="conversation.response.requested.v1",
        payload={"job_id": str(job.id), "message_id": str(message.id)},
        available_at=now,
    )
    conversation.revision += 1
    conversation.updated_at = now
    uow.conversations.add_message(message)
    uow.jobs.add(job)
    uow.outbox.add(event)
    result = ConversationMessageAcceptedResponse(
        message=_message_response(message),
        conversation_id=conversation.id,
        conversation_revision=conversation.revision,
        response_job_id=job.id,
        job_state=JobState.QUEUED.value,
    )
    _complete_idempotent(
        decision, response_status=202, response_body=result.model_dump(mode="json")
    )
    await uow.commit()
    return result


async def get_weekly_checkin_due(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    now: datetime | None = None,
) -> WeeklyCheckinDueResponse:
    now = now or datetime.now(UTC)
    user = await _active_user(uow, principal)
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Profile")
    week_start = iso_week_start(now, profile.timezone)
    checkin = await uow.weekly_checkins.get_for_week(user.id, week_start)
    if checkin is None:
        return WeeklyCheckinDueResponse(
            due=True, week_start=week_start, timezone=profile.timezone
        )
    questions = await uow.weekly_checkins.list_questions(checkin.definition_version)
    responses = await uow.weekly_checkins.list_responses(checkin.id)
    return WeeklyCheckinDueResponse(
        due=checkin.completed_at is None,
        week_start=week_start,
        timezone=profile.timezone,
        checkin=_checkin_response(checkin, questions, responses),
    )


async def create_weekly_checkin(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    key: str,
    now: datetime | None = None,
) -> WeeklyCheckinResponse:
    now = now or datetime.now(UTC)
    user = await _active_user(uow, principal)
    profile = await uow.profiles.get(user.id, for_update=True)
    if profile is None:
        raise not_found("Profile")
    week_start = iso_week_start(now, profile.timezone)
    decision = await _begin_idempotent(
        uow,
        scope="weekly_checkin.create",
        subject=str(user.id),
        key=key,
        payload={"week_start": week_start.isoformat()},
        now=now,
    )
    if decision.replay_body is not None:
        return WeeklyCheckinResponse.model_validate(decision.replay_body)
    existing = await uow.weekly_checkins.get_for_week(
        user.id, week_start, for_update=True
    )
    if existing is not None:
        raise conflict(
            "weekly_checkin_already_exists", "This weekly check-in already exists."
        )
    questions = await uow.weekly_checkins.list_questions(
        WEEKLY_CHECKIN_DEFINITION_VERSION
    )
    if not questions or not any(question.required for question in questions):
        raise conflict(
            "weekly_checkin_definition_unavailable",
            "Weekly check-in definition is unavailable.",
        )
    conversation = Conversation(
        id=uuid.uuid4(),
        user_id=user.id,
        thread_type="weekly_checkin",
        status="active",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    checkin = WeeklyCheckin(
        id=uuid.uuid4(),
        user_id=user.id,
        week_start=week_start,
        definition_version=WEEKLY_CHECKIN_DEFINITION_VERSION,
        timezone=profile.timezone,
        conversation_id=conversation.id,
        revision=1,
    )
    uow.conversations.add(conversation)
    uow.weekly_checkins.add(checkin)
    result = _checkin_response(checkin, questions, [])
    _complete_idempotent(
        decision, response_status=201, response_body=result.model_dump(mode="json")
    )
    await uow.commit()
    return result


async def put_weekly_checkin_answer(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    checkin_id: uuid.UUID,
    question_id: uuid.UUID,
    expected_revision: int,
    key: str,
    request: WeeklyCheckinAnswerRequest,
    now: datetime | None = None,
) -> WeeklyCheckinAnswerResponse:
    now = now or datetime.now(UTC)
    user = await _active_user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="weekly_checkin.response.put",
        subject=str(user.id),
        key=key,
        payload={
            "checkin_id": str(checkin_id),
            "question_id": str(question_id),
            "revision": expected_revision,
            **request.model_dump(mode="json"),
        },
        now=now,
    )
    if decision.replay_body is not None:
        return WeeklyCheckinAnswerResponse.model_validate(decision.replay_body)
    checkin = await uow.weekly_checkins.get_owned(checkin_id, user.id, for_update=True)
    if checkin is None:
        raise not_found("Weekly check-in")
    if checkin.completed_at is not None:
        raise conflict(
            "weekly_checkin_completed", "This weekly check-in is already complete."
        )
    if checkin.revision != expected_revision:
        raise precondition_failed(
            "The weekly check-in has changed; fetch its current ETag."
        )
    question = await uow.weekly_checkins.get_question(question_id)
    if question is None:
        raise not_found("Weekly check-in question")
    if question.version != checkin.definition_version:
        raise conflict(
            "weekly_checkin_definition_mismatch",
            "Question does not belong to this weekly check-in definition.",
        )
    validate_weekly_answer(
        request.answer,
        answer_type=question.answer_type,
        answer_schema=question.answer_schema,
    )
    if await uow.weekly_checkins.get_response(checkin.id, question.id) is not None:
        raise conflict(
            "weekly_checkin_response_already_exists",
            "Each weekly check-in question has exactly one immutable response.",
        )
    response = WeeklyResponse(
        id=uuid.uuid4(),
        weekly_checkin_id=checkin.id,
        question_id=question.id,
        answer=request.answer,
        answered_at=now,
    )
    uow.weekly_checkins.add_response(response)
    await uow.flush()
    checkin.revision += 1
    result = WeeklyCheckinAnswerResponse(
        checkin_id=checkin.id,
        question_id=question.id,
        revision=checkin.revision,
        completed_at=None,
        answered_at=now,
    )
    _complete_idempotent(
        decision, response_status=200, response_body=result.model_dump(mode="json")
    )
    await uow.commit()
    return result


async def complete_weekly_checkin(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    checkin_id: uuid.UUID,
    expected_revision: int,
    key: str,
    now: datetime | None = None,
) -> WeeklyCheckinResponse:
    now = now or datetime.now(UTC)
    user = await _active_user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="weekly_checkin.complete",
        subject=str(user.id),
        key=key,
        payload={"checkin_id": str(checkin_id), "revision": expected_revision},
        now=now,
    )
    if decision.replay_body is not None:
        return WeeklyCheckinResponse.model_validate(decision.replay_body)
    checkin = await uow.weekly_checkins.get_owned(checkin_id, user.id, for_update=True)
    if checkin is None:
        raise not_found("Weekly check-in")
    if checkin.completed_at is not None:
        raise conflict(
            "weekly_checkin_completed", "This weekly check-in is already complete."
        )
    if checkin.revision != expected_revision:
        raise precondition_failed(
            "The weekly check-in has changed; fetch its current ETag."
        )
    required, answered = await uow.weekly_checkins.count_required_answered(
        checkin.id, checkin.definition_version
    )
    if required == 0 or required != answered:
        raise conflict(
            "weekly_checkin_incomplete",
            "Every required weekly check-in question must be answered before completion.",
        )
    checkin.completed_at = now
    checkin.revision += 1
    questions = await uow.weekly_checkins.list_questions(checkin.definition_version)
    responses = await uow.weekly_checkins.list_responses(checkin.id)
    result = _checkin_response(checkin, questions, responses)
    _complete_idempotent(
        decision, response_status=200, response_body=result.model_dump(mode="json")
    )
    await uow.commit()
    return result
