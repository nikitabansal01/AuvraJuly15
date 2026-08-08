"""Derived cycle state. There is deliberately no write route here."""

from __future__ import annotations

from datetime import date

from fastapi import Depends, Query

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import CycleStateResponse
from app.v2.application.cycle import cycle_state
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.get(
    "/me/cycle",
    response_model=CycleStateResponse,
    tags=["v2-cycle"],
    operation_id="getMyCycleV2",
)
async def read_cycle(
    as_of: date | None = Query(default=None),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> CycleStateResponse:
    """Recording a period is POST /me/observations with code=period_start."""

    return await cycle_state(uow, principal=principal, as_of=as_of)
