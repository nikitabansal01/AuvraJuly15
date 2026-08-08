"""Database runtime for v2 only; legacy ORM metadata is never imported here."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.v2.runtime.config import settings


def _async_database_url(database_url: str) -> str:
    parsed = make_url(database_url)
    query = dict(parsed.query)
    query.pop("sslmode", None)
    query.pop("ssl", None)
    return parsed.set(drivername="postgresql+asyncpg", query=query).render_as_string(
        hide_password=False
    )


def _database_connect_args() -> dict[str, object]:
    """Return driver settings safe for the configured connection mode."""

    connect_args: dict[str, object] = {
        "command_timeout": settings.DATABASE_POOL_TIMEOUT_SECONDS,
        "server_settings": {
            "statement_timeout": str(settings.DATABASE_STATEMENT_TIMEOUT_MS),
        },
    }
    if settings.DATABASE_CONNECTION_MODE == "pooler":
        # PgBouncer transaction pooling cannot safely reuse asyncpg prepared
        # statements between logical clients.
        connect_args.update(
            {
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
            }
        )
    if settings.ENVIRONMENT in {"staging", "production"}:
        connect_args["ssl"] = "require"
    return connect_args


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Create a bounded direct pool or a pooler-safe NullPool intentionally."""

    options: dict[str, object] = {
        "pool_pre_ping": True,
        "echo": False,
        "connect_args": _database_connect_args(),
    }
    if settings.DATABASE_CONNECTION_MODE == "pooler":
        options["poolclass"] = NullPool
    else:
        options.update(
            {
                "pool_size": settings.DATABASE_POOL_SIZE,
                "max_overflow": settings.DATABASE_MAX_OVERFLOW,
                "pool_timeout": settings.DATABASE_POOL_TIMEOUT_SECONDS,
                "pool_recycle": settings.DATABASE_POOL_RECYCLE_SECONDS,
            }
        )
    return create_async_engine(_async_database_url(settings.DATABASE_URL), **options)


@lru_cache(maxsize=1)
def get_async_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def check_database_readiness() -> None:
    """Run an actual short query without creating or changing schema."""

    async with get_engine().connect() as connection:
        value = (await connection.execute(text("SELECT 1"))).scalar_one()
    if value != 1:
        raise RuntimeError("database readiness query returned an unexpected result")


async def dispose_database() -> None:
    """Dispose socket resources when the ASGI process stops."""

    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_async_session_maker.cache_clear()
    get_engine.cache_clear()


AsyncSessionFactory = Callable[[], AsyncSession]
