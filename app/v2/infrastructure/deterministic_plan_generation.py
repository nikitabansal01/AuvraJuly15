"""Deterministic test doubles for the plan-generation application ports.

They are intentionally separate from production composition.  Tests can use
them to characterize orchestration without network credentials or provider
responses, but no deployment should wire these adapters into a live service.
"""

from __future__ import annotations

import hashlib
import io
import time
from collections.abc import Mapping, Sequence
from typing import Any

from PIL import Image

from app.v2.application.plan_generation import (
    GeneratedImage,
    StructuredPlanResponse,
    invocation_metadata,
)
from app.v2.domain.plan_generation import CANONICAL_VARIANT_TYPES, EvidenceSource


class DeterministicStructuredPlanGateway:
    _TELEMETRY_KEY = b"auvra-deterministic-telemetry-key"

    async def generate(
        self,
        *,
        task: str,
        prompt_version: str,
        context: Mapping[str, Any],
        evidence: Sequence[EvidenceSource],
    ) -> StructuredPlanResponse:
        citation_url = evidence[0].canonical_url
        content = {"actions": [_action(slot, citation_url) for slot in range(1, 5)]}
        return StructuredPlanResponse(
            content=content,
            invocation=invocation_metadata(
                provider="deterministic-test",
                operation="generate",
                task=task,
                prompt_version=prompt_version,
                model="deterministic-test",
                request_payload=dict(context),
                response_payload=content,
                telemetry_hmac_key=self._TELEMETRY_KEY,
                started_at=time.monotonic(),
            ),
        )


class DeterministicEvidenceResolver:
    async def resolve(self, query: str) -> Sequence[EvidenceSource]:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        return [
            EvidenceSource(
                canonical_url=f"https://evidence.example/{digest}",
                title=f"Evidence for {query}",
            )
        ]


class DeterministicImageGenerator:
    async def generate(self, *, prompt: str) -> GeneratedImage:
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        buffer = io.BytesIO()
        Image.new("RGB", (1, 1), color=tuple(digest[:3])).save(buffer, format="PNG")
        return GeneratedImage(content=buffer.getvalue(), mime_type="image/png")


def _action(slot: int, citation_url: str) -> dict[str, Any]:
    return {
        "category": "wellbeing",
        "title": f"Action {slot}",
        "purpose": "Supports a practical wellbeing routine.",
        "instructions": ["Choose a comfortable pace."],
        "image_prompt": f"Wellbeing activity {slot}",
        "citation_urls": [citation_url],
        "variants": [
            {
                "variant_type": variant_type,
                "title": f"{variant_type} action {slot}",
                "instructions": ["Use the shorter adaptation."],
                "image_prompt": f"{variant_type} wellbeing activity {slot}",
            }
            for variant_type in CANONICAL_VARIANT_TYPES
        ],
    }
