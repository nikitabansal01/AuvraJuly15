"""Checked-in Alembic-head verification for v2 process startup and readiness."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.v2.persistence.base import VERSION_TABLE, VERSION_TABLE_SCHEMA
from app.v2.persistence.database import get_engine


@lru_cache(maxsize=1)
def expected_schema_head() -> str:
    """Return the sole recovery migration head shipped in this image."""

    repository_root = Path(__file__).resolve().parents[3]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise RuntimeError(
            "The checked-in v2 migration history must have exactly one head"
        )
    return heads[0]


async def check_database_schema_head() -> None:
    """Fail closed unless the connected database is at this image's migration head."""

    expected = expected_schema_head()
    try:
        async with get_engine().connect() as connection:
            actual = (
                await connection.execute(
                    text(
                        "SELECT version_num FROM "
                        f'"{VERSION_TABLE_SCHEMA}"."{VERSION_TABLE}"'
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:
        raise RuntimeError("Database migration version cannot be verified") from exc
    if actual != expected:
        raise RuntimeError(
            "Database schema is not at the checked-in Alembic head "
            f"(expected {expected}, got {actual or 'none'})"
        )
