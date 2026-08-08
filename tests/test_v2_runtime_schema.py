"""Fail-closed schema-version checks for independently deployed v2 processes."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.v2.runtime import schema


def test_checked_in_recovery_history_has_the_engagement_ledger_head():
    # A newly added migration must deliberately update this contract and its
    # deployment/rehearsal evidence rather than silently changing process gates.
    assert schema.expected_schema_head() == "20260808_0014"


class _Result:
    def __init__(self, version: str | None) -> None:
        self.version = version

    def scalar_one_or_none(self) -> str | None:
        return self.version


class _Connection:
    def __init__(self, version: str | None) -> None:
        self.version = version

    async def execute(self, statement):
        assert "alembic_version" in str(statement)
        return _Result(self.version)


class _Engine:
    def __init__(self, version: str | None) -> None:
        self.version = version

    @asynccontextmanager
    async def connect(self):
        yield _Connection(self.version)


@pytest.mark.anyio
async def test_schema_check_rejects_a_stale_database(monkeypatch):
    monkeypatch.setattr(schema, "get_engine", lambda: _Engine("20260808_0008"))
    with pytest.raises(RuntimeError, match="checked-in Alembic head"):
        await schema.check_database_schema_head()
