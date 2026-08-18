"""Production composition root for the separately deployed v2 plan worker."""
from __future__ import annotations

import logging
import os
import socket

import anyio

from app.v2.runtime.config import (
    settings,
    validate_plan_worker_configuration,
)
from app.v2.runtime.logging import configure_logging
from app.v2.runtime.schema import check_database_schema_head
from app.v2.persistence.database import check_database_readiness
from app.v2.application.plan_generation import PlanGenerationOrchestrator
from app.v2.application.conversation_job_runner import ConversationResponseJobRunner
from app.v2.application.plan_job_runner import PlanGenerationJobRunner
from app.v2.application.plan_materialization import PlanMaterializer
from app.v2.application.account_lifecycle import (
    AccountErasurePorts,
    AccountLifecycleJobRunner,
)
from app.v2.infrastructure.account_lifecycle import (
    AesGcmAccountExportCipher,
    CanonicalAccountExportBuilder,
    DocumentedErasureReleaseGate,
    FailClosedErasureReleaseGate,
    FailClosedUserCacheEraser,
    FirebaseAdminIdentityEraser,
    PostgresRuntimeCheckpointEraser,
    PostgresUserObjectReferenceSource,
    RedisUserCacheEraser,
    SupabasePrivateAccountStorage,
)
from app.v2.infrastructure.auth.firebase_runtime import initialize_v2_firebase
from app.v2.infrastructure.plan_generation_openai import (
    OpenAIStructuredPlanGateway,
)
from app.v2.infrastructure.plan_generation_providers import (
    CloudflareFluxImageGateway,
    GeminiConversationGateway,
    PubmedEvidenceResolver,
    SupabasePermanentMediaStore,
)
from app.v2.infrastructure.worker import PostgresJobWorker, run_worker, run_workers


def build_plan_worker() -> PostgresJobWorker:
    """Build only real providers; development fakes never enter this process."""

    validate_plan_worker_configuration()
    plan_gateway = OpenAIStructuredPlanGateway(
        api_key=settings.V2_OPENAI_API_KEY,
        model=settings.V2_OPENAI_MODEL,
        telemetry_hmac_key=settings.V2_TELEMETRY_HMAC_KEY.encode("utf-8"),
    )
    evidence_resolver = PubmedEvidenceResolver(
        tool=settings.V2_PUBMED_TOOL,
        email=settings.V2_PUBMED_EMAIL,
        min_interval_seconds=settings.V2_PUBMED_MIN_INTERVAL_SECONDS,
    )
    image_generator = CloudflareFluxImageGateway(
        account_id=settings.V2_CLOUDFLARE_ACCOUNT_ID,
        api_token=settings.V2_CLOUDFLARE_API_TOKEN,
        model=settings.V2_CLOUDFLARE_IMAGE_MODEL,
        telemetry_hmac_key=settings.V2_TELEMETRY_HMAC_KEY.encode("utf-8"),
    )
    media_store = SupabasePermanentMediaStore(
        project_url=settings.V2_SUPABASE_URL,
        service_role_key=settings.V2_SUPABASE_SERVICE_ROLE_KEY,
        bucket=settings.V2_PLAN_MEDIA_BUCKET,
    )
    orchestrator = PlanGenerationOrchestrator(
        plan_gateway=plan_gateway,
        evidence_resolver=evidence_resolver,
        image_generator=image_generator,
        media_store=media_store,
    )
    runner = PlanGenerationJobRunner(
        orchestrator=orchestrator, materializer=PlanMaterializer()
    )
    worker_id = os.getenv("RENDER_INSTANCE_ID") or socket.gethostname()
    return PostgresJobWorker(
        worker_id=f"plan-worker:{worker_id}",
        handler=runner.handle,
        lease_seconds=settings.V2_WORKER_LEASE_SECONDS,
        timeout_seconds=settings.V2_PLAN_JOB_TIMEOUT_SECONDS,
        shutdown_seconds=settings.V2_WORKER_SHUTDOWN_SECONDS,
        job_type="plan_generation",
        close_resources=lambda: _close_providers(
            plan_gateway,
            evidence_resolver,
            image_generator,
            media_store,
        ),
    )


def build_conversation_worker() -> PostgresJobWorker:
    """Compose the real conversation provider into the same worker process."""

    validate_plan_worker_configuration()
    gateway = GeminiConversationGateway(
        api_key=settings.V2_GEMINI_API_KEY,
        model=settings.V2_GEMINI_MODEL,
        telemetry_hmac_key=settings.V2_TELEMETRY_HMAC_KEY.encode("utf-8"),
    )
    worker_id = os.getenv("RENDER_INSTANCE_ID") or socket.gethostname()
    return PostgresJobWorker(
        worker_id=f"conversation-worker:{worker_id}",
        handler=ConversationResponseJobRunner(gateway=gateway).handle,
        lease_seconds=settings.V2_WORKER_LEASE_SECONDS,
        timeout_seconds=settings.V2_CONVERSATION_JOB_TIMEOUT_SECONDS,
        shutdown_seconds=settings.V2_WORKER_SHUTDOWN_SECONDS,
        job_type="conversation_response.v1",
        close_resources=lambda: _close_providers(gateway),
    )


def build_account_workers() -> tuple[PostgresJobWorker, PostgresJobWorker]:
    """Compose account jobs only after worker-only release configuration validates."""
    validate_plan_worker_configuration()
    storage = SupabasePrivateAccountStorage(
        project_url=settings.V2_SUPABASE_URL,
        service_role_key=settings.V2_SUPABASE_SERVICE_ROLE_KEY,
        export_bucket=settings.V2_ACCOUNT_EXPORT_BUCKET,
        user_object_source=PostgresUserObjectReferenceSource(),
    )
    redis_client = None
    cache_eraser = FailClosedUserCacheEraser()
    if settings.V2_DELETION_ENABLED:
        import redis.asyncio as redis

        redis_client = redis.from_url(settings.V2_REDIS_URL, decode_responses=True)
        cache_eraser = RedisUserCacheEraser(redis_client)
    runner = AccountLifecycleJobRunner(
        exports=CanonicalAccountExportBuilder(
            cipher=AesGcmAccountExportCipher(settings.V2_ACCOUNT_EXPORT_ENCRYPTION_KEY)
        ),
        ports=AccountErasurePorts(
            identity=FirebaseAdminIdentityEraser(),
            storage=storage,
            checkpoints=PostgresRuntimeCheckpointEraser(),
            cache=cache_eraser,
            # The release gate runs before every destructive port.  An
            # intentionally disabled deployment can still service exports,
            # but a stale deletion job cannot reach Firebase or storage.
            release_gate=(
                DocumentedErasureReleaseGate(
                    approval_reference=settings.V2_DELETION_APPROVAL_REFERENCE
                )
                if settings.V2_DELETION_ENABLED
                else FailClosedErasureReleaseGate()
            ),
        ),
    )
    worker_id = os.getenv("RENDER_INSTANCE_ID") or socket.gethostname()
    closer = lambda: _close_providers(storage, redis_client)
    common = dict(
        lease_seconds=settings.V2_WORKER_LEASE_SECONDS,
        shutdown_seconds=settings.V2_WORKER_SHUTDOWN_SECONDS,
        close_resources=closer,
    )
    return (
        PostgresJobWorker(
            worker_id=f"account-export-worker:{worker_id}",
            handler=runner.handle,
            timeout_seconds=settings.V2_PLAN_JOB_TIMEOUT_SECONDS,
            job_type="account_export",
            **common,
        ),
        PostgresJobWorker(
            worker_id=f"account-deletion-worker:{worker_id}",
            handler=runner.handle,
            timeout_seconds=settings.V2_PLAN_JOB_TIMEOUT_SECONDS,
            job_type="account_deletion",
            lease_seconds=settings.V2_WORKER_LEASE_SECONDS,
            shutdown_seconds=settings.V2_WORKER_SHUTDOWN_SECONDS,
        ),
    )


async def _close_providers(*providers: object) -> None:
    error: Exception | None = None
    for provider in providers:
        close = getattr(provider, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as exc:
                # Finish closing every client before letting the process report
                # the first shutdown failure.
                if error is None:
                    error = exc
    if error is not None:
        raise error


async def run_plan_worker() -> None:
    """Verify the deployed schema before this process can claim a job."""

    validate_plan_worker_configuration()
    await check_database_readiness()
    await check_database_schema_head()
    worker = build_plan_worker()
    try:
        await run_worker(worker)
    finally:
        # These gateways own persistent HTTP clients. Close them after a
        # normal exit or a Render SIGTERM instead of relying on interpreter
        # shutdown.
        await worker.aclose()


async def run_v2_worker() -> None:
    """Run plan and conversation job filters concurrently in one Render worker."""

    validate_plan_worker_configuration()
    await check_database_readiness()
    await check_database_schema_head()
    if settings.V2_DELETION_ENABLED:
        # Firebase is deliberately initialized only in an explicitly enabled
        # deletion worker; plan, conversation, and export jobs need none.
        initialize_v2_firebase()
    workers = (
        build_plan_worker(),
        build_conversation_worker(),
        *build_account_workers(),
    )
    try:
        await run_workers(workers)
    finally:
        for worker in workers:
            await worker.aclose()


logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging(settings.LOG_LEVEL)
    logger.info("auvra-v2-worker starting: plan, conversation, and account lanes")
    anyio.run(run_v2_worker)


if __name__ == "__main__":
    main()
