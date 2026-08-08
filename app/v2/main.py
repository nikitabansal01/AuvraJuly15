"""The production ASGI composition root for the clean v2 API only."""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.v2.api.problem_details import _problem_response
from app.v2.api.router import router as api_v2_router
from app.v2.application.errors import ApplicationProblem
from app.v2.infrastructure.auth.firebase_runtime import initialize_v2_firebase
from app.v2.persistence.database import check_database_readiness, dispose_database
from app.v2.runtime.config import settings, validate_api_configuration
from app.v2.runtime.logging import RequestLogAdapter, configure_logging
from app.v2.runtime.abuse_controls import (
    RequestBodyLimitMiddleware,
    close_rate_limit_backend,
    public_onboarding_rate_limit_middleware,
)
from app.v2.runtime.schema import check_database_schema_head

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
logger = RequestLogAdapter(logging.getLogger(__name__), {})


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str):
        return existing
    supplied = request.headers.get("X-Request-ID", "")
    return supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())


def _v2_problem(
    request: Request, status: int, title: str, code: str, detail: str
) -> JSONResponse:
    return _problem_response(
        request,
        ApplicationProblem(status, title, code, detail),
        _request_id(request),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prove configuration, database and token verification at startup."""

    validate_api_configuration()
    try:
        await check_database_readiness()
        await check_database_schema_head()
        initialize_v2_firebase()
    except Exception:
        # Startup failures can contain driver/provider details.  The process
        # exit is the signal; logs retain only the stable event marker.
        logger.error("v2_api_startup_verification_failed")
        await dispose_database()
        raise
    logger.info("v2 API startup verification completed")
    try:
        yield
    finally:
        await close_rate_limit_backend()
        await dispose_database()
        logger.info("v2 API shutdown completed")


def create_application() -> FastAPI:
    """Compose the v2 API without importing legacy routes, models or services."""

    configure_logging(settings.LOG_LEVEL)
    app = FastAPI(
        title="AUVRA API",
        description="AUVRA v2 mobile API",
        version="2.0.0",
        openapi_version="3.1.1",
        docs_url="/docs"
        if settings.ENVIRONMENT not in {"staging", "production"}
        else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    # FastAPI may otherwise emit its library default despite the declared
    # constructor value; the checked-in mobile contract is OpenAPI 3.1.1.
    app.openapi_version = "3.1.1"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS,
        expose_headers=["ETag", "X-Request-ID"],
    )
    if settings.ENVIRONMENT in {"staging", "production"}:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

    _install_request_middleware(app)
    app.add_middleware(
        RequestBodyLimitMiddleware, max_bytes=settings.V2_MAX_REQUEST_BODY_BYTES
    )
    _register_exception_handlers(app)
    app.include_router(api_v2_router, prefix="/api/v2")
    return app


def _install_request_middleware(app: FastAPI) -> None:
    """Attach correlation and audit-safe request completion logging."""

    @app.middleware("http")
    async def correlate_and_log(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = _request_id(request)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await public_onboarding_rate_limit_middleware(request, call_next)
        except ApplicationProblem as problem:
            # public_onboarding_rate_limit_middleware is called directly from
            # this @app.middleware("http") function, not from inside the
            # routed ASGI chain that Starlette's ExceptionMiddleware wraps.
            # The @app.exception_handler(ApplicationProblem) registered below
            # only intercepts exceptions raised inside that inner chain, so an
            # ApplicationProblem raised here (for example too_many_requests's
            # 429 with its Retry-After header, or a Redis-outage 503) would
            # otherwise fall through to the generic Exception handler and
            # report a bare 500 -- correct in that it fails closed and never
            # leaks internals, but the wrong status for a client to act on.
            response = _problem_response(request, problem, request_id)
        except Exception:
            # Exception handlers below turn v2 errors into problem details.
            logger.error("v2_request_pipeline_failed", extra={"request_id": request_id})
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request completed method=%s path=%s status=%s duration_ms=%d",
            request.method,
            request.url.path,
            response.status_code,
            int((time.perf_counter() - started) * 1000),
            extra={"request_id": request_id},
        )
        return response


def _register_exception_handlers(app: FastAPI) -> None:
    """Keep v2 failures RFC 9457-shaped without changing legacy behavior."""

    @app.exception_handler(ApplicationProblem)
    async def application_problem(
        request: Request, problem: ApplicationProblem
    ) -> JSONResponse:
        return _problem_response(request, problem, _request_id(request))

    @app.exception_handler(RequestValidationError)
    async def validation_problem(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if not request.url.path.startswith("/api/v2"):
            return JSONResponse(status_code=422, content={"detail": exc.errors()})
        return _v2_problem(
            request,
            422,
            "Unprocessable Content",
            "validation_failed",
            "The request did not satisfy the API contract.",
        )

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException) -> JSONResponse:
        if not request.url.path.startswith("/api/v2"):
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _v2_problem(
            request, exc.status_code, "Request Failed", "http_error", detail
        )

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_problem(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if not request.url.path.startswith("/api/v2"):
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _v2_problem(
            request, exc.status_code, "Request Failed", "http_error", detail
        )

    @app.exception_handler(Exception)
    async def internal_problem(request: Request, _: Exception) -> JSONResponse:
        if not request.url.path.startswith("/api/v2"):
            return JSONResponse(
                status_code=500, content={"detail": "Internal server error."}
            )
        return _v2_problem(
            request,
            500,
            "Internal Server Error",
            "internal_error",
            "The request could not be completed.",
        )


app = create_application()
