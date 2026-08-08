"""Durable account export and erasure commands.

This module deliberately has no Firebase, Redis, object-storage, or LangGraph
imports.  It owns the workflow state; infrastructure adapters perform one
idempotent external step at a time and return only redacted verification facts.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select, text

from app.v2.application.contracts import AccountExportResponse, DeletionResponse
from app.v2.application.errors import conflict, not_found, service_unavailable
from app.v2.application.services import _begin_idempotent, _complete_idempotent
from app.v2.domain.enums import JobState, UserStatus
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.models import GenerationJob, OutboxEvent, User
from app.v2.persistence.models_engagement import (
    AccountExport,
    DeletionReceipt,
    DeletionRequest,
    DeletionStep,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


EXPORT_TTL = timedelta(days=7)
ERASURE_STEPS = (
    "identity_revoked",
    "private_storage_erased",
    "runtime_checkpoints_erased",
    "cache_erased",
    "postgres_graph_erased",
)


class RecentAuthenticationPolicy(Protocol):
    async def require_recent(self, principal: VerifiedPrincipal) -> None:
        """Raise a safe application problem when fresh reauthentication is absent."""


class FailClosedRecentAuthenticationPolicy:
    """Temporary safe default until Firebase recent-auth claims are wired."""

    async def require_recent(self, principal: VerifiedPrincipal) -> None:
        del principal
        raise service_unavailable(
            "recent_authentication_unavailable",
            "Account export and deletion are unavailable until recent "
            "authentication is configured.",
        )


class SubjectFingerprint(Protocol):
    """Create the pseudonymous subject value retained in an erasure receipt."""

    def fingerprint(self, *, auth_provider: str, auth_subject: str) -> str:
        ...


class AccountExportBuilder(Protocol):
    async def build(
        self, *, export_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[bytes, str]:
        """Return encrypted export bytes and a SHA-256 manifest checksum; never a URL."""


class AccountLifecycleFailure(RuntimeError):
    """A redacted failure that the durable worker can classify safely.

    Account lifecycle handlers must never return provider exception text: it can
    contain a subject, object path, or credentials.  The generic worker accepts
    this shape without importing this account domain.
    """

    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PrivateExportAsset:
    provider: str
    bucket: str
    object_key: str


class PrivateExportStorage(Protocol):
    async def put_export(
        self, *, export_id: uuid.UUID, content: bytes, expires_at: datetime
    ) -> PrivateExportAsset:
        """Persist under a deterministic private key for ``export_id``.

        Retries must return the same private object reference; this port never
        returns a signed or public URL.
        """
        ...

    async def delete_user_objects(self, *, user_id: uuid.UUID) -> None:
        ...


class FirebaseIdentityEraser(Protocol):
    async def revoke_and_delete(self, *, auth_provider: str, auth_subject: str) -> None:
        ...


class RuntimeCheckpointEraser(Protocol):
    async def delete_user_runtime(self, *, user_id: uuid.UUID) -> None:
        ...


class UserCacheEraser(Protocol):
    async def purge_user(self, *, user_id: uuid.UUID) -> None:
        ...


class ErasureReleaseGate(Protocol):
    """Authorize irreversible external deletion for the bound request only.

    The implementation is deliberately separate from recent authentication.
    Recent authentication proves that the requester controls the account;
    retention/legal-hold approval determines whether irreversible destruction
    may begin.  A missing gate must fail closed.
    """

    async def require_authorized(
        self, *, deletion_request_id: uuid.UUID, subject_hash: str
    ) -> None:
        ...


@dataclass(frozen=True, slots=True)
class AccountErasurePorts:
    identity: FirebaseIdentityEraser
    storage: PrivateExportStorage
    checkpoints: RuntimeCheckpointEraser
    cache: UserCacheEraser
    release_gate: ErasureReleaseGate


@dataclass(frozen=True, slots=True)
class DeletionContext:
    user: User
    subject_hash: str


@dataclass(frozen=True, slots=True)
class ExportWork:
    export_id: uuid.UUID
    expires_at: datetime


async def _active_user(uow: SqlAlchemyUnitOfWork, principal: VerifiedPrincipal) -> User:
    user = await uow.users.get_by_subject(
        principal.auth_provider, principal.subject, for_update=True
    )
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise not_found("Resource")
    return user


async def _deletion_user(
    uow: SqlAlchemyUnitOfWork, principal: VerifiedPrincipal
) -> User:
    """Resolve a deletion owner while allowing only an idempotent retry in pending state."""

    user = await uow.users.get_by_subject(
        principal.auth_provider, principal.subject, for_update=True
    )
    if user is None or user.status not in {
        UserStatus.ACTIVE.value,
        UserStatus.DELETION_PENDING.value,
    }:
        raise not_found("Resource")
    return user


def _job(
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    job_type: str,
    payload: dict[str, str],
    now: datetime,
) -> GenerationJob:
    return GenerationJob(
        id=job_id,
        user_id=user_id,
        job_type=job_type,
        state=JobState.QUEUED.value,
        progress=0,
        phase="queued",
        request_payload=payload,
        available_at=now,
        max_attempts=10,
    )


async def request_account_export(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    key: str,
    recent_authentication: RecentAuthenticationPolicy,
    now: datetime | None = None,
) -> AccountExportResponse:
    now = now or datetime.now(UTC)
    await recent_authentication.require_recent(principal)
    user = await _active_user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="account.export.create",
        subject=str(user.id),
        key=key,
        payload={},
        now=now,
    )
    if decision.replay_body is not None:
        return AccountExportResponse.model_validate(decision.replay_body)
    job_id, export_id = uuid.uuid4(), uuid.uuid4()
    expires_at = now + EXPORT_TTL
    export = AccountExport(
        id=export_id,
        user_id=user.id,
        generation_job_id=job_id,
        state="requested",
        expires_at=expires_at,
    )
    uow.session.add(
        _job(
            job_id=job_id,
            user_id=user.id,
            job_type="account_export",
            payload={"export_id": str(export_id)},
            now=now,
        )
    )
    uow.session.add(export)
    uow.outbox.add(
        OutboxEvent(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            aggregate_type="account_export",
            aggregate_id=export_id,
            event_type="account.export-requested.v1",
            payload={"export_id": str(export_id), "job_id": str(job_id)},
        )
    )
    response = AccountExportResponse(
        export_id=export_id, job_id=job_id, state="requested", expires_at=expires_at
    )
    _complete_idempotent(
        decision, response_status=202, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


async def request_account_deletion(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    key: str,
    recent_authentication: RecentAuthenticationPolicy,
    subject_fingerprint: SubjectFingerprint,
    now: datetime | None = None,
) -> DeletionResponse:
    now = now or datetime.now(UTC)
    await recent_authentication.require_recent(principal)
    user = await _deletion_user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="account.deletion.create",
        subject=str(user.id),
        key=key,
        payload={},
        now=now,
    )
    if decision.replay_body is not None:
        return DeletionResponse.model_validate(decision.replay_body)
    if user.status != UserStatus.ACTIVE.value:
        raise conflict(
            "deletion_already_requested",
            "An account deletion request is already in progress.",
        )
    # The lock on the user plus partial unique active-request index makes a
    # duplicate request deterministic even when callers use distinct keys.
    existing = await uow.session.scalar(
        select(DeletionRequest).where(
            DeletionRequest.user_id == user.id,
            DeletionRequest.state.in_(("requested", "running", "retry_wait")),
        )
    )
    if existing is not None:
        raise conflict(
            "deletion_already_requested",
            "An account deletion request is already in progress.",
        )
    request_id, job_id = uuid.uuid4(), uuid.uuid4()
    request = DeletionRequest(
        id=request_id,
        user_id=user.id,
        subject_hash=subject_fingerprint.fingerprint(
            auth_provider=user.auth_provider,
            auth_subject=user.auth_subject,
        ),
        state="requested",
        requested_at=now,
        generation_job_id=job_id,
        verification_summary={},
    )
    uow.session.add(request)
    uow.session.add(
        _job(
            job_id=job_id,
            user_id=user.id,
            job_type="account_deletion",
            payload={"deletion_request_id": str(request_id)},
            now=now,
        )
    )
    for step_name in ERASURE_STEPS:
        uow.session.add(
            DeletionStep(
                id=uuid.uuid4(), deletion_request_id=request_id, step_name=step_name
            )
        )
    uow.outbox.add(
        OutboxEvent(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            aggregate_type="account_deletion",
            aggregate_id=request_id,
            event_type="account.deletion-requested.v1",
            payload={"deletion_request_id": str(request_id), "job_id": str(job_id)},
        )
    )
    user.status = UserStatus.DELETION_PENDING.value
    response = DeletionResponse(
        deletion_request_id=request_id, job_id=job_id, state="requested"
    )
    _complete_idempotent(
        decision, response_status=202, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


UowFactory = Callable[[], SqlAlchemyUnitOfWork]


class AccountLifecycleJobRunner:
    """Per-job handler; external effects precede the trusted DB graph erase."""

    def __init__(
        self,
        *,
        exports: AccountExportBuilder,
        ports: AccountErasurePorts,
        uow_factory: UowFactory = SqlAlchemyUnitOfWork,
    ) -> None:
        if exports is None:
            raise ValueError("Account export builder is required")
        if any(
            port is None
            for port in (
                ports.identity,
                ports.storage,
                ports.checkpoints,
                ports.cache,
                ports.release_gate,
            )
        ):
            raise ValueError("All account-erasure ports are required")
        self._exports, self._ports, self._uow_factory = exports, ports, uow_factory

    async def handle(self, job: Any) -> dict[str, Any]:
        try:
            if job.job_type == "account_export":
                return await self._export(job.id, job.user_id, job.request_payload)
            if job.job_type == "account_deletion":
                return await self._delete(job.id, job.user_id, job.request_payload)
            raise AccountLifecycleFailure(
                "unsupported_account_lifecycle_job", retryable=False
            )
        except AccountLifecycleFailure as exc:
            await self._record_lifecycle_failure(job, exc)
            raise

    async def _export(
        self, job_id: uuid.UUID, user_id: uuid.UUID, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        export_id = _uuid_payload(payload, "export_id")
        work = await self._start_export(export_id, job_id, user_id)
        if work is None:
            return {"export_id": str(export_id), "state": "ready"}
        content, checksum = await self._build_export(work, user_id)
        asset = await self._store_export(work, content)
        await self._publish_export(work, user_id, checksum, asset)
        return {"export_id": str(export_id), "state": "ready"}

    async def _start_export(
        self, export_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID
    ) -> ExportWork | None:
        async with self._uow_factory() as uow:
            record = await uow.session.scalar(
                select(AccountExport)
                .where(AccountExport.id == export_id)
                .with_for_update()
            )
            if (
                record is None
                or record.user_id != user_id
                or record.generation_job_id != job_id
            ):
                raise AccountLifecycleFailure(
                    "account_export_context_unavailable", retryable=False
                )
            if record.state == "ready":
                await uow.commit()
                return None
            if record.expires_at <= datetime.now(UTC):
                record.state, record.failure_code = "expired", "account_export_expired"
                await uow.commit()
                raise AccountLifecycleFailure("account_export_expired", retryable=False)
            record.state = "running"
            record.failure_code = None
            expires_at = record.expires_at
            await uow.commit()
        return ExportWork(export_id=export_id, expires_at=expires_at)

    async def _build_export(
        self, work: ExportWork, user_id: uuid.UUID
    ) -> tuple[bytes, str]:
        try:
            content, checksum = await self._exports.build(
                export_id=work.export_id, user_id=user_id
            )
        except AccountLifecycleFailure:
            raise
        except Exception as exc:
            raise AccountLifecycleFailure(
                "account_export_build_failed", retryable=True
            ) from exc
        if not isinstance(content, bytes) or not isinstance(checksum, str):
            raise AccountLifecycleFailure(
                "account_export_invalid_payload", retryable=False
            )
        if hashlib.sha256(content).hexdigest() != checksum:
            raise AccountLifecycleFailure(
                "account_export_checksum_mismatch", retryable=False
            )
        return content, checksum

    async def _store_export(
        self, work: ExportWork, content: bytes
    ) -> PrivateExportAsset:
        try:
            asset = await self._ports.storage.put_export(
                export_id=work.export_id, content=content, expires_at=work.expires_at
            )
        except AccountLifecycleFailure:
            raise
        except Exception as exc:
            raise AccountLifecycleFailure(
                "account_export_storage_failed", retryable=True
            ) from exc
        if not _valid_private_export_asset(asset):
            raise AccountLifecycleFailure(
                "account_export_storage_invalid", retryable=False
            )
        return asset

    async def _publish_export(
        self,
        work: ExportWork,
        user_id: uuid.UUID,
        checksum: str,
        asset: PrivateExportAsset,
    ) -> None:
        async with self._uow_factory() as uow:
            record = await uow.session.scalar(
                select(AccountExport)
                .where(AccountExport.id == work.export_id)
                .with_for_update()
            )
            if record is None or record.user_id != user_id:
                raise AccountLifecycleFailure(
                    "account_export_context_unavailable", retryable=False
                )
            if record.expires_at <= datetime.now(UTC):
                record.state, record.failure_code = "expired", "account_export_expired"
                await uow.commit()
                raise AccountLifecycleFailure("account_export_expired", retryable=False)
            record.state, record.storage_provider, record.bucket, record.object_key = (
                "ready",
                asset.provider,
                asset.bucket,
                asset.object_key,
            )
            record.manifest_sha256, record.ready_at = checksum, datetime.now(UTC)
            await uow.commit()

    async def _delete(
        self, job_id: uuid.UUID, user_id: uuid.UUID, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        request_id = _uuid_payload(payload, "deletion_request_id")
        context = await self._deletion_context(request_id, job_id, user_id)
        try:
            await self._ports.release_gate.require_authorized(
                deletion_request_id=request_id, subject_hash=context.subject_hash
            )
        except AccountLifecycleFailure:
            raise
        except Exception as exc:
            raise AccountLifecycleFailure(
                "erasure_release_not_authorized", retryable=False
            ) from exc
        await self._run_step(
            request_id,
            "identity_revoked",
            lambda: self._ports.identity.revoke_and_delete(
                auth_provider=context.user.auth_provider,
                auth_subject=context.user.auth_subject,
            ),
        )
        await self._run_step(
            request_id,
            "private_storage_erased",
            lambda: self._ports.storage.delete_user_objects(user_id=user_id),
        )
        await self._run_step(
            request_id,
            "runtime_checkpoints_erased",
            lambda: self._ports.checkpoints.delete_user_runtime(user_id=user_id),
        )
        await self._run_step(
            request_id,
            "cache_erased",
            lambda: self._ports.cache.purge_user(user_id=user_id),
        )
        await self._erase_postgres(request_id, user_id)
        return {"deletion_request_id": str(request_id), "state": "completed"}

    async def _deletion_context(
        self, request_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID
    ) -> DeletionContext:
        async with self._uow_factory() as uow:
            request = await uow.session.scalar(
                select(DeletionRequest)
                .where(DeletionRequest.id == request_id)
                .with_for_update()
            )
            user = await uow.session.get(User, user_id, with_for_update=True)
            if (
                request is None
                or user is None
                or request.user_id != user_id
                or request.generation_job_id != job_id
            ):
                raise AccountLifecycleFailure(
                    "account_deletion_context_unavailable", retryable=False
                )
            if request.state == "completed":
                raise AccountLifecycleFailure(
                    "account_deletion_context_unavailable", retryable=False
                )
            request.state, request.current_step, request.attempt_count = (
                "running",
                None,
                request.attempt_count + 1,
            )
            await uow.commit()
            return DeletionContext(user=user, subject_hash=request.subject_hash)

    async def _run_step(
        self,
        request_id: uuid.UUID,
        step_name: str,
        action: Callable[[], Awaitable[None]],
    ) -> None:
        async with self._uow_factory() as uow:
            step_position = ERASURE_STEPS.index(step_name)
            if step_position:
                previous_steps = ERASURE_STEPS[:step_position]
                verified_count = await uow.session.scalar(
                    select(func.count())
                    .select_from(DeletionStep)
                    .where(
                        DeletionStep.deletion_request_id == request_id,
                        DeletionStep.step_name.in_(previous_steps),
                        DeletionStep.state == "verified",
                    )
                )
                if verified_count != len(previous_steps):
                    raise AccountLifecycleFailure(
                        "deletion_step_order_violation", retryable=False
                    )
            step = await uow.session.scalar(
                select(DeletionStep)
                .where(
                    DeletionStep.deletion_request_id == request_id,
                    DeletionStep.step_name == step_name,
                )
                .with_for_update()
            )
            if step is None:
                raise AccountLifecycleFailure("deletion_step_missing", retryable=False)
            if step.state == "verified":
                await uow.commit()
                return
            step.state, step.attempt_count, step.error_code = (
                "running",
                step.attempt_count + 1,
                None,
            )
            request = await uow.session.get(DeletionRequest, request_id)
            if request is None:
                raise RuntimeError("account_deletion_context_unavailable")
            request.current_step, request.state = step_name, "running"
            await uow.commit()
        try:
            await action()
        except AccountLifecycleFailure as exc:
            await self._record_step_failure(request_id, step_name, exc.code)
            raise
        except Exception as exc:
            failure = AccountLifecycleFailure("provider_step_failed", retryable=True)
            await self._record_step_failure(request_id, step_name, failure.code)
            raise failure from exc
        async with self._uow_factory() as uow:
            step = await uow.session.scalar(
                select(DeletionStep)
                .where(
                    DeletionStep.deletion_request_id == request_id,
                    DeletionStep.step_name == step_name,
                )
                .with_for_update()
            )
            if step is None:
                raise AccountLifecycleFailure("deletion_step_missing", retryable=False)
            step.state, step.verified_at, step.error_code = (
                "verified",
                datetime.now(UTC),
                None,
            )
            request = await uow.session.get(DeletionRequest, request_id)
            if request is not None:
                request.current_step, request.last_error_code = None, None
            await uow.commit()

    async def _erase_postgres(self, request_id: uuid.UUID, user_id: uuid.UUID) -> None:
        # This is a deliberately named, migration-owned trusted procedure.  It
        # is not an application-level FK/trigger bypass and cannot erase an
        # arbitrary table or subject.
        try:
            async with self._uow_factory() as uow:
                await uow.session.execute(
                    text("SELECT ops.erase_account_graph(:request_id, :user_id)"),
                    {
                        "request_id": request_id,
                        "user_id": user_id,
                    },
                )
                await uow.commit()
        except AccountLifecycleFailure:
            raise
        except Exception as exc:
            await self._record_step_failure(
                request_id, "postgres_graph_erased", "postgres_erasure_failed"
            )
            raise AccountLifecycleFailure(
                "postgres_erasure_failed", retryable=True
            ) from exc

    async def _record_step_failure(
        self, request_id: uuid.UUID, step_name: str, code: str
    ) -> None:
        async with self._uow_factory() as uow:
            step = await uow.session.scalar(
                select(DeletionStep)
                .where(
                    DeletionStep.deletion_request_id == request_id,
                    DeletionStep.step_name == step_name,
                )
                .with_for_update()
            )
            if step is not None and step.state != "verified":
                step.state, step.error_code = "failed", code
            request = await uow.session.get(DeletionRequest, request_id)
            if request is not None and request.state != "completed":
                request.state, request.last_error_code, request.current_step = (
                    "retry_wait",
                    code,
                    step_name,
                )
            await uow.commit()

    async def _record_lifecycle_failure(
        self, job: Any, failure: AccountLifecycleFailure
    ) -> None:
        """Persist only a stable code; no provider exception or health content."""

        if job.job_type == "account_export":
            export_id = _try_uuid_payload(job.request_payload, "export_id")
            if export_id is None:
                return
            async with self._uow_factory() as uow:
                record = await uow.session.scalar(
                    select(AccountExport)
                    .where(AccountExport.id == export_id)
                    .with_for_update()
                )
                if record is not None and record.state not in {"ready", "expired"}:
                    record.failure_code = failure.code
                    if not failure.retryable:
                        record.state = "failed"
                await uow.commit()
        elif job.job_type == "account_deletion":
            request_id = _try_uuid_payload(job.request_payload, "deletion_request_id")
            if request_id is None:
                return
            async with self._uow_factory() as uow:
                request = await uow.session.get(DeletionRequest, request_id)
                if request is not None and request.state != "completed":
                    request.last_error_code = failure.code
                    request.state = "retry_wait" if failure.retryable else "failed"
                await uow.commit()


def _uuid_payload(payload: Mapping[str, Any], name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[name]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountLifecycleFailure(
            "invalid_account_lifecycle_payload", retryable=False
        ) from exc


def _try_uuid_payload(payload: Mapping[str, Any], name: str) -> uuid.UUID | None:
    try:
        return _uuid_payload(payload, name)
    except AccountLifecycleFailure:
        return None


def _valid_private_export_asset(asset: object) -> bool:
    """Reject public URLs, path traversal, and malformed storage references."""

    if not isinstance(asset, PrivateExportAsset):
        return False
    values = (asset.provider, asset.bucket, asset.object_key)
    return (
        all(isinstance(value, str) and value.strip() for value in values)
        and "//" not in asset.object_key
        and ".." not in asset.object_key.split("/")
        and not asset.object_key.startswith("/")
        and "http:" not in asset.object_key.lower()
        and "https:" not in asset.object_key.lower()
    )
