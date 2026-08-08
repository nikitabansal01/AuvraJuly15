"""Declarative base and schema constants for the canonical v2 database."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


APP_SCHEMA = "app"
OPS_SCHEMA = "ops"

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
