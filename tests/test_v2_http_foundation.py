"""Contract-level tests for v2 HTTP and application foundation rules."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.v2.api.dependencies import (
    require_assessment_revision,
    require_profile_revision,
)
from app.v2.api.routes import router
from app.v2.application.contracts import AssessmentWriteRequest, ClaimOnboardingRequest
from app.v2.application.errors import ApplicationProblem
from app.v2.application.services import (
    _begin_idempotent,
    _complete_idempotent,
    _validate_claim_consents,
    get_job,
    get_latest_plan_generation,
    _job_response,
    patch_profile,
    put_assessment,
)
from app.v2.domain.enums import IdempotencyState, OnboardingStatus, UserStatus
from app.v2.domain.identity import VerifiedPrincipal


NOW = datetime(2026, 8, 8, tzinfo=UTC)
PRINCIPAL = VerifiedPrincipal(
    auth_provider="firebase",
    subject="owner",
    email=None,
    email_verified=False,
    display_name=None,
)


class FakeIdempotencyRepository:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.records = {}

    async def reserve(self, record, *, now):
        key = (record.scope, record.subject, record.idempotency_key)
        async with self.lock:
            stored = self.records.get(key)
            if stored is None:
                self.records[key] = record
                return record, True
            if stored.expires_at <= now:
                stored.request_hash = record.request_hash
                stored.state = IdempotencyState.STARTED.value
                stored.response_status = None
                stored.response_body = None
                stored.expires_at = record.expires_at
                return stored, True
            return stored, False


class FakeOnboardingRepository:
    def __init__(self, session, current=None) -> None:
        self.session, self.current, self.assessments = session, current, []

    async def get_session(self, _, *, for_update=False):
        return self.session

    async def get_current_assessment(self, _, *, for_update=False):
        return self.current

    def add_assessment(self, assessment):
        self.current = assessment
        self.assessments.append(assessment)


class FakeProfileRepository:
    def __init__(self, profile) -> None:
        self.profile = profile

    async def get(self, _, *, for_update=False):
        return self.profile


class FakeUsersRepository:
    def __init__(self, user) -> None:
        self.user = user

    async def get_by_subject(self, _, subject, *, for_update=False):
        return self.user if subject == "owner" else None


class FakeUow:
    def __init__(
        self, *, onboarding=None, users=None, profiles=None, jobs=None
    ) -> None:
        self.idempotency = FakeIdempotencyRepository()
        self.onboarding = onboarding
        self.users = users
        self.profiles = profiles
        self.jobs = jobs
        self.commits = 0

    async def commit(self):
        self.commits += 1


def request(age=30):
    return AssessmentWriteRequest.model_validate(
        {
            "schema_version": "mobile-questionnaire.v1",
            "timezone": "UTC",
            "answers": {"age": age, "period_description": "Regular"},
        }
    )


@pytest.mark.anyio
async def test_assessment_starts_at_zero_and_requires_exact_revision():
    session = SimpleNamespace(
        proof_hash="f" * 64,
        expires_at=NOW + timedelta(hours=1),
        status=OnboardingStatus.ACTIVE.value,
    )
    from app.v2.application.services import _proof_hash

    proof = "proof-token-for-test-only-which-is-long-enough"
    session.proof_hash = _proof_hash(proof)
    uow = FakeUow(onboarding=FakeOnboardingRepository(session))
    created = await put_assessment(
        uow,
        session_id=uuid4(),
        proof_token=proof,
        idempotency_key="assessment-create-0001",
        expected_version=0,
        request=request(),
        now=NOW,
    )
    assert created.version == 0
    with pytest.raises(ApplicationProblem) as error:
        await put_assessment(
            uow,
            session_id=created.session_id,
            proof_token=proof,
            idempotency_key="assessment-update-0001",
            expected_version=4,
            request=request(31),
            now=NOW,
        )
    assert error.value.status == 412


@pytest.mark.anyio
async def test_latest_plan_generation_is_owner_scoped_and_keeps_terminal_jobs_discoverable():
    owner = SimpleNamespace(id=uuid4(), status=UserStatus.ACTIVE.value)
    terminal = SimpleNamespace(
        id=uuid4(),
        user_id=owner.id,
        job_type="plan_generation",
        state="dead_letter",
        progress=100,
        phase="dead_letter",
        request_payload={"local_date": "2026-08-07"},
        created_at=NOW,
        updated_at=NOW,
        error_code="provider_timeout",
    )

    class Jobs:
        async def get_latest_plan_generation(self, user_id, local_date):
            assert user_id == owner.id
            assert local_date.isoformat() == "2026-08-07"
            return terminal

    uow = FakeUow(
        users=FakeUsersRepository(owner),
        profiles=FakeProfileRepository(SimpleNamespace(timezone="America/Los_Angeles")),
        jobs=Jobs(),
    )
    response = await get_latest_plan_generation(
        uow,
        principal=PRINCIPAL,
        requested_date=None,
        now=datetime(2026, 8, 8, 6, tzinfo=UTC),
    )
    assert response.job_id == terminal.id
    assert response.state.value == "dead_letter"
    assert response.local_date.isoformat() == "2026-08-07"


@pytest.mark.anyio
async def test_latest_plan_generation_conceals_other_owners_and_nonplan_jobs_have_no_date():
    owner = SimpleNamespace(id=uuid4(), status=UserStatus.ACTIVE.value)

    class Jobs:
        async def get_latest_plan_generation(self, user_id, local_date):
            assert user_id == owner.id
            return None

    uow = FakeUow(
        users=FakeUsersRepository(owner),
        profiles=FakeProfileRepository(SimpleNamespace(timezone="UTC")),
        jobs=Jobs(),
    )
    with pytest.raises(ApplicationProblem) as error:
        await get_latest_plan_generation(
            uow,
            principal=PRINCIPAL,
            requested_date=datetime(2026, 8, 7, tzinfo=UTC).date(),
        )
    assert error.value.status == 404

    response = _job_response(
        SimpleNamespace(
            id=uuid4(),
            job_type="conversation_response.v1",
            state="queued",
            progress=0,
            phase="queued",
            request_payload={"conversation_id": str(uuid4())},
            created_at=NOW,
            updated_at=NOW,
            error_code=None,
        )
    )
    assert response.local_date is None


@pytest.mark.anyio
async def test_idempotency_replays_rejects_reuse_and_takes_over_expiry():
    uow = FakeUow()
    first = await _begin_idempotent(
        uow,
        scope="test",
        subject="subject",
        key="idempotency-key-0001",
        payload={"x": 1},
        now=NOW,
    )
    _complete_idempotent(first, response_status=200, response_body={"ok": True})
    replay = await _begin_idempotent(
        uow,
        scope="test",
        subject="subject",
        key="idempotency-key-0001",
        payload={"x": 1},
        now=NOW,
    )
    assert replay.replay_body == {"ok": True}
    with pytest.raises(ApplicationProblem) as error:
        await _begin_idempotent(
            uow,
            scope="test",
            subject="subject",
            key="idempotency-key-0001",
            payload={"x": 2},
            now=NOW,
        )
    assert error.value.code == "idempotency_key_reused"
    first.record.expires_at = NOW - timedelta(seconds=1)
    takeover = await _begin_idempotent(
        uow,
        scope="test",
        subject="subject",
        key="idempotency-key-0001",
        payload={"x": 2},
        now=NOW,
    )
    assert takeover.record.state == IdempotencyState.STARTED.value
    assert takeover.replay_body is None


@pytest.mark.anyio
async def test_concurrent_idempotency_retry_never_leaves_two_winners():
    uow = FakeUow()

    async def attempt():
        return await _begin_idempotent(
            uow,
            scope="test",
            subject="subject",
            key="concurrent-key-0001",
            payload={},
            now=NOW,
        )

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    assert sum(isinstance(result, Exception) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1


@pytest.mark.anyio
async def test_profile_precondition_and_cross_user_job_access_are_enforced():
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id, status=UserStatus.ACTIVE.value, email=None, email_verified=False
    )
    profile = SimpleNamespace(
        display_name=None,
        timezone="UTC",
        locale="en",
        version=2,
        created_at=NOW,
        updated_at=NOW,
    )
    uow = FakeUow(
        users=FakeUsersRepository(user), profiles=FakeProfileRepository(profile)
    )
    from app.v2.application.contracts import ProfilePatchRequest

    with pytest.raises(ApplicationProblem) as error:
        await patch_profile(
            uow,
            principal=PRINCIPAL,
            idempotency_key="profile-update-0001",
            expected_version=1,
            request=ProfilePatchRequest(display_name="Name"),
            now=NOW,
        )
    assert error.value.status == 412

    other_job = SimpleNamespace(id=uuid4(), user_id=uuid4())

    class Jobs:
        async def get_owned(self, job_id, user_id):
            return (
                other_job
                if other_job.id == job_id and other_job.user_id == user_id
                else None
            )

    uow.jobs = Jobs()
    with pytest.raises(ApplicationProblem) as error:
        await get_job(uow, principal=PRINCIPAL, job_id=other_job.id)
    assert error.value.status == 404


def test_questionnaire_rejects_unknown_keys_and_consent_versions():
    with pytest.raises(ValidationError):
        AssessmentWriteRequest.model_validate(
            {
                "schema_version": "mobile-questionnaire.v2",
                "timezone": "UTC",
                "answers": {"age": 30},
            }
        )
    with pytest.raises(ValidationError):
        AssessmentWriteRequest.model_validate(
            {
                "schema_version": "mobile-questionnaire.v1",
                "timezone": "UTC",
                "answers": {"unknown": "value"},
            }
        )
    claim = ClaimOnboardingRequest.model_validate(
        {
            "consents": [
                {
                    "consent_type": "privacy",
                    "document_version": "invented",
                    "granted": True,
                },
                {
                    "consent_type": "health_data_processing",
                    "document_version": "health-data-processing.v1",
                    "granted": True,
                },
            ]
        }
    )
    with pytest.raises(ApplicationProblem) as error:
        _validate_claim_consents(
            claim,
            {
                "privacy": "privacy.v1",
                "health_data_processing": "health-data-processing.v1",
            },
        )
    assert error.value.code == "invalid_consent_document_version"


@pytest.mark.anyio
async def test_runtime_openapi_advertises_preconditions_and_answer_schema():
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    schema = app.openapi()
    operation = schema["paths"]["/api/v2/onboarding/sessions/{session_id}/assessment"][
        "put"
    ]
    assert operation["operationId"] == "putOnboardingAssessmentV2"
    assert {"412", "428"}.issubset(operation["responses"])
    assert any(item["name"] == "If-Match" for item in operation["parameters"])
    assert "MobileQuestionnaireV1" in schema["components"]["schemas"]
    assert await require_assessment_revision('"0"') == 0
    assert await require_profile_revision('"1"') == 1


def test_runtime_openapi_advertises_symptom_observation_creation_contract():
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    operation = app.openapi()["paths"]["/api/v2/me/symptom-observations"]["post"]
    assert operation["operationId"] == "createSymptomObservation"
    assert operation["responses"]["201"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/SymptomObservationResponse")
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["required"]
        for parameter in operation["parameters"]
    )


def test_runtime_openapi_advertises_selected_variant_replacement_contract():
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    operation = app.openapi()["paths"]["/api/v2/me/plans/{plan_id}/replacements"][
        "post"
    ]
    assert operation["operationId"] == "replacePlanWithSelectedVariantV2"
    assert {"201", "412", "428"}.issubset(operation["responses"])
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["Idempotency-Key"]["required"]
    assert "If-Match" in parameters
    assert operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/PlanReplacementRequest")


def test_runtime_openapi_advertises_owner_scoped_generation_recovery_contract():
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    operation = app.openapi()["paths"]["/api/v2/me/plan-generations/latest"]["get"]
    assert operation["operationId"] == "getMyLatestPlanGenerationV2"
    assert any(
        parameter["name"] == "local_date" for parameter in operation["parameters"]
    )
    assert "404" in operation["responses"]
