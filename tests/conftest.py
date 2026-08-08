"""Shared test fixtures.

The PostgreSQL suites all talk to one database, and several of them drive
Alembic up and down to prove a migration is reversible. Without isolation those
two facts interact badly: rows written by one test survive into another test's
downgrade, where a constraint that did not exist when the row was written is
applied to it, and the failure surfaces far from its cause.

Truncating the canonical fact tables after each test makes the suite
order-independent, so a failure names the test that caused it.

Versioned definition tables are exempt. A definition is reference data that
tests seed once and then reference by id, so clearing it between tests would
break the check-in suites rather than isolate them.
"""

from __future__ import annotations

import os

import pytest


#: Versioned reference data, not per-test facts. Rows here are seeded once and
#: cited by id, and `weekly_checkin_responses` carries a foreign key to them.
DEFINITION_TABLES = frozenset({"weekly_checkin_questions"})


@pytest.fixture(autouse=True)
def clean_canonical_tables():
    """Leave the canonical schema empty after every test that touches it."""

    yield

    database_url = os.getenv("AUVRA_TEST_DATABASE_URL")
    if not database_url:
        return

    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            tables = [
                qualified
                for qualified, name in connection.execute(
                    text(
                        "SELECT quote_ident(schemaname) || '.' "
                        "|| quote_ident(tablename), tablename "
                        "FROM pg_tables WHERE schemaname IN ('app', 'ops')"
                    )
                ).all()
                if name not in DEFINITION_TABLES
            ]
            if tables:
                # One statement so foreign keys never see a partial delete, and
                # CASCADE so ordering between tables does not matter.
                connection.execute(
                    text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
                )
    except Exception:
        # A test may deliberately leave the schema mid-migration; cleanup is a
        # convenience for the next test, never a reason to fail this one.
        pass
    finally:
        engine.dispose()
