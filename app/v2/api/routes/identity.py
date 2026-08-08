"""Authenticated identity and profile routes."""

from fastapi import Depends, Header, Response

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow, require_profile_revision
from app.v2.api.routes.common import domain_router, etag_response
from app.v2.application.contracts import ProfilePatchRequest, ProfileResponse
from app.v2.application.services import get_profile, patch_profile
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.get(
    "/me/profile",
    response_model=ProfileResponse,
    tags=["v2-identity"],
    operation_id="getMyProfileV2",
    responses={200: etag_response},
)
async def read_profile(
    response: Response,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ProfileResponse:
    result = await get_profile(uow, principal=principal)
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.patch(
    "/me/profile",
    response_model=ProfileResponse,
    tags=["v2-identity"],
    operation_id="updateMyProfileV2",
    responses={200: etag_response},
)
async def update_profile(
    body: ProfilePatchRequest,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_version: int = Depends(require_profile_revision),
) -> ProfileResponse:
    result = await patch_profile(
        uow,
        principal=principal,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        request=body,
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result
