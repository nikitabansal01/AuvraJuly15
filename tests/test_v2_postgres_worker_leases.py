"""PostgresJobWorker's claim/heartbeat/retry SQL against a real asyncpg driver.

These exist because a fake, in-memory Session (as in test_v2_worker_runtime.py)
cannot catch a driver-level parameter-typing mismatch: asyncpg binds a Python
int strictly, and `(:lease || ' seconds')::interval` asked it to bind that int
as text for the `||` operator. That shipped, passed the full suite, and only
failed the moment the worker ran against real PostgreSQL in production. The
fix uses `make_interval(secs => :lease)`, which accepts a bound int directly.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="Worker lease tests require AUVRA_TEST_DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _dispose_shared_engine():
    yield
    import asyncio

    from app.v2.persistence.database import dispose_database

    asyncio.run(dispose_database())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])


def _seed_user_and_job(connection, *, job_type: str = "plan_generation") -> uuid.UUID:
    from sqlalchemy import text

    user_id, job_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.users (id, auth_provider, auth_subject) "
            "VALUES (:id, 'firebase', :subject)"
        ),
        {"id": user_id, "subject": f"worker-{user_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO ops.generation_jobs "
            "(id, user_id, job_type, request_payload) "
            "VALUES (:id, :user_id, :job_type, "
            "CAST(:payload AS jsonb))"
        ),
        {
            "id": job_id,
            "user_id": user_id,
            "job_type": job_type,
            "payload": '{"local_date": "2026-08-08"}',
        },
    )
    return job_id


@pytest.mark.anyio
async def test_claim_binds_an_integer_lease_without_a_driver_type_error() -> None:
    """This is exactly the call that raised DataError in production."""
    from app.v2.infrastructure.worker import PostgresJobWorker
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        job_id = _seed_user_and_job(connection)

    worker = PostgresJobWorker(
        worker_id="test-worker",
        handler=lambda job, uow: None,
        job_type="plan_generation",
        lease_seconds=60,
    )
    async with SqlAlchemyUnitOfWork() as uow:
        claimed = await worker.claim(uow)

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.attempt_count == 1


@pytest.mark.anyio
async def test_heartbeat_extends_the_lease_with_the_bound_integer() -> None:
    from app.v2.infrastructure.worker import PostgresJobWorker
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        _seed_user_and_job(connection)

    worker = PostgresJobWorker(
        worker_id="test-worker",
        handler=lambda job, uow: None,
        job_type="plan_generation",
        lease_seconds=60,
    )
    async with SqlAlchemyUnitOfWork() as uow:
        claimed = await worker.claim(uow)

    async with SqlAlchemyUnitOfWork() as uow:
        renewed = await worker.heartbeat(uow, claimed)
    assert renewed is True


@pytest.mark.anyio
async def test_a_claimed_job_is_not_claimable_again_until_its_lease_expires() -> None:
    from app.v2.infrastructure.worker import PostgresJobWorker
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        _seed_user_and_job(connection)

    worker = PostgresJobWorker(
        worker_id="test-worker",
        handler=lambda job, uow: None,
        job_type="plan_generation",
        lease_seconds=60,
    )
    async with SqlAlchemyUnitOfWork() as uow:
        first = await worker.claim(uow)
    async with SqlAlchemyUnitOfWork() as uow:
        second = await worker.claim(uow)

    assert first is not None
    assert second is None


@pytest.mark.anyio
async def test_retry_backoff_binds_a_computed_float_without_a_type_error() -> None:
    """The same string-concat bug existed in the power-of-two backoff path."""
    from app.v2.infrastructure.worker import PostgresJobWorker
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        _seed_user_and_job(connection)

    worker = PostgresJobWorker(
        worker_id="test-worker",
        handler=lambda job, uow: None,
        job_type="plan_generation",
        lease_seconds=60,
    )
    async with SqlAlchemyUnitOfWork() as uow:
        claimed = await worker.claim(uow)

    async with SqlAlchemyUnitOfWork() as uow:
        # retryable=True exercises the LEAST(300, 2 ^ attempt_count) branch.
        await worker._retry_or_dead_letter(
            uow, claimed, error_code="provider_timeout", retryable=True
        )

    from sqlalchemy import text

    with _engine().begin() as connection:
        state, available_at = connection.execute(
            text("SELECT state, available_at FROM ops.generation_jobs WHERE id = :id"),
            {"id": claimed.id},
        ).one()
    assert state == "retry_wait"
    assert available_at > datetime.now(UTC)


def _seed_outbox_event(connection) -> uuid.UUID:
    """An outbox event needs an aggregate to describe; a user works as one."""
    from sqlalchemy import text

    user_id, event_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.users (id, auth_provider, auth_subject) "
            "VALUES (:id, 'firebase', :subject)"
        ),
        {"id": user_id, "subject": f"outbox-{user_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO ops.outbox_events "
            "(id, aggregate_type, aggregate_id, event_type, payload) "
            "VALUES (:id, 'user', :user_id, 'test.event', '{}'::jsonb)"
        ),
        {"id": event_id, "user_id": user_id},
    )
    return event_id


class _NullPublisher:
    async def publish(self, event) -> None:  # noqa: ANN001 - test double
        return None


@pytest.mark.anyio
async def test_outbox_claim_binds_an_integer_lease_without_a_driver_type_error() -> None:
    """The identical bug existed in the outbox worker's own lease query."""
    from app.v2.infrastructure.outbox_worker import PostgresOutboxWorker
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        event_id = _seed_outbox_event(connection)

    worker = PostgresOutboxWorker(
        worker_id="test-outbox-worker",
        publisher=_NullPublisher(),
        lease_seconds=60,
    )
    async with SqlAlchemyUnitOfWork() as uow:
        claimed = await worker.claim(uow)

    assert claimed is not None
    assert claimed.id == event_id


@pytest.mark.anyio
async def test_outbox_heartbeat_extends_the_lease_with_the_bound_integer() -> None:
    from app.v2.infrastructure.outbox_worker import PostgresOutboxWorker
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        _seed_outbox_event(connection)

    worker = PostgresOutboxWorker(
        worker_id="test-outbox-worker",
        publisher=_NullPublisher(),
        lease_seconds=60,
    )
    async with SqlAlchemyUnitOfWork() as uow:
        claimed = await worker.claim(uow)

    async with SqlAlchemyUnitOfWork() as uow:
        renewed = await worker.heartbeat(uow, claimed)
    assert renewed is True
