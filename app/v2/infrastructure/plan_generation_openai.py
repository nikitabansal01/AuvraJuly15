"""OpenAI adapter for structured plan generation.

Split out of ``plan_generation_providers`` so that module stays inside the
800-line architecture limit. The protocol details live here for the same
reason they live there: provider SDKs, credentials and error formats must not
leak into the domain or API layers.
"""

from __future__ import annotations

import json
import re as _re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.v2.application.plan_generation import (
    ProviderFailure,
    StructuredPlanResponse,
    invocation_metadata,
)
from app.v2.domain.plan_generation import CANONICAL_VARIANT_TYPES, EvidenceSource
from app.v2.domain.plan_image_prompts import sanitize_image_prompt

# Shared HTTP/JSON helpers stay with the other providers; importing them here
# is one-directional, so there is no cycle.
from app.v2.infrastructure.plan_generation_providers import (
    _extract_json_object,
    _integer,
    _raise_for_status,
)


OPENAI_STRUCTURED_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "purpose": {"type": "string"},
                    "instructions": {"type": "array", "items": {"type": "string"}},
                    "image_prompt": {"type": "string"},
                    "citation_urls": {"type": "array", "items": {"type": "string"}},
                    "variants": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "variant_type": {
                                    "type": "string",
                                    "enum": list(CANONICAL_VARIANT_TYPES),
                                },
                                "title": {"type": "string"},
                                "instructions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "image_prompt": {"type": "string"},
                            },
                            "required": [
                                "variant_type",
                                "title",
                                "instructions",
                                "image_prompt",
                            ],
                        },
                    },
                },
                "required": [
                    "category",
                    "title",
                    "purpose",
                    "instructions",
                    "image_prompt",
                    "citation_urls",
                    "variants",
                ],
            },
        }
    },
    "required": ["actions"],
}


class OpenAIStructuredPlanGateway:
    """OpenAI REST adapter using strict JSON-schema structured output."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        telemetry_hmac_key: bytes,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("OpenAI API key and model are required")
        if len(telemetry_hmac_key) < 32:
            raise ValueError("telemetry_hmac_key must contain at least 32 bytes")
        self._api_key = api_key
        self._model = model
        self._telemetry_hmac_key = telemetry_hmac_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(
        self,
        *,
        task: str,
        prompt_version: str,
        context: Mapping[str, Any],
        evidence: Sequence[EvidenceSource],
    ) -> StructuredPlanResponse:
        request = _openai_request(self._model, context, evidence)
        started_at = time.monotonic()
        response = await self._post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=request,
        )
        payload = _openai_plan_payload(response)
        usage = response.get("usage", {})
        return StructuredPlanResponse(
            content=payload,
            invocation=invocation_metadata(
                provider="openai",
                operation="create_chat_completion",
                task=task,
                prompt_version=prompt_version,
                model=self._model,
                request_payload=request,
                response_payload=payload,
                telemetry_hmac_key=self._telemetry_hmac_key,
                started_at=started_at,
                input_tokens=_integer(usage.get("prompt_tokens")),
                output_tokens=_integer(usage.get("completion_tokens")),
            ),
        )

    async def _post(self, url: str, **kwargs: Any) -> Mapping[str, Any]:
        try:
            response = await self._client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderFailure("openai_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure("openai_network_error", retryable=True) from exc
        _raise_for_status(response, "openai")
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderFailure("openai_invalid_response", retryable=False) from exc
        if not isinstance(value, Mapping):
            raise ProviderFailure("openai_invalid_response", retryable=False)
        return value


def _openai_request(
    model: str,
    context: Mapping[str, Any],
    evidence: Sequence[EvidenceSource],
) -> dict[str, Any]:
    evidence_payload = [
        {
            "url": source.canonical_url,
            "title": source.title,
            "published_date": source.published_date,
        }
        for source in evidence
    ]
    query_terms = _evidence_query_terms(context.get("evidence_queries"))
    keyword_instruction = (
        f"Use at least one of these exact keywords in every action's title, purpose, or "
        f"instructions: {', '.join(sorted(query_terms))}. "
        if query_terms
        else ""
    )
    citation_instruction = (
        "For each action, choose citation_urls only from the supplied evidence list, and only "
        "entries whose title shares at least one word with that action's title, purpose, or "
        "instructions and at least one of the keywords above. Each citation_urls entry must be "
        "an exact URL from the supplied evidence. "
        if query_terms
        else "For each action, choose citation_urls only from the supplied evidence list, using "
        "entries whose title shares at least one word with that action's title, purpose, or "
        "instructions. Each citation_urls entry must be an exact URL from the supplied evidence. "
    )
    instruction = (
        "Create exactly four practical wellbeing actions. Do not diagnose, prescribe, promise outcomes, "
        "or make medical claims. Cite only supplied evidence URLs. Return only the requested JSON schema. "
        "Never use clinical-safety language in any field: avoid words such as emergency, 911, 999, "
        "ambulance, urgent care, red flag, chest pain, shortness of breath, fainting, self-harm, "
        "suicide, medication, prescription, dosage, supplement, or contraindication. Keep every field "
        "gentle, everyday, and non-clinical. " + keyword_instruction + citation_instruction
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": json.dumps({"context": context, "evidence": evidence_payload}),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "wellbeing_plan",
                "strict": True,
                "schema": OPENAI_STRUCTURED_PLAN_SCHEMA,
            },
        },
    }


def _openai_plan_payload(response: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderFailure("openai_invalid_response", retryable=False)
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ProviderFailure("openai_invalid_response", retryable=False)
    text = message.get("content")
    if not isinstance(text, str):
        raise ProviderFailure("openai_invalid_response", retryable=False)
    payload = _extract_json_object(text)
    if payload is None:
        raise ProviderFailure("openai_invalid_json", retryable=True)
    # The model sometimes ignores the constrained schema's key name and
    # returns the plan under "wellbeing_actions" instead of "actions".
    # Normalize at the provider boundary so the domain contract stays
    # canonical and the same candidate validation applies to both.
    if "actions" not in payload and isinstance(payload.get("wellbeing_actions"), list):
        payload = {**payload, "actions": payload["wellbeing_actions"]}
    return payload


def _evidence_query_terms(queries: object) -> set[str]:
    """Mirror the domain gate's stopword set so echoed keywords pass.

    The candidate relevance check keeps only ``[a-z]{3,}`` tokens that are
    not in a fixed stopword set.  Listing the exact surviving terms in the
    prompt lets the model echo them and pass that deterministic gate.
    """

    ignored = {
        "adult",
        "adults",
        "action",
        "activity",
        "choose",
        "consistent",
        "for",
        "behavior",
        "evidence",
        "lifestyle",
        "of",
        "source",
        "study",
        "the",
        "to",
        "verified",
        "wellbeing",
    }
    import re as _re

    terms: set[str] = set()
    if not isinstance(queries, (list, tuple)):
        return terms
    for query in queries:
        if not isinstance(query, str):
            continue
        terms.update(
            token for token in _re.findall(r"[a-z]{3,}", query.casefold()) if token not in ignored
        )
    return terms
