"""Weekly check-in history and the care-plan check-in thread."""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, Query, status

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow, require_plan_revision
from app.v2.api.routes.common import domain_router
from app.v2.application.checkins import (
    get_weekly_checkin,
    list_weekly_checkins,
    open_plan_checkin,
)
from app.v2.application.contracts import (
    ConversationResponse,
    WeeklyCheckinPageResponse,
    WeeklyCheckinResponse,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.get(
    "/me/weekly-checkins",
    response_model=WeeklyCheckinPageResponse,
    tags=["v2-conversations"],
    operation_id="listWeeklyCheckinsV2",
)
async def read_weekly_checkins(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: uuid.UUID | None = Query(default=None),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WeeklyCheckinPageResponse:
    return await list_weekly_checkins(
        uow, principal=principal, limit=limit, cursor=cursor
    )


@router.get(
    "/me/weekly-checkins/{checkin_id}",
    response_model=WeeklyCheckinResponse,
    tags=["v2-conversations"],
    operation_id="getWeeklyCheckinV2",
)
async def read_weekly_checkin(
    checkin_id: uuid.UUID,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WeeklyCheckinResponse:
    """Lets a partially answered check-in be resumed after a cold start."""

    return await get_weekly_checkin(uow, principal=principal, checkin_id=checkin_id)


@router.post(
    "/me/plans/{plan_id}/checkin",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-conversations"],
    operation_id="createPlanCheckinV2",
)
async def create_plan_checkin(
    plan_id: uuid.UUID,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_revision: int = Depends(require_plan_revision),
) -> ConversationResponse:
    """The plan revision is required so a thread cannot open against a plan
    the client has not seen."""

    return await open_plan_checkin(
        uow,
        principal=principal,
        plan_id=plan_id,
        revision=expected_revision,
        key=idempotency_key,
    )
