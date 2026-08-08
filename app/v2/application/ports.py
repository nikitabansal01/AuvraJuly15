"""Provider-neutral ports consumed by the v2 application and API adapters."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app.v2.domain.identity import VerifiedPrincipal


class IdentityTokenVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedPrincipal:
        """Return a verified principal or raise a provider-neutral auth error."""


@dataclass(frozen=True, slots=True)
class AiResult:
    content: dict[str, Any]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class AiGateway(Protocol):
    async def invoke(
        self, *, task: str, prompt_version: str, payload: dict[str, Any]
    ) -> AiResult:
        ...


class EvidenceGateway(Protocol):
    async def resolve(self, query: str) -> list[dict[str, str]]:
        ...


class ImageGateway(Protocol):
    async def create(self, prompt: str) -> dict[str, str]:
        ...


@dataclass(frozen=True, slots=True)
class OutboxEventMessage:
    """Consumer-facing event envelope; consumers deduplicate on ``id``."""

    id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: Mapping[str, Any]


class OutboxPublisher(Protocol):
    """External event-bus boundary, deliberately invoked outside a DB transaction."""

    async def publish(self, event: OutboxEventMessage) -> None:
        """Publish at least once; consumers must persist an event-id deduplication key."""
