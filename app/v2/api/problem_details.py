"""RFC 9457 responses scoped to v2 routes without changing legacy errors."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from app.v2.application.errors import ApplicationProblem

logger = logging.getLogger(__name__)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and _SAFE_REQUEST_ID.fullmatch(existing):
        return existing
    supplied = request.headers.get("X-Request-ID", "")
    if _SAFE_REQUEST_ID.fullmatch(supplied):
        return supplied
    return str(uuid.uuid4())


def _problem_response(
    request: Request,
    problem: ApplicationProblem,
    request_id: str,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": problem.type_uri,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": request.url.path,
        "code": problem.code,
        "request_id": request_id,
        **problem.extensions,
    }
    headers = {**problem.headers, "X-Request-ID": request_id}
    return JSONResponse(
        status_code=problem.status,
        content=body,
        headers=headers,
        media_type="application/problem+json",
    )


class ProblemDetailsRoute(APIRoute):
    """Convert only v2 failures to stable problem-detail documents."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            request_id = _request_id(request)
            request.state.request_id = request_id
            try:
                response = await original(request)
            except ApplicationProblem as problem:
                return _problem_response(request, problem, request_id)
            except RequestValidationError as exc:
                errors = [
                    {
                        "location": ".".join(str(part) for part in error["loc"]),
                        "message": error["msg"],
                        "kind": error["type"],
                    }
                    for error in exc.errors()
                ]
                problem = ApplicationProblem(
                    422,
                    "Unprocessable Content",
                    "validation_failed",
                    "The request did not satisfy the API contract.",
                    extensions={"errors": errors},
                )
                return _problem_response(request, problem, request_id)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
                problem = ApplicationProblem(
                    exc.status_code,
                    "Request Failed",
                    "http_error",
                    detail,
                    headers=dict(exc.headers or {}),
                )
                return _problem_response(request, problem, request_id)
            except Exception:
                # Do not serialize an exception message or traceback: provider
                # and validation exceptions can include private request data.
                logger.error(
                    "v2_request_unhandled_failure",
                    extra={"request_id": request_id, "path": request.url.path},
                )
                problem = ApplicationProblem(
                    500,
                    "Internal Server Error",
                    "internal_error",
                    "The request could not be completed.",
                )
                return _problem_response(request, problem, request_id)

            response.headers["X-Request-ID"] = request_id
            return response

        return handler
