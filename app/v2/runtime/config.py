"""Typed, fail-closed configuration for processes that serve AUVRA v2.

This module deliberately has no dependency on the legacy configuration tree.
The API and worker validate distinct secret sets.  The worker receives Firebase
credentials only when the explicitly enabled deletion lane needs them.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class RuntimeSettings(BaseSettings):
    """The complete configuration contract for v2 API and worker processes."""

    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str = "postgresql://user:password@localhost/auvra_db"
    # Direct connections use a deliberately small application-side pool.  A
    # transaction pooler owns connection pooling itself and therefore uses
    # SQLAlchemy NullPool (and disables prepared statements) instead.
    DATABASE_CONNECTION_MODE: Literal["direct", "pooler"] = "direct"
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 2
    DATABASE_POOL_TIMEOUT_SECONDS: int = 30
    DATABASE_POOL_RECYCLE_SECONDS: int = 1800
    DATABASE_STATEMENT_TIMEOUT_MS: int = 15_000

    ALLOWED_HOSTS: list[str] = ["*"]
    CORS_ORIGINS: list[str] = []
    CORS_ALLOW_METHODS: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    CORS_ALLOW_HEADERS: list[str] = [
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "If-Match",
        "X-Onboarding-Proof",
        "X-Request-ID",
    ]

    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_PRIVATE_KEY_ID: str = ""
    FIREBASE_PRIVATE_KEY: str = ""
    FIREBASE_CLIENT_EMAIL: str = ""
    FIREBASE_CLIENT_ID: str = ""

    V2_GUEST_PROOF_SECRET: str = "auvra-v2-local-guest-proof-secret-change-me"
    V2_REQUIRED_CONSENT_VERSIONS: dict[str, str] = {
        "privacy": "privacy.v1",
        "health_data_processing": "health-data-processing.v1",
    }

    # Worker-only credentials. They intentionally are not required by
    # ``validate_api_configuration`` and must not be set on the API service.
    V2_GEMINI_API_KEY: str = ""
    V2_GEMINI_MODEL: str = "gemini-2.5-flash"
    V2_TELEMETRY_HMAC_KEY: str = ""
    V2_CLOUDFLARE_ACCOUNT_ID: str = ""
    V2_CLOUDFLARE_API_TOKEN: str = ""
    V2_CLOUDFLARE_IMAGE_MODEL: str = "@cf/black-forest-labs/flux-1-schnell"
    V2_SUPABASE_URL: str = ""
    V2_SUPABASE_SERVICE_ROLE_KEY: str = ""
    V2_PLAN_MEDIA_BUCKET: str = "plan-images"
    V2_PUBMED_TOOL: str = "auvra"
    V2_PUBMED_EMAIL: str = ""
    V2_PUBMED_MIN_INTERVAL_SECONDS: float = 0.34
    V2_WORKER_LEASE_SECONDS: int = 60
    V2_PLAN_JOB_TIMEOUT_SECONDS: int = 300
    V2_CONVERSATION_JOB_TIMEOUT_SECONDS: int = 90
    V2_ACCOUNT_EXPORT_ENCRYPTION_KEY: str = ""
    V2_ACCOUNT_EXPORT_BUCKET: str = "account-exports"
    # Redis is shared infrastructure, never business truth.  The API uses it
    # only for short-lived distributed abuse controls; the worker uses it for
    # queues/cache cleanup when those lanes are enabled.
    V2_REDIS_URL: str = ""
    V2_MAX_REQUEST_BODY_BYTES: int = 1_048_576
    V2_PUBLIC_ONBOARDING_LIMIT: int = 10
    V2_PUBLIC_ONBOARDING_WINDOW_SECONDS: int = 600
    V2_COSTLY_MUTATION_LIMIT: int = 6
    V2_COSTLY_MUTATION_WINDOW_SECONDS: int = 300
    V2_TRUSTED_PROXY_CIDRS: list[str] = []
    V2_DELETION_ENABLED: bool = False
    V2_DELETION_APPROVAL_REFERENCE: str = ""
    AUVRA_DELETION_RECEIPT_HMAC_KEY: str = ""
    V2_WORKER_SHUTDOWN_SECONDS: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = RuntimeSettings()

_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "change_me",
        "replace-me",
        "replace_me",
        "placeholder",
    }
)
_PLACEHOLDER_PREFIXES = ("your-", "[your-", "<your-", "placeholder-")


def _missing_or_placeholder(value: str) -> bool:
    normalized = (value or "").strip().lower()
    return normalized in _PLACEHOLDER_VALUES or normalized.startswith(
        _PLACEHOLDER_PREFIXES
    )


def _validate_database_url(value: str) -> str | None:
    if _missing_or_placeholder(value):
        return "DATABASE_URL is missing or a placeholder"
    try:
        parsed = make_url(value)
    except Exception:
        return "DATABASE_URL is invalid"
    if (
        not parsed.drivername.startswith("postgresql")
        and parsed.drivername != "postgres"
    ):
        return "DATABASE_URL must use PostgreSQL"
    if settings.ENVIRONMENT in {"staging", "production"}:
        sslmode = parsed.query.get("sslmode", "").lower()
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            return "DATABASE_URL must require TLS in staging or production"
        if (parsed.username, parsed.password) in {
            ("user", "password"),
            ("username", "password"),
        }:
            return "DATABASE_URL contains placeholder credentials"
    return None


def _database_connection_errors() -> list[str]:
    """Validate the intentional database connection strategy.

    Supabase transaction poolers cannot safely retain asyncpg prepared
    statements or application-side connection pools.  Direct connections can,
    but only within a small, explicit connection budget.
    """

    mode = settings.DATABASE_CONNECTION_MODE
    pool_size = settings.DATABASE_POOL_SIZE
    overflow = settings.DATABASE_MAX_OVERFLOW
    timeout = settings.DATABASE_POOL_TIMEOUT_SECONDS
    recycle = settings.DATABASE_POOL_RECYCLE_SECONDS
    errors: list[str] = []
    if mode == "direct":
        if not 1 <= pool_size <= 10:
            errors.append("DATABASE_POOL_SIZE must be between 1 and 10 for direct mode")
        if not 0 <= overflow <= 10 or pool_size + overflow > 12:
            errors.append(
                "DATABASE_MAX_OVERFLOW must keep the direct connection budget at 12 or less"
            )
    elif mode == "pooler":
        if pool_size != 0 or overflow != 0:
            errors.append(
                "DATABASE_POOL_SIZE and DATABASE_MAX_OVERFLOW must both be 0 for pooler mode"
            )
    else:  # Literal validation protects RuntimeSettings; retain this for test seams.
        errors.append("DATABASE_CONNECTION_MODE must be direct or pooler")
    if not 1 <= timeout <= 120:
        errors.append("DATABASE_POOL_TIMEOUT_SECONDS must be between 1 and 120")
    if not 60 <= recycle <= 3600:
        errors.append("DATABASE_POOL_RECYCLE_SECONDS must be between 60 and 3600")
    if not 1_000 <= settings.DATABASE_STATEMENT_TIMEOUT_MS <= 120_000:
        errors.append("DATABASE_STATEMENT_TIMEOUT_MS must be between 1000 and 120000")
    return errors


def _api_operational_errors() -> list[str]:
    errors: list[str] = []
    if _missing_or_placeholder(settings.V2_REDIS_URL):
        errors.append("V2_REDIS_URL is required for distributed API rate limiting")
    elif not settings.V2_REDIS_URL.startswith("rediss://"):
        errors.append("V2_REDIS_URL must use TLS (rediss://) in staging or production")
    if not 1_024 <= settings.V2_MAX_REQUEST_BODY_BYTES <= 10_485_760:
        errors.append("V2_MAX_REQUEST_BODY_BYTES must be between 1024 and 10485760")
    for name, value in {
        "V2_PUBLIC_ONBOARDING_LIMIT": settings.V2_PUBLIC_ONBOARDING_LIMIT,
        "V2_PUBLIC_ONBOARDING_WINDOW_SECONDS": settings.V2_PUBLIC_ONBOARDING_WINDOW_SECONDS,
        "V2_COSTLY_MUTATION_LIMIT": settings.V2_COSTLY_MUTATION_LIMIT,
        "V2_COSTLY_MUTATION_WINDOW_SECONDS": settings.V2_COSTLY_MUTATION_WINDOW_SECONDS,
    }.items():
        if not 1 <= value <= 86_400:
            errors.append(f"{name} must be between 1 and 86400")
    return errors


def _raise_if_invalid(errors: list[str], process: str) -> None:
    if errors:
        raise RuntimeError(f"Invalid {process} configuration: " + "; ".join(errors))


def validate_api_configuration() -> None:
    """Fail closed before a v2 API process can accept production traffic."""

    if settings.ENVIRONMENT not in {"staging", "production"}:
        return

    errors: list[str] = []
    if error := _validate_database_url(settings.DATABASE_URL):
        errors.append(error)
    errors.extend(_database_connection_errors())
    errors.extend(_api_operational_errors())
    if not settings.ALLOWED_HOSTS or "*" in settings.ALLOWED_HOSTS:
        errors.append("ALLOWED_HOSTS must contain explicit API hosts")
    # Native/mobile-only deployments make no browser cross-origin requests.
    # An empty allowlist is therefore the safe default; a wildcard is not.
    if "*" in settings.CORS_ORIGINS:
        errors.append("CORS_ORIGINS must not contain a wildcard origin")

    worker_credentials = {
        "V2_GEMINI_API_KEY": settings.V2_GEMINI_API_KEY,
        "V2_TELEMETRY_HMAC_KEY": settings.V2_TELEMETRY_HMAC_KEY,
        "V2_CLOUDFLARE_API_TOKEN": settings.V2_CLOUDFLARE_API_TOKEN,
        "V2_SUPABASE_SERVICE_ROLE_KEY": settings.V2_SUPABASE_SERVICE_ROLE_KEY,
        "V2_ACCOUNT_EXPORT_ENCRYPTION_KEY": settings.V2_ACCOUNT_EXPORT_ENCRYPTION_KEY,
        "V2_DELETION_APPROVAL_REFERENCE": settings.V2_DELETION_APPROVAL_REFERENCE,
        "AUVRA_DELETION_RECEIPT_HMAC_KEY": settings.AUVRA_DELETION_RECEIPT_HMAC_KEY,
    }
    if any(value.strip() for value in worker_credentials.values()):
        errors.append(
            "worker-only credentials must not be present in the API environment"
        )

    firebase_values = {
        "FIREBASE_PROJECT_ID": settings.FIREBASE_PROJECT_ID,
        "FIREBASE_PRIVATE_KEY_ID": settings.FIREBASE_PRIVATE_KEY_ID,
        "FIREBASE_PRIVATE_KEY": settings.FIREBASE_PRIVATE_KEY,
        "FIREBASE_CLIENT_EMAIL": settings.FIREBASE_CLIENT_EMAIL,
        "FIREBASE_CLIENT_ID": settings.FIREBASE_CLIENT_ID,
    }
    errors.extend(
        f"{name} is missing or a placeholder"
        for name, value in firebase_values.items()
        if _missing_or_placeholder(value)
    )

    if (
        len(settings.V2_GUEST_PROOF_SECRET.encode("utf-8")) < 32
        or settings.V2_GUEST_PROOF_SECRET
        == "auvra-v2-local-guest-proof-secret-change-me"
    ):
        errors.append("V2_GUEST_PROOF_SECRET must be a unique 32-byte secret")
    required_consents = {"privacy", "health_data_processing"}
    if set(settings.V2_REQUIRED_CONSENT_VERSIONS) != required_consents or any(
        not value.strip() for value in settings.V2_REQUIRED_CONSENT_VERSIONS.values()
    ):
        errors.append(
            "V2_REQUIRED_CONSENT_VERSIONS must explicitly set required documents"
        )
    _raise_if_invalid(errors, "v2 API")


def _worker_firebase_errors() -> list[str]:
    firebase_values = {
        "FIREBASE_PROJECT_ID": settings.FIREBASE_PROJECT_ID,
        "FIREBASE_PRIVATE_KEY_ID": settings.FIREBASE_PRIVATE_KEY_ID,
        "FIREBASE_PRIVATE_KEY": settings.FIREBASE_PRIVATE_KEY,
        "FIREBASE_CLIENT_EMAIL": settings.FIREBASE_CLIENT_EMAIL,
        "FIREBASE_CLIENT_ID": settings.FIREBASE_CLIENT_ID,
    }
    if settings.V2_DELETION_ENABLED:
        return [
            f"{name} is missing or a placeholder"
            for name, value in firebase_values.items()
            if _missing_or_placeholder(value)
        ]
    return (
        ["Firebase credentials require V2_DELETION_ENABLED in the worker"]
        if any(value.strip() for value in firebase_values.values())
        else []
    )


def _worker_provider_errors() -> list[str]:
    worker_values = {
        "V2_GEMINI_API_KEY": settings.V2_GEMINI_API_KEY,
        "V2_GEMINI_MODEL": settings.V2_GEMINI_MODEL,
        "V2_TELEMETRY_HMAC_KEY": settings.V2_TELEMETRY_HMAC_KEY,
        "V2_CLOUDFLARE_ACCOUNT_ID": settings.V2_CLOUDFLARE_ACCOUNT_ID,
        "V2_CLOUDFLARE_API_TOKEN": settings.V2_CLOUDFLARE_API_TOKEN,
        "V2_SUPABASE_URL": settings.V2_SUPABASE_URL,
        "V2_SUPABASE_SERVICE_ROLE_KEY": settings.V2_SUPABASE_SERVICE_ROLE_KEY,
        "V2_PUBMED_TOOL": settings.V2_PUBMED_TOOL,
        "V2_PUBMED_EMAIL": settings.V2_PUBMED_EMAIL,
    }
    errors = [
        f"{name} is missing or a placeholder"
        for name, value in worker_values.items()
        if _missing_or_placeholder(value)
    ]
    if len(settings.V2_TELEMETRY_HMAC_KEY.encode("utf-8")) < 32:
        errors.append("V2_TELEMETRY_HMAC_KEY must contain at least 32 bytes")
    if not settings.V2_SUPABASE_URL.startswith("https://"):
        errors.append("V2_SUPABASE_URL must be an HTTPS project URL")
    if settings.V2_PUBMED_MIN_INTERVAL_SECONDS <= 0:
        errors.append("V2_PUBMED_MIN_INTERVAL_SECONDS must be positive")
    if settings.V2_WORKER_LEASE_SECONDS < 6:
        errors.append("V2_WORKER_LEASE_SECONDS must be at least 6")
    if settings.V2_PLAN_JOB_TIMEOUT_SECONDS < settings.V2_WORKER_LEASE_SECONDS:
        errors.append("V2_PLAN_JOB_TIMEOUT_SECONDS must be at least the worker lease")
    if settings.V2_CONVERSATION_JOB_TIMEOUT_SECONDS < settings.V2_WORKER_LEASE_SECONDS:
        errors.append(
            "V2_CONVERSATION_JOB_TIMEOUT_SECONDS must be at least the worker lease"
        )
    if settings.V2_WORKER_SHUTDOWN_SECONDS < 1:
        errors.append("V2_WORKER_SHUTDOWN_SECONDS must be positive")
    export_values = {
        "V2_ACCOUNT_EXPORT_ENCRYPTION_KEY": settings.V2_ACCOUNT_EXPORT_ENCRYPTION_KEY,
        "V2_ACCOUNT_EXPORT_BUCKET": settings.V2_ACCOUNT_EXPORT_BUCKET,
    }
    errors.extend(
        f"{name} is missing or a placeholder"
        for name, value in export_values.items()
        if _missing_or_placeholder(value)
    )
    return errors


def _deletion_worker_errors() -> list[str]:
    if not settings.V2_DELETION_ENABLED:
        return []
    errors: list[str] = []
    if _missing_or_placeholder(settings.V2_REDIS_URL):
        errors.append("V2_REDIS_URL is missing or a placeholder")
    if _missing_or_placeholder(settings.V2_DELETION_APPROVAL_REFERENCE):
        errors.append(
            "V2_DELETION_APPROVAL_REFERENCE is required when deletion is enabled"
        )
    if len(settings.AUVRA_DELETION_RECEIPT_HMAC_KEY.encode("utf-8")) < 32:
        errors.append("AUVRA_DELETION_RECEIPT_HMAC_KEY must contain at least 32 bytes")
    return errors


def validate_plan_worker_configuration() -> None:
    """Validate provider, export, and conditionally enabled deletion lanes."""

    if settings.ENVIRONMENT not in {"staging", "production"}:
        return

    errors = _worker_firebase_errors() + _worker_provider_errors()
    if error := _validate_database_url(settings.DATABASE_URL):
        errors.append(error)
    errors.extend(_database_connection_errors())
    errors.extend(_deletion_worker_errors())
    _raise_if_invalid(errors, "v2 plan-worker")
