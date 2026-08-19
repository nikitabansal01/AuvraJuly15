"""PostgreSQL 17 proofs for durable export and account erasure recovery."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="PostgreSQL account-lifecycle tests require AUVRA_TEST_DATABASE_URL",
)


def _async_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.v2.persistence.database import _async_database_url

    engine = create_async_engine(_async_database_url(os.environ["AUVRA_TEST_DATABASE_URL"]))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create_user(*, subject_prefix: str) -> tuple[uuid.UUID, str]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.v2.persistence.database import _async_database_url

    user_id = uuid.uuid4()
    subject = f"{subject_prefix}-{user_id}"
    engine = create_async_engine(_async_database_url(os.environ["AUVRA_TEST_DATABASE_URL"]))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO app.users (id, auth_provider, auth_subject) "
                    "VALUES (:id, 'firebase', :subject)"
                ),
                {"id": user_id, "subject": subject},
            )
    finally:
        await engine.dispose()
    return user_id, subject


@pytest.mark.anyio
async def test_runtime_checkpoint_eraser_only_removes_v2_owned_threads() -> None:
    """Exercise the pinned vendor fixture without touching a real runtime."""

    from sqlalchemy import text

    from app.v2.infrastructure.account_lifecycle import PostgresRuntimeCheckpointEraser
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    engine, factory = _async_factory()
    fixture_created = False
    owner_id, other_id = uuid.uuid4(), uuid.uuid4()
    owner_thread, other_thread = f"user:{owner_id}:care", f"user:{other_id}:care"
    try:
        async with engine.begin() as connection:
            existing = await connection.scalar(
                text(
                    """SELECT count(*) FROM pg_tables WHERE schemaname = 'public'
                    AND tablename IN ('checkpoint_migrations', 'checkpoints',
                                      'checkpoint_blobs', 'checkpoint_writes')"""
                )
            )
            if existing:
                pytest.skip("shared database already has LangGraph runtime tables")
            fixture_created = True
            await connection.execute(
                text("CREATE TABLE checkpoint_migrations (v integer PRIMARY KEY)")
            )
            await connection.execute(
                text(
                    """CREATE TABLE checkpoints (
                    thread_id text NOT NULL, checkpoint_ns text NOT NULL DEFAULT '',
                    checkpoint_id text NOT NULL, parent_checkpoint_id text, type text,
                    checkpoint jsonb NOT NULL, metadata jsonb NOT NULL DEFAULT '{}',
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"""
                )
            )
            await connection.execute(
                text(
                    """CREATE TABLE checkpoint_blobs (
                    thread_id text NOT NULL, checkpoint_ns text NOT NULL DEFAULT '',
                    channel text NOT NULL, version text NOT NULL, type text NOT NULL,
                    blob bytea, PRIMARY KEY (thread_id, checkpoint_ns, channel, version))"""
                )
            )
            await connection.execute(
                text(
                    """CREATE TABLE checkpoint_writes (
                    thread_id text NOT NULL, checkpoint_ns text NOT NULL DEFAULT '',
                    checkpoint_id text NOT NULL, task_id text NOT NULL,
                    idx integer NOT NULL, channel text NOT NULL, type text, blob bytea NOT NULL,
                    task_path text NOT NULL DEFAULT '',
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx))"""
                )
            )
            await connection.execute(text("INSERT INTO checkpoint_migrations (v) VALUES (9)"))
            for thread_id, checkpoint_id in (
                (owner_thread, "owner"),
                (other_thread, "other"),
            ):
                await connection.execute(
                    text(
                        """INSERT INTO checkpoints
                        (thread_id, checkpoint_id, checkpoint, metadata)
                        VALUES (:thread_id, :checkpoint_id, '{}'::jsonb, '{}'::jsonb)"""
                    ),
                    {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
                )
                await connection.execute(
                    text(
                        """INSERT INTO checkpoint_blobs
                        (thread_id, channel, version, type) VALUES
                        (:thread_id, 'state', :checkpoint_id, 'msgpack')"""
                    ),
                    {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
                )
                await connection.execute(
                    text(
                        """INSERT INTO checkpoint_writes
                        (thread_id, checkpoint_id, task_id, idx, channel, blob)
                        VALUES (:thread_id, :checkpoint_id, 'task', 0, 'state', '\\x00')"""
                    ),
                    {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
                )

        eraser = PostgresRuntimeCheckpointEraser(uow_factory=lambda: SqlAlchemyUnitOfWork(factory))
        await eraser.delete_user_runtime(user_id=owner_id)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread_id"),
                    {"thread_id": owner_thread},
                )
                == 0
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM checkpoint_writes WHERE thread_id = :thread_id"),
                    {"thread_id": owner_thread},
                )
                == 0
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM checkpoint_blobs WHERE thread_id = :thread_id"),
                    {"thread_id": owner_thread},
                )
                == 0
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread_id"),
                    {"thread_id": other_thread},
                )
                == 1
            )
            assert await connection.scalar(text("SELECT count(*) FROM checkpoint_migrations")) == 1
    finally:
        if fixture_created:
            async with engine.begin() as connection:
                await connection.execute(text("DROP TABLE checkpoint_writes"))
                await connection.execute(text("DROP TABLE checkpoint_blobs"))
                await connection.execute(text("DROP TABLE checkpoints"))
                await connection.execute(text("DROP TABLE checkpoint_migrations"))
        await engine.dispose()


class _ReadyRelease:
    async def require_authorized(self, *, deletion_request_id, subject_hash) -> None:
        assert isinstance(deletion_request_id, uuid.UUID)
        assert len(subject_hash) == 64


class _Identity:
    def __init__(self) -> None:
        self.calls = 0

    async def revoke_and_delete(self, *, auth_provider, auth_subject) -> None:
        assert auth_provider == "firebase"
        assert auth_subject
        self.calls += 1


class _Storage:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once, self.delete_calls, self.put_calls = fail_once, 0, 0

    async def put_export(self, *, export_id, content, expires_at):
        from app.v2.application.account_lifecycle import PrivateExportAsset

        assert content and expires_at > datetime.now(UTC)
        self.put_calls += 1
        return PrivateExportAsset("test", "private-exports", f"exports/v1/{export_id}.bin")

    async def delete_user_objects(self, *, user_id) -> None:
        assert isinstance(user_id, uuid.UUID)
        self.delete_calls += 1
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated provider body must never persist")


class _Checkpoints:
    async def delete_user_runtime(self, *, user_id) -> None:
        assert isinstance(user_id, uuid.UUID)


class _Cache:
    async def purge_user(self, *, user_id) -> None:
        assert isinstance(user_id, uuid.UUID)


class _ExportBuilder:
    async def build(self, *, export_id, user_id):
        del user_id
        content = f"encrypted-export:{export_id}".encode("ascii")
        return content, hashlib.sha256(content).hexdigest()


def _claimed_job(*, job_id: uuid.UUID, user_id: uuid.UUID, job_type: str, payload: dict):
    from app.v2.infrastructure.worker import ClaimedJob

    return ClaimedJob(
        id=job_id,
        user_id=user_id,
        job_type=job_type,
        request_payload=MappingProxyType(payload),
        attempt_count=1,
        max_attempts=10,
        lease_token="test:lease",
    )


@pytest.mark.anyio
async def test_canonical_export_is_encrypted_and_excludes_operational_job_payloads() -> None:
    from sqlalchemy import text

    from app.v2.infrastructure.account_lifecycle import (
        AesGcmAccountExportCipher,
        CanonicalAccountExportBuilder,
    )
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    user_id, _ = await _create_user(subject_prefix="canonical-export")
    export_id = uuid.uuid4()
    engine, factory = _async_factory()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO app.user_profiles "
                    "(user_id, display_name, timezone, locale, version) "
                    "VALUES (:user_id, 'Export Test', 'UTC', 'en', 1)"
                ),
                {"user_id": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO app.user_observations "
                    "(id, user_id, observation_type, code, observed_at, "
                    " observed_local_date, observed_timezone, value_numeric, "
                    " value_unit, client_observation_id, note) "
                    "VALUES (:id, :user_id, 'symptom', 'headache', now(), "
                    "current_date, 'UTC', 3, 'score_0_10', :client_id, "
                    "'private note')"
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "client_id": uuid.uuid4(),
                },
            )
        cipher = AesGcmAccountExportCipher("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
        builder = CanonicalAccountExportBuilder(
            cipher=cipher,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        )
        encrypted, checksum = await builder.build(export_id=export_id, user_id=user_id)
        assert hashlib.sha256(encrypted).hexdigest() == checksum
        assert b"private note" not in encrypted
        payload = json.loads(cipher.decrypt_for_test(export_id=export_id, payload=encrypted))
        assert payload["format"] == "auvra.account-export.v1"
        assert payload["datasets"]["profile"][0]["display_name"] == "Export Test"
        assert payload["datasets"]["user_observations"][0]["note"] == "private note"
        assert "generation_jobs" not in payload["datasets"]
        assert "auth_subject" not in payload["datasets"]["account"][0]
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM app.users WHERE id = :id"), {"id": user_id})
        await engine.dispose()


@pytest.mark.anyio
async def test_export_is_private_replay_safe_and_result_has_no_object_reference() -> None:
    from sqlalchemy import text

    from app.v2.application.account_lifecycle import (
        AccountErasurePorts,
        AccountLifecycleJobRunner,
    )
    from app.v2.persistence.models import GenerationJob
    from app.v2.persistence.models_engagement import AccountExport
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    user_id, _ = await _create_user(subject_prefix="account-export")
    export_id, job_id = uuid.uuid4(), uuid.uuid4()
    engine, factory = _async_factory()
    try:
        async with SqlAlchemyUnitOfWork(factory) as uow:
            assert uow.session is not None
            now = datetime.now(UTC)
            uow.session.add(
                GenerationJob(
                    id=job_id,
                    user_id=user_id,
                    job_type="account_export",
                    request_payload={"export_id": str(export_id)},
                    available_at=now,
                    max_attempts=10,
                )
            )
            uow.session.add(
                AccountExport(
                    id=export_id,
                    user_id=user_id,
                    generation_job_id=job_id,
                    state="requested",
                    expires_at=now + timedelta(days=7),
                )
            )
            await uow.commit()
        storage = _Storage()
        runner = AccountLifecycleJobRunner(
            exports=_ExportBuilder(),
            ports=AccountErasurePorts(
                identity=_Identity(),
                storage=storage,
                checkpoints=_Checkpoints(),
                cache=_Cache(),
                release_gate=_ReadyRelease(),
            ),
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        )
        job = _claimed_job(
            job_id=job_id,
            user_id=user_id,
            job_type="account_export",
            payload={"export_id": str(export_id)},
        )
        first = await runner.handle(job)
        replay = await runner.handle(job)
        assert first == replay == {"export_id": str(export_id), "state": "ready"}
        assert storage.put_calls == 1
        assert set(first) == {"export_id", "state"}
        async with engine.connect() as connection:
            state, provider, bucket, object_key, checksum = (
                await connection.execute(
                    text(
                        "SELECT state, storage_provider, bucket, object_key, manifest_sha256 "
                        "FROM ops.account_exports WHERE id = :id"
                    ),
                    {"id": export_id},
                )
            ).one()
        assert (state, provider, bucket) == ("ready", "test", "private-exports")
        assert object_key == f"exports/v1/{export_id}.bin"
        assert checksum and len(checksum) == 64
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM app.users WHERE id = :id"), {"id": user_id})
        await engine.dispose()


@pytest.mark.anyio
async def test_deletion_failure_records_retry_then_completes_only_after_all_steps() -> None:
    from sqlalchemy import text

    from app.v2.application.account_lifecycle import (
        AccountErasurePorts,
        AccountLifecycleJobRunner,
        AccountLifecycleFailure,
        ERASURE_STEPS,
    )
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    user_id, subject = await _create_user(subject_prefix="account-delete")
    request_id, job_id = uuid.uuid4(), uuid.uuid4()
    engine, factory = _async_factory()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO ops.generation_jobs "
                    "(id, user_id, job_type, request_payload, max_attempts) "
                    "VALUES (:id, :user_id, 'account_deletion', "
                    "CAST(:payload AS jsonb), 10)"
                ),
                {
                    "id": job_id,
                    "user_id": user_id,
                    "payload": '{"deletion_request_id":"' + str(request_id) + '"}',
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO ops.deletion_requests "
                    "(id, user_id, subject_hash, state, requested_at, "
                    "generation_job_id, verification_summary) "
                    "VALUES (:id, :user_id, :subject_hash, 'requested', "
                    "now(), :job_id, '{}'::jsonb)"
                ),
                {
                    "id": request_id,
                    "user_id": user_id,
                    "subject_hash": hashlib.sha256(subject.encode()).hexdigest(),
                    "job_id": job_id,
                },
            )
            for step_name in ERASURE_STEPS:
                await connection.execute(
                    text(
                        "INSERT INTO ops.deletion_steps "
                        "(id, deletion_request_id, step_name) "
                        "VALUES (:id, :request_id, :step_name)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "request_id": request_id,
                        "step_name": step_name,
                    },
                )
        identity, storage = _Identity(), _Storage(fail_once=True)
        runner = AccountLifecycleJobRunner(
            exports=_ExportBuilder(),
            ports=AccountErasurePorts(
                identity=identity,
                storage=storage,
                checkpoints=_Checkpoints(),
                cache=_Cache(),
                release_gate=_ReadyRelease(),
            ),
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        )
        job = _claimed_job(
            job_id=job_id,
            user_id=user_id,
            job_type="account_deletion",
            payload={"deletion_request_id": str(request_id)},
        )
        with pytest.raises(AccountLifecycleFailure, match="provider_step_failed"):
            await runner.handle(job)
        async with engine.connect() as connection:
            state, error_code, step_state = (
                await connection.execute(
                    text(
                        "SELECT request.state, request.last_error_code, step.state "
                        "FROM ops.deletion_requests request JOIN ops.deletion_steps step "
                        "ON step.deletion_request_id = request.id "
                        "WHERE request.id = :id AND step.step_name = 'private_storage_erased'"
                    ),
                    {"id": request_id},
                )
            ).one()
            user_exists = await connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM app.users WHERE id = :id)"),
                {"id": user_id},
            )
        assert (state, error_code, step_state, user_exists) == (
            "retry_wait",
            "provider_step_failed",
            "failed",
            True,
        )
        completed = await runner.handle(job)
        assert completed == {
            "deletion_request_id": str(request_id),
            "state": "completed",
        }
        async with engine.connect() as connection:
            receipt, final_state, user_exists = (
                await connection.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM ops.deletion_receipts WHERE deletion_request_id = :id), "
                        "(SELECT state FROM ops.deletion_requests WHERE id = :id), "
                        "EXISTS(SELECT 1 FROM app.users WHERE id = :user_id)"
                    ),
                    {"id": request_id, "user_id": user_id},
                )
            ).one()
        assert (receipt, final_state, user_exists) == (True, "completed", False)
        # The completed ledger step prevents a normal retry from repeating an
        # already verified identity operation; the concrete Firebase port is
        # still idempotent for a crash between provider success and this mark.
        assert identity.calls == 1
        assert storage.delete_calls == 2
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM app.users WHERE id = :id"), {"id": user_id})
            await connection.execute(
                text("DELETE FROM ops.deletion_requests WHERE id = :id"),
                {"id": request_id},
            )
        await engine.dispose()
