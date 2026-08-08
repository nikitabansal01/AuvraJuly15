"""API and worker credentials have deliberately separate production boundaries."""
from types import SimpleNamespace

import pytest

import app.core.config as config


def _api_only_settings():
    return SimpleNamespace(
        ENVIRONMENT="production",
        ALLOWED_HOSTS=["api.example.test"],
        CORS_ORIGINS=["https://app.example.test"],
        ENABLE_LEGACY_V1=False,
        FIREBASE_PROJECT_ID="project",
        FIREBASE_PRIVATE_KEY_ID="key-id",
        FIREBASE_PRIVATE_KEY="private-key",
        FIREBASE_CLIENT_EMAIL="service@example.test",
        FIREBASE_CLIENT_ID="client-id",
        V2_GUEST_PROOF_SECRET="x" * 32,
        V2_REQUIRED_CONSENT_VERSIONS={
            "privacy": "privacy.v1",
            "health_data_processing": "health.v1",
        },
        V2_GEMINI_API_KEY="",
        V2_GEMINI_MODEL="gemini-test",
        V2_TELEMETRY_HMAC_KEY="",
        V2_CLOUDFLARE_ACCOUNT_ID="",
        V2_CLOUDFLARE_API_TOKEN="",
        V2_SUPABASE_URL="",
        V2_SUPABASE_SERVICE_ROLE_KEY="",
        V2_PUBMED_TOOL="auvra",
        V2_PUBMED_EMAIL="",
        V2_PUBMED_MIN_INTERVAL_SECONDS=0.34,
        V2_WORKER_LEASE_SECONDS=60,
    )


def test_api_validation_does_not_require_worker_only_secrets(monkeypatch):
    monkeypatch.setattr(config, "settings", _api_only_settings())
    config.validate_production_configuration()
    with pytest.raises(RuntimeError, match="plan-worker"):
        config.validate_plan_worker_configuration()
