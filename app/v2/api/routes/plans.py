"""Plan command and read routes; generation remains deliberately asynchronous."""

from datetime import date
import uuid

from fastapi import Depends, Header, status

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow, require_costly_mutation_capacity
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import (
    CurrentPlanResponse,
    JobResponse,
    PlanGenerationRequest,
    PlanReplacementRequest,
    PlanReplacementResponse,
)
from app.v2.application.plan_replacement import replace_plan_with_selected_variant
from app.v2.api.dependencies import require_plan_revision
from app.v2.application.services import (
    get_current_plan,
    get_latest_plan_generation,
    request_plan_generation,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.post(
    "/plan-generations",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["v2-plans"],
    operation_id="requestPlanGenerationV2",
)
async def create_plan_generation(
    body: PlanGenerationRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    _: None = Depends(require_costly_mutation_capacity),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> JobResponse:
    return await request_plan_generation(
        uow,
        principal=principal,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.get(
    "/me/plan-generations/latest",
    response_model=JobResponse,
    tags=["v2-plans"],
    operation_id="getMyLatestPlanGenerationV2",
)
async def read_latest_plan_generation(
    local_date: date | None = None,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> JobResponse:
    return await get_latest_plan_generation(
        uow,
        principal=principal,
        requested_date=local_date,
    )


@router.get(
    "/me/plans/today",
    response_model=CurrentPlanResponse,
    tags=["v2-plans"],
    operation_id="getMyCurrentPlanV2",
)
async def read_current_plan(
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    local_date: date | None = None,
) -> CurrentPlanResponse:
    return await get_current_plan(
        uow,
        principal=principal,
        requested_date=local_date,
    )


@router.post(
    "/me/plans/{plan_id}/replacements",
    response_model=PlanReplacementResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-plans"],
    operation_id="replacePlanWithSelectedVariantV2",
)
async def create_plan_replacement(
    plan_id: uuid.UUID,
    body: PlanReplacementRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_revision: int = Depends(require_plan_revision),
) -> PlanReplacementResponse:
    return await replace_plan_with_selected_variant(
        uow,
        principal=principal,
        plan_id=plan_id,
        revision=expected_revision,
        key=idempotency_key,
        request=body,
    )
