"""Focused durable-outbox worker tests using the worker's database-session seam."""
from __future__ import annotations

from types import MappingProxyType
from uuid import uuid4

import anyio
import pytest

from app.v2.infrastructure.deterministic_outbox import DeterministicOutboxPublisher
from app.v2.infrastructure.outbox_worker import (
    ClaimedOutboxEvent,
    OutboxPublishFailure,
    PostgresOutboxWorker,
)


@pytest.fixture
def anyio_backend() -> str:
    """The bundled Trio release is not compatible with the pinned Python 3.14."""

    return "asyncio"


class Result:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class ClaimResult:
    def __init__(self, value) -> None:
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value


class Session:
    def __init__(self, claimed=None, *, mark_rowcount: int = 1) -> None:
        self.claimed = claimed
        self.mark_rowcount = mark_rowcount
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        source = str(statement)
        self.calls.append((source, params))
        if "RETURNING event.id" in source:
            return ClaimResult(self.claimed)
        if "SET state = 'published'" in source:
            return Result(self.mark_rowcount)
        return Result()


class Uow:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


def claimed(attempt_count: int = 1) -> dict:
    return {
        "id": uuid4(),
        "aggregate_type": "conversation",
        "aggregate_id": uuid4(),
        "event_type": "conversation.response.requested.v1",
        "payload": {"message_id": "safe"},
        "attempt_count": attempt_count,
        "max_attempts": 3,
    }


def event() -> ClaimedOutboxEvent:
    return ClaimedOutboxEvent(
        id=uuid4(),
        aggregate_type="conversation",
        aggregate_id=uuid4(),
        event_type="conversation.response.requested.v1",
        payload=MappingProxyType({"message_id": "safe"}),
        attempt_count=1,
        max_attempts=3,
        lease_token="worker:lease",
    )


@pytest.mark.anyio
async def test_concurrent_claims_are_skip_locked_and_detached() -> None:
    first, second = Uow(Session(claimed())), Uow(Session(claimed()))
    worker = PostgresOutboxWorker("worker", DeterministicOutboxPublisher())
    one, two = await worker.claim(first), await worker.claim(second)
    assert one is not None and two is not None and one.id != two.id
    assert "FOR UPDATE SKIP LOCKED" in first.session.calls[2][0]
    assert "FOR UPDATE SKIP LOCKED" in second.session.calls[2][0]
    with pytest.raises(TypeError):
        one.payload["message_id"] = "changed"


@pytest.mark.anyio
async def test_restart_recovers_expired_lease_and_increments_attempts() -> None:
    uow = Uow(Session(claimed(attempt_count=2)))
    worker = PostgresOutboxWorker("restarted-worker", DeterministicOutboxPublisher())
    snapshot = await worker.claim(uow)
    assert snapshot is not None and snapshot.attempt_count == 2
    claim_sql = uow.session.calls[2][0]
    assert "state = 'running' AND lease_expires_at < now()" in claim_sql
    assert "attempt_count = attempt_count + 1" in claim_sql


@pytest.mark.anyio
async def test_lease_expiry_is_the_only_recovery_path_for_running_events() -> None:
    uow = Uow(Session())
    worker = PostgresOutboxWorker("worker", DeterministicOutboxPublisher())
    assert await worker.claim(uow) is None
    terminalization_sql = "\n".join(call[0] for call in uow.session.calls[:2])
    assert "state = 'running' AND lease_expires_at < now()" in terminalization_sql
    assert "lease_owner = NULL, lease_expires_at = NULL" in terminalization_sql


@pytest.mark.anyio
async def test_provider_call_happens_after_claim_commit() -> None:
    uow = Uow(Session(claimed()))
    observed_commits: list[int] = []

    class Publisher:
        async def publish(self, _message) -> None:
            observed_commits.append(uow.commits)

    worker = PostgresOutboxWorker("worker", Publisher())
    assert await worker.run_once(uow)
    assert observed_commits == [1]
    assert "SET state = 'published'" in uow.session.calls[-1][0]


@pytest.mark.anyio
async def test_provider_timeout_retries_with_a_redacted_stable_code() -> None:
    class SlowPublisher:
        async def publish(self, _message) -> None:
            await anyio.sleep(2)

    uow = Uow(Session(claimed()))
    worker = PostgresOutboxWorker(
        "worker", SlowPublisher(), lease_seconds=6, timeout_seconds=1
    )
    assert await worker.run_once(uow)
    retry_sql, params = uow.session.calls[-1]
    assert "LEAST(300, 2 ^ attempt_count)" in retry_sql
    assert params is not None and params["error"] == "provider_timeout"


@pytest.mark.anyio
async def test_duplicate_publication_is_explicit_after_post_publish_process_death() -> None:
    publisher = DeterministicOutboxPublisher()
    recovered = claimed(1)
    first = Uow(Session(recovered, mark_rowcount=0))
    recovered_again = {**recovered, "attempt_count": 2}
    second = Uow(Session(recovered_again, mark_rowcount=0))
    worker = PostgresOutboxWorker("worker", publisher)
    assert await worker.run_once(first)
    assert await worker.run_once(second)
    assert len(publisher.calls) == 2
    assert publisher.calls[0].id == publisher.calls[1].id
    assert first.rollbacks == second.rollbacks == 1


@pytest.mark.anyio
async def test_nonretryable_provider_failure_becomes_terminal_failed_state() -> None:
    uow = Uow(Session())
    worker = PostgresOutboxWorker("worker", DeterministicOutboxPublisher())
    await worker._retry_or_fail(
        uow,
        event(),
        OutboxPublishFailure("consumer_contract_rejected", retryable=False).code,
        False,
    )
    sql, params = uow.session.calls[-1]
    assert "THEN 'failed' ELSE 'pending'" in sql
    assert params is not None and params["terminal"] is True


def test_provider_errors_cannot_leak_exception_text() -> None:
    assert (
        PostgresOutboxWorker._error_code(RuntimeError("provider secret: x"))
        == "publisher_failed"
    )
    with pytest.raises(ValueError):
        OutboxPublishFailure("provider secret: x")
