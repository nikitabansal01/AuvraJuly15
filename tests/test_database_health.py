"""Focused tests for database startup and health-probe behavior."""

import json

import pytest

from app.api.v1.endpoints import health
from app.core import database


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, value=1):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement):
        assert str(statement) == "SELECT 1"
        return _ScalarResult(self.value)


class _Engine:
    def __init__(self, value=1):
        self.value = value

    def connect(self):
        return _Connection(self.value)


def test_database_connection_executes_real_readiness_query(monkeypatch):
    monkeypatch.setattr(database, "engine", _Engine())

    database.check_database_connection()


def test_database_connection_rejects_unexpected_result(monkeypatch):
    monkeypatch.setattr(database, "engine", _Engine(value=0))

    with pytest.raises(RuntimeError, match="unexpected result"):
        database.check_database_connection()


async def test_liveness_does_not_touch_database(monkeypatch):
    def fail_if_called():
        raise AssertionError("liveness must not query the database")

    monkeypatch.setattr(health, "check_database_connection", fail_if_called)

    response = await health.health_check()

    assert response["status"] == "healthy"


def test_readiness_reports_database_available(monkeypatch):
    monkeypatch.setattr(health, "check_database_connection", lambda: None)

    response = health.database_readiness_check()

    assert response == {"status": "ready", "database": "available"}


def test_readiness_returns_503_without_leaking_error(monkeypatch):
    def unavailable():
        raise RuntimeError("secret connection detail")

    monkeypatch.setattr(health, "check_database_connection", unavailable)

    response = health.database_readiness_check()

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready",
        "database": "unavailable",
    }
    assert b"secret connection detail" not in response.body


def test_production_configuration_requires_both_database_urls(monkeypatch):
    monkeypatch.setattr(database.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(database.settings, "DATABASE_URL", "")
    monkeypatch.setattr(
        database.settings,
        "LANGGRAPH_CHECKPOINT_POSTGRES_DSN",
        "postgresql://postgres:[YOUR-PASSWORD]@example.invalid/postgres",
    )

    with pytest.raises(RuntimeError) as exc_info:
        database.validate_database_configuration()

    error = str(exc_info.value)
    assert "DATABASE_URL" in error
    assert "LANGGRAPH_CHECKPOINT_POSTGRES_DSN" in error
    assert "YOUR-PASSWORD" not in error


@pytest.mark.parametrize("unsafe_mode", ["disable", "allow", "prefer"])
def test_production_configuration_rejects_unsafe_tls_modes(
    monkeypatch, unsafe_mode
):
    monkeypatch.setattr(database.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        database.settings,
        "DATABASE_URL",
        f"postgresql://app:private-value@example.invalid/app?sslmode={unsafe_mode}",
    )
    monkeypatch.setattr(
        database.settings,
        "LANGGRAPH_CHECKPOINT_POSTGRES_DSN",
        "postgresql://app:private-value@example.invalid/app?sslmode=require",
    )

    with pytest.raises(RuntimeError) as exc_info:
        database.validate_database_configuration()

    assert "sslmode" in str(exc_info.value)
    assert "private-value" not in str(exc_info.value)


def test_tls_normalization_adds_require_without_dropping_query_options():
    normalized = database.normalize_postgres_tls_url(
        "postgresql://app:secret@example.invalid/app?application_name=auvra"
    )

    assert "application_name=auvra" in normalized
    assert "sslmode=require" in normalized


def test_tls_normalization_preserves_stronger_verification_mode():
    normalized = database.normalize_postgres_tls_url(
        "postgresql://app:secret@example.invalid/app?sslmode=verify-full"
    )

    assert "sslmode=verify-full" in normalized


def test_production_initialization_does_not_create_tables(monkeypatch):
    monkeypatch.setattr(database.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        database.settings,
        "DATABASE_URL",
        "postgresql://valid.example/app",
    )
    monkeypatch.setattr(
        database.settings,
        "LANGGRAPH_CHECKPOINT_POSTGRES_DSN",
        "postgresql://valid.example/app",
    )
    monkeypatch.setattr(database, "create_tables", lambda: pytest.fail("create_all called"))
    monkeypatch.setattr(database, "check_database_connection", lambda: None)

    database.initialize_database()


def test_database_initialization_surfaces_connectivity_failure(monkeypatch):
    monkeypatch.setattr(database.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        database.settings,
        "DATABASE_URL",
        "postgresql://valid.example/app",
    )
    monkeypatch.setattr(
        database.settings,
        "LANGGRAPH_CHECKPOINT_POSTGRES_DSN",
        "postgresql://valid.example/app",
    )

    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(database, "check_database_connection", unavailable)

    with pytest.raises(RuntimeError, match="database unavailable"):
        database.initialize_database()


async def test_production_langgraph_never_falls_back_to_sqlite(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.langgraph.graphs import care_plan_checkin

    monkeypatch.setattr(care_plan_checkin.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        care_plan_checkin.settings,
        "LANGGRAPH_CHECKPOINT_POSTGRES_DSN",
        "",
    )

    with pytest.raises(
        RuntimeError,
        match="LANGGRAPH_CHECKPOINT_POSTGRES_DSN is required",
    ):
        await care_plan_checkin._create_async_checkpointer()
