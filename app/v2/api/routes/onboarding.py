"""Guest onboarding and authenticated claim routes."""

import uuid
from fastapi import Depends, Header, Response, status

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow, require_assessment_revision
from app.v2.api.routes.common import domain_router, etag_response
from app.v2.application.contracts import (
    AssessmentResponse,
    AssessmentWriteRequest,
    ClaimOnboardingRequest,
    ClaimOnboardingResponse,
    OnboardingSessionResponse,
)
from app.v2.application.services import (
    claim_onboarding,
    create_onboarding_session,
    put_assessment,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork
from app.v2.runtime.config import settings

router = domain_router()


@router.post(
    "/onboarding/sessions",
    response_model=OnboardingSessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-onboarding"],
    operation_id="createOnboardingSessionV2",
)
async def create_session(
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> OnboardingSessionResponse:
    return await create_onboarding_session(
        uow,
        idempotency_key=idempotency_key,
        guest_proof_secret=settings.V2_GUEST_PROOF_SECRET,
        required_consent_versions=settings.V2_REQUIRED_CONSENT_VERSIONS,
    )


@router.put(
    "/onboarding/sessions/{session_id}/assessment",
    response_model=AssessmentResponse,
    tags=["v2-onboarding"],
    operation_id="putOnboardingAssessmentV2",
    responses={200: etag_response},
)
async def write_assessment(
    session_id: uuid.UUID,
    body: AssessmentWriteRequest,
    response: Response,
    proof_token: str = Header(alias="X-Onboarding-Proof", min_length=32, max_length=256),
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    expected_version: int = Depends(require_assessment_revision),
) -> AssessmentResponse:
    result = await put_assessment(
        uow,
        session_id=session_id,
        proof_token=proof_token,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        request=body,
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post(
    "/onboarding/sessions/{session_id}/claim",
    response_model=ClaimOnboardingResponse,
    tags=["v2-onboarding"],
    operation_id="claimOnboardingSessionV2",
)
async def claim_session(
    session_id: uuid.UUID,
    body: ClaimOnboardingRequest,
    proof_token: str = Header(alias="X-Onboarding-Proof", min_length=32, max_length=256),
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ClaimOnboardingResponse:
    return await claim_onboarding(
        uow,
        session_id=session_id,
        proof_token=proof_token,
        idempotency_key=idempotency_key,
        principal=principal,
        request=body,
        required_consent_versions=settings.V2_REQUIRED_CONSENT_VERSIONS,
    )
