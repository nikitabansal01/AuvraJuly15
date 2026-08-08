"""Durable plan-generation handler composed from I/O and atomic publication."""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.v2.application.plan_generation import (
    PlanGenerationOrchestrator,
    PlanGenerationRequest,
    ProviderFailure,
)
from app.v2.application.plan_materialization import PlanMaterializer
from app.v2.domain.plan_evidence import (
    assessment_context_from_answers,
    evidence_queries_for,
)
from app.v2.domain.plan_generation import PlanCandidateRejected
from app.v2.infrastructure.worker import ClaimedJob, TerminalJobFailure
from app.v2.persistence.models import OnboardingAssessment, UserProfile
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

UowFactory = Callable[[], SqlAlchemyUnitOfWork]


@dataclass(frozen=True, slots=True)
class PlanGenerationContext:
    request: PlanGenerationRequest


class PlanGenerationJobRunner:
    """Read a short snapshot, make provider calls, then publish in one short UoW."""

    def __init__(
        self,
        *,
        orchestrator: PlanGenerationOrchestrator,
        materializer: PlanMaterializer,
        uow_factory: UowFactory = SqlAlchemyUnitOfWork,
    ) -> None:
        self._orchestrator = orchestrator
        self._materializer = materializer
        self._uow_factory = uow_factory

    async def handle(self, job: ClaimedJob) -> dict[str, Any]:
        if job.job_type != "plan_generation":
            raise TerminalJobFailure("unsupported_job_type")
        context = await self._load_context(job)
        # This line is intentionally outside any UoW. It is the only code path
        # that can contact Gemini, PubMed, Cloudflare, or Supabase Storage.
        try:
            bundle = await self._orchestrator.generate(context.request)
        except PlanCandidateRejected as exc:
            # Candidate policy failures are deterministic and must dead-letter;
            # retrying the same untrusted response cannot make it safe.
            raise TerminalJobFailure(exc.reason_code) from exc
        except ProviderFailure as exc:
            async with self._uow_factory() as uow:
                await self._materializer.record_provider_failure(
                    uow, job=job, failure=exc
                )
            raise
        async with self._uow_factory() as uow:
            published = await self._materializer.finalize(uow, job=job, bundle=bundle)
        return published.safe_result()

    async def _load_context(self, job: ClaimedJob) -> PlanGenerationContext:
        assessment_id = self._assessment_id(job.request_payload)
        async with self._uow_factory() as uow:
            if uow.session is None:
                raise RuntimeError(
                    "UnitOfWork must be entered before loading job context"
                )
            assessment = await uow.session.get(OnboardingAssessment, assessment_id)
            profile = await uow.session.get(UserProfile, job.user_id)
            if (
                assessment is None
                or assessment.user_id != job.user_id
                or profile is None
            ):
                raise TerminalJobFailure("generation_context_unavailable")
            timezone = self._validated_timezone(job.request_payload)
            if profile.timezone != timezone or assessment.timezone != timezone:
                raise TerminalJobFailure("generation_context_changed")
            if assessment.version != self._assessment_version(job.request_payload):
                raise TerminalJobFailure("generation_context_changed")
            typed_context = assessment_context_from_answers(assessment.answers)
            request_context = typed_context.provider_context(
                timezone=timezone,
                local_date=str(job.request_payload["local_date"]),
            )
            evidence_queries = evidence_queries_for(typed_context)
            await uow.commit()
        return PlanGenerationContext(
            request=PlanGenerationRequest(
                task="plan_generation",
                prompt_version="plan.v1",
                request_context=request_context,
                evidence_queries=evidence_queries,
                generation_job_id=str(job.id),
            )
        )

    @staticmethod
    def _assessment_id(payload: Mapping[str, object]) -> uuid.UUID:
        value = payload.get("assessment_id")
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise TerminalJobFailure("invalid_job_payload") from exc

    @staticmethod
    def _assessment_version(payload: Mapping[str, object]) -> int:
        value = payload.get("assessment_version")
        if not isinstance(value, int) or value < 0:
            raise TerminalJobFailure("invalid_job_payload")
        return value

    @staticmethod
    def _validated_timezone(payload: Mapping[str, object]) -> str:
        timezone = payload.get("timezone")
        if not isinstance(timezone, str) or not timezone:
            raise TerminalJobFailure("invalid_job_payload")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise TerminalJobFailure("invalid_job_timezone") from exc
        return timezone
