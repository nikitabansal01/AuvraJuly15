"""Application use cases for the first production v2 slice."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.v2.application.contracts import (
    AssessmentResponse,
    AssessmentWriteRequest,
    ClaimOnboardingRequest,
    ClaimOnboardingResponse,
    ConsentRequirement,
    CurrentPlanResponse,
    JobResponse,
    MediaAssetResponse,
    OnboardingSessionResponse,
    PlanGenerationRequest,
    PlanItemResponse,
    PlanVariantResponse,
    ProfilePatchRequest,
    ProfileResponse,
)
from app.v2.application.errors import (
    conflict,
    not_found,
    precondition_failed,
    service_unavailable,
    unprocessable_content,
)
from app.v2.domain.enums import (
    IdempotencyState,
    JobState,
    MediaStatus,
    OnboardingStatus,
    PlanStatus,
    UserStatus,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.domain.plans import IncompleteReadyPlan, require_ready_plan_complete
from app.v2.persistence.models import (
    ConsentRecord,
    GenerationJob,
    IdempotencyRecord,
    OnboardingAssessment,
    OnboardingSession,
    OutboxEvent,
    User,
    UserProfile,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


@dataclass(slots=True)
class IdempotencyDecision:
    record: IdempotencyRecord
    replay_body: dict[str, Any] | None


DEFAULT_REQUIRED_CONSENT_VERSIONS = {
    "privacy": "privacy.v1",
    "health_data_processing": "health-data-processing.v1",
}


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derive_guest_proof(secret: str, key: str, session_id: uuid.UUID) -> str:
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("V2_GUEST_PROOF_SECRET must contain at least 32 bytes")
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{key}:{session_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _proof_hash(proof: str) -> str:
    return hashlib.sha256(proof.encode("utf-8")).hexdigest()


async def _begin_idempotent(
    uow: SqlAlchemyUnitOfWork,
    *,
    scope: str,
    subject: str,
    key: str,
    payload: dict[str, Any],
    now: datetime,
) -> IdempotencyDecision:
    request_hash = _canonical_hash(payload)
    candidate = IdempotencyRecord(
        id=uuid.uuid4(),
        scope=scope,
        subject=subject,
        idempotency_key=key,
        request_hash=request_hash,
        state=IdempotencyState.STARTED.value,
        expires_at=now + timedelta(hours=24),
    )
    record, won = await uow.idempotency.reserve(candidate, now=now)
    if won:
        return IdempotencyDecision(record=record, replay_body=None)
    if not hmac.compare_digest(record.request_hash, request_hash):
        raise conflict(
            "idempotency_key_reused",
            "The Idempotency-Key was already used with a different request.",
        )
    if record.state == IdempotencyState.COMPLETED.value:
        if record.response_body is None:
            raise RuntimeError("completed idempotency record has no response")
        return IdempotencyDecision(record=record, replay_body=record.response_body)
    raise conflict(
        "request_in_progress",
        "A request with this Idempotency-Key is still in progress.",
    )


def _complete_idempotent(
    decision: IdempotencyDecision,
    *,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    decision.record.state = IdempotencyState.COMPLETED.value
    decision.record.response_status = response_status
    decision.record.response_body = response_body


def _require_valid_guest_session(
    onboarding_session: OnboardingSession | None,
    proof_token: str,
    now: datetime,
) -> OnboardingSession:
    if onboarding_session is None:
        raise not_found("Onboarding session")
    if not hmac.compare_digest(onboarding_session.proof_hash, _proof_hash(proof_token)):
        # Hide whether the session ID itself exists.
        raise not_found("Onboarding session")
    if onboarding_session.expires_at <= now:
        raise conflict("onboarding_expired", "The onboarding session has expired.")
    if onboarding_session.status == OnboardingStatus.REVOKED.value:
        raise conflict("onboarding_revoked", "The onboarding session was revoked.")
    return onboarding_session


def _profile_response(user: User, profile: UserProfile) -> ProfileResponse:
    return ProfileResponse(
        user_id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        display_name=profile.display_name,
        timezone=profile.timezone,
        locale=profile.locale,
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _record_claim_consents(
    uow: SqlAlchemyUnitOfWork,
    user_id: uuid.UUID,
    request: ClaimOnboardingRequest,
    now: datetime,
) -> None:
    for consent in request.consents:
        uow.consents.add(
            ConsentRecord(
                id=uuid.uuid4(),
                user_id=user_id,
                consent_type=consent.consent_type,
                document_version=consent.document_version,
                granted=consent.granted,
                decided_at=now,
            )
        )


def _required_consent_requirements(
    required_consent_versions: dict[str, str],
) -> list[ConsentRequirement]:
    return [
        ConsentRequirement(consent_type=consent_type, document_version=document_version)
        for consent_type, document_version in sorted(required_consent_versions.items())
    ]


def _validate_claim_consents(
    request: ClaimOnboardingRequest,
    required_consent_versions: dict[str, str],
) -> None:
    supplied = {consent.consent_type: consent for consent in request.consents}
    if set(supplied) != set(required_consent_versions):
        raise unprocessable_content(
            "invalid_consent_requirements",
            "Consent decisions must exactly match the server-required consent documents.",
        )
    for consent_type, document_version in required_consent_versions.items():
        consent = supplied[consent_type]
        if consent.document_version != document_version:
            raise unprocessable_content(
                "invalid_consent_document_version",
                "A consent decision referenced a document version not required by the server.",
            )


async def _attach_assessments_to_user(
    uow: SqlAlchemyUnitOfWork, session_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    for assessment_version in await uow.onboarding.list_assessments(session_id):
        assessment_version.user_id = user_id


async def create_onboarding_session(
    uow: SqlAlchemyUnitOfWork,
    *,
    idempotency_key: str,
    guest_proof_secret: str,
    required_consent_versions: dict[str, str] | None = None,
    now: datetime | None = None,
) -> OnboardingSessionResponse:
    now = now or datetime.now(UTC)
    if required_consent_versions is None:
        required_consent_versions = DEFAULT_REQUIRED_CONSENT_VERSIONS
    decision = await _begin_idempotent(
        uow,
        scope="onboarding.session.create",
        subject="public",
        key=idempotency_key,
        payload={},
        now=now,
    )
    if decision.replay_body is not None:
        session_id = uuid.UUID(decision.replay_body["session_id"])
        proof_token = _derive_guest_proof(
            guest_proof_secret, idempotency_key, session_id
        )
        return OnboardingSessionResponse(
            session_id=session_id,
            proof_token=proof_token,
            expires_at=datetime.fromisoformat(decision.replay_body["expires_at"]),
            required_consents=_required_consent_requirements(required_consent_versions),
        )

    session_id = uuid.uuid4()
    proof_token = _derive_guest_proof(guest_proof_secret, idempotency_key, session_id)
    expires_at = now + timedelta(hours=24)
    onboarding_session = OnboardingSession(
        id=session_id,
        proof_hash=_proof_hash(proof_token),
        status=OnboardingStatus.ACTIVE.value,
        expires_at=expires_at,
    )
    uow.onboarding.add_session(onboarding_session)
    response = OnboardingSessionResponse(
        session_id=session_id,
        proof_token=proof_token,
        expires_at=expires_at,
        required_consents=_required_consent_requirements(required_consent_versions),
    )
    stored_response = {
        "session_id": str(session_id),
        "expires_at": expires_at.isoformat(),
    }
    _complete_idempotent(decision, response_status=201, response_body=stored_response)
    await uow.commit()
    return response


async def put_assessment(
    uow: SqlAlchemyUnitOfWork,
    *,
    session_id: uuid.UUID,
    proof_token: str,
    idempotency_key: str,
    expected_version: int,
    request: AssessmentWriteRequest,
    now: datetime | None = None,
) -> AssessmentResponse:
    now = now or datetime.now(UTC)
    payload = {"expected_version": expected_version, **request.model_dump(mode="json")}
    decision = await _begin_idempotent(
        uow,
        scope="onboarding.assessment.put",
        subject=str(session_id),
        key=idempotency_key,
        payload=payload,
        now=now,
    )
    if decision.replay_body is not None:
        return AssessmentResponse.model_validate(decision.replay_body)

    onboarding_session = _require_valid_guest_session(
        await uow.onboarding.get_session(session_id, for_update=True),
        proof_token,
        now,
    )
    if onboarding_session.status != OnboardingStatus.ACTIVE.value:
        raise conflict(
            "onboarding_already_claimed",
            "A claimed onboarding assessment cannot be replaced.",
        )

    previous = await uow.onboarding.get_current_assessment(session_id, for_update=True)
    version = 0
    if previous is None and expected_version != 0:
        raise precondition_failed(
            "The onboarding assessment does not have the requested revision."
        )
    if previous is not None:
        if previous.version != expected_version:
            raise precondition_failed(
                "The onboarding assessment has changed; fetch its current ETag."
            )
        previous.is_current = False
        version = previous.version + 1

    assessment = OnboardingAssessment(
        id=uuid.uuid4(),
        session_id=session_id,
        version=version,
        schema_version=request.schema_version,
        timezone=request.timezone,
        answers=request.answers.model_dump(mode="json", exclude_none=True),
        is_current=True,
        validated_at=now,
    )
    uow.onboarding.add_assessment(assessment)
    response = AssessmentResponse(
        assessment_id=assessment.id,
        session_id=session_id,
        version=version,
        schema_version=assessment.schema_version,
        timezone=assessment.timezone,
        validated_at=now,
    )
    _complete_idempotent(
        decision, response_status=200, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


async def claim_onboarding(
    uow: SqlAlchemyUnitOfWork,
    *,
    session_id: uuid.UUID,
    proof_token: str,
    idempotency_key: str,
    principal: VerifiedPrincipal,
    request: ClaimOnboardingRequest,
    required_consent_versions: dict[str, str] | None = None,
    now: datetime | None = None,
) -> ClaimOnboardingResponse:
    now = now or datetime.now(UTC)
    if required_consent_versions is None:
        required_consent_versions = DEFAULT_REQUIRED_CONSENT_VERSIONS
    _validate_claim_consents(request, required_consent_versions)
    decision = await _begin_idempotent(
        uow,
        scope="onboarding.session.claim",
        subject=principal.subject,
        key=idempotency_key,
        payload={"session_id": str(session_id), **request.model_dump(mode="json")},
        now=now,
    )
    if decision.replay_body is not None:
        return ClaimOnboardingResponse.model_validate(decision.replay_body)

    onboarding_session = _require_valid_guest_session(
        await uow.onboarding.get_session(session_id, for_update=True),
        proof_token,
        now,
    )
    assessment = await uow.onboarding.get_current_assessment(
        session_id, for_update=True
    )
    if assessment is None:
        raise conflict(
            "assessment_required",
            "A validated current assessment is required before claim.",
        )

    user = await uow.users.get_by_subject(
        principal.auth_provider, principal.subject, for_update=True
    )
    if onboarding_session.status == OnboardingStatus.CLAIMED.value:
        if user is None or onboarding_session.claimed_user_id != user.id:
            raise conflict(
                "onboarding_claimed_by_other_user",
                "The onboarding session has already been claimed.",
            )
        profile = await uow.profiles.get(user.id)
        if profile is None:
            raise RuntimeError("claimed user is missing its profile")
    else:
        if user is None:
            user = User(
                id=uuid.uuid4(),
                auth_provider=principal.auth_provider,
                auth_subject=principal.subject,
                email=principal.email,
                email_verified=principal.email_verified,
                status=UserStatus.ACTIVE.value,
            )
            uow.users.add(user)
            await uow.flush()
        elif user.status != UserStatus.ACTIVE.value:
            raise conflict("account_unavailable", "The account is not active.")

        profile = await uow.profiles.get(user.id, for_update=True)
        if profile is None:
            profile = UserProfile(
                user_id=user.id,
                display_name=principal.display_name,
                timezone=assessment.timezone,
                locale="en",
                version=1,
            )
            uow.profiles.add(profile)

        _record_claim_consents(uow, user.id, request, now)
        await _attach_assessments_to_user(uow, session_id, user.id)
        onboarding_session.status = OnboardingStatus.CLAIMED.value
        onboarding_session.claimed_user_id = user.id
        onboarding_session.claimed_at = now

    await uow.flush()
    response = ClaimOnboardingResponse(
        user_id=user.id,
        assessment_id=assessment.id,
        profile=_profile_response(user, profile),
    )
    _complete_idempotent(
        decision, response_status=200, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


async def get_profile(
    uow: SqlAlchemyUnitOfWork, *, principal: VerifiedPrincipal
) -> ProfileResponse:
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise not_found("Profile")
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Profile")
    return _profile_response(user, profile)


async def patch_profile(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    idempotency_key: str,
    request: ProfilePatchRequest,
    expected_version: int,
    now: datetime | None = None,
) -> ProfileResponse:
    now = now or datetime.now(UTC)
    user = await uow.users.get_by_subject(
        principal.auth_provider, principal.subject, for_update=True
    )
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise not_found("Profile")
    decision = await _begin_idempotent(
        uow,
        scope="profile.patch",
        subject=str(user.id),
        key=idempotency_key,
        payload={
            "expected_version": expected_version,
            **request.model_dump(mode="json", exclude_unset=True),
        },
        now=now,
    )
    if decision.replay_body is not None:
        return ProfileResponse.model_validate(decision.replay_body)

    profile = await uow.profiles.get(user.id, for_update=True)
    if profile is None:
        raise not_found("Profile")
    if profile.version != expected_version:
        raise precondition_failed("The profile has changed; fetch its current ETag.")
    updates = request.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(profile, field, value)
    profile.version += 1
    profile.updated_at = now
    response = _profile_response(user, profile)
    _complete_idempotent(
        decision, response_status=200, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


async def request_plan_generation(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    idempotency_key: str,
    request: PlanGenerationRequest,
    now: datetime | None = None,
) -> JobResponse:
    now = now or datetime.now(UTC)
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise not_found("Profile")
    profile = await uow.profiles.get(user.id)
    assessment = await uow.onboarding.get_current_assessment_for_user(user.id)
    if profile is None or assessment is None:
        raise conflict(
            "onboarding_required",
            "A claimed current assessment is required before plan generation.",
        )

    local_date = request.local_date or now.astimezone(ZoneInfo(profile.timezone)).date()
    request_payload = {
        "local_date": local_date.isoformat(),
        "timezone": profile.timezone,
        "assessment_id": str(assessment.id),
        "assessment_version": assessment.version,
    }
    decision = await _begin_idempotent(
        uow,
        scope="plan_generation.create",
        subject=str(user.id),
        key=idempotency_key,
        payload=request_payload,
        now=now,
    )
    if decision.replay_body is not None:
        return JobResponse.model_validate(decision.replay_body)

    job = GenerationJob(
        id=uuid.uuid4(),
        user_id=user.id,
        job_type="plan_generation",
        state=JobState.QUEUED.value,
        progress=0,
        phase="queued",
        request_payload=request_payload,
        max_attempts=3,
        available_at=now,
    )
    event = OutboxEvent(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        aggregate_type="generation_job",
        aggregate_id=job.id,
        event_type="plan.generation.requested",
        payload={"job_id": str(job.id), "user_id": str(user.id)},
        available_at=now,
    )
    uow.jobs.add(job)
    uow.outbox.add(event)
    await uow.flush()
    response = _job_response(job)
    _complete_idempotent(
        decision, response_status=202, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


async def get_job(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    job_id: uuid.UUID,
) -> JobResponse:
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None:
        raise not_found("Job")
    job = await uow.jobs.get_owned(job_id, user.id)
    if job is None:
        # Deliberately conceal whether another user owns this ID.
        raise not_found("Job")
    return _job_response(job)


async def get_latest_plan_generation(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    requested_date: date | None,
    now: datetime | None = None,
) -> JobResponse:
    """Discover the latest durable plan-generation job for the authenticated owner.

    Terminal jobs remain discoverable so a cold-start client can surface the
    actual failure/retry state or reload a just-published plan, never creating
    a duplicate merely because its volatile process state was lost.
    """
    now = now or datetime.now(UTC)
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None:
        raise not_found("Plan generation")
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Plan generation")
    local_date = requested_date or now.astimezone(ZoneInfo(profile.timezone)).date()
    job = await uow.jobs.get_latest_plan_generation(user.id, local_date)
    if job is None:
        raise not_found("Plan generation")
    return _job_response(job)


def _job_response(job: GenerationJob) -> JobResponse:
    return JobResponse(
        job_id=job.id,
        job_type=job.job_type,
        state=JobState(job.state),
        progress=job.progress,
        phase=job.phase,
        local_date=_job_local_date(job.request_payload),
        created_at=job.created_at,
        updated_at=job.updated_at,
        error_code=job.error_code,
    )


def _job_local_date(payload: Mapping[str, Any]) -> date | None:
    value = payload.get("local_date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def get_current_plan(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    requested_date: date | None,
    now: datetime | None = None,
) -> CurrentPlanResponse:
    now = now or datetime.now(UTC)
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None:
        raise not_found("Current plan")
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Current plan")
    local_date = requested_date or now.astimezone(ZoneInfo(profile.timezone)).date()
    plan = await uow.plans.get_current_ready(user.id, local_date)
    if plan is None:
        raise not_found("Current plan")

    try:
        require_ready_plan_complete(plan)
    except IncompleteReadyPlan:
        raise service_unavailable(
            "plan_incomplete",
            "The current plan is not ready for presentation.",
        ) from None

    return CurrentPlanResponse(
        plan_id=plan.id,
        revision=plan.revision,
        status=PlanStatus(plan.status),
        local_date=plan.local_date,
        timezone=plan.timezone,
        cycle_snapshot=plan.cycle_snapshot,
        items=[
            PlanItemResponse(
                item_id=item.id,
                slot=item.slot,
                category=item.category,
                title=item.title,
                purpose=item.purpose,
                instructions=item.instructions,
                hero_image=_asset_response(item.hero_asset),
                variants=[
                    PlanVariantResponse(
                        variant_id=variant.id,
                        variant_type=variant.variant_type,
                        content=variant.content,
                        image=_asset_response(variant.asset),
                    )
                    for variant in sorted(
                        item.variants, key=lambda current: current.variant_type
                    )
                ],
            )
            for item in sorted(plan.items, key=lambda current: current.slot)
        ],
        published_at=plan.published_at,
    )


def _asset_response(asset) -> MediaAssetResponse:
    return MediaAssetResponse(
        asset_id=asset.id,
        public_url=asset.public_url,
        alt_text=asset.alt_text,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        status=MediaStatus(asset.status),
    )
