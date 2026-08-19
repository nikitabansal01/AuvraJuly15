"""Concrete, fail-closed account-lifecycle adapters.

These adapters are deliberately separate from the workflow state machine.  A
composition root must supply every port explicitly; there is no default that
pretends Firebase, object storage, LangGraph checkpoints, or Redis have been
erased.  Provider errors are converted to stable codes and never logged here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.concurrency import run_in_threadpool
from firebase_admin import auth, exceptions
from sqlalchemy import text

from app.v2.application.account_lifecycle import (
    AccountExportBuilder,
    AccountLifecycleFailure,
    ErasureReleaseGate,
    FirebaseIdentityEraser,
    PrivateExportAsset,
    PrivateExportStorage,
    RecentAuthenticationPolicy,
    RuntimeCheckpointEraser,
    SubjectFingerprint,
    UserCacheEraser,
)
from app.v2.application.errors import forbidden, service_unavailable
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


class FirebaseRecentAuthenticationPolicy(RecentAuthenticationPolicy):
    """Require the signed Firebase ``auth_time`` fact to be genuinely recent."""

    def __init__(self, *, maximum_age: timedelta = timedelta(minutes=5)) -> None:
        self._maximum_age = maximum_age

    async def require_recent(self, principal: VerifiedPrincipal) -> None:
        authenticated_at = principal.authenticated_at
        if authenticated_at is None:
            raise service_unavailable(
                "recent_authentication_unavailable",
                "Account export and deletion require a verifiable recent authentication claim.",
            )
        now = datetime.now(UTC)
        if (
            authenticated_at > now + timedelta(minutes=1)
            or now - authenticated_at > self._maximum_age
        ):
            raise forbidden(
                "recent_authentication_required",
                "Please sign in again before exporting or deleting this account.",
            )


class EnvironmentHmacSubjectFingerprint(SubjectFingerprint):
    """HMAC a provider subject with a required, rotation-capable secret."""

    ENVIRONMENT_VARIABLE = "AUVRA_DELETION_RECEIPT_HMAC_KEY"

    def __init__(self, secret: str | None = None) -> None:
        configured = secret if secret is not None else os.getenv(self.ENVIRONMENT_VARIABLE)
        if not configured or len(configured.encode("utf-8")) < 32:
            raise service_unavailable(
                "deletion_receipt_key_unavailable",
                "Account deletion is unavailable until a 32-byte receipt key is configured.",
            )
        self._key = configured.encode("utf-8")

    def fingerprint(self, *, auth_provider: str, auth_subject: str) -> str:
        value = f"{auth_provider}:{auth_subject}".encode("utf-8")
        return hmac.new(self._key, value, hashlib.sha256).hexdigest()


class FailClosedErasureReleaseGate(ErasureReleaseGate):
    """Block irreversible deletion until an owner/legal policy is wired.

    This is intentional.  A request is durable, but no external erasure occurs
    merely because an environment happened to start a worker.
    """

    async def require_authorized(
        self, *, deletion_request_id: uuid.UUID, subject_hash: str
    ) -> None:
        del deletion_request_id, subject_hash
        # A request remains tombstoned/retryable while the documented approval
        # is pending.  No provider call is made, and an operator can requeue
        # the durable job after the gate is configured.
        raise AccountLifecycleFailure("erasure_release_not_authorized", retryable=True)


class DocumentedErasureReleaseGate(ErasureReleaseGate):
    """A composition-time acknowledgement of the approved retention decision.

    ``approval_reference`` is deliberately an opaque, non-user identifier
    (for example, an approved change/retention decision reference).  It is not
    stored in user data or job results.  The deployment owner must only supply
    it after legal-hold and retention policy review; tests use a fake gate.
    """

    def __init__(self, *, approval_reference: str) -> None:
        normalized = approval_reference.strip()
        if len(normalized) < 8 or len(normalized) > 128:
            raise ValueError("A documented erasure approval reference is required")
        self._approval_reference = normalized

    async def require_authorized(
        self, *, deletion_request_id: uuid.UUID, subject_hash: str
    ) -> None:
        del deletion_request_id, subject_hash
        # Construction is the explicit deployment gate.  Do not log the value:
        # it can identify an internal incident or legal matter.
        if not self._approval_reference:
            raise AccountLifecycleFailure("erasure_release_not_authorized", retryable=False)


class FirebaseAdminIdentityEraser(FirebaseIdentityEraser):
    """Idempotently revoke and remove one Firebase identity.

    The absent-user result is success: a worker can die after Firebase deletes
    the identity but before it records its step ledger entry.
    """

    async def revoke_and_delete(self, *, auth_provider: str, auth_subject: str) -> None:
        if auth_provider != "firebase" or not auth_subject.strip():
            raise AccountLifecycleFailure("identity_eraser_subject_invalid", retryable=False)
        try:
            await run_in_threadpool(lambda: auth.revoke_refresh_tokens(auth_subject))
            await run_in_threadpool(lambda: auth.delete_user(auth_subject))
        except auth.UserNotFoundError:
            return
        except _FIREBASE_RETRYABLE_ERRORS as exc:
            raise AccountLifecycleFailure("firebase_identity_unavailable", retryable=True) from exc
        except (auth.InvalidUidError, auth.UidAlreadyExistsError) as exc:
            raise AccountLifecycleFailure(
                "identity_eraser_subject_invalid", retryable=False
            ) from exc
        except Exception as exc:
            raise AccountLifecycleFailure("firebase_identity_erase_failed", retryable=True) from exc


class AesGcmAccountExportCipher:
    """Versioned AEAD envelope for the private export object.

    The storage reference is private *and* the bytes are encrypted before
    upload.  The caller supplies a dedicated 32-byte base64url key, normally
    from the secret manager at worker start; it is never retained in the DB.
    """

    PREFIX = b"AUVRA-ACCOUNT-EXPORT/v1\n"

    def __init__(self, key: str | bytes) -> None:
        try:
            raw = (
                key
                if isinstance(key, bytes)
                else base64.urlsafe_b64decode(_padded_base64(key.strip()))
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("Account export encryption key is invalid") from exc
        if len(raw) != 32:
            raise ValueError("Account export encryption key must be 32 bytes")
        self._cipher = AESGCM(raw)

    def encrypt(self, *, export_id: uuid.UUID, plaintext: bytes) -> bytes:
        nonce = os.urandom(12)
        associated_data = self.PREFIX + export_id.bytes
        return self.PREFIX + nonce + self._cipher.encrypt(nonce, plaintext, associated_data)

    def decrypt_for_test(self, *, export_id: uuid.UUID, payload: bytes) -> bytes:
        """Test-only verifier; delivery must use a separately authorized path."""

        if not payload.startswith(self.PREFIX) or len(payload) <= len(self.PREFIX) + 12:
            raise ValueError("Account export envelope is invalid")
        nonce_start = len(self.PREFIX)
        nonce = payload[nonce_start : nonce_start + 12]
        encrypted = payload[nonce_start + 12 :]
        return self._cipher.decrypt(nonce, encrypted, self.PREFIX + export_id.bytes)


UowFactory = Callable[[], SqlAlchemyUnitOfWork]


class CanonicalAccountExportBuilder(AccountExportBuilder):
    """Build one versioned, deterministic export from explicit owned tables.

    Queries intentionally enumerate user-owned data instead of dumping schemas
    or operational jobs.  Provider prompts, raw logs, credentials and secret
    values never become part of the export.  The result is encrypted before it
    leaves process memory.
    """

    def __init__(
        self,
        *,
        cipher: AesGcmAccountExportCipher,
        uow_factory: UowFactory = SqlAlchemyUnitOfWork,
    ) -> None:
        self._cipher = cipher
        self._uow_factory = uow_factory

    async def build(self, *, export_id: uuid.UUID, user_id: uuid.UUID) -> tuple[bytes, str]:
        datasets: dict[str, list[dict[str, Any]]] = {}
        async with self._uow_factory() as uow:
            if uow.session is None:
                raise AccountLifecycleFailure("account_export_context_unavailable", retryable=False)
            for name, statement in _EXPORT_QUERIES:
                rows = await uow.session.execute(text(statement), {"user_id": user_id})
                datasets[name] = [_json_safe(dict(row)) for row in rows.mappings().all()]
            await uow.commit()
        payload = {
            "format": "auvra.account-export.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "datasets": datasets,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        encrypted = self._cipher.encrypt(export_id=export_id, plaintext=encoded)
        return encrypted, hashlib.sha256(encrypted).hexdigest()


class SupabasePrivateAccountStorage(PrivateExportStorage):
    """Private Supabase Storage adapter for encrypted exports and owned media.

    It records no URL and accepts only a dedicated export bucket.  Erasure
    deletes the explicit references found in canonical DB records, not a broad
    bucket prefix that could affect another account.
    """

    def __init__(
        self,
        *,
        project_url: str,
        service_role_key: str,
        export_bucket: str,
        user_object_source: "UserObjectReferenceSource",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if (
            not project_url.startswith("https://")
            or not service_role_key.strip()
            or not _valid_bucket(export_bucket)
        ):
            raise ValueError("Private Supabase export storage credentials are required")
        self._project_url = project_url.rstrip("/")
        self._service_role_key = service_role_key
        self._export_bucket = export_bucket
        self._object_source = user_object_source
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(45.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def put_export(
        self, *, export_id: uuid.UUID, content: bytes, expires_at: datetime
    ) -> PrivateExportAsset:
        if expires_at <= datetime.now(UTC) or not content:
            raise AccountLifecycleFailure("account_export_invalid_payload", retryable=False)
        object_key = f"exports/v1/{export_id}.bin"
        path = self._object_path(self._export_bucket, object_key)
        try:
            response = await self._client.post(
                f"{self._project_url}/storage/v1/object/{path}",
                headers={
                    **self._headers(),
                    "Content-Type": "application/octet-stream",
                    "x-upsert": "false",
                },
                content=content,
            )
        except httpx.TimeoutException as exc:
            raise AccountLifecycleFailure("account_export_storage_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AccountLifecycleFailure("account_export_storage_network", retryable=True) from exc
        if response.status_code in {400, 409}:
            await self._verify_existing(
                bucket=self._export_bucket,
                object_key=object_key,
                expected_sha256=hashlib.sha256(content).hexdigest(),
            )
        elif response.status_code not in {200, 201}:
            raise _storage_failure(response.status_code, "account_export_storage_write")
        return PrivateExportAsset(
            provider="supabase", bucket=self._export_bucket, object_key=object_key
        )

    async def delete_user_objects(self, *, user_id: uuid.UUID) -> None:
        references = await self._object_source.references_for_user(user_id=user_id)
        for reference in references:
            if not _valid_bucket(reference.bucket) or not _valid_object_key(reference.object_key):
                raise AccountLifecycleFailure("account_storage_reference_invalid", retryable=False)
            try:
                object_url = (
                    f"{self._project_url}/storage/v1/object/"
                    f"{self._object_path(reference.bucket, reference.object_key)}"
                )
                response = await self._client.request(
                    "DELETE",
                    object_url,
                    headers=self._headers(),
                )
            except httpx.TimeoutException as exc:
                raise AccountLifecycleFailure(
                    "account_storage_delete_timeout", retryable=True
                ) from exc
            except httpx.HTTPError as exc:
                raise AccountLifecycleFailure(
                    "account_storage_delete_network", retryable=True
                ) from exc
            if response.status_code not in {200, 204, 404}:
                raise _storage_failure(response.status_code, "account_storage_delete")

    async def _verify_existing(self, *, bucket: str, object_key: str, expected_sha256: str) -> None:
        try:
            response = await self._client.get(
                f"{self._project_url}/storage/v1/object/{self._object_path(bucket, object_key)}",
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise AccountLifecycleFailure(
                "account_export_storage_verify_timeout", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise AccountLifecycleFailure(
                "account_export_storage_verify_network", retryable=True
            ) from exc
        if response.status_code != 200:
            raise _storage_failure(response.status_code, "account_export_storage_verify")
        if not hmac.compare_digest(hashlib.sha256(response.content).hexdigest(), expected_sha256):
            raise AccountLifecycleFailure(
                "account_export_storage_existing_mismatch", retryable=False
            )

    def _object_path(self, bucket: str, object_key: str) -> str:
        return quote(f"{bucket}/{object_key}", safe="/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._service_role_key}",
            "apikey": self._service_role_key,
        }


@dataclass(frozen=True, slots=True)
class StoredObjectReference:
    bucket: str
    object_key: str


class UserObjectReferenceSource:
    async def references_for_user(self, *, user_id: uuid.UUID) -> Sequence[StoredObjectReference]:
        raise NotImplementedError


class PostgresUserObjectReferenceSource(UserObjectReferenceSource):
    """Find only canonical media/export references owned by one user."""

    def __init__(self, *, uow_factory: UowFactory = SqlAlchemyUnitOfWork) -> None:
        self._uow_factory = uow_factory

    async def references_for_user(self, *, user_id: uuid.UUID) -> Sequence[StoredObjectReference]:
        async with self._uow_factory() as uow:
            if uow.session is None:
                raise AccountLifecycleFailure(
                    "account_storage_reference_unavailable", retryable=True
                )
            rows = await uow.session.execute(
                text(
                    """
                    SELECT bucket, object_key FROM app.media_assets
                    WHERE owner_user_id = :user_id
                    UNION
                    SELECT bucket, object_key FROM ops.account_exports
                    WHERE user_id = :user_id
                      AND bucket IS NOT NULL AND object_key IS NOT NULL
                    """
                ),
                {"user_id": user_id},
            )
            await uow.commit()
        return tuple(
            StoredObjectReference(bucket=row.bucket, object_key=row.object_key) for row in rows
        )


class FailClosedRuntimeCheckpointEraser(RuntimeCheckpointEraser):
    """Prevent false completion until runtime checkpoint ownership is configured."""

    async def delete_user_runtime(self, *, user_id: uuid.UUID) -> None:
        del user_id
        raise AccountLifecycleFailure("runtime_checkpoint_eraser_unavailable", retryable=False)


class PostgresRuntimeCheckpointEraser(RuntimeCheckpointEraser):
    """Erase only the installed LangGraph v2 user-thread namespace.

    These are vendor tables in the database's dedicated v2 runtime namespace
    (the PostgreSQL checkpointer's default ``public`` schema).  We recognize
    the exact currently pinned checkpointer layout before writing anything.
    ``checkpoint_migrations`` is global vendor state and is inspected but
    never deleted.
    """

    _SCHEMA = "public"
    _DELETE_ORDER = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
    _EXPECTED_COLUMNS = {
        "checkpoint_migrations": {"v"},
        "checkpoints": {
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "parent_checkpoint_id",
            "type",
            "checkpoint",
            "metadata",
        },
        "checkpoint_blobs": {
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
            "type",
            "blob",
        },
        "checkpoint_writes": {
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "task_path",
            "idx",
            "channel",
            "type",
            "blob",
        },
    }

    def __init__(self, *, uow_factory: UowFactory = SqlAlchemyUnitOfWork) -> None:
        self._uow_factory = uow_factory

    async def delete_user_runtime(self, *, user_id: uuid.UUID) -> None:
        namespace = f"user:{user_id}:"
        async with self._uow_factory() as uow:
            if uow.session is None:
                raise AccountLifecycleFailure(
                    "runtime_checkpoint_schema_unavailable", retryable=True
                )
            rows = await uow.session.execute(
                text(
                    """SELECT table_name, column_name FROM information_schema.columns
                    WHERE table_schema = :schema
                      AND table_name IN ('checkpoint_migrations', 'checkpoints',
                                         'checkpoint_blobs', 'checkpoint_writes')
                    ORDER BY table_name, ordinal_position"""
                ),
                {"schema": self._SCHEMA},
            )
            observed: dict[str, set[str]] = {name: set() for name in self._EXPECTED_COLUMNS}
            for row in rows:
                observed[row.table_name].add(row.column_name)
            if observed != self._EXPECTED_COLUMNS:
                raise AccountLifecycleFailure(
                    "runtime_checkpoint_schema_unrecognized", retryable=False
                )
            for table_name in self._DELETE_ORDER:
                await uow.session.execute(
                    text(
                        f"DELETE FROM {self._SCHEMA}.{table_name} "
                        "WHERE thread_id LIKE (:namespace || '%')"
                    ),
                    {"namespace": namespace},
                )
            remaining = await uow.session.scalar(
                text(
                    """SELECT count(*) FROM (
                    SELECT thread_id FROM public.checkpoint_writes WHERE thread_id LIKE (:namespace || '%')
                    UNION ALL SELECT thread_id FROM public.checkpoint_blobs WHERE thread_id LIKE (:namespace || '%')
                    UNION ALL SELECT thread_id FROM public.checkpoints WHERE thread_id LIKE (:namespace || '%')
                    ) erased"""
                ),
                {"namespace": namespace},
            )
            if remaining:
                raise AccountLifecycleFailure("runtime_checkpoint_erase_unverified", retryable=True)
            await uow.commit()


class RedisUserCacheEraser(UserCacheEraser):
    """Erase only the documented, UID-scoped v2 cache namespace."""

    def __init__(self, redis_client: Any, *, prefix: str = "auvra:v2:user") -> None:
        if not prefix or "*" in prefix:
            raise ValueError("Cache erasure prefix must be an exact namespace")
        self._redis = redis_client
        self._prefix = prefix.rstrip(":")

    async def purge_user(self, *, user_id: uuid.UUID) -> None:
        pattern = f"{self._prefix}:{user_id}:*"
        try:
            keys = [key async for key in self._redis.scan_iter(match=pattern, count=100)]
            if keys:
                await self._redis.unlink(*keys)
        except Exception as exc:
            raise AccountLifecycleFailure("account_cache_erase_failed", retryable=True) from exc


class FailClosedUserCacheEraser(UserCacheEraser):
    """Keep an unapproved deletion lane from claiming cache erasure."""

    async def purge_user(self, *, user_id: uuid.UUID) -> None:
        del user_id
        raise AccountLifecycleFailure("account_cache_eraser_unavailable", retryable=False)


def _padded_base64(value: str) -> str:
    return value + "=" * (-len(value) % 4)


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Decimal):
        # Exported as a string so an exact decimal never becomes a lossy float
        # on the way out of the user's own data.
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _valid_bucket(bucket: str) -> bool:
    return bool(bucket) and "/" not in bucket and ".." not in bucket


def _valid_object_key(object_key: str) -> bool:
    return bool(object_key) and not object_key.startswith("/") and ".." not in object_key.split("/")


def _storage_failure(status_code: int, operation: str) -> AccountLifecycleFailure:
    retryable = status_code in {408, 429, 500, 502, 503, 504}
    return AccountLifecycleFailure(operation, retryable=retryable)


_FIREBASE_RETRYABLE_ERRORS = (
    exceptions.AbortedError,
    exceptions.DeadlineExceededError,
    exceptions.InternalError,
    exceptions.ResourceExhaustedError,
    exceptions.UnavailableError,
    exceptions.UnknownError,
)


# Each statement has an explicit owner predicate.  Global research definitions,
# telemetry and operational jobs are excluded by design.
_EXPORT_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "account",
        "SELECT id, email, email_verified, status, created_at, updated_at "
        "FROM app.users WHERE id = :user_id",
    ),
    (
        "profile",
        "SELECT user_id, display_name, timezone, locale, version, created_at, updated_at FROM app.user_profiles WHERE user_id = :user_id",
    ),
    (
        "consents",
        "SELECT id, consent_type, document_version, granted, decided_at FROM app.consent_records WHERE user_id = :user_id ORDER BY decided_at, id",
    ),
    (
        "onboarding_assessments",
        "SELECT id, session_id, version, schema_version, timezone, answers, is_current, validated_at, created_at, updated_at FROM app.onboarding_assessments WHERE user_id = :user_id ORDER BY validated_at, id",
    ),
    (
        "plans",
        "SELECT id, local_date, timezone, revision, is_current, status, cycle_snapshot, context_snapshot, published_at, created_at, updated_at FROM app.action_plans WHERE user_id = :user_id ORDER BY local_date, revision",
    ),
    (
        "plan_items",
        "SELECT item.id, item.plan_id, item.slot, item.category, item.title, item.purpose, item.instructions, item.status, item.supersedes_item_id, item.created_at, item.updated_at FROM app.action_plan_items item JOIN app.action_plans plan ON plan.id = item.plan_id WHERE plan.user_id = :user_id ORDER BY item.plan_id, item.slot",
    ),
    (
        "plan_item_variants",
        "SELECT variant.id, variant.item_id, variant.variant_type, variant.content, variant.created_at, variant.updated_at FROM app.action_plan_item_variants variant JOIN app.action_plan_items item ON item.id = variant.item_id JOIN app.action_plans plan ON plan.id = item.plan_id WHERE plan.user_id = :user_id ORDER BY variant.item_id, variant.variant_type",
    ),
    (
        "action_events",
        "SELECT id, plan_item_id, client_event_id, event_type, occurred_at, decision_local_date, decision_timezone, payload, recorded_at FROM app.action_item_events WHERE user_id = :user_id ORDER BY occurred_at, id",
    ),
    (
        "daily_reviews",
        "SELECT id, plan_id, local_date, timezone, status, completed_at, created_at, updated_at FROM app.daily_reviews WHERE user_id = :user_id ORDER BY local_date, id",
    ),
    (
        "daily_review_items",
        "SELECT item.id, item.daily_review_id, item.plan_item_id, item.outcome, item.note, item.answered_at FROM app.daily_review_items item JOIN app.daily_reviews review ON review.id = item.daily_review_id WHERE review.user_id = :user_id ORDER BY item.daily_review_id, item.id",
    ),
    (
        "plan_refreshes",
        "SELECT id, idempotency_key, reason, local_date, timezone, requested_at, completed_plan_id, old_item_id, new_item_id, accepted_at, created_at, updated_at FROM app.plan_refreshes WHERE user_id = :user_id ORDER BY requested_at, id",
    ),
    (
        "streak_days",
        "SELECT id, local_date, kind, timezone, evidence_type, evidence_id, adjudication_state, earned_at FROM app.streak_days WHERE user_id = :user_id ORDER BY local_date, id",
    ),
    (
        "reward_ledger",
        "SELECT id, source_type, source_id, event_type, asset_type, quantity, created_at FROM app.reward_ledger WHERE user_id = :user_id ORDER BY created_at, id",
    ),
    (
        "conversations",
        "SELECT id, status, thread_type, revision, created_at, updated_at FROM app.conversations WHERE user_id = :user_id ORDER BY created_at, id",
    ),
    (
        "conversation_messages",
        "SELECT message.id, message.conversation_id, message.sequence, message.role, message.content, message.client_message_id, message.metadata_json, message.created_at FROM app.conversation_messages message JOIN app.conversations conversation ON conversation.id = message.conversation_id WHERE conversation.user_id = :user_id ORDER BY message.conversation_id, message.sequence",
    ),
    (
        "conversation_summaries",
        "SELECT summary.id, summary.conversation_id, summary.through_message_id, summary.summary, summary.created_at FROM app.conversation_summaries summary JOIN app.conversations conversation ON conversation.id = summary.conversation_id WHERE conversation.user_id = :user_id ORDER BY summary.conversation_id, summary.created_at",
    ),
    (
        "weekly_checkins",
        "SELECT id, week_start, definition_version, timezone, conversation_id, revision, completed_at, created_at, updated_at FROM app.weekly_checkins WHERE user_id = :user_id ORDER BY week_start, id",
    ),
    (
        "weekly_checkin_responses",
        "SELECT response.id, response.weekly_checkin_id, response.question_id, response.answer, response.answered_at FROM app.weekly_checkin_responses response JOIN app.weekly_checkins checkin ON checkin.id = response.weekly_checkin_id WHERE checkin.user_id = :user_id ORDER BY response.weekly_checkin_id, response.id",
    ),
    (
        "user_observations",
        "SELECT id, observation_type, code, catalog_version, observed_at, observed_local_date, observed_timezone, value_numeric, value_unit, value_codes, value_text, source, supersedes_id, note, recorded_at FROM app.user_observations WHERE user_id = :user_id ORDER BY observed_at, id",
    ),
    (
        "media_assets",
        "SELECT id, storage_provider, bucket, object_key, content_sha256, mime_type, alt_text, width, height, status, created_at, updated_at FROM app.media_assets WHERE owner_user_id = :user_id ORDER BY created_at, id",
    ),
)
