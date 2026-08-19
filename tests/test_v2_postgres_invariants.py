"""Executable PostgreSQL 17 tests for cross-row v2 publication invariants."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, date, datetime

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="PostgreSQL trigger tests require AUVRA_TEST_DATABASE_URL",
)


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])


def _insert_user_and_job(connection) -> tuple[uuid.UUID, uuid.UUID]:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    connection.execute(
        text("INSERT INTO app.users (id, auth_subject) " "VALUES (:id, :subject)"),
        {"id": user_id, "subject": f"test-{user_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO ops.generation_jobs "
            "(id, user_id, job_type, request_payload) "
            "VALUES (:id, :user_id, 'plan_generation', CAST(:payload AS jsonb))"
        ),
        {"id": job_id, "user_id": user_id, "payload": "{}"},
    )
    return user_id, job_id


def test_latest_plan_generation_repository_is_owner_scoped_and_deterministic() -> None:
    """PG17 proof for recovery's owner filter and created_at/id tie-breaker."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.v2.persistence.database import _async_database_url
    from app.v2.persistence.repositories import JobRepository

    owner_id, other_id = uuid.uuid4(), uuid.uuid4()
    oldest_id, newest_id = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
    other_job_id = uuid.uuid4()
    target_day = date(2026, 8, 8)
    with _engine().begin() as connection:
        for user_id, subject in ((owner_id, "owner"), (other_id, "other")):
            connection.execute(
                text("INSERT INTO app.users (id, auth_subject) VALUES (:id, :subject)"),
                {"id": user_id, "subject": f"recovery-{subject}-{user_id}"},
            )
        payload = json.dumps({"local_date": target_day.isoformat()})
        rows = (
            (oldest_id, owner_id, "failed", datetime(2026, 8, 8, 10, tzinfo=UTC)),
            (newest_id, owner_id, "dead_letter", datetime(2026, 8, 8, 10, tzinfo=UTC)),
            (other_job_id, other_id, "ready", datetime(2026, 8, 8, 12, tzinfo=UTC)),
        )
        for job_id, user_id, state, created_at in rows:
            connection.execute(
                text(
                    "INSERT INTO ops.generation_jobs "
                    "(id, user_id, job_type, state, request_payload, created_at, updated_at) "
                    "VALUES (:id, :user_id, 'plan_generation', :state, "
                    "CAST(:payload AS jsonb), :created_at, :created_at)"
                ),
                {
                    "id": job_id,
                    "user_id": user_id,
                    "state": state,
                    "payload": payload,
                    "created_at": created_at,
                },
            )

    async def read_latest() -> tuple[uuid.UUID | None, uuid.UUID | None]:
        engine = create_async_engine(_async_database_url(os.environ["AUVRA_TEST_DATABASE_URL"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                repository = JobRepository(session)
                owner_job = await repository.get_latest_plan_generation(owner_id, target_day)
                other_job = await repository.get_latest_plan_generation(other_id, target_day)
                return (
                    owner_job.id if owner_job else None,
                    other_job.id if other_job else None,
                )
        finally:
            await engine.dispose()

    assert asyncio.run(read_latest()) == (newest_id, other_job_id)


def _insert_asset(connection, user_id: uuid.UUID, ordinal: int) -> uuid.UUID:
    from sqlalchemy import text

    asset_id = uuid.uuid4()
    digest = hashlib.sha256(f"asset-{asset_id}".encode()).hexdigest()
    connection.execute(
        text(
            "INSERT INTO app.media_assets "
            "(id, owner_user_id, storage_provider, bucket, object_key, public_url, "
            "content_sha256, mime_type, alt_text, status) "
            "VALUES (:id, :user_id, 'test', 'plans', :key, :url, :digest, "
            "'image/png', :alt, 'ready')"
        ),
        {
            "id": asset_id,
            "user_id": user_id,
            "key": f"test/{asset_id}.png",
            "url": f"https://assets.example.test/{asset_id}.png",
            "digest": digest,
            "alt": f"Test action illustration {ordinal}",
        },
    )
    return asset_id


def _insert_complete_plan(
    connection,
) -> tuple[uuid.UUID, list[uuid.UUID], list[uuid.UUID]]:
    from sqlalchemy import text

    user_id, job_id = _insert_user_and_job(connection)
    plan_id = uuid.uuid4()
    assets = [_insert_asset(connection, user_id, ordinal) for ordinal in range(16)]
    connection.execute(
        text(
            "INSERT INTO app.action_plans "
            "(id, user_id, generation_job_id, local_date, timezone, revision, "
            "is_current, status, cycle_snapshot, context_snapshot) "
            "VALUES (:id, :user_id, :job_id, :local_date, 'UTC', 1, false, "
            "'archived', CAST(:cycle AS jsonb), CAST(:context AS jsonb))"
        ),
        {
            "id": plan_id,
            "user_id": user_id,
            "job_id": job_id,
            "local_date": date(2026, 8, 8),
            "cycle": "{}",
            "context": "{}",
        },
    )
    item_ids: list[uuid.UUID] = []
    for slot in range(1, 5):
        item_id = uuid.uuid4()
        item_ids.append(item_id)
        connection.execute(
            text(
                "INSERT INTO app.action_plan_items "
                "(id, plan_id, slot, category, title, purpose, instructions, "
                "hero_asset_id) VALUES (:id, :plan_id, :slot, 'wellness', :title, "
                ":purpose, CAST(:instructions AS jsonb), :asset_id)"
            ),
            {
                "id": item_id,
                "plan_id": plan_id,
                "slot": slot,
                "title": f"Action {slot}",
                "purpose": "A test-only action",
                "instructions": json.dumps({"steps": ["test"]}),
                "asset_id": assets[(slot - 1) * 4],
            },
        )
        for variant in range(1, 4):
            connection.execute(
                text(
                    "INSERT INTO app.action_plan_item_variants "
                    "(id, item_id, variant_type, content, asset_id) "
                    "VALUES (:id, :item_id, :variant_type, CAST(:content AS jsonb), "
                    ":asset_id)"
                ),
                {
                    "id": uuid.uuid4(),
                    "item_id": item_id,
                    "variant_type": f"variant_{variant}",
                    "content": "{}",
                    "asset_id": assets[(slot - 1) * 4 + variant],
                },
            )
    connection.execute(
        text(
            "UPDATE app.action_plans SET status = 'ready', is_current = true, "
            "published_at = :now WHERE id = :id"
        ),
        {"id": plan_id, "now": datetime.now(UTC)},
    )
    return plan_id, item_ids, assets


def _add_citations(connection, item_ids: list[uuid.UUID]) -> None:
    from sqlalchemy import text

    source_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.research_sources "
            "(id, source_type, source_external_id, canonical_url, title, metadata_json) "
            "VALUES (:id, 'pubmed', :external_id, :url, 'Test source', '{}'::jsonb)"
        ),
        {
            "id": source_id,
            "external_id": str(source_id),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{source_id.int % 1000000}/",
        },
    )
    for item_id in item_ids:
        connection.execute(
            text(
                "INSERT INTO app.action_item_citations "
                "(id, source_id, plan_item_id, claim) "
                "VALUES (:id, :source_id, :item_id, 'Test claim')"
            ),
            {"id": uuid.uuid4(), "source_id": source_id, "item_id": item_id},
        )


def test_selected_variant_replacement_and_daily_grant_are_transactional() -> None:
    """PG17 proof: replacement is complete/replay-safe and grants do not double."""
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as connection:
        plan_id, item_ids, _ = _insert_complete_plan(connection)
        _add_citations(connection, item_ids)
        user_id, subject = connection.execute(
            text(
                "SELECT user_id, auth_subject FROM app.action_plans JOIN app.users "
                "ON app.users.id = app.action_plans.user_id WHERE action_plans.id = :id"
            ),
            {"id": plan_id},
        ).one()
        selected_variant_id = connection.execute(
            text(
                "SELECT id FROM app.action_plan_item_variants "
                "WHERE item_id = :item_id ORDER BY variant_type LIMIT 1"
            ),
            {"item_id": item_ids[0]},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE app.action_plan_item_variants SET content = "
                '\'{"title": "Selected variant", "instructions": ["selected"]}\'::jsonb '
                "WHERE id = :id"
            ),
            {"id": selected_variant_id},
        )

    async def run() -> tuple[object, object]:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.v2.application.contracts import (
            DailyReviewRequest,
            PlanReplacementRequest,
        )
        from app.v2.application.engagement import daily_review
        from app.v2.application.plan_replacement import (
            replace_plan_with_selected_variant,
        )
        from app.v2.domain.identity import VerifiedPrincipal
        from app.v2.persistence.database import _async_database_url
        from app.v2.persistence.uow import SqlAlchemyUnitOfWork

        async_engine = create_async_engine(
            _async_database_url(os.environ["AUVRA_TEST_DATABASE_URL"])
        )
        factory = async_sessionmaker(async_engine, expire_on_commit=False)
        principal = VerifiedPrincipal("firebase", subject, None, False, None)
        replacement_request = PlanReplacementRequest(
            item_id=item_ids[0],
            selected_variant_id=selected_variant_id,
            reason="not_a_fit",
        )
        async with SqlAlchemyUnitOfWork(factory) as uow:
            first = await replace_plan_with_selected_variant(
                uow,
                principal=principal,
                plan_id=plan_id,
                revision=1,
                key="pg-replacement-replay-0001",
                request=replacement_request,
                now=datetime(2026, 8, 10, tzinfo=UTC),
            )
        async with SqlAlchemyUnitOfWork(factory) as uow:
            replay = await replace_plan_with_selected_variant(
                uow,
                principal=principal,
                plan_id=plan_id,
                revision=1,
                key="pg-replacement-replay-0001",
                request=replacement_request,
                now=datetime(2026, 8, 10, tzinfo=UTC),
            )
        review_request = DailyReviewRequest.model_validate(
            {"items": [{"plan_item_id": item_id, "outcome": "completed"} for item_id in item_ids]}
        )

        async def submit() -> object:
            async with SqlAlchemyUnitOfWork(factory) as uow:
                return await daily_review(
                    uow,
                    principal=principal,
                    plan_id=plan_id,
                    revision=1,
                    key="pg-review-concurrent-0001",
                    request=review_request,
                    now=datetime(2026, 8, 10, tzinfo=UTC),
                )

        review_a, review_b = await asyncio.gather(submit(), submit())
        await async_engine.dispose()
        assert review_a == review_b
        return first, replay

    first, replay = asyncio.run(run())
    assert first == replay
    with engine.connect() as connection:
        successor_id, successor_revision = connection.execute(
            text(
                "SELECT id, revision FROM app.action_plans "
                "WHERE user_id = :user_id AND local_date = :local_date AND is_current"
            ),
            {"user_id": user_id, "local_date": date(2026, 8, 8)},
        ).one()
        assert successor_id == first.plan_id
        assert successor_revision == 2
        assert (
            connection.execute(
                text(
                    "SELECT count(DISTINCT asset_id) FROM ("
                    "SELECT hero_asset_id AS asset_id FROM app.action_plan_items WHERE plan_id = :plan_id "
                    "UNION ALL SELECT asset_id FROM app.action_plan_item_variants "
                    "WHERE item_id IN (SELECT id FROM app.action_plan_items WHERE plan_id = :plan_id)"
                    ") assets"
                ),
                {"plan_id": successor_id},
            ).scalar_one()
            == 16
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM app.action_item_citations WHERE plan_item_id IN "
                    "(SELECT id FROM app.action_plan_items WHERE plan_id = :plan_id)"
                ),
                {"plan_id": successor_id},
            ).scalar_one()
            == 4
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM app.plan_refreshes WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM app.streak_days WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM app.reward_ledger WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
    # This rehearsal database is shared across PG17 tests. Remove only this
    # test's UUID-scoped graph; the database itself is never dropped/rebuilt.
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM ops.idempotency_keys WHERE subject = :user_id"),
            {"user_id": str(user_id)},
        )
        for table in (
            "app.plan_refreshes",
            "app.reward_ledger",
            "app.streak_days",
            "app.daily_reviews",
            "app.action_plans",
            "ops.generation_jobs",
            "app.users",
        ):
            column = "id" if table == "app.users" else "user_id"
            connection.execute(
                text(f"DELETE FROM {table} WHERE {column} = :user_id"),
                {"user_id": user_id},
            )


def test_ready_plan_requires_complete_graph_and_ready_permanent_assets() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with pytest.raises(DBAPIError, match="four-item/sixteen-image"):
        with engine.begin() as connection:
            user_id, job_id = _insert_user_and_job(connection)
            connection.execute(
                text(
                    "INSERT INTO app.action_plans "
                    "(id, user_id, generation_job_id, local_date, timezone, revision, "
                    "is_current, status, cycle_snapshot, context_snapshot, published_at) "
                    "VALUES (:id, :user_id, :job_id, :local_date, 'UTC', 1, true, "
                    "'ready', '{}'::jsonb, '{}'::jsonb, :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "job_id": job_id,
                    "local_date": date(2026, 8, 8),
                    "now": datetime.now(UTC),
                },
            )

    with engine.begin() as connection:
        plan_id, _, assets = _insert_complete_plan(connection)

    with pytest.raises(DBAPIError, match="four-item/sixteen-image"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE app.media_assets SET status = 'failed' WHERE id = :id"),
                {"id": assets[0]},
            )

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM app.action_plans WHERE id = :id"),
                {"id": plan_id},
            ).scalar_one()
            == "ready"
        )


def test_completed_review_is_complete_owned_and_immutable() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with engine.begin() as connection:
        plan_id, item_ids, _ = _insert_complete_plan(connection)
        user_id = connection.execute(
            text("SELECT user_id FROM app.action_plans WHERE id = :id"),
            {"id": plan_id},
        ).scalar_one()
        review_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO app.daily_reviews "
                "(id, user_id, plan_id, local_date, timezone) "
                "VALUES (:id, :user_id, :plan_id, :local_date, 'UTC')"
            ),
            {
                "id": review_id,
                "user_id": user_id,
                "plan_id": plan_id,
                "local_date": date(2026, 8, 8),
            },
        )
        for item_id in item_ids:
            connection.execute(
                text(
                    "INSERT INTO app.daily_review_items "
                    "(id, daily_review_id, plan_item_id, outcome, answered_at) "
                    "VALUES (:id, :review_id, :item_id, 'completed', :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "review_id": review_id,
                    "item_id": item_id,
                    "now": datetime.now(UTC),
                },
            )
        connection.execute(
            text(
                "UPDATE app.daily_reviews SET status = 'completed', completed_at = :now "
                "WHERE id = :id"
            ),
            {"id": review_id, "now": datetime.now(UTC)},
        )

    with pytest.raises(DBAPIError, match="completed review header is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE app.daily_reviews SET timezone = 'Asia/Kolkata' WHERE id = :id"),
                {"id": review_id},
            )

    with pytest.raises(DBAPIError, match="completed review items are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM app.daily_review_items "
                    "WHERE daily_review_id = :review_id AND plan_item_id = :item_id"
                ),
                {"review_id": review_id, "item_id": item_ids[0]},
            )


def test_action_event_owner_and_local_decision_are_database_invariants() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with engine.begin() as connection:
        plan_id, item_ids, _ = _insert_complete_plan(connection)
        owner_id = connection.execute(
            text("SELECT user_id FROM app.action_plans WHERE id = :id"),
            {"id": plan_id},
        ).scalar_one()
        other_user_id, _ = _insert_user_and_job(connection)

    with pytest.raises(DBAPIError, match="action event must match"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app.action_item_events "
                    "(id, user_id, plan_item_id, client_event_id, event_type, "
                    "occurred_at, decision_local_date, decision_timezone) "
                    "VALUES (:id, :user_id, :item_id, :client_id, 'completed', "
                    ":occurred_at, :local_date, 'UTC')"
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": other_user_id,
                    "item_id": item_ids[0],
                    "client_id": uuid.uuid4(),
                    "occurred_at": datetime(2026, 8, 8, 12, tzinfo=UTC),
                    "local_date": date(2026, 8, 8),
                },
            )

    with pytest.raises(DBAPIError, match="action event must match"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app.action_item_events "
                    "(id, user_id, plan_item_id, client_event_id, event_type, "
                    "occurred_at, decision_local_date, decision_timezone) "
                    "VALUES (:id, :user_id, :item_id, :client_id, 'completed', "
                    ":occurred_at, :local_date, 'UTC')"
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": owner_id,
                    "item_id": item_ids[0],
                    "client_id": uuid.uuid4(),
                    "occurred_at": datetime(2026, 8, 7, 12, tzinfo=UTC),
                    "local_date": date(2026, 8, 8),
                },
            )


def test_incomplete_review_cannot_commit_completed_state() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with pytest.raises(DBAPIError, match=r"answer exactly its .*plan items"):
        with engine.begin() as connection:
            plan_id, item_ids, _ = _insert_complete_plan(connection)
            user_id = connection.execute(
                text("SELECT user_id FROM app.action_plans WHERE id = :id"),
                {"id": plan_id},
            ).scalar_one()
            review_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO app.daily_reviews "
                    "(id, user_id, plan_id, local_date, timezone) "
                    "VALUES (:id, :user_id, :plan_id, :local_date, 'UTC')"
                ),
                {
                    "id": review_id,
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "local_date": date(2026, 8, 8),
                },
            )
            for item_id in item_ids[:3]:
                connection.execute(
                    text(
                        "INSERT INTO app.daily_review_items "
                        "(id, daily_review_id, plan_item_id, outcome, answered_at) "
                        "VALUES (:id, :review_id, :item_id, 'completed', :now)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "review_id": review_id,
                        "item_id": item_id,
                        "now": datetime.now(UTC),
                    },
                )
            connection.execute(
                text(
                    "UPDATE app.daily_reviews SET status = 'completed', "
                    "completed_at = :now WHERE id = :id"
                ),
                {"id": review_id, "now": datetime.now(UTC)},
            )


def test_engagement_migration_downgrade_restores_prior_review_trigger() -> None:
    """A one-step rollback must leave the inherited review trigger executable."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    config = Config("alembic.ini")
    command.upgrade(config, "20260808_0005")
    try:
        command.downgrade(config, "20260808_0004")
        engine = _engine()
        with engine.begin() as connection:
            plan_id, _, _ = _insert_complete_plan(connection)
            user_id = connection.execute(
                text("SELECT user_id FROM app.action_plans WHERE id = :id"),
                {"id": plan_id},
            ).scalar_one()
            review_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO app.daily_reviews "
                    "(id, user_id, plan_id, local_date, timezone) "
                    "VALUES (:id, :user_id, :plan_id, :local_date, 'UTC')"
                ),
                {
                    "id": review_id,
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "local_date": date(2026, 8, 8),
                },
            )
            definition = connection.execute(
                text("SELECT pg_get_functiondef('app.assert_completed_review(uuid)'::regprocedure)")
            ).scalar_one()
            assert "WHERE plan_id = v_plan_id;" in definition
            assert "active plan items" not in definition
    finally:
        command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# Migration 0012: reward assets, balance integrity and freeze evidence.
#
# Before 0012, app.assert_streak_day_scope returned early for every freeze row,
# so a `frozen` streak day could cite any evidence_id at all. These tests are
# the executable proof that the bypass is closed.
# ---------------------------------------------------------------------------


def _insert_user(connection) -> uuid.UUID:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    connection.execute(
        text("INSERT INTO app.users (id, auth_subject) VALUES (:id, :subject)"),
        {"id": user_id, "subject": f"test-{user_id}"},
    )
    return user_id


def _grant_freeze(connection, user_id: uuid.UUID) -> uuid.UUID:
    """Grant one freeze token so a later redeem does not overdraw."""
    from sqlalchemy import text

    ledger_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.reward_ledger "
            "(id, user_id, source_type, source_id, event_type, asset_type, "
            " asset_key, quantity) "
            "VALUES (:id, :user_id, 'reward_claim', :source_id, 'grant', "
            "'freeze', 'streak_freeze', 1)"
        ),
        {"id": ledger_id, "user_id": user_id, "source_id": uuid.uuid4()},
    )
    return ledger_id


def _redeem_freeze(connection, user_id: uuid.UUID, streak_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy import text

    ledger_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.reward_ledger "
            "(id, user_id, source_type, source_id, event_type, asset_type, "
            " asset_key, quantity) "
            "VALUES (:id, :user_id, 'streak_freeze', :streak_id, 'redeem', "
            "'freeze', 'streak_freeze', -1)"
        ),
        {"id": ledger_id, "user_id": user_id, "streak_id": streak_id},
    )
    return ledger_id


def _insert_frozen_day(connection, user_id, streak_id, evidence_id, day) -> None:
    from sqlalchemy import text

    connection.execute(
        text(
            "INSERT INTO app.streak_days "
            "(id, user_id, local_date, kind, timezone, evidence_type, "
            " evidence_id, adjudication_state) "
            "VALUES (:id, :user_id, :day, 'daily', 'UTC', 'freeze', "
            ":evidence_id, 'frozen')"
        ),
        {
            "id": streak_id,
            "user_id": user_id,
            "day": day,
            "evidence_id": evidence_id,
        },
    )


def test_frozen_day_citing_no_ledger_row_is_rejected() -> None:
    """The exploit: a frozen day with a dangling evidence_id extended a streak."""
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="own redeemed freeze token"):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            _insert_frozen_day(connection, user_id, uuid.uuid4(), uuid.uuid4(), date(2026, 8, 1))


def test_frozen_day_citing_another_users_freeze_is_rejected() -> None:
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="own redeemed freeze token"):
        with _engine().begin() as connection:
            owner_id = _insert_user(connection)
            other_id = _insert_user(connection)
            _grant_freeze(connection, other_id)
            streak_id = uuid.uuid4()
            stolen = _redeem_freeze(connection, other_id, streak_id)
            _insert_frozen_day(connection, owner_id, streak_id, stolen, date(2026, 8, 2))


def test_frozen_day_citing_a_grant_rather_than_a_redeem_is_rejected() -> None:
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="own redeemed freeze token"):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            grant_id = _grant_freeze(connection, user_id)
            _insert_frozen_day(connection, user_id, uuid.uuid4(), grant_id, date(2026, 8, 3))


def test_frozen_day_with_its_own_redeemed_token_is_accepted() -> None:
    from sqlalchemy import text

    with _engine().begin() as connection:
        user_id = _insert_user(connection)
        _grant_freeze(connection, user_id)
        streak_id = uuid.uuid4()
        ledger_id = _redeem_freeze(connection, user_id, streak_id)
        _insert_frozen_day(connection, user_id, streak_id, ledger_id, date(2026, 8, 4))
        state = connection.execute(
            text("SELECT adjudication_state FROM app.streak_days WHERE id = :id"),
            {"id": streak_id},
        ).scalar_one()
    assert state == "frozen"


def test_redeeming_a_freeze_without_a_balance_is_rejected() -> None:
    """Balances are computed, so the ledger itself must refuse to go negative."""
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="cannot go negative"):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            _redeem_freeze(connection, user_id, uuid.uuid4())


def test_one_entitlement_can_be_claimed_only_once() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    def claim(connection, user_id):
        connection.execute(
            text(
                "INSERT INTO app.reward_ledger "
                "(id, user_id, source_type, source_id, event_type, asset_type, "
                " asset_key, quantity) "
                "VALUES (:id, :user_id, 'reward_claim', :source_id, 'grant', "
                "'entitlement', 'diet_prefs', 1)"
            ),
            {"id": uuid.uuid4(), "user_id": user_id, "source_id": uuid.uuid4()},
        )

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            claim(connection, user_id)
            claim(connection, user_id)


def test_points_carry_no_asset_key_and_named_assets_require_one() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    def insert(connection, user_id, asset_type, asset_key):
        connection.execute(
            text(
                "INSERT INTO app.reward_ledger "
                "(id, user_id, source_type, source_id, event_type, asset_type, "
                " asset_key, quantity) "
                "VALUES (:id, :user_id, 'daily_review', :source_id, 'grant', "
                ":asset_type, :asset_key, 1)"
            ),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "source_id": uuid.uuid4(),
                "asset_type": asset_type,
                "asset_key": asset_key,
            },
        )

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            insert(connection, _insert_user(connection), "points", "should_be_null")

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            insert(connection, _insert_user(connection), "freeze", None)
