"""Catalog expectations plus optional PostgreSQL reflection parity for v2 DDL."""
import os
import sys

import pytest

if sys.version_info < (3, 10):
    pytest.skip(
        "v2 ORM uses PEP 604 annotations and requires Python 3.10+",
        allow_module_level=True,
    )

from app.v2.persistence import V2Base


def test_v2_metadata_has_canonical_catalog() -> None:
    expected = {
        "app.action_plans",
        "app.action_item_events",
        "app.daily_reviews",
        "app.streak_days",
        "app.reward_ledger",
        "app.weekly_checkin_questions",
        "app.weekly_checkin_responses",
        "app.action_item_citations",
        "app.plan_evaluations",
        "ops.deletion_requests",
        "ops.account_exports",
        "ops.deletion_steps",
        "ops.deletion_receipts",
    }
    actual = {
        f"{table.schema}.{table.name}" for table in V2Base.metadata.tables.values()
    }
    assert expected <= actual
    assert "question_id" in V2Base.metadata.tables["app.weekly_checkin_responses"].c
    deletion_request = V2Base.metadata.tables["ops.deletion_requests"]
    assert any(
        "subject_hash" in str(constraint.sqltext)
        for constraint in deletion_request.constraints
        if hasattr(constraint, "sqltext")
    )
    assert "uq_deletion_requests_active_user" in {
        index.name for index in deletion_request.indexes
    }


def test_account_deletion_has_one_canonical_command() -> None:
    from app.v2.application import account_lifecycle, engagement

    assert hasattr(account_lifecycle, "request_account_deletion")
    assert not hasattr(engagement, "request_deletion")


@pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason=(
        "PostgreSQL parity requires AUVRA_TEST_DATABASE_URL; SQLite cannot validate "
        "schemas, partial indexes, or deferred triggers"
    ),
)
def test_postgres_reflection_matches_v2_metadata() -> None:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])
    inspector = inspect(engine)
    for table in V2Base.metadata.tables.values():
        columns = {
            item["name"]
            for item in inspector.get_columns(table.name, schema=table.schema)
        }
        assert set(table.c.keys()) <= columns
        reflected_fks = {
            (fk["constrained_columns"][0], fk["referred_table"])
            for fk in inspector.get_foreign_keys(table.name, schema=table.schema)
        }
        for foreign_key in table.foreign_key_constraints:
            element = next(iter(foreign_key.elements))
            assert (element.parent.name, element.column.table.name) in reflected_fks


@pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="PostgreSQL trigger assertions require AUVRA_TEST_DATABASE_URL",
)
def test_postgres_has_all_completed_review_integrity_triggers() -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])
    with engine.connect() as connection:
        names = set(
            connection.execute(
                text(
                    "SELECT trigger.tgname FROM pg_trigger trigger "
                    "JOIN pg_class relation ON relation.oid = trigger.tgrelid "
                    "JOIN pg_namespace schema ON schema.oid = relation.relnamespace "
                    "WHERE schema.nspname = 'app' AND NOT trigger.tgisinternal"
                )
            ).scalars()
        )
    assert {
        "ck_completed_review_coverage",
        "ck_completed_review_items_coverage",
        "ck_reviewed_plan_items_coverage",
        "guard_completed_review_items",
        "guard_reviewed_plan_items",
        "guard_completed_review_header",
    } <= names


@pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="PostgreSQL constraint-name parity requires AUVRA_TEST_DATABASE_URL",
)
def test_postgres_engagement_ledger_constraints_match_metadata_names() -> None:
    """Keep 0011's named ledger invariants from silently drifting from the ORM."""
    from sqlalchemy import create_engine, inspect

    inspector = inspect(create_engine(os.environ["AUVRA_TEST_DATABASE_URL"]))
    refresh_checks = {
        item["name"]
        for item in inspector.get_check_constraints("plan_refreshes", schema="app")
    }
    refresh_unique = {
        item["name"]
        for item in inspector.get_unique_constraints("plan_refreshes", schema="app")
    }
    streak_checks = {
        item["name"]
        for item in inspector.get_check_constraints("streak_days", schema="app")
    }
    streak_unique = {
        item["name"]
        for item in inspector.get_unique_constraints("streak_days", schema="app")
    }
    assert {
        "ck_plan_refreshes_nonempty_reason",
        "ck_plan_refreshes_timezone_nonempty",
    } <= refresh_checks
    assert {
        "uq_plan_refreshes_old_item_id",
        "uq_plan_refreshes_new_item_id",
    } <= refresh_unique
    assert {
        "ck_streak_days_ck_streak_days_kind",
        "ck_streak_days_ck_streak_days_evidence",
        "ck_streak_days_ck_streak_days_state",
        "ck_streak_days_state_evidence",
        "ck_streak_days_timezone_nonempty",
    } <= streak_checks
    assert "uq_streak_days_evidence_type_evidence_id" in streak_unique
