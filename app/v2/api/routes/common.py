"""Shared HTTP adapter wiring for the small v2 route modules."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow
from app.v2.api.problem_details import ProblemDetailsRoute
from app.v2.application.contracts import ProblemDetail
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

problem_responses = {
    code: {"model": ProblemDetail, "content": {"application/problem+json": {}}}
    for code in (400, 401, 403, 404, 409, 412, 422, 428, 500, 503)
}

etag_response = {
    "headers": {
        "ETag": {
            "description": "Quoted strong revision ETag for this resource.",
            "schema": {"type": "string", "pattern": '^"[0-9]+"$'},
        }
    }
}

UowDependency = Annotated[SqlAlchemyUnitOfWork, Depends(get_uow)]
PrincipalDependency = Annotated[VerifiedPrincipal, Depends(get_verified_principal)]


def domain_router() -> APIRouter:
    """Build one problem-detail-aware router for each HTTP domain."""

    return APIRouter(route_class=ProblemDetailsRoute, responses=problem_responses)
