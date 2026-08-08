"""Test-only deterministic adapters; production adapters belong beside them."""
from __future__ import annotations

import hashlib
from app.v2.application.ports import AiResult


class DeterministicAiGateway:
    async def invoke(
        self, *, task: str, prompt_version: str, payload: dict
    ) -> AiResult:
        digest = hashlib.sha256(
            f"{task}:{prompt_version}:{sorted(payload.items())}".encode()
        ).hexdigest()
        return AiResult(
            content={"digest": digest},
            model="deterministic-test",
            input_tokens=0,
            output_tokens=0,
        )


class DeterministicEvidenceGateway:
    async def resolve(self, query: str) -> list[dict[str, str]]:
        return [
            {
                "url": "https://evidence.invalid/"
                + hashlib.sha256(query.encode()).hexdigest()[:12],
                "title": "Deterministic evidence",
            }
        ]


class DeterministicImageGateway:
    async def create(self, prompt: str) -> dict[str, str]:
        return {
            "public_url": "https://images.invalid/"
            + hashlib.sha256(prompt.encode()).hexdigest(),
            "alt_text": "Generated illustration",
        }
