"""Worker lease and retry tests using a small database-session seam."""
from __future__ import annotations

from types import MappingProxyType
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.v2.application.plan_generation import ProviderFailure
import app.v2.infrastructure.plan_worker_entrypoint as worker_entrypoint
from app.v2.infrastructure.worker import (
    ClaimedJob,
    PostgresJobWorker,
    TerminalJobFailure,
)


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
    def __init__(self, claimed=None, rowcount: int = 1) -> None:
        self.claimed, self.rowcount, self.calls = claimed, rowcount, []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if "RETURNING job.id" in str(statement):
            return ClaimResult(self.claimed)
        return Result(self.rowcount)


class Uow:
    def __init__(self, session: Session) -> None:
        self.session, self.commits, self.rollbacks = session, 0, 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


def job() -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        user_id=uuid4(),
        job_type="plan_generation",
        request_payload=MappingProxyType({"local_date": "2026-08-08"}),
        attempt_count=1,
        max_attempts=3,
        lease_token="a:lease",
    )


@pytest.mark.anyio
async def test_claim_uses_skip_locked_and_returns_detached_immutable_snapshot():
    claimed = {
        "id": uuid4(),
        "user_id": uuid4(),
        "job_type": "plan_generation",
        "request_payload": {"local_date": "2026-08-08", "nested": {"values": [1]}},
        "attempt_count": 1,
        "max_attempts": 3,
    }
    session = Session(claimed=claimed)
    uow = Uow(session)
    worker = PostgresJobWorker("a", lambda _: None, job_type="plan_generation")
    snapshot = await worker.claim(uow)
    assert snapshot is not None
    assert snapshot.lease_token.startswith("a:")
    assert "SKIP LOCKED" in session.calls[2][0]
    assert "jsonb_typeof(request_payload)" in session.calls[0][0]
    assert "attempt_count >= max_attempts" in session.calls[1][0]
    assert "lease_expires_at < now()" in session.calls[0][0]
    assert "lease_expires_at < now()" in session.calls[1][0]
    assert "WITH candidate" in session.calls[2][0]
    with pytest.raises(TypeError):
        snapshot.request_payload["local_date"] = "2026-08-09"
    with pytest.raises(TypeError):
        snapshot.request_payload["nested"]["values"] = ()
    assert uow.commits == 1


@pytest.mark.anyio
async def test_heartbeat_is_lease_checked():
    session = Session(rowcount=0)
    uow = Uow(session)
    worker = PostgresJobWorker("a", lambda _: None, job_type="plan_generation")
    assert not await worker.heartbeat(uow, job())
    assert uow.commits == 1


@pytest.mark.anyio
async def test_retry_and_terminal_errors_have_safe_distinct_states():
    session = Session()
    uow = Uow(session)
    worker = PostgresJobWorker("a", lambda _: None, job_type="plan_generation")
    await worker._retry_or_dead_letter(uow, job(), "invalid", retryable=False)
    assert "'dead_letter'" in session.calls[-1][0]
    assert session.calls[-1][1]["terminal"] is True
    assert (
        worker._error_code(ProviderFailure("provider_retry", retryable=True))
        == "provider_retry"
    )
    assert not worker._retryable(TerminalJobFailure("invalid_output"))


def test_error_codes_never_include_provider_exception_text():
    assert "secret" not in PostgresJobWorker._error_code(
        RuntimeError("secret provider body")
    )
    assert PostgresJobWorker._retryable(RuntimeError("database reset"))


@pytest.mark.anyio
async def test_resource_shutdown_runs_registered_closer():
    closed = False

    async def close() -> None:
        nonlocal closed
        closed = True

    worker = PostgresJobWorker(
        "a", lambda _: None, job_type="plan_generation", close_resources=close
    )
    await worker.aclose()
    assert closed


@pytest.mark.anyio
async def test_plan_worker_checks_schema_before_claiming_work(monkeypatch):
    events = []

    class Worker:
        async def aclose(self) -> None:
            events.append("close")

    async def database() -> None:
        events.append("database")

    async def schema() -> None:
        events.append("schema")

    async def run(worker) -> None:
        assert isinstance(worker, Worker)
        events.append("run")

    monkeypatch.setattr(
        worker_entrypoint,
        "validate_plan_worker_configuration",
        lambda: events.append("config"),
    )
    monkeypatch.setattr(worker_entrypoint, "check_database_readiness", database)
    monkeypatch.setattr(worker_entrypoint, "check_database_schema_head", schema)
    monkeypatch.setattr(worker_entrypoint, "build_plan_worker", lambda: Worker())
    monkeypatch.setattr(worker_entrypoint, "run_worker", run)

    await worker_entrypoint.run_plan_worker()
    assert events == ["config", "database", "schema", "run", "close"]


@pytest.mark.anyio
async def test_combined_worker_routes_all_job_lanes_and_closes_shared_resources_once(
    monkeypatch,
):
    events = []

    class Worker:
        def __init__(self, job_type):
            self.job_type = job_type

        async def aclose(self):
            events.append(f"close:{self.job_type}")

    async def database():
        events.append("database")

    async def schema():
        events.append("schema")

    async def run(workers):
        events.append(tuple(worker.job_type for worker in workers))

    monkeypatch.setattr(
        worker_entrypoint,
        "validate_plan_worker_configuration",
        lambda: events.append("config"),
    )
    monkeypatch.setattr(worker_entrypoint, "check_database_readiness", database)
    monkeypatch.setattr(worker_entrypoint, "check_database_schema_head", schema)
    monkeypatch.setattr(
        worker_entrypoint, "build_plan_worker", lambda: Worker("plan_generation")
    )
    monkeypatch.setattr(
        worker_entrypoint,
        "build_conversation_worker",
        lambda: Worker("conversation_response.v1"),
    )
    monkeypatch.setattr(
        worker_entrypoint,
        "build_account_workers",
        lambda: (Worker("account_export"), Worker("account_deletion")),
    )
    monkeypatch.setattr(worker_entrypoint, "run_workers", run)

    await worker_entrypoint.run_v2_worker()
    assert events == [
        "config",
        "database",
        "schema",
        (
            "plan_generation",
            "conversation_response.v1",
            "account_export",
            "account_deletion",
        ),
        "close:plan_generation",
        "close:conversation_response.v1",
        "close:account_export",
        "close:account_deletion",
    ]


@pytest.mark.anyio
async def test_enabled_deletion_lane_initializes_firebase_before_workers(monkeypatch):
    events = []

    class Worker:
        job_type = "plan_generation"

        async def aclose(self):
            return None

    async def noop():
        return None

    async def run(workers):
        events.append(tuple(worker.job_type for worker in workers))

    monkeypatch.setattr(
        worker_entrypoint, "settings", SimpleNamespace(V2_DELETION_ENABLED=True)
    )
    monkeypatch.setattr(
        worker_entrypoint, "validate_plan_worker_configuration", lambda: None
    )
    monkeypatch.setattr(worker_entrypoint, "check_database_readiness", noop)
    monkeypatch.setattr(worker_entrypoint, "check_database_schema_head", noop)
    monkeypatch.setattr(
        worker_entrypoint, "initialize_v2_firebase", lambda: events.append("firebase")
    )
    monkeypatch.setattr(worker_entrypoint, "build_plan_worker", Worker)
    monkeypatch.setattr(worker_entrypoint, "build_conversation_worker", Worker)
    monkeypatch.setattr(
        worker_entrypoint, "build_account_workers", lambda: (Worker(), Worker())
    )
    monkeypatch.setattr(worker_entrypoint, "run_workers", run)

    await worker_entrypoint.run_v2_worker()
    assert events[0] == "firebase"


def test_combined_worker_rejects_duplicate_job_filters():
    with pytest.raises(ValueError, match="distinct job_type"):
        __import__("anyio").run(
            __import__(
                "app.v2.infrastructure.worker", fromlist=["run_workers"]
            ).run_workers,
            (
                PostgresJobWorker("a", lambda _: None, job_type="plan_generation"),
                PostgresJobWorker("b", lambda _: None, job_type="plan_generation"),
            ),
        )


def test_idle_polling_backs_off_but_stays_responsive_to_work() -> None:
    """An idle worker must not query an external database every second.

    Four workers polling once a second spent 3.7 GB of a 5 GB monthly
    bandwidth allowance asking Supabase whether an empty queue had work.
    Backing off geometrically while idle cuts that by roughly thirty times,
    and resetting the moment a job is claimed keeps a busy queue draining at
    full speed.
    """
    from app.v2.infrastructure.worker import MAX_IDLE_POLL_SECONDS, next_poll_delay

    base = 1.0
    delay = base

    # Idle: the delay grows geometrically and then stops at the ceiling.
    seen = []
    for _ in range(10):
        delay = next_poll_delay(
            delay, worked=False, base=base, maximum=MAX_IDLE_POLL_SECONDS
        )
        seen.append(delay)
    assert seen[0] == 2.0
    assert seen[1] == 4.0
    assert max(seen) == MAX_IDLE_POLL_SECONDS
    assert all(d <= MAX_IDLE_POLL_SECONDS for d in seen)

    # Claiming a job returns immediately to the base interval.
    assert next_poll_delay(
        MAX_IDLE_POLL_SECONDS, worked=True, base=base, maximum=MAX_IDLE_POLL_SECONDS
    ) == base


def test_backoff_never_returns_less_than_the_base_interval() -> None:
    from app.v2.infrastructure.worker import next_poll_delay

    assert next_poll_delay(0.0, worked=False, base=2.0, maximum=30.0) >= 2.0
    assert next_poll_delay(0.0, worked=True, base=2.0, maximum=30.0) == 2.0


def test_a_base_above_the_ceiling_is_respected_rather_than_shrunk() -> None:
    """A deployment that deliberately polls slowly must not be sped up."""
    from app.v2.infrastructure.worker import next_poll_delay

    assert next_poll_delay(60.0, worked=False, base=60.0, maximum=60.0) == 60.0
