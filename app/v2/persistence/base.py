"""Declarative base and schema constants for the canonical v2 database."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


APP_SCHEMA = "app"
OPS_SCHEMA = "ops"

# The v2 chain records its head under its own name. The serving database still
# carries the legacy `public.alembic_version`, pinned to the superseded v1
# baseline, which is not a revision in this chain; sharing that table would make
# `alembic upgrade` fail to locate the stored revision. A distinct name lets the
# canonical schema coexist with the legacy schema in one database without either
# owning the other's history.
#
# This deliberately lives in `public` rather than `ops`: migration 0002 drops
# `ops` on downgrade, and Alembic writes the version row after running the
# downgrade, so a version table inside `ops` makes `downgrade base` unrunnable.
# `public` always exists, so no bootstrap DDL is needed either.
VERSION_TABLE = "alembic_version_v2"
VERSION_TABLE_SCHEMA = "public"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class V2Base(DeclarativeBase):
    """Metadata root kept separate from the legacy public-schema models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
