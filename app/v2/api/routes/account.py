"""Owner-scoped, recent-authenticated account lifecycle routes."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, status

from app.v2.api.dependencies import require_costly_mutation_capacity
from app.v2.api.routes.common import PrincipalDependency, UowDependency, domain_router
from app.v2.application.account_lifecycle import (
    RecentAuthenticationPolicy,
    SubjectFingerprint,
    request_account_deletion,
    request_account_export,
)
from app.v2.application.contracts import AccountExportResponse, DeletionResponse
from app.v2.infrastructure.account_lifecycle import (
    EnvironmentHmacSubjectFingerprint,
    FirebaseRecentAuthenticationPolicy,
)
from app.v2.runtime.config import settings
from app.v2.application.errors import service_unavailable


router = domain_router()


def get_recent_authentication_policy() -> RecentAuthenticationPolicy:
    """The Firebase adapter fails closed when a signed auth_time is absent."""

    return FirebaseRecentAuthenticationPolicy()


def get_subject_fingerprint() -> SubjectFingerprint:
    """Do not permit a deletion receipt without its keyed pseudonym secret."""

    require_account_deletion_enabled()
    return EnvironmentHmacSubjectFingerprint()


RecentAuthenticationDependency = Annotated[
    RecentAuthenticationPolicy, Depends(get_recent_authentication_policy)
]
SubjectFingerprintDependency = Annotated[
    SubjectFingerprint, Depends(get_subject_fingerprint)
]


def require_account_deletion_enabled() -> None:
    if not settings.V2_DELETION_ENABLED:
        raise service_unavailable(
            "account_deletion_unavailable",
            "Account deletion is temporarily unavailable.",
        )


@router.post(
    "/me/exports",
    response_model=AccountExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["v2-account"],
    operation_id="createMyExport",
)
async def create_account_export(
    principal: PrincipalDependency,
    uow: UowDependency,
    _: Annotated[None, Depends(require_costly_mutation_capacity)],
    recent_authentication: RecentAuthenticationDependency,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
) -> AccountExportResponse:
    return await request_account_export(
        uow,
        principal=principal,
        key=idempotency_key,
        recent_authentication=recent_authentication,
    )


@router.delete(
    "/me",
    response_model=DeletionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["v2-account"],
    operation_id="deleteMe",
)
async def delete_account(
    principal: PrincipalDependency,
    uow: UowDependency,
    recent_authentication: RecentAuthenticationDependency,
    subject_fingerprint: SubjectFingerprintDependency,
    _: Annotated[None, Depends(require_account_deletion_enabled)],
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
) -> DeletionResponse:
    return await request_account_deletion(
        uow,
        principal=principal,
        key=idempotency_key,
        recent_authentication=recent_authentication,
        subject_fingerprint=subject_fingerprint,
    )
