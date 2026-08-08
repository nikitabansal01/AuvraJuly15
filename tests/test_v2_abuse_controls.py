"""Focused production safeguards: bounded payloads and distributed limits."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import app.v2.runtime.abuse_controls as controls
from app.v2.application.errors import ApplicationProblem
from app.v2.domain.identity import VerifiedPrincipal


class FakeLimitBackend:
    def __init__(self, decision: controls.RateLimitDecision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    async def check(self, **kwargs):
        self.calls.append(kwargs)
        return self.decision


@pytest.mark.anyio
async def test_body_limit_returns_problem_details_before_a_handler_runs():
    app = FastAPI()
    called = False

    @app.post("/api/v2/echo")
    async def echo():
        nonlocal called
        called = True
        return {"ok": True}

    app.add_middleware(controls.RequestBodyLimitMiddleware, max_bytes=4)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v2/echo",
            content=b"12345",
            headers={"X-Request-ID": "request-1234"},
        )
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"] == "request-1234"
    assert response.json()["code"] == "request_body_too_large"
    assert not called


@pytest.mark.anyio
async def test_chunked_body_limit_rejects_without_content_length():
    app = FastAPI()

    @app.post("/api/v2/echo")
    async def echo():
        return {"ok": True}

    wrapped = controls.RequestBodyLimitMiddleware(app, max_bytes=4)
    sent = []
    chunks = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"45", "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    await wrapped(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v2/echo",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
            "http_version": "1.1",
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 413
    assert b"request_body_too_large" in sent[1]["body"]


@pytest.mark.anyio
async def test_costly_mutation_limit_returns_stable_retryable_problem(monkeypatch):
    backend = FakeLimitBackend(controls.RateLimitDecision(False, 37))
    monkeypatch.setattr(
        controls,
        "settings",
        SimpleNamespace(
            ENVIRONMENT="production",
            V2_REDIS_URL="rediss://redis.example.test/0",
            V2_COSTLY_MUTATION_LIMIT=6,
            V2_COSTLY_MUTATION_WINDOW_SECONDS=300,
        ),
    )
    monkeypatch.setattr(controls, "get_rate_limit_backend", lambda: backend)
    with pytest.raises(ApplicationProblem) as exc:
        await controls.enforce_costly_mutation_limit(
            VerifiedPrincipal("firebase", "uid-123", None, False, None)
        )
    assert exc.value.status == 429
    assert exc.value.code == "rate_limit_exceeded"
    assert exc.value.headers["Retry-After"] == "37"
    assert backend.calls[0]["bucket"] == "costly-mutation"


@pytest.mark.anyio
async def test_redis_counter_key_never_contains_the_actor_identifier():
    observed = {}

    class Redis:
        async def eval(self, script, keys, key, window):
            observed.update(
                {"script": script, "keys": keys, "key": key, "window": window}
            )
            return (1, 300)

    limiter = controls.RedisFixedWindowRateLimiter(Redis())
    assert (
        await limiter.check(
            bucket="costly-mutation",
            subject="firebase:uid-123",
            limit=6,
            window_seconds=300,
        )
    ).allowed
    assert "uid-123" not in observed["key"]


@pytest.mark.anyio
async def test_rate_limit_storage_fails_closed_in_production(monkeypatch):
    class Unavailable:
        async def check(self, **kwargs):
            raise controls.RateLimitUnavailable()

    monkeypatch.setattr(
        controls,
        "settings",
        SimpleNamespace(
            ENVIRONMENT="production",
            V2_REDIS_URL="rediss://redis.example.test/0",
            V2_PUBLIC_ONBOARDING_LIMIT=10,
            V2_PUBLIC_ONBOARDING_WINDOW_SECONDS=600,
            V2_TRUSTED_PROXY_CIDRS=[],
        ),
    )
    monkeypatch.setattr(controls, "get_rate_limit_backend", lambda: Unavailable())
    with pytest.raises(ApplicationProblem) as exc:
        await controls._check_or_raise(
            bucket="public-onboarding",
            subject="127.0.0.1",
            limit=10,
            window_seconds=600,
        )
    assert exc.value.status == 503
    assert exc.value.code == "rate_limit_unavailable"


def test_only_explicit_trusted_proxies_can_supply_forwarded_client_ip(monkeypatch):
    app = FastAPI()
    request = httpx.Request(
        "POST",
        "http://testserver/api/v2/onboarding/sessions",
        headers={"X-Forwarded-For": "203.0.113.8"},
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v2/onboarding/sessions",
        "headers": [(key.lower(), value) for key, value in request.headers.raw],
        "client": ("10.0.0.7", 1234),
        "app": app,
    }
    from starlette.requests import Request

    monkeypatch.setattr(
        controls, "settings", SimpleNamespace(V2_TRUSTED_PROXY_CIDRS=[])
    )
    assert controls._trusted_client_ip(Request(scope)) == "10.0.0.7"
    monkeypatch.setattr(
        controls,
        "settings",
        SimpleNamespace(V2_TRUSTED_PROXY_CIDRS=["10.0.0.0/8"]),
    )
    assert controls._trusted_client_ip(Request(scope)) == "203.0.113.8"


def test_rate_limit_client_retries_transient_connection_failures(monkeypatch):
    """A dropped idle TCP connection must not 503 a real user's request.

    Fail-closed is deliberate when Redis is genuinely down, but a managed
    Redis drops idle connections routinely; without a retry that single blip
    became a user-visible 503 during this deployment.
    """
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    import redis.asyncio as redis_asyncio

    captured = {}

    def fake_from_url(url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        controls.settings, "V2_REDIS_URL", "rediss://redis.example.test/0"
    )
    monkeypatch.setattr(redis_asyncio, "from_url", fake_from_url)
    controls.get_rate_limit_backend.cache_clear()
    try:
        controls.get_rate_limit_backend()
    finally:
        controls.get_rate_limit_backend.cache_clear()

    assert captured["retry"] is not None
    assert set(captured["retry_on_error"]) == {RedisConnectionError, RedisTimeoutError}
    # Fail-closed still holds: retries are bounded, not infinite.
    assert captured["retry"]._retries == 2
    assert captured["health_check_interval"] == 30


@pytest.mark.anyio
async def test_a_genuinely_unavailable_backend_still_fails_closed(monkeypatch):
    """After retries are exhausted the limiter must refuse, never allow."""

    class AlwaysFailing:
        async def eval(self, *args, **kwargs):
            raise RuntimeError("redis is down")

    limiter = controls.RedisFixedWindowRateLimiter(AlwaysFailing())
    with pytest.raises(controls.RateLimitUnavailable):
        await limiter.check(
            bucket="public-onboarding", subject="1.2.3.4", limit=10, window_seconds=600
        )
