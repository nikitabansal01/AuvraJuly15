"""Provider-neutral orchestration for generating a complete v2 plan bundle.

The orchestrator does not own a Unit of Work.  It only makes external calls,
validates their results, and returns a self-contained bundle for a later,
short atomic persistence/publication transaction.
"""

from __future__ import annotations

import asyncio
import logging
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from app.v2.domain.plan_generation import (
    EvidenceSource,
    PlanCandidate,
    PlanCandidateRejected,
    candidate_from_payload,
    validate_candidate_evidence,
)
from app.v2.domain.plan_image_prompts import image_prompt_token

logger = logging.getLogger(__name__)


class ProviderFailure(RuntimeError):
    """A sanitized provider failure suitable for a job retry decision."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        observed_invocations: Sequence[AiInvocationMetadata] = (),
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.observed_invocations = tuple(observed_invocations)


@dataclass(frozen=True, slots=True)
class AiInvocationMetadata:
    provider: str
    operation: str
    task: str
    prompt_version: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_minor: int
    latency_ms: int
    result_status: str
    input_hash: str
    output_hash: str | None
    currency_code: str = "USD"
    price_version: str = "provider-default.v1"


@dataclass(frozen=True, slots=True)
class StructuredPlanResponse:
    content: Mapping[str, Any]
    invocation: AiInvocationMetadata


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    content: bytes
    mime_type: str
    invocation: AiInvocationMetadata | None = None


@dataclass(frozen=True, slots=True)
class StoredMedia:
    provider: str
    bucket: str
    object_key: str
    public_url: str
    content_sha256: str
    mime_type: str
    width: int | None
    height: int | None


@dataclass(frozen=True, slots=True)
class GeneratedPlanAsset:
    role: str
    variant_type: str | None
    action_slot: int
    alt_text: str
    media: StoredMedia
    invocation: AiInvocationMetadata | None = None


@dataclass(frozen=True, slots=True)
class PlanGenerationRequest:
    task: str
    prompt_version: str
    request_context: Mapping[str, Any]
    evidence_queries: tuple[str, ...]
    generation_job_id: str | None = None


@dataclass(frozen=True, slots=True)
class PlanGenerationBundle:
    candidate: PlanCandidate
    evidence_sources: tuple[EvidenceSource, ...]
    assets: tuple[GeneratedPlanAsset, ...]
    invocation: AiInvocationMetadata

    @property
    def invocations(self) -> tuple[AiInvocationMetadata, ...]:
        image_calls = tuple(
            asset.invocation for asset in self.assets if asset.invocation is not None
        )
        return (self.invocation, *image_calls)


class StructuredPlanGateway(Protocol):
    async def generate(
        self,
        *,
        task: str,
        prompt_version: str,
        context: Mapping[str, Any],
        evidence: Sequence[EvidenceSource],
    ) -> StructuredPlanResponse:
        ...


class EvidenceResolver(Protocol):
    async def resolve(self, query: str) -> Sequence[EvidenceSource]:
        ...


class ImageGenerator(Protocol):
    async def generate(self, *, prompt: str) -> GeneratedImage:
        ...


class PermanentMediaStore(Protocol):
    async def put(
        self, *, content: bytes, mime_type: str, object_key: str
    ) -> StoredMedia:
        ...

    async def delete(self, *, object_key: str) -> None:
        ...


def _action_count(payload: Mapping[str, Any]) -> int:
    actions = payload.get("actions")
    return len(actions) if isinstance(actions, list) else -1


def _first_action_shape(payload: Mapping[str, Any]) -> str:
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        return _shape_of(actions)
    action = actions[0]
    if not isinstance(action, Mapping):
        return _shape_of(action)
    return ", ".join(
        f"{key}={_shape_of(value)}" for key, value in action.items()
    )


def _shape_of(value: object) -> str:
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, Mapping):
        return f"dict{tuple(sorted(value.keys()))}"
    if isinstance(value, str):
        return f"str({len(value)})"
    return type(value).__name__


class PlanGenerationOrchestrator:
    """Build an unpublished, complete plan bundle; never exposes partial media."""

    def __init__(
        self,
        *,
        plan_gateway: StructuredPlanGateway,
        evidence_resolver: EvidenceResolver,
        image_generator: ImageGenerator,
        media_store: PermanentMediaStore,
    ) -> None:
        self._plan_gateway = plan_gateway
        self._evidence_resolver = evidence_resolver
        self._image_generator = image_generator
        self._media_store = media_store

    async def generate(self, request: PlanGenerationRequest) -> PlanGenerationBundle:
        """Run network-bound work outside a transaction and clean up on failure."""

        sources = await self._resolve_evidence(request.evidence_queries)
        # The provider never sees the bounded evidence queries, yet the
        # domain gate requires each action's visible text to share
        # vocabulary with them. Surface the queries (safe categorical
        # strings, never raw answers) so the model can echo the same terms.
        provider_context = dict(request.request_context)
        provider_context["evidence_queries"] = list(request.evidence_queries)
        response = await self._plan_gateway.generate(
            task=request.task,
            prompt_version=request.prompt_version,
            context=provider_context,
            evidence=sources,
        )
        logger.info(
            "plan gateway response keys=%s actions_count=%s wellbeing=%s",
            sorted(response.content.keys()),
            _action_count(response.content),
            _shape_of(response.content.get("wellbeing_actions")),
        )
        try:
            candidate = candidate_from_payload(response.content)
        except PlanCandidateRejected as exc:
            logger.warning(
                "plan candidate rejected reason=%s first_action=%s",
                exc.reason_code,
                _first_action_shape(response.content),
            )
            raise
        validate_candidate_evidence(candidate, sources, request.evidence_queries)
        uploaded: list[GeneratedPlanAsset] = []
        observed_images: list[AiInvocationMetadata] = []
        try:
            for action in candidate.actions:
                uploaded.append(
                    await self._generate_asset(
                        request.generation_job_id,
                        action.slot,
                        "hero",
                        None,
                        action.title,
                        observed_images,
                    )
                )
                for variant in action.variants:
                    uploaded.append(
                        await self._generate_asset(
                            request.generation_job_id,
                            action.slot,
                            "variant",
                            variant.variant_type,
                            variant.title,
                            observed_images,
                        )
                    )
            _require_distinct_media(uploaded)
        except ProviderFailure as exc:
            await self._cleanup(uploaded)
            raise ProviderFailure(
                exc.code,
                retryable=exc.retryable,
                observed_invocations=(
                    response.invocation,
                    *observed_images,
                    *exc.observed_invocations,
                ),
            ) from exc
        except BaseException:
            await self._cleanup(uploaded)
            raise
        return PlanGenerationBundle(
            candidate=candidate,
            evidence_sources=tuple(sources),
            assets=tuple(uploaded),
            invocation=response.invocation,
        )

    async def _resolve_evidence(
        self, queries: Sequence[str]
    ) -> tuple[EvidenceSource, ...]:
        if not queries:
            raise PlanCandidateRejected("candidate_evidence_missing_queries")
        resolved = await asyncio.gather(
            *(self._evidence_resolver.resolve(query) for query in queries)
        )
        unique = {
            source.canonical_url: source for sources in resolved for source in sources
        }
        if not unique:
            raise PlanCandidateRejected("candidate_evidence_missing_sources")
        return tuple(unique.values())

    async def _generate_asset(
        self,
        generation_job_id: str | None,
        slot: int,
        role: str,
        variant_type: str | None,
        alt_text: str,
        observed_invocations: list[AiInvocationMetadata],
    ) -> GeneratedPlanAsset:
        try:
            prompt = image_prompt_token(slot=slot, role=role, variant_type=variant_type)
        except ValueError as exc:
            raise PlanCandidateRejected("candidate_image_template_invalid") from exc
        image = await self._image_generator.generate(prompt=prompt)
        if image.invocation is not None:
            observed_invocations.append(image.invocation)
        media = await self._media_store.put(
            content=image.content,
            mime_type=image.mime_type,
            object_key=_media_key(
                generation_job_id,
                slot,
                role,
                variant_type,
                image.content,
                image.mime_type,
            ),
        )
        return GeneratedPlanAsset(
            role=role,
            variant_type=variant_type,
            action_slot=slot,
            alt_text=alt_text,
            media=media,
            invocation=image.invocation,
        )

    async def _cleanup(self, uploaded: Sequence[GeneratedPlanAsset]) -> None:
        results = await asyncio.gather(
            *(
                self._media_store.delete(object_key=asset.media.object_key)
                for asset in uploaded
            ),
            return_exceptions=True,
        )
        del results


def invocation_metadata(
    *,
    provider: str,
    operation: str,
    task: str,
    prompt_version: str,
    model: str,
    request_payload: Mapping[str, Any],
    response_payload: Mapping[str, Any] | None,
    telemetry_hmac_key: bytes,
    started_at: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_minor: int = 0,
    result_status: str = "succeeded",
) -> AiInvocationMetadata:
    """Create keyed, non-reversible metadata without retaining health text."""

    if len(telemetry_hmac_key) < 32:
        raise ValueError("telemetry_hmac_key must contain at least 32 bytes")

    return AiInvocationMetadata(
        provider=provider,
        operation=operation,
        task=task,
        prompt_version=prompt_version,
        model=model,
        input_tokens=max(input_tokens, 0),
        output_tokens=max(output_tokens, 0),
        cost_minor=max(cost_minor, 0),
        latency_ms=max(int((time.monotonic() - started_at) * 1000), 0),
        result_status=result_status,
        input_hash=_payload_hash(request_payload, telemetry_hmac_key),
        output_hash=(
            _payload_hash(response_payload, telemetry_hmac_key)
            if response_payload is not None
            else None
        ),
    )


def _payload_hash(payload: Mapping[str, Any], key: bytes) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def _media_key(
    generation_job_id: str | None,
    slot: int,
    role: str,
    variant_type: str | None,
    content: bytes,
    mime_type: str,
) -> str:
    digest = hashlib.sha256(content).hexdigest()
    suffix = variant_type or "hero"
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(mime_type)
    if extension is None:
        raise ProviderFailure("unsupported_image_type", retryable=False)
    namespace = generation_job_id or "test"
    if not namespace or "/" in namespace or len(namespace) > 64:
        raise ProviderFailure("invalid_generation_job_namespace", retryable=False)
    return (
        f"plans/v2/{namespace}/{digest[:2]}/{digest}-{slot}-{role}-{suffix}.{extension}"
    )


def _require_distinct_media(assets: Sequence[GeneratedPlanAsset]) -> None:
    if len(assets) != 16 or len({asset.media.content_sha256 for asset in assets}) != 16:
        raise PlanCandidateRejected("candidate_media_not_distinct")
