"""Account lifecycle command contracts that do not require real providers."""
from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

import app.v2.api.routes.account as account_routes
from app.v2.application.account_lifecycle import (
    AccountLifecycleFailure,
    ERASURE_STEPS,
    _uuid_payload,
    request_account_deletion,
    request_account_export,
)
from app.v2.domain.enums import UserStatus
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.infrastructure.account_lifecycle import (
    AesGcmAccountExportCipher,
    EnvironmentHmacSubjectFingerprint,
    FailClosedErasureReleaseGate,
    StoredObjectReference,
    SupabasePrivateAccountStorage,
)
from app.v2.infrastructure.worker import PostgresJobWorker
from app.v2.persistence.models import User


NOW = datetime(2026, 8, 8, tzinfo=UTC)
PRINCIPAL = VerifiedPrincipal("firebase", "owner", None, False, None, NOW)


class _AllowRecent:
    async def require_recent(self, principal) -> None:
        assert principal is PRINCIPAL


class _Users:
    def __init__(self, user) -> None:
        self.user = user

    async def get_by_subject(self, _, subject, *, for_update=False):
        assert for_update
        return self.user if subject == "owner" else None


class _Idempotency:
    def __init__(self) -> None:
        self.records = {}

    async def reserve(self, record, *, now):
        key = (record.scope, record.subject, record.idempotency_key)
        existing = self.records.get(key)
        if existing is None:
            self.records[key] = record
            return record, True
        return existing, False


class _Session:
    def __init__(self) -> None:
        self.added = []

    def add(self, item) -> None:
        self.added.append(item)

    async def scalar(self, statement):
        del statement
        return None


class _Outbox:
    def __init__(self) -> None:
        self.events = []

    def add(self, event) -> None:
        self.events.append(event)


class _Uow:
    def __init__(self) -> None:
        self.user = User(
            id=uuid4(),
            auth_provider="firebase",
            auth_subject="owner",
            status=UserStatus.ACTIVE.value,
        )
        self.users = _Users(self.user)
        self.idempotency = _Idempotency()
        self.session = _Session()
        self.outbox = _Outbox()
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.anyio
async def test_deletion_replays_after_pending_and_links_its_job() -> None:
    uow = _Uow()
    fingerprint = EnvironmentHmacSubjectFingerprint(
        "test-only-receipt-secret-with-32-bytes"
    )
    first = await request_account_deletion(
        uow,
        principal=PRINCIPAL,
        key="deletion-key-0001",
        recent_authentication=_AllowRecent(),
        subject_fingerprint=fingerprint,
        now=NOW,
    )
    replay = await request_account_deletion(
        uow,
        principal=PRINCIPAL,
        key="deletion-key-0001",
        recent_authentication=_AllowRecent(),
        subject_fingerprint=fingerprint,
        now=NOW,
    )
    request = next(
        item for item in uow.session.added if item.__tablename__ == "deletion_requests"
    )
    steps = [
        item for item in uow.session.added if item.__tablename__ == "deletion_steps"
    ]
    assert replay == first
    assert request.generation_job_id == first.job_id
    assert [step.step_name for step in steps] == list(ERASURE_STEPS)
    assert request.subject_hash != PRINCIPAL.subject
    assert len(request.subject_hash) == 64


@pytest.mark.anyio
async def test_export_accepts_no_fingerprint_and_never_returns_an_object_url() -> None:
    uow = _Uow()
    result = await request_account_export(
        uow,
        principal=PRINCIPAL,
        key="export-key-0001",
        recent_authentication=_AllowRecent(),
        now=NOW,
    )
    assert (
        "subject_fingerprint"
        not in inspect.signature(request_account_export).parameters
    )
    assert "url" not in result.model_dump(mode="json")
    assert result.expires_at > NOW


def test_export_envelope_is_versioned_authenticated_and_binds_its_export_id() -> None:
    key = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    cipher = AesGcmAccountExportCipher(key)
    export_id = uuid4()
    payload = cipher.encrypt(export_id=export_id, plaintext=b'{"private":"health"}')
    assert payload.startswith(AesGcmAccountExportCipher.PREFIX)
    assert b"health" not in payload
    assert (
        cipher.decrypt_for_test(export_id=export_id, payload=payload)
        == b'{"private":"health"}'
    )
    with pytest.raises(Exception):
        cipher.decrypt_for_test(export_id=uuid4(), payload=payload)


@pytest.mark.anyio
async def test_missing_erasure_release_gate_is_terminal_and_never_calls_provider() -> None:
    with pytest.raises(AccountLifecycleFailure, match="erasure_release_not_authorized"):
        await FailClosedErasureReleaseGate().require_authorized(
            deletion_request_id=uuid4(), subject_hash="a" * 64
        )
    failure = AccountLifecycleFailure("erasure_release_not_authorized", retryable=True)
    assert PostgresJobWorker._error_code(failure) == "erasure_release_not_authorized"
    assert PostgresJobWorker._retryable(failure)


def test_malformed_lifecycle_payload_is_a_stable_terminal_failure() -> None:
    with pytest.raises(
        AccountLifecycleFailure, match="invalid_account_lifecycle_payload"
    ) as raised:
        _uuid_payload({}, "export_id")

    assert raised.value.code == "invalid_account_lifecycle_payload"
    assert PostgresJobWorker._error_code(raised.value) == raised.value.code
    assert not PostgresJobWorker._retryable(raised.value)


def test_disabled_deletion_route_fails_before_a_request_can_be_durable(monkeypatch):
    monkeypatch.setattr(account_routes.settings, "V2_DELETION_ENABLED", False)
    with pytest.raises(Exception) as raised:
        account_routes.require_account_deletion_enabled()
    assert raised.value.status == 503
    assert raised.value.code == "account_deletion_unavailable"


@pytest.mark.anyio
async def test_private_storage_uses_no_public_url_and_erasure_only_uses_owned_refs() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201 if request.method == "POST" else 404)

    class References:
        async def references_for_user(self, *, user_id):
            assert user_id
            return (StoredObjectReference("plan-images", "plans/v2/a.png"),)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    storage = SupabasePrivateAccountStorage(
        project_url="https://project.supabase.co",
        service_role_key="service-role-secret",
        export_bucket="private-exports",
        user_object_source=References(),
        client=client,
    )
    export_id = uuid4()
    asset = await storage.put_export(
        export_id=export_id,
        content=b"encrypted",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    await storage.delete_user_objects(user_id=uuid4())
    await client.aclose()
    assert asset.object_key == f"exports/v1/{export_id}.bin"
    assert all("/object/public/" not in str(request.url) for request in seen)
    assert seen[0].headers["x-upsert"] == "false"
    assert seen[1].method == "DELETE"
