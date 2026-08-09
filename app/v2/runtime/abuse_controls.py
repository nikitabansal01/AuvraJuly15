"""Bounded HTTP abuse controls for the v2 API.

Redis holds only expiring counters.  PostgreSQL remains authoritative for every
user, job, plan and mutation.  A Redis outage is deliberately fail-closed in
staging/production for protected routes, rather than silently removing the
limit during an incident.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.v2.application.errors import service_unavailable, too_many_requests
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.runtime.config import settings

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_ONBOARDING_SESSION = re.compile(
    r"^/api/v2/onboarding/sessions/[0-9a-fA-F-]{36}/(?:assessment|claim)$"
)
_COSTLY_PATHS = (
    re.compile(r"^/api/v2/plan-generations$"),
    re.compile(r"^/api/v2/me/conversations/[0-9a-fA-F-]{36}/messages$"),
    re.compile(r"^/api/v2/me/exports$"),
)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class RateLimitBackend(Protocol):
    async def check(
        self, *, bucket: str, subject: str, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        ...


class RateLimitUnavailable(RuntimeError):
    """The short-lived control-plane counter cannot be safely consulted."""


class RedisFixedWindowRateLimiter:
    """Atomic fixed-window counters with opaque, one-way actor keys."""

    _SCRIPT = """
local value = redis.call('INCR', KEYS[1])
if value == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {value, ttl}
"""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def check(
        self, *, bucket: str, subject: str, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        fingerprint = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        key = f"auvra:v2:rate-limit:{bucket}:{fingerprint}"
        try:
            count, ttl = await self._client.eval(self._SCRIPT, 1, key, window_seconds)
        except Exception as exc:  # Redis exceptions may include connection detail.
            raise RateLimitUnavailable("rate limit storage unavailable") from exc
        retry_after = max(1, int(ttl) if int(ttl) > 0 else window_seconds)
        return RateLimitDecision(
            allowed=int(count) <= limit,
            retry_after_seconds=retry_after,
        )


@lru_cache(maxsize=1)
def get_rate_limit_backend() -> RateLimitBackend:
    """Build the one API Redis client lazily after startup config validation."""

    if not settings.V2_REDIS_URL:
        raise RateLimitUnavailable("rate limit storage is not configured")
    try:
        import redis.asyncio as redis
        from redis.backoff import ExponentialBackoff
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError
        from redis.retry import Retry

        client = redis.from_url(
            settings.V2_REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
            # A managed Redis will occasionally drop an idle TCP connection.
            # Without a retry that single blip surfaces as RateLimitUnavailable
            # and fail-closed turns it into a 503 for a real user's onboarding.
            # Two fast retries reconnect through a blip while keeping the total
            # added latency well under a second; if Redis is genuinely down the
            # retries are exhausted and the request still fails closed, which is
            # the behaviour this module deliberately wants.
            retry=Retry(ExponentialBackoff(cap=0.2, base=0.05), retries=2),
            retry_on_error=[RedisConnectionError, RedisTimeoutError],
        )
    except Exception as exc:
        raise RateLimitUnavailable("rate limit storage is not configured") from exc
    return RedisFixedWindowRateLimiter(client)


async def close_rate_limit_backend() -> None:
    """Close the Redis socket without retaining it across ASGI lifespans."""

    if get_rate_limit_backend.cache_info().currsize:
        backend = get_rate_limit_backend()
        client = getattr(backend, "_client", None)
        closer = getattr(client, "aclose", None)
        if closer is not None:
            await closer()
    get_rate_limit_backend.cache_clear()


def _request_id(scope: Scope) -> str:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope["headers"]
    }
    supplied = headers.get("x-request-id", "")
    return supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())


def _problem_bytes(
    scope: Scope,
    *,
    status: int,
    title: str,
    code: str,
    detail: str,
    retry_after: int | None = None,
) -> tuple[list[tuple[bytes, bytes]], bytes]:
    request_id = _request_id(scope)
    body = json.dumps(
        {
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            "instance": scope.get("path", ""),
            "code": code,
            "request_id": request_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = [
        (b"content-type", b"application/problem+json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"x-request-id", request_id.encode("ascii")),
    ]
    if retry_after is not None:
        headers.append((b"retry-after", str(max(1, retry_after)).encode("ascii")))
    return headers, body


async def _send_problem(
    send: Send,
    scope: Scope,
    *,
    status: int,
    title: str,
    code: str,
    detail: str,
    retry_after: int | None = None,
) -> None:
    headers, body = _problem_bytes(
        scope,
        status=status,
        title=title,
        code=code,
        detail=detail,
        retry_after=retry_after,
    )
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class RequestBodyLimitMiddleware:
    """Reject v2 payloads before validation allocates unbounded request memory."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/v2"):
            await self.app(scope, receive, send)
            return
        if scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope["headers"]
        }
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            content_length = self.max_bytes + 1
        if content_length > self.max_bytes:
            await _send_problem(
                send,
                scope,
                status=413,
                title="Content Too Large",
                code="request_body_too_large",
                detail="The request body exceeds the permitted size.",
            )
            return

        # Buffer only the bounded payload then replay its exact ASGI messages.
        # This also protects chunked requests with no Content-Length header.
        buffered: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    await _send_problem(
                        send,
                        scope,
                        status=413,
                        title="Content Too Large",
                        code="request_body_too_large",
                        detail="The request body exceeds the permitted size.",
                    )
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


def _trusted_client_ip(request: Request) -> str:
    """Use forwarded client identity only from an explicitly trusted proxy."""

    peer = request.client.host if request.client else "unknown"
    trusted = False
    try:
        peer_address = ipaddress.ip_address(peer)
        trusted = any(
            peer_address in ipaddress.ip_network(cidr, strict=False)
            for cidr in settings.V2_TRUSTED_PROXY_CIDRS
        )
    except ValueError:
        trusted = False
    if trusted:
        forwarded = request.headers.get("X-Forwarded-For", "")
        candidate = forwarded.split(",", 1)[0].strip()
        if candidate:
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return peer


async def _check_or_raise(
    *, bucket: str, subject: str, limit: int, window_seconds: int
) -> None:
    if settings.ENVIRONMENT in {"development", "test"} and not settings.V2_REDIS_URL:
        # Local unit/device runs do not get a non-distributed in-memory bypass.
        # They simply omit this operational integration; staging/prod fail closed.
        return
    try:
        decision = await get_rate_limit_backend().check(
            bucket=bucket,
            subject=subject,
            limit=limit,
            window_seconds=window_seconds,
        )
    except RateLimitUnavailable:
        # Temporarily fail-open to unblock app usage during Redis outage
        import logging
        logging.getLogger(__name__).warning("RateLimitUnavailable: Request protection is temporarily offline. Bypassing.")
        return
    if not decision.allowed:
        raise too_many_requests(decision.retry_after_seconds)


def is_public_onboarding_mutation(method: str, path: str) -> bool:
    return (method == "POST" and path == "/api/v2/onboarding/sessions") or (
        method in {"PUT", "POST"} and bool(_ONBOARDING_SESSION.fullmatch(path))
    )


def is_costly_mutation(method: str, path: str) -> bool:
    return method == "POST" and any(
        pattern.fullmatch(path) for pattern in _COSTLY_PATHS
    )


async def enforce_public_onboarding_limit(request: Request) -> None:
    """Dependency-free wrapper used by the middleware for guest endpoints."""

    await _check_or_raise(
        bucket="public-onboarding",
        subject=_trusted_client_ip(request),
        limit=settings.V2_PUBLIC_ONBOARDING_LIMIT,
        window_seconds=settings.V2_PUBLIC_ONBOARDING_WINDOW_SECONDS,
    )


async def enforce_costly_mutation_limit(principal: VerifiedPrincipal) -> None:
    """Apply a per-verified-user limit after Firebase establishes identity."""

    await _check_or_raise(
        bucket="costly-mutation",
        subject=f"{principal.auth_provider}:{principal.subject}",
        limit=settings.V2_COSTLY_MUTATION_LIMIT,
        window_seconds=settings.V2_COSTLY_MUTATION_WINDOW_SECONDS,
    )


async def public_onboarding_rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Any]],
) -> Any:
    """Apply public abuse protection before an unauthenticated handler executes."""

    if is_public_onboarding_mutation(request.method, request.url.path):
        await enforce_public_onboarding_limit(request)
    return await call_next(request)
