"""The runner must close every database transaction before provider I/O."""
from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from uuid import uuid4

import pytest

from app.v2.application.plan_job_runner import PlanGenerationJobRunner
from app.v2.application.plan_materialization import PublishedPlan
from app.v2.application.plan_materialization import PlanMaterializer
from app.v2.application.plan_generation import PlanGenerationRequest
from app.v2.domain.plan_generation import PlanCandidateRejected
from app.v2.infrastructure.worker import ClaimedJob
from app.v2.infrastructure.worker import TerminalJobFailure


class Session:
    def __init__(self, assessment, profile) -> None:
        self.assessment, self.profile = assessment, profile

    async def get(self, model, value):
        del value
        return (
            self.assessment
            if model.__name__ == "OnboardingAssessment"
            else self.profile
        )


class Uow:
    def __init__(self, session) -> None:
        self.session, self.closed, self.committed = session, False, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def commit(self):
        self.committed = True


@pytest.mark.anyio
async def test_provider_orchestration_runs_after_context_uow_has_closed():
    user_id, assessment_id = uuid4(), uuid4()
    loader_uow = Uow(
        Session(
            SimpleNamespace(
                id=assessment_id,
                user_id=user_id,
                timezone="UTC",
                version=0,
                answers={
                    "lifestyle_focus": ["move", "pause"],
                    "other_concerns": ["My name is Alex and my phone is 555-0100"],
                },
            ),
            SimpleNamespace(timezone="UTC"),
        )
    )
    finalize_uow = Uow(Session(None, None))
    uows = iter((loader_uow, finalize_uow))
    observed = {}

    class Orchestrator:
        async def generate(self, request):
            observed["loader_closed"] = loader_uow.closed
            observed["context"] = request.request_context
            return "bundle"

    class Materializer:
        async def finalize(self, uow, *, job, bundle):
            assert uow is finalize_uow and bundle == "bundle"
            return PublishedPlan(uuid4(), 1, "2026-08-08")

    runner = PlanGenerationJobRunner(
        orchestrator=Orchestrator(),
        materializer=Materializer(),
        uow_factory=lambda: next(uows),
    )
    job = ClaimedJob(
        id=uuid4(),
        user_id=user_id,
        job_type="plan_generation",
        request_payload=MappingProxyType(
            {
                "assessment_id": str(assessment_id),
                "assessment_version": 0,
                "timezone": "UTC",
                "local_date": "2026-08-08",
            }
        ),
        attempt_count=1,
        max_attempts=3,
        lease_token="worker:lease",
    )
    result = await runner.handle(job)
    assert observed["loader_closed"] and loader_uow.committed
    assert observed["context"]["lifestyle_focus"] == ["move", "pause"]
    assert "other_concerns" not in observed["context"]
    assert "Alex" not in str(observed["context"])
    assert result["revision"] == 1


def test_runner_rejects_invalid_or_changed_timezone_snapshot():
    with pytest.raises(TerminalJobFailure, match="invalid_job_timezone"):
        PlanGenerationJobRunner._validated_timezone({"timezone": "Mars/Olympus"})


def test_non_ai_provider_failures_are_not_misclassified_as_ai_invocations():
    assert PlanMaterializer._failure_provider("pubmed_timeout") is None
    assert PlanMaterializer._failure_provider("storage_timeout") is None
    assert PlanMaterializer._failure_provider("gemini_timeout") == "gemini"


@pytest.mark.anyio
async def test_candidate_safety_rejection_is_terminal_not_retryable():
    user_id, assessment_id = uuid4(), uuid4()
    loader_uow = Uow(
        Session(
            SimpleNamespace(
                id=assessment_id, user_id=user_id, timezone="UTC", version=0, answers={}
            ),
            SimpleNamespace(timezone="UTC"),
        )
    )

    class UnsafeOrchestrator:
        async def generate(self, request: PlanGenerationRequest):
            del request
            raise PlanCandidateRejected(
                "candidate_safety_medication", policy_version="plan-safety.v2"
            )

    runner = PlanGenerationJobRunner(
        orchestrator=UnsafeOrchestrator(),
        materializer=object(),
        uow_factory=lambda: loader_uow,
    )
    job = ClaimedJob(
        id=uuid4(),
        user_id=user_id,
        job_type="plan_generation",
        request_payload=MappingProxyType(
            {
                "assessment_id": str(assessment_id),
                "assessment_version": 0,
                "timezone": "UTC",
                "local_date": "2026-08-08",
            }
        ),
        attempt_count=1,
        max_attempts=3,
        lease_token="worker:lease",
    )
    with pytest.raises(TerminalJobFailure, match="candidate_safety_medication"):
        await runner.handle(job)
