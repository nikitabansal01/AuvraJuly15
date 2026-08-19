"""PostgreSQL migration safety checks for the durable outbox revision."""
from __future__ import annotations

import os
import uuid

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="PostgreSQL 17 outbox migration tests require AUVRA_TEST_DATABASE_URL",
)


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])


def test_outbox_migration_round_trip_and_refuses_durable_fact_loss() -> None:
    """Empty schema round-trips; retry/lease facts make downgrade fail closed."""

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import DBAPIError

    config = Config("alembic.ini")
    command.upgrade(config, "20260808_0009")
    try:
        command.upgrade(config, "20260808_0010")
        inspector = inspect(_engine())
        columns = {
            column["name"] for column in inspector.get_columns("outbox_events", schema="ops")
        }
        assert {
            "max_attempts",
            "lease_owner",
            "lease_expires_at",
            "heartbeat_at",
            "error_code",
            "finished_at",
        } <= columns

        command.downgrade(config, "20260808_0009")
        previous_columns = {
            column["name"]
            for column in inspect(_engine()).get_columns("outbox_events", schema="ops")
        }
        assert "max_attempts" not in previous_columns

        command.upgrade(config, "20260808_0010")
        event_id = uuid.uuid4()
        with _engine().begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ops.outbox_events "
                    "(id, aggregate_type, aggregate_id, event_type, payload, attempt_count) "
                    "VALUES (:id, 'test', :aggregate_id, 'outbox.test', '{}'::jsonb, 1)"
                ),
                {"id": event_id, "aggregate_id": uuid.uuid4()},
            )
        with pytest.raises(DBAPIError, match="cannot downgrade durable outbox schema"):
            command.downgrade(config, "20260808_0009")
        with _engine().begin() as connection:
            connection.execute(
                text("DELETE FROM ops.outbox_events WHERE id = :id"), {"id": event_id}
            )
        command.downgrade(config, "20260808_0009")
    finally:
        command.upgrade(config, "head")
