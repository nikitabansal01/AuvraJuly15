"""Transport-neutral application problems represented as RFC 9457 responses."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ApplicationProblem(Exception):
    status: int
    title: str
    code: str
    detail: str
    type_uri: str = "about:blank"
    headers: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


def bad_request(code: str, detail: str) -> ApplicationProblem:
    return ApplicationProblem(400, "Bad Request", code, detail)


def unauthorized(detail: str = "Authentication is required.") -> ApplicationProblem:
    return ApplicationProblem(
        401,
        "Unauthorized",
        "authentication_required",
        detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(code: str, detail: str) -> ApplicationProblem:
    return ApplicationProblem(403, "Forbidden", code, detail)


def not_found(resource: str) -> ApplicationProblem:
    return ApplicationProblem(
        404,
        "Not Found",
        "resource_not_found",
        f"{resource} was not found.",
    )


def conflict(code: str, detail: str) -> ApplicationProblem:
    return ApplicationProblem(409, "Conflict", code, detail)


def precondition_required(detail: str) -> ApplicationProblem:
    return ApplicationProblem(428, "Precondition Required", "precondition_required", detail)


def precondition_failed(detail: str) -> ApplicationProblem:
    return ApplicationProblem(412, "Precondition Failed", "precondition_failed", detail)


def unprocessable_content(code: str, detail: str) -> ApplicationProblem:
    return ApplicationProblem(422, "Unprocessable Content", code, detail)


def service_unavailable(code: str, detail: str) -> ApplicationProblem:
    return ApplicationProblem(503, "Service Unavailable", code, detail)


def too_many_requests(retry_after_seconds: int) -> ApplicationProblem:
    """Return a stable, retryable RFC 9457 limit response."""

    return ApplicationProblem(
        429,
        "Too Many Requests",
        "rate_limit_exceeded",
        "Too many requests were received. Please retry later.",
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )
