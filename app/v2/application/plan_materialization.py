"""Short, lease-checked transaction that makes a generated plan visible once."""
from __future__ import annotations

import uuid
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.v2.application.plan_generation import PlanGenerationBundle, ProviderFailure
from app.v2.domain.enums import MediaStatus, PlanItemStatus, PlanStatus
from app.v2.infrastructure.worker import ClaimedJob, LeaseLost, TerminalJobFailure
from app.v2.persistence.models import (
    ActionPlan,
    ActionPlanItem,
    ActionPlanItemVariant,
    GenerationJob,
    MediaAsset,
    OutboxEvent,
)
from app.v2.persistence.models_engagement import (
    AiInvocation,
    ResearchCitation,
    ResearchSource,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class PublishedPlan:
    plan_id: uuid.UUID
    revision: int
    local_date: str

    def safe_result(self) -> dict[str, str | int]:
        return {
            "plan_id": str(self.plan_id),
            "revision": self.revision,
            "local_date": self.local_date,
        }


class PlanMaterializer:
    """Persist only a validated complete bundle in one commit.

    Uploaded storage objects are content-addressed. Re-running after a crash is
    therefore safe: the transaction reuses the job's existing publication if
    present and no partial database graph is ever committed.
    """

    async def finalize(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        job: ClaimedJob,
        bundle: PlanGenerationBundle,
    ) -> PublishedPlan:
        session = self._session(uow)
        stored_job = await self._locked_job(session, job)
        local_date, timezone = self._request_timing(job.request_payload)
        existing = await self._existing_plan(session, job)
        if existing is not None:
            return PublishedPlan(
                existing.id, existing.revision, existing.local_date.isoformat()
            )
        source_ids = await self._upsert_sources(uow, bundle)
        asset_ids = await self._upsert_assets(uow, job, bundle)
        plan = await self._add_plan(
            uow, job, bundle, asset_ids, source_ids, local_date, timezone
        )
        self._add_invocations(session, job, bundle)
        published = await self._mark_published(session, stored_job, job, plan)
        await uow.commit()
        return published

    async def record_provider_failure(
        self, uow: SqlAlchemyUnitOfWork, *, job: ClaimedJob, failure: ProviderFailure
    ) -> None:
        session = self._session(uow)
        stored_job = await self._locked_job(session, job)
        provider = self._failure_provider(failure.code)
        digest = hashlib.sha256(failure.code.encode("utf-8")).hexdigest()
        for observed in failure.observed_invocations:
            self._add_invocation(session, job, observed)
        if provider is not None:
            session.add(
                AiInvocation(
                    id=uuid.uuid4(),
                    user_id=job.user_id,
                    generation_job_id=job.id,
                    provider=provider,
                    operation="generate_image"
                    if provider == "cloudflare_workers_ai"
                    else "generate_plan",
                    task="plan_image_generation"
                    if provider == "cloudflare_workers_ai"
                    else "plan_generation",
                    prompt_version="plan-image.v1"
                    if provider == "cloudflare_workers_ai"
                    else "plan.v1",
                    model="unknown",
                    input_tokens=0,
                    output_tokens=0,
                    cost_minor=0,
                    currency_code="USD",
                    price_version="provider-default.v1",
                    latency_ms=0,
                    result_status="failed",
                    input_hash=digest,
                    output_hash=None,
                )
            )
        del stored_job
        await uow.commit()

    async def _locked_job(self, session, job: ClaimedJob) -> GenerationJob:
        stored = await session.scalar(
            select(GenerationJob).where(GenerationJob.id == job.id).with_for_update()
        )
        if stored is None:
            raise TerminalJobFailure("job_not_found")
        if stored.state == "ready":
            raise TerminalJobFailure("ready_job_already_acknowledged")
        if stored.state != "running" or stored.lease_owner != job.lease_token:
            raise LeaseLost()
        if stored.user_id != job.user_id or stored.job_type != "plan_generation":
            raise TerminalJobFailure("invalid_job_payload")
        return stored

    async def _existing_plan(self, session, job: ClaimedJob) -> ActionPlan | None:
        return await session.scalar(
            select(ActionPlan)
            .where(ActionPlan.generation_job_id == job.id)
            .with_for_update()
        )

    async def _add_plan(
        self, uow, job, bundle, asset_ids, source_ids, local_date, timezone
    ):
        session = self._session(uow)
        revision = await self._next_revision(session, job, local_date)
        plan = self._plan(job, local_date, timezone, revision)
        session.add(plan)
        await uow.flush()
        await self._add_items(uow, plan, bundle, asset_ids, source_ids)
        return plan

    async def _next_revision(self, session, job: ClaimedJob, local_date) -> int:
        previous = await session.scalar(
            select(ActionPlan.revision)
            .where(
                ActionPlan.user_id == job.user_id, ActionPlan.local_date == local_date
            )
            .order_by(ActionPlan.revision.desc())
            .limit(1)
            .with_for_update()
        )
        return (previous or 0) + 1

    def _plan(
        self, job: ClaimedJob, local_date, timezone: str, revision: int
    ) -> ActionPlan:
        return ActionPlan(
            id=uuid.uuid4(),
            user_id=job.user_id,
            generation_job_id=job.id,
            local_date=local_date,
            timezone=timezone,
            revision=revision,
            is_current=False,
            status=PlanStatus.ARCHIVED.value,
            cycle_snapshot=self._cycle_snapshot(job.request_payload),
            context_snapshot=self._safe_context_snapshot(job.request_payload),
        )

    async def _add_items(self, uow, plan, bundle, asset_ids, source_ids) -> None:
        session = self._session(uow)
        for action in bundle.candidate.actions:
            item = ActionPlanItem(
                id=uuid.uuid4(),
                plan_id=plan.id,
                slot=action.slot,
                category=action.category,
                title=action.title,
                purpose=action.purpose,
                instructions={"steps": list(action.instructions)},
                hero_asset_id=asset_ids[(action.slot, "hero", None)],
                status=PlanItemStatus.ACTIVE.value,
            )
            session.add(item)
            await uow.flush()
            self._add_variants(session, item, action, asset_ids)
            self._add_citations(session, item, action, source_ids)

    @staticmethod
    def _add_variants(session, item, action, asset_ids) -> None:
        for variant in action.variants:
            session.add(
                ActionPlanItemVariant(
                    id=uuid.uuid4(),
                    item_id=item.id,
                    variant_type=variant.variant_type,
                    content={
                        "title": variant.title,
                        "instructions": list(variant.instructions),
                    },
                    asset_id=asset_ids[(action.slot, "variant", variant.variant_type)],
                )
            )

    @staticmethod
    def _add_citations(session, item, action, source_ids) -> None:
        for citation_url in action.citation_urls:
            session.add(
                ResearchCitation(
                    id=uuid.uuid4(),
                    source_id=source_ids[citation_url],
                    plan_item_id=item.id,
                    claim=action.purpose,
                )
            )

    @staticmethod
    def _add_invocations(
        session, job: ClaimedJob, bundle: PlanGenerationBundle
    ) -> None:
        for invocation in bundle.invocations:
            PlanMaterializer._add_invocation(session, job, invocation)

    @staticmethod
    def _add_invocation(session, job: ClaimedJob, invocation) -> None:
        session.add(
            AiInvocation(
                id=uuid.uuid4(),
                user_id=job.user_id,
                generation_job_id=job.id,
                provider=invocation.provider,
                operation=invocation.operation,
                task=invocation.task,
                prompt_version=invocation.prompt_version,
                model=invocation.model,
                input_tokens=invocation.input_tokens,
                output_tokens=invocation.output_tokens,
                cost_minor=invocation.cost_minor,
                currency_code=invocation.currency_code,
                price_version=invocation.price_version,
                latency_ms=invocation.latency_ms,
                result_status=invocation.result_status,
                input_hash=invocation.input_hash,
                output_hash=invocation.output_hash,
            )
        )

    async def _mark_published(self, session, stored_job, job, plan) -> PublishedPlan:
        await session.execute(
            update(ActionPlan)
            .where(
                ActionPlan.user_id == job.user_id,
                ActionPlan.local_date == plan.local_date,
                ActionPlan.is_current.is_(True),
            )
            .values(is_current=False, status=PlanStatus.ARCHIVED.value)
        )
        plan.status, plan.is_current, plan.published_at = (
            PlanStatus.READY.value,
            True,
            datetime.now(UTC),
        )
        published = PublishedPlan(plan.id, plan.revision, plan.local_date.isoformat())
        stored_job.state, stored_job.progress, stored_job.phase = "ready", 100, "ready"
        stored_job.error_code, stored_job.result_payload = None, published.safe_result()
        stored_job.finished_at, stored_job.lease_owner, stored_job.lease_expires_at = (
            datetime.now(UTC),
            None,
            None,
        )
        session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                owner_user_id=job.user_id,
                aggregate_type="action_plan",
                aggregate_id=plan.id,
                event_type="plan.ready",
                payload={
                    "plan_id": str(plan.id),
                    "job_id": str(job.id),
                    "user_id": str(job.user_id),
                },
            )
        )
        return published

    async def _upsert_sources(
        self, uow: SqlAlchemyUnitOfWork, bundle: PlanGenerationBundle
    ) -> dict[str, uuid.UUID]:
        session = self._session(uow)
        result: dict[str, uuid.UUID] = {}
        for source in bundle.evidence_sources:
            source_type, external_id = self._pubmed_identity(source.canonical_url)
            statement = (
                insert(ResearchSource)
                .values(
                    id=uuid.uuid4(),
                    source_type=source_type,
                    source_external_id=external_id,
                    canonical_url=source.canonical_url,
                    title=source.title,
                    metadata_json={},
                )
                .on_conflict_do_update(
                    index_elements=["canonical_url"],
                    set_={"title": source.title, "updated_at": datetime.now(UTC)},
                )
                .returning(ResearchSource.id)
            )
            result[source.canonical_url] = (
                await session.execute(statement)
            ).scalar_one()
        return result

    async def _upsert_assets(
        self, uow: SqlAlchemyUnitOfWork, job: ClaimedJob, bundle: PlanGenerationBundle
    ) -> dict[tuple[int, str, str | None], uuid.UUID]:
        session = self._session(uow)
        result: dict[tuple[int, str, str | None], uuid.UUID] = {}
        for generated in bundle.assets:
            media = generated.media
            statement = (
                insert(MediaAsset)
                .values(
                    id=uuid.uuid4(),
                    owner_user_id=job.user_id,
                    generation_job_id=job.id,
                    storage_provider=media.provider,
                    bucket=media.bucket,
                    object_key=media.object_key,
                    public_url=media.public_url,
                    content_sha256=media.content_sha256,
                    mime_type=media.mime_type,
                    alt_text=generated.alt_text,
                    width=media.width,
                    height=media.height,
                    status=MediaStatus.READY.value,
                )
                .on_conflict_do_nothing(
                    index_elements=["storage_provider", "bucket", "object_key"]
                )
                .returning(MediaAsset.id)
            )
            key = (generated.action_slot, generated.role, generated.variant_type)
            if key in result:
                raise TerminalJobFailure("duplicate_generated_asset")
            asset_id = (await session.execute(statement)).scalar_one_or_none()
            if asset_id is None:
                asset = await session.scalar(
                    select(MediaAsset).where(
                        MediaAsset.storage_provider == media.provider,
                        MediaAsset.bucket == media.bucket,
                        MediaAsset.object_key == media.object_key,
                    )
                )
                if asset is None or asset.owner_user_id != job.user_id:
                    raise TerminalJobFailure("media_asset_owner_conflict")
                asset_id = asset.id
            result[key] = asset_id
        if len(result) != 16:
            raise TerminalJobFailure("incomplete_generated_assets")
        return result

    @staticmethod
    def _request_timing(payload: Mapping[str, object]):
        from datetime import date

        local_date = payload.get("local_date")
        timezone = payload.get("timezone")
        if (
            not isinstance(local_date, str)
            or not isinstance(timezone, str)
            or not timezone
        ):
            raise TerminalJobFailure("invalid_job_payload")
        try:
            ZoneInfo(timezone)
            return date.fromisoformat(local_date), timezone
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise TerminalJobFailure("invalid_job_payload") from exc

    @staticmethod
    def _cycle_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        value = payload.get("cycle_snapshot")
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _safe_context_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        assessment_id = payload.get("assessment_id")
        return (
            {"assessment_id": assessment_id} if isinstance(assessment_id, str) else {}
        )

    @staticmethod
    def _pubmed_identity(url: str) -> tuple[str, str]:
        prefix = "https://pubmed.ncbi.nlm.nih.gov/"
        if not url.startswith(prefix) or not url.endswith("/"):
            raise TerminalJobFailure("unverified_evidence_source")
        pmid = url.removeprefix(prefix).removesuffix("/")
        if not pmid.isdigit():
            raise TerminalJobFailure("unverified_evidence_source")
        return "pubmed", pmid

    @staticmethod
    def _published_from_result(payload: Mapping[str, object] | None) -> PublishedPlan:
        if not isinstance(payload, Mapping):
            raise TerminalJobFailure("ready_job_missing_result")
        try:
            return PublishedPlan(
                uuid.UUID(str(payload["plan_id"])),
                int(payload["revision"]),
                str(payload["local_date"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise TerminalJobFailure("ready_job_missing_result") from exc

    @staticmethod
    def _failure_provider(code: str) -> str | None:
        if code.startswith("cloudflare_image"):
            return "cloudflare_workers_ai"
        if code.startswith("gemini"):
            return "gemini"
        return None

    @staticmethod
    def _session(uow: SqlAlchemyUnitOfWork):
        if uow.session is None:
            raise RuntimeError("UnitOfWork must be entered before materialization")
        return uow.session
