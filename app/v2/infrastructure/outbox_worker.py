"""PostgreSQL-authoritative at-least-once outbox publication worker.

The publisher call is intentionally outside the claim transaction.  If the
process dies after the provider accepts an event but before its ``published``
update commits, a recovered lease can publish the same event again.  Consumers
must therefore durably deduplicate by ``OutboxEventMessage.id``.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import anyio
from sqlalchemy import text

from app.v2.application.ports import OutboxEventMessage, OutboxPublisher
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


UowFactory = Callable[[], SqlAlchemyUnitOfWork]
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    """Detached event snapshot and the lease token that authorizes completion."""

    id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    payload: Mapping[str, Any]
    attempt_count: int
    max_attempts: int
    lease_token: str

    def message(self) -> OutboxEventMessage:
        return OutboxEventMessage(
            id=str(self.id),
            aggregate_type=self.aggregate_type,
            aggregate_id=str(self.aggregate_id),
            event_type=self.event_type,
            payload=self.payload,
        )


class OutboxPublishFailure(RuntimeError):
    """A provider-neutral failure with a stable redacted code."""

    def __init__(self, code: str, *, retryable: bool = True) -> None:
        if not _ERROR_CODE.fullmatch(code):
            raise ValueError("outbox failure code must be a stable redacted identifier")
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class LeaseLost(RuntimeError):
    """A later owner recovered the event while this worker was publishing."""


class PostgresOutboxWorker:
    """Bounded event publisher using PostgreSQL leases as the only work authority."""

    def __init__(
        self,
        worker_id: str,
        publisher: OutboxPublisher,
        *,
        lease_seconds: int = 60,
        timeout_seconds: int = 30,
        uow_factory: UowFactory = SqlAlchemyUnitOfWork,
    ) -> None:
        if lease_seconds < 6:
            raise ValueError("lease_seconds must allow at least two heartbeats")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.worker_id = worker_id
        self.publisher = publisher
        self.lease_seconds = lease_seconds
        self.timeout_seconds = timeout_seconds
        self.uow_factory = uow_factory

    async def claim(self, uow: SqlAlchemyUnitOfWork) -> ClaimedOutboxEvent | None:
        """Atomically lease one due event and commit before provider I/O starts."""

        session = self._session(uow)
        await self._terminalize_unclaimable(session)
        token = f"{self.worker_id}:{uuid.uuid4()}"
        result = await session.execute(
            text(
                """WITH candidate AS (
                    SELECT id
                    FROM ops.outbox_events
                    WHERE ((state = 'pending' AND available_at <= now())
                       OR (state = 'running' AND lease_expires_at < now()))
                      AND attempt_count < max_attempts
                      AND jsonb_typeof(payload) = 'object'
                    ORDER BY available_at, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ops.outbox_events event
                SET state = 'running', lease_owner = :token,
                    lease_expires_at = now() + make_interval(secs => :lease),
                    heartbeat_at = now(), attempt_count = attempt_count + 1
                FROM candidate
                WHERE event.id = candidate.id
                RETURNING event.id, event.aggregate_type, event.aggregate_id,
                          event.event_type, event.payload, event.attempt_count,
                          event.max_attempts"""
            ),
            {"token": token, "lease": self.lease_seconds},
        )
        claimed = result.mappings().one_or_none()
        await uow.commit()
        if claimed is None:
            return None
        payload = claimed["payload"]
        if not isinstance(payload, dict):
            await self._fail_invalid_payload(uow, claimed["id"], token)
            return None
        return ClaimedOutboxEvent(
            id=claimed["id"],
            aggregate_type=claimed["aggregate_type"],
            aggregate_id=claimed["aggregate_id"],
            event_type=claimed["event_type"],
            payload=_freeze_payload(payload),
            attempt_count=claimed["attempt_count"],
            max_attempts=claimed["max_attempts"],
            lease_token=token,
        )

    async def run_once(self, uow: SqlAlchemyUnitOfWork) -> bool:
        event = await self.claim(uow)
        if event is None:
            return False
        try:
            await self._publish_with_heartbeats(event)
        except LeaseLost:
            return True
        except Exception as exc:
            await self._retry_or_fail(
                uow, event, self._error_code(exc), self._retryable(exc)
            )
            return True
        await self._mark_published(uow, event)
        return True

    async def heartbeat(
        self, uow: SqlAlchemyUnitOfWork, event: ClaimedOutboxEvent
    ) -> bool:
        result = await self._session(uow).execute(
            text(
                """UPDATE ops.outbox_events
                SET heartbeat_at = now(),
                    lease_expires_at = now() + make_interval(secs => :lease)
                WHERE id = :id AND state = 'running' AND lease_owner = :token"""
            ),
            {"id": event.id, "token": event.lease_token, "lease": self.lease_seconds},
        )
        await uow.commit()
        return bool(result.rowcount)

    async def _terminalize_unclaimable(self, session: Any) -> None:
        await session.execute(
            text(
                """UPDATE ops.outbox_events
                SET state = 'failed', error_code = 'invalid_event_payload', finished_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE jsonb_typeof(payload) <> 'object'
                  AND ((state = 'pending' AND available_at <= now())
                       OR (state = 'running' AND lease_expires_at < now()))"""
            )
        )
        await session.execute(
            text(
                """UPDATE ops.outbox_events
                SET state = 'failed', error_code = 'attempts_exhausted', finished_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE attempt_count >= max_attempts
                  AND ((state = 'pending' AND available_at <= now())
                       OR (state = 'running' AND lease_expires_at < now()))"""
            )
        )

    async def _fail_invalid_payload(
        self, uow: SqlAlchemyUnitOfWork, event_id: uuid.UUID, token: str
    ) -> None:
        await self._session(uow).execute(
            text(
                """UPDATE ops.outbox_events
                SET state = 'failed', error_code = 'invalid_event_payload', finished_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE id = :id AND state = 'running' AND lease_owner = :token"""
            ),
            {"id": event_id, "token": token},
        )
        await uow.commit()

    async def _mark_published(
        self, uow: SqlAlchemyUnitOfWork, event: ClaimedOutboxEvent
    ) -> None:
        result = await self._session(uow).execute(
            text(
                """UPDATE ops.outbox_events
                SET state = 'published', published_at = now(), finished_at = now(),
                    error_code = NULL, lease_owner = NULL, lease_expires_at = NULL
                WHERE id = :id AND state = 'running' AND lease_owner = :token"""
            ),
            {"id": event.id, "token": event.lease_token},
        )
        if not result.rowcount:
            await uow.rollback()
        else:
            await uow.commit()

    async def _publish_with_heartbeats(self, event: ClaimedOutboxEvent) -> None:
        done = anyio.Event()
        failure: list[Exception] = []
        publisher_scope = anyio.CancelScope()

        async def publish() -> None:
            try:
                with publisher_scope, anyio.fail_after(self.timeout_seconds):
                    await self.publisher.publish(event.message())
            except BaseException as exc:
                if isinstance(exc, Exception):
                    failure.append(exc)
                else:
                    failure.append(RuntimeError("publisher_cancelled"))
            finally:
                done.set()

        async def heartbeat() -> None:
            while not done.is_set():
                with anyio.move_on_after(max(1, self.lease_seconds // 3)):
                    await done.wait()
                if done.is_set():
                    break
                async with self.uow_factory() as heartbeat_uow:
                    if not await self.heartbeat(heartbeat_uow, event):
                        failure.append(LeaseLost())
                        publisher_scope.cancel()
                        done.set()
                        break

        async with anyio.create_task_group() as group:
            group.start_soon(publish)
            group.start_soon(heartbeat)
        if failure:
            raise failure[0]

    async def _retry_or_fail(
        self,
        uow: SqlAlchemyUnitOfWork,
        event: ClaimedOutboxEvent,
        error_code: str,
        retryable: bool,
    ) -> None:
        terminal = not retryable
        await self._session(uow).execute(
            text(
                """UPDATE ops.outbox_events
                SET state = CASE WHEN :terminal OR attempt_count >= max_attempts
                                 THEN 'failed' ELSE 'pending' END,
                    error_code = :error,
                    available_at = CASE WHEN :terminal OR attempt_count >= max_attempts
                                        THEN available_at
                                        ELSE now() + make_interval(secs => LEAST(300, 2 ^ attempt_count)) END,
                    finished_at = CASE WHEN :terminal OR attempt_count >= max_attempts
                                       THEN now() ELSE NULL END,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE id = :id AND state = 'running' AND lease_owner = :token"""
            ),
            {
                "id": event.id,
                "token": event.lease_token,
                "error": error_code,
                "terminal": terminal,
            },
        )
        await uow.commit()

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, OutboxPublishFailure):
            return exc.code
        if isinstance(exc, TimeoutError):
            return "provider_timeout"
        return "publisher_failed"

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        return not isinstance(exc, OutboxPublishFailure) or exc.retryable

    @staticmethod
    def _session(uow: SqlAlchemyUnitOfWork) -> Any:
        if uow.session is None:
            raise RuntimeError("UnitOfWork must be entered before worker use")
        return uow.session


def _freeze_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    def freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return MappingProxyType(
                {str(key): freeze(item) for key, item in value.items()}
            )
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        return value

    return freeze(dict(payload))
