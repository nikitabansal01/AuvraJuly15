"""Tests for the Cloudflare Workers AI image provider adapter."""

import base64

import pytest

from app.services.image_library_service import ImageLibraryService


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_cloudflare_provider_decodes_image(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "secret-token")
    monkeypatch.setenv("IMAGE_PROVIDER", "cloudflare")
    service = ImageLibraryService()
    await service.client.aclose()
    expected = b"real-image-bytes"
    service.client = _Client(
        _Response(
            200,
            {
                "success": True,
                "result": {"image": base64.b64encode(expected).decode()},
                "errors": [],
            },
        )
    )

    result = await service._call_cloudflare_image("Flax seeds", "food", "hero")

    assert result == expected
    url, request = service.client.calls[0]
    assert url.endswith("/ai/run/@cf/black-forest-labs/flux-1-schnell")
    assert request["json"]["steps"] == 4
    assert request["headers"]["Authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_cloudflare_provider_handles_rate_limit(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "secret-token")
    service = ImageLibraryService()
    await service.client.aclose()
    service.client = _Client(_Response(429, {"success": False, "errors": []}))

    result = await service._call_cloudflare_image("Flax seeds")

    assert result is None
    assert service._cloudflare_retry_after > 0


@pytest.mark.asyncio
async def test_cloudflare_provider_caps_enhanced_prompt(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "secret-token")
    service = ImageLibraryService()
    await service.client.aclose()
    service.client = _Client(
        _Response(
            200,
            {
                "success": True,
                "result": {"image": base64.b64encode(b"image").decode()},
                "errors": [],
            },
        )
    )
    monkeypatch.setattr(service, "_enhance_prompt", lambda *args: "x" * 3000)

    await service._call_cloudflare_image("Long prompt")

    _, request = service.client.calls[0]
    assert len(request["json"]["prompt"]) == 2000
