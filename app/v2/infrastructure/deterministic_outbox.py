"""Deterministic outbox publisher for focused tests and local composition only."""
from __future__ import annotations

from collections.abc import Iterable

from app.v2.application.ports import OutboxEventMessage


class DeterministicOutboxPublisher:
    """Records every call, including deliberate duplicate delivery attempts."""

    def __init__(self, failures: Iterable[Exception] = ()) -> None:
        self.calls: list[OutboxEventMessage] = []
        self._failures = list(failures)

    async def publish(self, event: OutboxEventMessage) -> None:
        self.calls.append(event)
        if self._failures:
            raise self._failures.pop(0)
