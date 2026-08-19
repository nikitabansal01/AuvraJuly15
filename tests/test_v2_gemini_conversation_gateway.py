"""No-network proofs for the production Gemini conversation adapter."""
from __future__ import annotations

import json

import httpx
import pytest

from app.v2.application.conversation_response import (
    ConversationResponseRequest,
    ConversationSnapshotMessage,
)
from app.v2.application.plan_generation import ProviderFailure
from app.v2.infrastructure.plan_generation_providers import (
    GeminiConversationGateway,
    _extract_json_object,
)


def _request() -> ConversationResponseRequest:
    return ConversationResponseRequest(
        "general",
        "conversation.v1",
        "Offer supportive guidance.",
        (ConversationSnapshotMessage("user", "hello"),),
    )


@pytest.mark.anyio
async def test_gemini_conversation_adapter_parses_text_and_redacts_persisted_telemetry():
    seen = {}

    async def handler(request):
        seen.update({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": "I hear you.",
                "usage": {"total_input_tokens": 3, "total_output_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GeminiConversationGateway(
        api_key="secret",
        model="gemini-test",
        telemetry_hmac_key=b"k" * 32,
        client=client,
    )
    result = await gateway.respond(_request())
    assert result.content == "I hear you."
    assert result.invocation is not None
    assert result.invocation.input_hash != "hello"
    assert result.invocation.output_hash != "I hear you."
    assert "conversation_id" not in json.dumps(seen["body"])
    assert seen["body"]["store"] is False
    await client.aclose()


@pytest.mark.anyio
async def test_gemini_conversation_adapter_rejects_invalid_provider_response_without_body_leakage():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "completed"})
        )
    )
    gateway = GeminiConversationGateway(
        api_key="secret",
        model="gemini-test",
        telemetry_hmac_key=b"k" * 32,
        client=client,
    )
    with pytest.raises(ProviderFailure, match="gemini_invalid_response"):
        await gateway.respond(_request())
    await client.aclose()


def test_gemini_conversation_adapter_fails_closed_without_credentials():
    with pytest.raises(ValueError, match="API key"):
        GeminiConversationGateway(api_key="", model="x", telemetry_hmac_key=b"k" * 32)


def test_extract_json_object_handles_markdown_fences_and_prose():
    payload = {"actions": [{"category": "eat"}]}
    canonical = json.dumps(payload)
    assert _extract_json_object(canonical) == payload
    assert _extract_json_object(f"```json\n{canonical}\n```") == payload
    assert _extract_json_object(f"Here is your plan:\n```\n{canonical}\n```\nEnjoy!") == payload
    assert _extract_json_object(f"Leading prose {canonical} trailing prose") == payload


def test_extract_json_object_rejects_non_object_or_broken_json():
    assert _extract_json_object("[]") is None
    assert _extract_json_object('{"broken": ') is None
    assert _extract_json_object("no json here") is None
    assert _extract_json_object("") is None
