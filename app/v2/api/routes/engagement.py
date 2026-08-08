"""Authenticated engagement routes."""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, status

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow, require_plan_revision
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import (
    ActionEventRequest,
    ActionEventResponse,
    DailyReviewRequest,
    DailyReviewResponse,
    ProgressSummaryResponse,
    SymptomObservationRequest,
    SymptomObservationResponse,
)
from app.v2.application.engagement import (
    daily_review,
    progress_summary,
    record_action_event,
    record_symptom,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.post(
    "/me/symptom-observations",
    response_model=SymptomObservationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-engagement"],
    operation_id="createSymptomObservation",
)
async def create_symptom_observation(
    body: SymptomObservationRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> SymptomObservationResponse:
    return await record_symptom(
        uow,
        principal=principal,
        key=idempotency_key,
        request=body,
    )


@router.post(
    "/me/plans/{plan_id}/items/{item_id}/events",
    response_model=ActionEventResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-engagement"],
    operation_id="recordActionItemEventV2",
)
async def create_action_event(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ActionEventRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_revision: int = Depends(require_plan_revision),
) -> ActionEventResponse:
    return await record_action_event(
        uow,
        principal=principal,
        plan_id=plan_id,
        item_id=item_id,
        revision=expected_revision,
        key=idempotency_key,
        request=body,
    )


@router.put(
    "/me/plans/{plan_id}/daily-review",
    response_model=DailyReviewResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-engagement"],
    operation_id="submitDailyReviewV2",
)
async def submit_daily_review(
    plan_id: uuid.UUID,
    body: DailyReviewRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_revision: int = Depends(require_plan_revision),
) -> DailyReviewResponse:
    return await daily_review(
        uow,
        principal=principal,
        plan_id=plan_id,
        revision=expected_revision,
        key=idempotency_key,
        request=body,
    )


@router.get(
    "/me/progress/summary",
    response_model=ProgressSummaryResponse,
    tags=["v2-engagement"],
    operation_id="getMyProgressSummaryV2",
)
async def read_progress_summary(
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ProgressSummaryResponse:
    return await progress_summary(uow, principal=principal)
