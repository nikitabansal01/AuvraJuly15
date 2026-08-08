import os
from typing import Dict, List, Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    # Basic settings
    PROJECT_NAME: str = "Auvra Backend API"
    PROJECT_DESCRIPTION: str = "Auvra Backend API Server"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # Firebase settings
    FIREBASE_PROJECT_ID: str = "your-firebase-project-id"
    FIREBASE_PRIVATE_KEY_ID: str = ""
    FIREBASE_PRIVATE_KEY: str = ""
    FIREBASE_CLIENT_EMAIL: str = ""
    FIREBASE_CLIENT_ID: str = ""
    FIREBASE_AUTH_DOMAIN: str = ""
    FIREBASE_STORAGE_BUCKET: str = ""
    FIREBASE_MESSAGING_SENDER_ID: str = ""
    FIREBASE_APP_ID: str = ""

    # CORS settings
    ALLOWED_HOSTS: List[str] = ["*"]
    CORS_ORIGINS: List[str] = ["*"]
    CORS_ALLOW_METHODS: List[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    CORS_ALLOW_HEADERS: List[str] = [
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "If-Match",
        "X-Onboarding-Proof",
        "X-Request-ID",
    ]

    # Database settings
    DATABASE_URL: str = "postgresql://user:password@localhost/auvra_db"

    # RAG settings
    FIRECRAWL_API_KEY: str = ""
    FIRECRAWL_BASE_URL: str = "https://api.firecrawl.dev/v0/scrape"

    # Pinecone settings
    PINECONE_API_KEY: str = ""
    PINECONE_ENVIRONMENT: str = ""
    PINECONE_INDEX: str = ""

    # OpenAI settings
    OPENAI_API_KEY: str = ""

    # Groq settings
    GROQ_API_KEY: str = ""

    # Gemini LLM settings (for "Others" text processing)
    GEMINI_API_KEY: str = ""
    ENABLE_LLM_OTHERS: bool = True
    LLM_OTHERS_TIMEOUT: int = 30

    # Redis settings
    REDIS_URL: str = "redis://localhost:6379"

    # Logging settings
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"

    # API settings
    API_V1_STR: str = "/api/v1"
    API_V2_STR: str = "/api/v2"
    ENABLE_LEGACY_V1: bool = False

    # V2 guest proofs are derived with this server-only HMAC secret.  The
    # development value is deliberately rejected by production validation.
    V2_GUEST_PROOF_SECRET: str = "auvra-v2-local-guest-proof-secret-change-me"
    # These development defaults are a complete, typed contract for local and
    # test clients. Production must configure concrete released documents.
    V2_REQUIRED_CONSENT_VERSIONS: Dict[str, str] = {
        "privacy": "privacy.v1",
        "health_data_processing": "health-data-processing.v1",
    }
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
    V2_WORKER_SHUTDOWN_SECONDS: int = 30

    # Production hardening flags (2026 rollout controls)
    FEATURE_GRAPH_UNIFICATION: bool = False
    FEATURE_FRONTEND_ENGINE_V2: bool = False
    FEATURE_STRICT_CONTRACT_MODE: bool = False

    # LangGraph checkpointing defaults
    LANGGRAPH_CHECKPOINT_POSTGRES_DSN: str = ""
    LANGGRAPH_CHECKPOINT_SQLITE_PATH: str = ".langgraph/checkpoints/langgraph.sqlite"

    # Care-plan endpoint guardrails / alert thresholds
    CARE_PLAN_EVENT_ERROR_RATE_ALERT_THRESHOLD: float = 0.05
    CARE_PLAN_EVENT_P95_LATENCY_MS_ALERT_THRESHOLD: int = 2500

    # File upload settings
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    UPLOAD_DIR: str = "uploads"

    # Action Plan Generation Settings
    ACTION_PLAN_MAX_RETRIES: int = 2
    ACTION_PLAN_TIMEOUT: int = 120
    PUBMED_RATE_LIMIT_DELAY: float = 0.5
    # Image similarity threshold for cache matching
    # 0.85 provides good balance: allows ~10-12 cache hits vs 2 at 0.90
    # Higher = stricter matches, more regeneration
    # Lower = more cache hits but potentially less accurate matches
    IMAGE_SIMILARITY_THRESHOLD: float = 0.85
    FEEDBACK_SUMMARIZE_THRESHOLD: int = 100

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Ignore additional fields
        # Allow fields with prefix model_ without warnings from Pydantic
        "protected_namespaces": (),
    }


# Environment-specific settings
class DevelopmentSettings(Settings):
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    ENVIRONMENT: str = "development"
    # Development environment: allow all hosts, relaxed security
    ALLOWED_HOSTS: List[str] = ["*", "localhost", "127.0.0.1", "0.0.0.0"]
    ENABLE_LEGACY_V1: bool = True


class ProductionSettings(Settings):
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"  # Changed from WARNING to INFO to see RAG debug logs
    ENVIRONMENT: str = "production"
    # Production must explicitly supply both lists as JSON environment values.
    ALLOWED_HOSTS: List[str] = []
    CORS_ORIGINS: List[str] = []
    ENABLE_LEGACY_V1: bool = False
    V2_REQUIRED_CONSENT_VERSIONS: Dict[str, str] = {}


# Select settings based on environment
def get_settings() -> Settings:
    environment = os.getenv("ENVIRONMENT", "development")

    if environment == "production":
        return ProductionSettings()
    else:
        return DevelopmentSettings()


settings = get_settings()


def validate_production_configuration() -> None:
    """Reject permissive or incomplete production security configuration."""

    if settings.ENVIRONMENT != "production":
        return

    errors: list[str] = []
    if not settings.ALLOWED_HOSTS or "*" in settings.ALLOWED_HOSTS:
        errors.append("ALLOWED_HOSTS must contain explicit production hosts")
    if not settings.CORS_ORIGINS or "*" in settings.CORS_ORIGINS:
        errors.append("CORS_ORIGINS must contain explicit trusted origins")
    if settings.ENABLE_LEGACY_V1:
        errors.append("ENABLE_LEGACY_V1 must be false in production")

    required_firebase = {
        "FIREBASE_PROJECT_ID": settings.FIREBASE_PROJECT_ID,
        "FIREBASE_PRIVATE_KEY_ID": settings.FIREBASE_PRIVATE_KEY_ID,
        "FIREBASE_PRIVATE_KEY": settings.FIREBASE_PRIVATE_KEY,
        "FIREBASE_CLIENT_EMAIL": settings.FIREBASE_CLIENT_EMAIL,
        "FIREBASE_CLIENT_ID": settings.FIREBASE_CLIENT_ID,
    }
    for key, value in required_firebase.items():
        normalized = (value or "").strip().lower()
        if not normalized or normalized.startswith("your-"):
            errors.append(f"{key} is missing or a placeholder")

    local_guest_secret = "auvra-v2-local-guest-proof-secret-change-me"
    if (
        len(settings.V2_GUEST_PROOF_SECRET.encode("utf-8")) < 32
        or settings.V2_GUEST_PROOF_SECRET == local_guest_secret
    ):
        errors.append("V2_GUEST_PROOF_SECRET must be a unique 32-byte production secret")

    expected_consents = {"privacy", "health_data_processing"}
    configured_consents = settings.V2_REQUIRED_CONSENT_VERSIONS
    if set(configured_consents) != expected_consents or any(
        not version.strip() for version in configured_consents.values()
    ):
        errors.append(
            "V2_REQUIRED_CONSENT_VERSIONS must explicitly set privacy and health_data_processing"
        )

    if errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))


def validate_plan_worker_configuration() -> None:
    """Reject an incomplete worker without leaking its provider secrets to the API."""

    if settings.ENVIRONMENT != "production":
        return
    errors: list[str] = []
    plan_worker_fields = {
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
    for key, value in plan_worker_fields.items():
        if not value.strip() or value.strip().lower().startswith("your-"):
            errors.append(f"{key} is missing or a placeholder")
    if len(settings.V2_TELEMETRY_HMAC_KEY.encode("utf-8")) < 32:
        errors.append("V2_TELEMETRY_HMAC_KEY must contain at least 32 bytes")
    if not settings.V2_SUPABASE_URL.startswith("https://"):
        errors.append("V2_SUPABASE_URL must be an HTTPS project URL")
    if settings.V2_PUBMED_MIN_INTERVAL_SECONDS <= 0:
        errors.append("V2_PUBMED_MIN_INTERVAL_SECONDS must be positive")
    if settings.V2_WORKER_LEASE_SECONDS < 6:
        errors.append("V2_WORKER_LEASE_SECONDS must be at least 6")
    if errors:
        raise RuntimeError("Invalid production plan-worker configuration: " + "; ".join(errors))
