"""Authenticated v2 conversation and weekly check-in HTTP adapters."""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, Query, Response, status

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import (
    get_uow,
    require_costly_mutation_capacity,
    require_conversation_revision,
    require_weekly_checkin_revision,
)
from app.v2.api.routes.common import domain_router, etag_response
from app.v2.application.conversations import (
    complete_weekly_checkin,
    create_conversation,
    create_conversation_message,
    create_weekly_checkin,
    get_conversation,
    get_weekly_checkin_due,
    list_conversations,
    put_weekly_checkin_answer,
)
from app.v2.application.contracts import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationMessageAcceptedResponse,
    ConversationMessageCreateRequest,
    ConversationPageResponse,
    ConversationResponse,
    WeeklyCheckinAnswerRequest,
    WeeklyCheckinAnswerResponse,
    WeeklyCheckinDueResponse,
    WeeklyCheckinResponse,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.get(
    "/me/conversations",
    response_model=ConversationPageResponse,
    tags=["v2-conversations"],
    operation_id="listConversationsV2",
)
async def list_my_conversations(
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ConversationPageResponse:
    return await list_conversations(
        uow, principal=principal, limit=limit, cursor=cursor
    )


@router.post(
    "/me/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-conversations"],
    operation_id="createConversationV2",
    responses={201: etag_response},
)
async def create_my_conversation(
    body: ConversationCreateRequest,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ConversationResponse:
    result = await create_conversation(
        uow, principal=principal, key=idempotency_key, request=body
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return result


@router.get(
    "/me/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    tags=["v2-conversations"],
    operation_id="getConversationV2",
    responses={200: etag_response},
)
async def get_my_conversation(
    conversation_id: uuid.UUID,
    response: Response,
    message_limit: int = Query(default=50, ge=1, le=100),
    message_cursor: str | None = Query(default=None, max_length=128),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ConversationDetailResponse:
    result = await get_conversation(
        uow,
        principal=principal,
        conversation_id=conversation_id,
        message_limit=message_limit,
        message_cursor=message_cursor,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return result


@router.post(
    "/me/conversations/{conversation_id}/messages",
    response_model=ConversationMessageAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["v2-conversations"],
    operation_id="createConversationMessageV2",
    responses={202: etag_response},
)
async def create_my_conversation_message(
    conversation_id: uuid.UUID,
    body: ConversationMessageCreateRequest,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    _: None = Depends(require_costly_mutation_capacity),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_revision: int = Depends(require_conversation_revision),
) -> ConversationMessageAcceptedResponse:
    result = await create_conversation_message(
        uow,
        principal=principal,
        conversation_id=conversation_id,
        expected_revision=expected_revision,
        key=idempotency_key,
        request=body,
    )
    response.headers["ETag"] = f'"{result.conversation_revision}"'
    return result


@router.get(
    "/me/weekly-checkins/due",
    response_model=WeeklyCheckinDueResponse,
    tags=["v2-checkins"],
    operation_id="getWeeklyCheckinDueV2",
)
async def get_my_weekly_checkin_due(
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WeeklyCheckinDueResponse:
    return await get_weekly_checkin_due(uow, principal=principal)


@router.post(
    "/me/weekly-checkins",
    response_model=WeeklyCheckinResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-checkins"],
    operation_id="createWeeklyCheckinV2",
    responses={201: etag_response},
)
async def create_my_weekly_checkin(
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WeeklyCheckinResponse:
    result = await create_weekly_checkin(uow, principal=principal, key=idempotency_key)
    response.headers["ETag"] = f'"{result.revision}"'
    return result


@router.put(
    "/me/weekly-checkins/{checkin_id}/responses/{question_id}",
    response_model=WeeklyCheckinAnswerResponse,
    tags=["v2-checkins"],
    operation_id="putWeeklyCheckinResponseV2",
    responses={200: etag_response},
)
async def put_my_weekly_checkin_response(
    checkin_id: uuid.UUID,
    question_id: uuid.UUID,
    body: WeeklyCheckinAnswerRequest,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_revision: int = Depends(require_weekly_checkin_revision),
) -> WeeklyCheckinAnswerResponse:
    result = await put_weekly_checkin_answer(
        uow,
        principal=principal,
        checkin_id=checkin_id,
        question_id=question_id,
        expected_revision=expected_revision,
        key=idempotency_key,
        request=body,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return result


@router.post(
    "/me/weekly-checkins/{checkin_id}/complete",
    response_model=WeeklyCheckinResponse,
    tags=["v2-checkins"],
    operation_id="completeWeeklyCheckinV2",
    responses={200: etag_response},
)
async def complete_my_weekly_checkin(
    checkin_id: uuid.UUID,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_revision: int = Depends(require_weekly_checkin_revision),
) -> WeeklyCheckinResponse:
    result = await complete_weekly_checkin(
        uow,
        principal=principal,
        checkin_id=checkin_id,
        expected_revision=expected_revision,
        key=idempotency_key,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return result
