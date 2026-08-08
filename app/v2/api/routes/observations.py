"""Canonical observation routes: preferences, body metrics, symptoms, cycle events."""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, Query, status

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import (
    CurrentObservationsResponse,
    ObservationCatalogResponse,
    ObservationPageResponse,
    ObservationResponse,
    ObservationWriteRequest,
)
from app.v2.application.observations import (
    catalog,
    current_observations,
    list_observations,
    record_observation,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()

_OBSERVATION_TYPE = Query(
    default=None, min_length=1, max_length=24, pattern=r"^[a-z_]+$"
)


@router.get(
    "/observation-catalog",
    response_model=ObservationCatalogResponse,
    tags=["v2-observations"],
    operation_id="getObservationCatalogV2",
)
async def read_catalog() -> ObservationCatalogResponse:
    """Public by design: the vocabulary contains no user data."""

    return catalog()


@router.post(
    "/me/observations",
    response_model=ObservationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-observations"],
    operation_id="createObservationV2",
)
async def create_observation(
    body: ObservationWriteRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ObservationResponse:
    return await record_observation(
        uow, principal=principal, request=body, key=idempotency_key
    )


@router.get(
    "/me/observations",
    response_model=ObservationPageResponse,
    tags=["v2-observations"],
    operation_id="listObservationsV2",
)
async def read_observations(
    observation_type: str | None = _OBSERVATION_TYPE,
    code: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: uuid.UUID | None = Query(default=None),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ObservationPageResponse:
    return await list_observations(
        uow,
        principal=principal,
        observation_type=observation_type,
        code=code,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/me/observations/current",
    response_model=CurrentObservationsResponse,
    tags=["v2-observations"],
    operation_id="getCurrentObservationsV2",
)
async def read_current(
    observation_type: str | None = _OBSERVATION_TYPE,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> CurrentObservationsResponse:
    return await current_observations(
        uow, principal=principal, observation_type=observation_type
    )
