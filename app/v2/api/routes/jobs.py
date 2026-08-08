"""Authenticated job polling routes."""

import uuid

from fastapi import Depends

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import JobResponse
from app.v2.application.services import get_job
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    tags=["v2-jobs"],
    operation_id="getMyJobV2",
)
async def read_job(
    job_id: uuid.UUID,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> JobResponse:
    return await get_job(uow, principal=principal, job_id=job_id)
