"""PostgreSQL-authoritative worker primitives for durable v2 jobs.

The worker deliberately passes a detached, immutable job snapshot to handlers.
Database work is limited to short lease/terminal transactions; handlers are free
to perform slow provider I/O only after the claim transaction has closed.
"""
from __future__ import annotations

import signal
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

import anyio
from sqlalchemy import text

from app.v2.application.plan_generation import ProviderFailure
from app.v2.persistence.models import GenerationJob
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A detached, minimal job hand-off that cannot mutate ORM state."""

    id: uuid.UUID
    user_id: uuid.UUID
    job_type: str
    request_payload: Mapping[str, Any]
    attempt_count: int
    max_attempts: int
    lease_token: str

    @property
    def local_date(self) -> date:
        return date.fromisoformat(str(self.request_payload["local_date"]))


class TerminalJobFailure(RuntimeError):
    """A validated, non-retryable job failure with a safe public code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RetryableJobFailure(RuntimeError):
    """A retryable dependency condition with a safe, stable job error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LeaseLost(RuntimeError):
    """The worker no longer owns the database lease for this job."""


JobHandler = Callable[[ClaimedJob], Awaitable[dict[str, Any]]]
UowFactory = Callable[[], SqlAlchemyUnitOfWork]
ResourceCloser = Callable[[], Awaitable[None]]


class PostgresJobWorker:
    def __init__(
        self,
        worker_id: str,
        handler: JobHandler,
        *,
        lease_seconds: int = 60,
        timeout_seconds: int = 300,
        shutdown_seconds: int = 30,
        job_type: str,
        uow_factory: UowFactory = SqlAlchemyUnitOfWork,
        close_resources: ResourceCloser | None = None,
    ) -> None:
        if lease_seconds < 6:
            raise ValueError(
                "lease_seconds must allow at least two heartbeat intervals"
            )
        if not job_type:
            raise ValueError("job_type is required for a least-privilege worker")
        self.worker_id = worker_id
        self.handler = handler
        self.lease_seconds = lease_seconds
        self.timeout_seconds = timeout_seconds
        self.shutdown_seconds = shutdown_seconds
        self.job_type = job_type
        self.uow_factory = uow_factory
        self._close_resources = close_resources

    async def aclose(self) -> None:
        """Release process-owned provider clients after the worker stops."""

        if self._close_resources is not None:
            await self._close_resources()

    async def claim(self, uow: SqlAlchemyUnitOfWork) -> ClaimedJob | None:
        """Claim exactly one runnable job and commit before returning it."""

        session = self._session(uow)
        token = f"{self.worker_id}:{uuid.uuid4()}"
        await self._terminalize_unclaimable(session)
        row = await session.execute(
            text(
                """WITH candidate AS (
                    SELECT id
                    FROM ops.generation_jobs
                    WHERE ((state IN ('queued', 'retry_wait') AND available_at <= now())
                       OR (state = 'running' AND lease_expires_at < now()))
                      AND attempt_count < max_attempts
                      AND jsonb_typeof(request_payload) = 'object'
                      AND job_type = :job_type
                    ORDER BY available_at, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ops.generation_jobs job
                SET state = 'running', lease_owner = :token,
                    lease_expires_at = now() + (:lease || ' seconds')::interval,
                    heartbeat_at = now(), started_at = COALESCE(started_at, now()),
                    attempt_count = attempt_count + 1, phase = 'running'
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.id, job.user_id, job.job_type, job.request_payload,
                          job.attempt_count, job.max_attempts"""
            ),
            {"token": token, "lease": self.lease_seconds, "job_type": self.job_type},
        )
        claimed = row.mappings().one_or_none()
        await uow.commit()
        if claimed is None:
            return None
        payload = claimed["request_payload"]
        if not isinstance(payload, dict):
            await session.execute(
                text(
                    """UPDATE ops.generation_jobs SET state = 'dead_letter', phase = 'dead_letter',
                    progress = 100, error_code = 'invalid_job_payload', finished_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL
                    WHERE id = :id AND state = 'running' AND lease_owner = :token"""
                ),
                {"id": claimed["id"], "token": token},
            )
            await uow.commit()
            return None
        return ClaimedJob(
            id=claimed["id"],
            user_id=claimed["user_id"],
            job_type=claimed["job_type"],
            request_payload=_freeze_payload(payload),
            attempt_count=claimed["attempt_count"],
            max_attempts=claimed["max_attempts"],
            lease_token=token,
        )

    async def _terminalize_unclaimable(self, session) -> None:
        """Run ordered terminal updates before the claim; never update one row twice in a CTE."""

        base = {"job_type": self.job_type}
        await session.execute(
            text(
                """UPDATE ops.generation_jobs
                SET state = 'dead_letter', phase = 'dead_letter', progress = 100,
                    error_code = 'invalid_job_payload', finished_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE jsonb_typeof(request_payload) <> 'object'
                  AND ((state IN ('queued', 'retry_wait') AND available_at <= now())
                       OR (state = 'running' AND lease_expires_at < now()))
                  AND job_type = :job_type"""
            ),
            base,
        )
        await session.execute(
            text(
                """UPDATE ops.generation_jobs
                SET state = 'dead_letter', phase = 'dead_letter', progress = 100,
                    error_code = 'attempts_exhausted', finished_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE attempt_count >= max_attempts
                  AND ((state IN ('queued', 'retry_wait') AND available_at <= now())
                       OR (state = 'running' AND lease_expires_at < now()))
                  AND job_type = :job_type"""
            ),
            base,
        )

    async def heartbeat(self, uow: SqlAlchemyUnitOfWork, job: ClaimedJob) -> bool:
        result = await self._session(uow).execute(
            text(
                """UPDATE ops.generation_jobs
                SET heartbeat_at = now(),
                    lease_expires_at = now() + (:lease || ' seconds')::interval
                WHERE id = :id AND state = 'running' AND lease_owner = :token"""
            ),
            {"id": job.id, "token": job.lease_token, "lease": self.lease_seconds},
        )
        await uow.commit()
        return bool(result.rowcount)

    async def run_once(self, uow: SqlAlchemyUnitOfWork) -> bool:
        job = await self.claim(uow)
        if job is None:
            return False
        try:
            result = await self._with_heartbeats(job)
        except LeaseLost:
            return True
        except Exception as exc:
            await self._retry_or_dead_letter(
                uow, job, self._error_code(exc), self._retryable(exc)
            )
            return True

        # A materializer may already have atomically published the plan and set
        # this job READY.  This guarded update makes that successful retry a no-op.
        result_update = await self._session(uow).execute(
            text(
                """UPDATE ops.generation_jobs
                SET state = 'ready', progress = 100, phase = 'ready', result_payload = :result,
                    finished_at = now(), lease_owner = NULL, lease_expires_at = NULL
                WHERE id = :id AND state = 'running' AND lease_owner = :token"""
            ),
            {"id": job.id, "token": job.lease_token, "result": result},
        )
        if not result_update.rowcount:
            await uow.rollback()
            return True
        await uow.commit()
        return True

    async def _with_heartbeats(self, job: ClaimedJob) -> dict[str, Any]:
        done = anyio.Event()
        result: dict[str, Any] = {}
        failure: list[Exception] = []
        handler_scope = anyio.CancelScope()

        async def execute() -> None:
            try:
                with handler_scope, anyio.fail_after(self.timeout_seconds):
                    value = await self.handler(job)
                    result.update(value)
            except BaseException as exc:
                if isinstance(exc, anyio.get_cancelled_exc_class()) and failure:
                    return
                if isinstance(exc, Exception):
                    failure.append(exc)
                else:
                    failure.append(RuntimeError("worker_cancelled"))
            finally:
                done.set()

        async def pulse() -> None:
            while not done.is_set():
                with anyio.move_on_after(max(1, self.lease_seconds // 3)):
                    await done.wait()
                if done.is_set():
                    break
                async with self.uow_factory() as heartbeat_uow:
                    if not await self.heartbeat(heartbeat_uow, job):
                        failure.append(LeaseLost())
                        handler_scope.cancel()
                        done.set()
                        break

        async with anyio.create_task_group() as group:
            group.start_soon(execute)
            group.start_soon(pulse)
        if failure:
            raise failure[0]
        return result

    async def _retry_or_dead_letter(
        self,
        uow: SqlAlchemyUnitOfWork,
        job: ClaimedJob,
        error_code: str,
        retryable: bool,
    ) -> None:
        terminal = not retryable
        await self._session(uow).execute(
            text(
                """UPDATE ops.generation_jobs
                SET state = CASE WHEN :terminal OR attempt_count >= max_attempts
                                 THEN 'dead_letter' ELSE 'retry_wait' END,
                    progress = CASE WHEN :terminal OR attempt_count >= max_attempts THEN 100 ELSE progress END,
                    phase = CASE WHEN :terminal OR attempt_count >= max_attempts THEN 'dead_letter' ELSE 'retry_wait' END,
                    error_code = :error,
                    available_at = CASE WHEN :terminal OR attempt_count >= max_attempts THEN available_at
                                        ELSE now() + (LEAST(300, 2 ^ attempt_count) || ' seconds')::interval END,
                    finished_at = CASE WHEN :terminal OR attempt_count >= max_attempts THEN now() ELSE NULL END,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE id = :id AND state = 'running' AND lease_owner = :token"""
            ),
            {
                "id": job.id,
                "token": job.lease_token,
                "error": error_code,
                "terminal": terminal,
            },
        )
        await uow.commit()

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, TerminalJobFailure):
            return exc.code
        if isinstance(exc, RetryableJobFailure):
            return exc.code
        if isinstance(exc, ProviderFailure):
            return exc.code
        if isinstance(exc, TimeoutError):
            return "provider_timeout"
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code and len(code) <= 64:
            return code
        return "worker_failed"

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, TerminalJobFailure):
            return False
        if isinstance(exc, RetryableJobFailure):
            return True
        if isinstance(exc, ProviderFailure):
            return exc.retryable
        retryable = getattr(exc, "retryable", None)
        if isinstance(retryable, bool):
            return retryable
        return True

    @staticmethod
    def _session(uow: SqlAlchemyUnitOfWork):
        if uow.session is None:
            raise RuntimeError("UnitOfWork must be entered before worker use")
        return uow.session


async def run_worker(worker: PostgresJobWorker, *, poll_seconds: float = 1.0) -> None:
    """Run one least-privilege worker (compatibility wrapper)."""

    await run_workers((worker,), poll_seconds=poll_seconds)


async def run_workers(
    workers: tuple[PostgresJobWorker, ...], *, poll_seconds: float = 1.0
) -> None:
    """Route distinct job types in one SIGTERM-aware process."""

    if not workers:
        raise ValueError("at least one worker is required")
    if len({worker.job_type for worker in workers}) != len(workers):
        raise ValueError("each worker must own a distinct job_type")

    stopping = anyio.Event()
    with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:

        async def watch() -> None:
            async for _ in signals:
                stopping.set()
                return

        async with anyio.create_task_group() as group:
            group.start_soon(watch)
            for worker in workers:
                group.start_soon(_run_worker_loop, worker, stopping, poll_seconds)
            await stopping.wait()


async def _run_worker_loop(
    worker: PostgresJobWorker, stopping: anyio.Event, poll_seconds: float
) -> None:
    while not stopping.is_set():
        worked = await _run_one_with_shutdown_drain(worker, stopping)
        if not worked and not stopping.is_set():
            await anyio.sleep(poll_seconds)


async def _run_one_with_shutdown_drain(
    worker: PostgresJobWorker, stopping: anyio.Event
) -> bool:
    """Apply the drain deadline only after SIGTERM, never to normal work."""

    done = anyio.Event()
    outcome = {"worked": False}

    async def execute() -> None:
        try:
            async with worker.uow_factory() as uow:
                outcome["worked"] = await worker.run_once(uow)
        finally:
            done.set()

    async def drain_after_signal(scope: anyio.CancelScope) -> None:
        await stopping.wait()
        with anyio.move_on_after(worker.shutdown_seconds):
            await done.wait()
        if not done.is_set():
            # The UoW rolls back; a new worker can safely reclaim after lease expiry.
            scope.cancel()

    async with anyio.create_task_group() as group:
        group.start_soon(execute)
        group.start_soon(drain_after_signal, group.cancel_scope)
        await done.wait()
        group.cancel_scope.cancel()
    return bool(outcome["worked"])


def _freeze_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Prevent handlers from accidentally mutating a claimed JSON document."""

    def freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return MappingProxyType(
                {str(key): freeze(item) for key, item in value.items()}
            )
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        return value

    frozen = freeze(dict(payload))
    if not isinstance(
        frozen, Mapping
    ):  # defensive: JSON object is required by the schema
        raise TerminalJobFailure("invalid_job_payload")
    return frozen
