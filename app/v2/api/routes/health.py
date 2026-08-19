"""Unauthenticated v2 health endpoints."""

from fastapi import Depends
from sqlalchemy import text

from app.v2.api.dependencies import get_uow
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import HealthResponse
from app.v2.application.errors import service_unavailable
from app.v2.persistence.uow import SqlAlchemyUnitOfWork
from app.v2.runtime.schema import check_database_schema_head

router = domain_router()


@router.get(
    "/health/live",
    response_model=HealthResponse,
    tags=["v2-operations"],
    operation_id="getLivenessV2",
)
async def liveness() -> HealthResponse:
    """Prove only that the API process can serve requests."""

    return HealthResponse(status="healthy", service="auvra-api", version="v2")


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    tags=["v2-operations"],
    operation_id="getReadinessV2",
)
async def readiness(uow: SqlAlchemyUnitOfWork = Depends(get_uow)) -> HealthResponse:
    """Accept traffic only when the canonical database is reachable."""

    if uow.session is None:
        raise service_unavailable("database_unavailable", "Database is unavailable.")
    try:
        await uow.session.execute(text("SELECT 1"))
        await check_database_schema_head()
    except Exception as exc:
        raise service_unavailable("database_unavailable", "Database is unavailable.") from exc
    return HealthResponse(status="ready", service="auvra-api", version="v2")
