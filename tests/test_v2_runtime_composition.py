"""Regression tests for the v2-only process composition boundary."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI

import app.v2.api.problem_details as problem_details
import app.v2.runtime.config as runtime_config
import app.v2.main as v2_main
from app.v2.main import create_application
from app.v2.runtime.logging import RedactingFilter, RedactingFormatter


def _settings(**overrides):
    values = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://user:pass@example.test:5432/auvra?sslmode=require",
        "DATABASE_CONNECTION_MODE": "direct",
        "DATABASE_POOL_SIZE": 5,
        "DATABASE_MAX_OVERFLOW": 2,
        "DATABASE_POOL_TIMEOUT_SECONDS": 30,
        "DATABASE_POOL_RECYCLE_SECONDS": 1800,
        "DATABASE_STATEMENT_TIMEOUT_MS": 15000,
        "ALLOWED_HOSTS": ["api.example.test"],
        "CORS_ORIGINS": ["https://app.example.test"],
        "CORS_ALLOW_METHODS": ["GET", "POST"],
        "CORS_ALLOW_HEADERS": ["Authorization", "Content-Type"],
        "LOG_LEVEL": "INFO",
        "FIREBASE_PROJECT_ID": "project",
        "FIREBASE_PRIVATE_KEY_ID": "private-key-id",
        "FIREBASE_PRIVATE_KEY": "private-key",
        "FIREBASE_CLIENT_EMAIL": "service@example.test",
        "FIREBASE_CLIENT_ID": "client-id",
        "V2_GUEST_PROOF_SECRET": "g" * 32,
        "V2_REQUIRED_CONSENT_VERSIONS": {
            "privacy": "privacy.v1",
            "health_data_processing": "health-data.v1",
        },
        "V2_GEMINI_API_KEY": "",
        "V2_GEMINI_MODEL": "gemini-2.5-flash",
        # Mirrors RuntimeSettings; a field the validators read but the fixture
        # omits fails as an AttributeError rather than a useful assertion.
        "V2_OPENAI_API_KEY": "",
        "V2_OPENAI_MODEL": "gpt-5-mini",
        "V2_TELEMETRY_HMAC_KEY": "",
        "V2_CLOUDFLARE_ACCOUNT_ID": "",
        "V2_CLOUDFLARE_API_TOKEN": "",
        "V2_CLOUDFLARE_IMAGE_MODEL": "@cf/test",
        "V2_SUPABASE_URL": "",
        "V2_SUPABASE_SERVICE_ROLE_KEY": "",
        "V2_PLAN_MEDIA_BUCKET": "plan-images",
        "V2_PUBMED_TOOL": "auvra",
        "V2_PUBMED_EMAIL": "",
        "V2_PUBMED_MIN_INTERVAL_SECONDS": 0.34,
        "V2_WORKER_LEASE_SECONDS": 60,
        "V2_PLAN_JOB_TIMEOUT_SECONDS": 300,
        "V2_CONVERSATION_JOB_TIMEOUT_SECONDS": 90,
        "V2_ACCOUNT_EXPORT_ENCRYPTION_KEY": "",
        "V2_ACCOUNT_EXPORT_BUCKET": "account-exports",
        "V2_REDIS_URL": "rediss://redis.example.test:6380/0",
        "V2_MAX_REQUEST_BODY_BYTES": 1_048_576,
        "V2_PUBLIC_ONBOARDING_LIMIT": 10,
        "V2_PUBLIC_ONBOARDING_WINDOW_SECONDS": 600,
        "V2_COSTLY_MUTATION_LIMIT": 6,
        "V2_COSTLY_MUTATION_WINDOW_SECONDS": 300,
        "V2_TRUSTED_PROXY_CIDRS": [],
        "V2_DELETION_ENABLED": False,
        "V2_DELETION_APPROVAL_REFERENCE": "",
        "AUVRA_DELETION_RECEIPT_HMAC_KEY": "",
        "V2_WORKER_SHUTDOWN_SECONDS": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_api_configuration_requires_no_worker_provider_credentials(monkeypatch):
    monkeypatch.setattr(runtime_config, "settings", _settings())
    runtime_config.validate_api_configuration()
    with pytest.raises(RuntimeError, match="plan-worker"):
        runtime_config.validate_plan_worker_configuration()


def test_api_configuration_accepts_firebase_credentials_without_optional_metadata(
    monkeypatch,
):
    """private_key_id/client_id are optional Admin SDK metadata, not secrets.

    google.oauth2.service_account.Credentials.from_service_account_info only
    requires client_email, token_uri and private_key to build a working,
    cryptographically valid credential; verified against the library source.
    A deployment whose original service-account JSON download is lost but
    whose private_key is intact and unchanged must still be able to start.
    """
    monkeypatch.setattr(
        runtime_config,
        "settings",
        _settings(FIREBASE_PRIVATE_KEY_ID="", FIREBASE_CLIENT_ID=""),
    )
    runtime_config.validate_api_configuration()


def test_api_configuration_still_rejects_a_missing_private_key(monkeypatch):
    """The field that is actually the secret must still fail closed."""
    monkeypatch.setattr(
        runtime_config,
        "settings",
        _settings(FIREBASE_PRIVATE_KEY=""),
    )
    with pytest.raises(RuntimeError, match="FIREBASE_PRIVATE_KEY is missing"):
        runtime_config.validate_api_configuration()


def test_api_configuration_rejects_wildcard_production_hosts(monkeypatch):
    monkeypatch.setattr(runtime_config, "settings", _settings(ALLOWED_HOSTS=["*"]))
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        runtime_config.validate_api_configuration()


def test_api_configuration_permits_empty_cors_for_mobile_only_production(monkeypatch):
    monkeypatch.setattr(runtime_config, "settings", _settings(CORS_ORIGINS=[]))
    runtime_config.validate_api_configuration()


def test_api_configuration_rejects_wildcard_cors(monkeypatch):
    monkeypatch.setattr(runtime_config, "settings", _settings(CORS_ORIGINS=["*"]))
    with pytest.raises(RuntimeError, match="wildcard origin"):
        runtime_config.validate_api_configuration()


@pytest.mark.parametrize(
    "value",
    [
        "private-key",
        "service@example.test",
        "postgresql://user:pass@example.test/auvra",
    ],
)
def test_placeholder_detection_does_not_reject_ordinary_configuration_values(value):
    assert not runtime_config._missing_or_placeholder(value)


@pytest.mark.parametrize("value", ["", "your-api-key", "[your-secret]", "changeme"])
def test_placeholder_detection_rejects_explicit_placeholder_values(value):
    assert runtime_config._missing_or_placeholder(value)


def test_api_configuration_rejects_worker_secrets(monkeypatch):
    monkeypatch.setattr(runtime_config, "settings", _settings(V2_GEMINI_API_KEY="secret"))
    with pytest.raises(RuntimeError, match="worker-only credentials"):
        runtime_config.validate_api_configuration()


@pytest.mark.parametrize(
    "name",
    [
        "V2_ACCOUNT_EXPORT_ENCRYPTION_KEY",
        "V2_DELETION_APPROVAL_REFERENCE",
        "AUVRA_DELETION_RECEIPT_HMAC_KEY",
    ],
)
def test_api_configuration_rejects_account_worker_secrets(monkeypatch, name):
    monkeypatch.setattr(runtime_config, "settings", _settings(**{name: "secret-value"}))
    with pytest.raises(RuntimeError, match="worker-only credentials"):
        runtime_config.validate_api_configuration()


def test_worker_configuration_requires_tls_and_sane_deadlines(monkeypatch):
    monkeypatch.setattr(
        runtime_config,
        "settings",
        _settings(
            DATABASE_URL="postgresql://user:pass@example.test:5432/auvra",
            FIREBASE_PROJECT_ID="",
            FIREBASE_PRIVATE_KEY_ID="",
            FIREBASE_PRIVATE_KEY="",
            FIREBASE_CLIENT_EMAIL="",
            FIREBASE_CLIENT_ID="",
            V2_GEMINI_API_KEY="gemini-secret",
            V2_TELEMETRY_HMAC_KEY="t" * 32,
            V2_CLOUDFLARE_ACCOUNT_ID="account-id",
            V2_CLOUDFLARE_API_TOKEN="cloudflare-secret",
            V2_SUPABASE_URL="https://project.supabase.test",
            V2_SUPABASE_SERVICE_ROLE_KEY="service-role-secret",
            V2_PUBMED_EMAIL="operations@example.test",
            V2_PLAN_JOB_TIMEOUT_SECONDS=5,
        ),
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL must require TLS"):
        runtime_config.validate_plan_worker_configuration()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"V2_REDIS_URL": ""}, "V2_REDIS_URL is required"),
        ({"V2_REDIS_URL": "redis://redis.example.test/0"}, "must use TLS"),
        (
            {"DATABASE_CONNECTION_MODE": "pooler"},
            "DATABASE_POOL_SIZE and DATABASE_MAX_OVERFLOW",
        ),
        ({"DATABASE_POOL_SIZE": 11}, "DATABASE_POOL_SIZE must be between"),
    ],
)
def test_api_configuration_fails_closed_for_operational_controls(monkeypatch, overrides, message):
    monkeypatch.setattr(runtime_config, "settings", _settings(**overrides))
    with pytest.raises(RuntimeError, match=message):
        runtime_config.validate_api_configuration()


def test_combined_worker_fails_closed_when_gemini_credentials_are_missing(monkeypatch):
    monkeypatch.setattr(
        runtime_config,
        "settings",
        _settings(
            FIREBASE_PROJECT_ID="",
            FIREBASE_PRIVATE_KEY_ID="",
            FIREBASE_PRIVATE_KEY="",
            FIREBASE_CLIENT_EMAIL="",
            FIREBASE_CLIENT_ID="",
            V2_TELEMETRY_HMAC_KEY="t" * 32,
            V2_CLOUDFLARE_ACCOUNT_ID="account",
            V2_CLOUDFLARE_API_TOKEN="token",
            V2_SUPABASE_URL="https://project.supabase.test",
            V2_SUPABASE_SERVICE_ROLE_KEY="role",
            V2_PUBMED_EMAIL="ops@example.test",
        ),
    )
    with pytest.raises(RuntimeError, match="V2_GEMINI_API_KEY"):
        runtime_config.validate_plan_worker_configuration()


def test_deletion_worker_requires_explicit_approval_and_credentials(monkeypatch):
    monkeypatch.setattr(
        runtime_config,
        "settings",
        _settings(
            FIREBASE_PROJECT_ID="project",
            FIREBASE_PRIVATE_KEY_ID="key-id",
            FIREBASE_PRIVATE_KEY="private-key",
            FIREBASE_CLIENT_EMAIL="worker@example.test",
            FIREBASE_CLIENT_ID="client-id",
            V2_GEMINI_API_KEY="gemini-secret",
            V2_TELEMETRY_HMAC_KEY="t" * 32,
            V2_CLOUDFLARE_ACCOUNT_ID="account",
            V2_CLOUDFLARE_API_TOKEN="token",
            V2_SUPABASE_URL="https://project.supabase.test",
            V2_SUPABASE_SERVICE_ROLE_KEY="role",
            V2_PUBMED_EMAIL="ops@example.test",
            V2_DELETION_ENABLED=True,
            V2_REDIS_URL="",
        ),
    )
    with pytest.raises(RuntimeError, match="V2_REDIS_URL"):
        runtime_config.validate_plan_worker_configuration()


def test_deletion_worker_accepts_firebase_credentials_without_optional_metadata(
    monkeypatch,
):
    """Same relaxation as the API: private_key_id/client_id are not required."""
    monkeypatch.setattr(
        runtime_config,
        "settings",
        _settings(
            FIREBASE_PROJECT_ID="project",
            FIREBASE_PRIVATE_KEY_ID="",
            FIREBASE_PRIVATE_KEY="private-key",
            FIREBASE_CLIENT_EMAIL="worker@example.test",
            FIREBASE_CLIENT_ID="",
            V2_GEMINI_API_KEY="gemini-secret",
            V2_OPENAI_API_KEY="openai-secret",
            V2_TELEMETRY_HMAC_KEY="t" * 32,
            V2_CLOUDFLARE_ACCOUNT_ID="account",
            V2_CLOUDFLARE_API_TOKEN="token",
            V2_SUPABASE_URL="https://project.supabase.test",
            V2_SUPABASE_SERVICE_ROLE_KEY="role",
            V2_PUBMED_EMAIL="ops@example.test",
            V2_ACCOUNT_EXPORT_ENCRYPTION_KEY="export-key",
            V2_ACCOUNT_EXPORT_BUCKET="account-exports",
            V2_DELETION_ENABLED=True,
            V2_DELETION_APPROVAL_REFERENCE="change-request-1234",
            AUVRA_DELETION_RECEIPT_HMAC_KEY="r" * 32,
            V2_REDIS_URL="rediss://redis.example.test/0",
        ),
    )
    runtime_config.validate_plan_worker_configuration()


def test_log_redaction_formats_before_removing_secret_values():
    record = __import__("logging").LogRecord(
        "test", 20, __file__, 1, "provider token=%s", ("very-secret",), None
    )
    assert RedactingFilter().filter(record)
    assert record.getMessage() == "provider token=[REDACTED]"


def test_log_filter_never_serializes_secret_exception_text_or_traceback():
    try:
        raise RuntimeError("upstream api_key=very-secret")
    except RuntimeError:
        exc_info = __import__("sys").exc_info()
    record = __import__("logging").LogRecord(
        "test", 40, __file__, 1, "provider failure", (), exc_info
    )
    assert RedactingFilter().filter(record)
    formatted = RedactingFormatter("%(message)s").format(record)
    assert "very-secret" not in formatted
    assert "RuntimeError" not in formatted


@pytest.mark.anyio
async def test_startup_failure_logs_only_stable_event_and_not_exception_text(
    monkeypatch,
):
    async def fail_readiness() -> None:
        raise RuntimeError("provider api_key=very-secret")

    async def dispose() -> None:
        return None

    events = []
    monkeypatch.setattr(v2_main, "validate_api_configuration", lambda: None)
    monkeypatch.setattr(v2_main, "check_database_readiness", fail_readiness)
    monkeypatch.setattr(v2_main, "dispose_database", dispose)
    monkeypatch.setattr(v2_main.logger, "error", lambda event: events.append(event))
    with pytest.raises(RuntimeError):
        async with v2_main.lifespan(create_application()):
            pass
    assert events == ["v2_api_startup_verification_failed"]


@pytest.mark.anyio
async def test_route_failure_logs_a_stable_event_without_exception_text(monkeypatch):
    events = []
    app = FastAPI()
    router = APIRouter(route_class=problem_details.ProblemDetailsRoute)

    @router.get("/explode")
    async def explode() -> None:
        raise RuntimeError("provider token=very-secret")

    app.include_router(router, prefix="/api/v2")
    monkeypatch.setattr(
        problem_details.logger,
        "error",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v2/explode", headers={"X-Request-ID": "request-1234"})
    assert response.status_code == 500
    assert events[0][0] == "v2_request_unhandled_failure"
    assert "very-secret" not in repr(events)


@pytest.mark.anyio
async def test_api_lifespan_requires_database_schema_at_checked_in_head(monkeypatch):
    events = []

    async def readiness() -> None:
        events.append("database")

    async def schema() -> None:
        events.append("schema")

    async def dispose() -> None:
        events.append("dispose")

    monkeypatch.setattr(v2_main, "validate_api_configuration", lambda: events.append("config"))
    monkeypatch.setattr(v2_main, "check_database_readiness", readiness)
    monkeypatch.setattr(v2_main, "check_database_schema_head", schema)
    monkeypatch.setattr(v2_main, "initialize_v2_firebase", lambda: events.append("firebase"))
    monkeypatch.setattr(v2_main, "dispose_database", dispose)

    async with v2_main.lifespan(create_application()):
        assert events == ["config", "database", "schema", "firebase"]
    assert events[-1] == "dispose"


@pytest.mark.anyio
async def test_v2_process_exposes_only_v2_application_routes_and_problem_details():
    app = create_application()
    paths = {route.path for route in app.routes}
    assert "/api/v1/health/ready" not in paths
    assert "/health" not in paths
    assert "/api/v2/health/live" in paths

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v2/does-not-exist", headers={"X-Request-ID": "request-1234"}
        )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"] == "request-1234"
    assert response.json()["request_id"] == "request-1234"


@pytest.mark.anyio
async def test_a_rate_limit_problem_keeps_its_status_and_retry_after_header(
    monkeypatch,
):
    """public_onboarding_rate_limit_middleware runs inside the custom
    @app.middleware("http") wrapper, outside the routed ASGI chain that
    @app.exception_handler(ApplicationProblem) actually intercepts. An
    ApplicationProblem raised there previously fell through to the generic
    Exception handler and reported a bare 500, losing the real status and any
    Retry-After header a client needs to back off correctly.
    """
    from app.v2.application.errors import too_many_requests

    async def fake_rate_limit(request, call_next):  # type: ignore[no-untyped-def]
        raise too_many_requests(retry_after_seconds=17)

    monkeypatch.setattr(v2_main, "public_onboarding_rate_limit_middleware", fake_rate_limit)
    app = create_application()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v2/onboarding/sessions",
            json={},
            headers={"X-Request-ID": "request-1234"},
        )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "rate_limit_exceeded"


@pytest.mark.anyio
async def test_a_rate_limit_backend_outage_reports_503_not_500(monkeypatch):
    """The Redis-unavailable case: this is exactly the failure this session
    hit live in production before the Key Value instance's IP allow list was
    configured. The app already failed safe (no leaked internals) but under
    the wrong status code."""
    from app.v2.application.errors import service_unavailable

    async def fake_rate_limit(request, call_next):  # type: ignore[no-untyped-def]
        raise service_unavailable(
            "rate_limit_unavailable",
            "Request protection is temporarily unavailable.",
        )

    monkeypatch.setattr(v2_main, "public_onboarding_rate_limit_middleware", fake_rate_limit)
    app = create_application()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v2/onboarding/sessions",
            json={},
            headers={"X-Request-ID": "request-1234"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == "rate_limit_unavailable"


def test_runtime_modules_do_not_import_legacy_process_components():
    root = __import__("pathlib").Path(__file__).parents[1]
    runtime_sources = [
        root / "app/v2/main.py",
        root / "app/v2/persistence/database.py",
        root / "app/v2/persistence/uow.py",
        root / "app/v2/api/routes/onboarding.py",
        root / "app/v2/infrastructure/plan_worker_entrypoint.py",
    ]
    forbidden = (
        "app.core.database",
        "app.core.firebase",
        "app.core.config",
        "app.api.v1",
        "app.langgraph",
        "app.services",
    )
    combined = "\n".join(path.read_text() for path in runtime_sources)
    assert not any(item in combined for item in forbidden)
    entrypoint = (root / "main.py").read_text()
    assert "app.v2.main:app" in entrypoint
    assert "app.core.config" not in entrypoint


def test_render_blueprint_uses_v2_health_predeploy_and_separate_secret_sets():
    root = __import__("pathlib").Path(__file__).parents[1]
    blueprint = (root / "render.yaml").read_text()
    assert "preDeployCommand: alembic upgrade head" in blueprint
    assert "healthCheckPath: /api/v2/health/ready" in blueprint
    assert "uvicorn app.v2.main:app" in blueprint
    assert "python -m app.v2.infrastructure.plan_worker_entrypoint" in blueprint
    assert "auvra-v2-worker" in blueprint
    assert "startCommand:" not in blueprint
    api_section, worker_section = blueprint.split("  - type: worker", maxsplit=1)
    assert "V2_GEMINI_API_KEY" not in api_section
    assert "V2_ACCOUNT_EXPORT_ENCRYPTION_KEY" not in api_section
    assert "FIREBASE_PRIVATE_KEY" in worker_section


@pytest.mark.parametrize(
    "url, expect_error",
    [
        ("rediss://user:pw@oregon-keyvalue.render.com:6379", False),
        # Render's private-network hostname: bare service id, no dots.
        ("redis://red-d9rojtf10e5c738hg8c0:6379", False),
        # Plaintext to a public FQDN really is unsafe and stays rejected.
        ("redis://oregon-keyvalue.render.com:6379", True),
        ("redis://localhost:6379", True),
    ],
)
def test_redis_transport_allows_private_network_but_not_public_plaintext(
    monkeypatch, url, expect_error
):
    """A blanket rediss:// rule forced every request onto the public internet.

    That is slower, less reliable and billed as egress -- it consumed most of a
    month's bandwidth allowance. The private-network URL is not a weaker
    choice: the traffic never leaves the network.
    """
    monkeypatch.setattr(runtime_config, "settings", _settings(V2_REDIS_URL=url))
    if expect_error:
        with pytest.raises(RuntimeError, match="V2_REDIS_URL"):
            runtime_config.validate_api_configuration()
    else:
        runtime_config.validate_api_configuration()
