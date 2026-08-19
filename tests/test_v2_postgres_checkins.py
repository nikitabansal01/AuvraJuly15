"""Care-plan thread scoping and weekly check-in history against PostgreSQL 17."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="Check-in tests require AUVRA_TEST_DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _dispose_shared_engine():
    yield
    import asyncio

    from app.v2.persistence.database import dispose_database

    asyncio.run(dispose_database())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])


def _principal(subject: str):
    from app.v2.domain.identity import VerifiedPrincipal

    return VerifiedPrincipal(
        auth_provider="firebase",
        subject=subject,
        email=None,
        email_verified=True,
        display_name=None,
    )


def _seed_user_with_plan(connection) -> tuple[uuid.UUID, str, uuid.UUID]:
    """One active user owning one published plan."""
    from sqlalchemy import text

    user_id, job_id, plan_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    subject = f"checkin-{user_id}"
    connection.execute(
        text(
            "INSERT INTO app.users (id, auth_provider, auth_subject) "
            "VALUES (:id, 'firebase', :subject)"
        ),
        {"id": user_id, "subject": subject},
    )
    connection.execute(
        text("INSERT INTO app.user_profiles (user_id, timezone) " "VALUES (:user_id, 'UTC')"),
        {"user_id": user_id},
    )
    connection.execute(
        text(
            "INSERT INTO ops.generation_jobs (id, user_id, job_type, request_payload) "
            "VALUES (:id, :user_id, 'plan_generation', '{}'::jsonb)"
        ),
        {"id": job_id, "user_id": user_id},
    )
    connection.execute(
        text(
            "INSERT INTO app.action_plans (id, user_id, generation_job_id, "
            " local_date, timezone, revision, is_current, status, cycle_snapshot, "
            " context_snapshot) "
            # 'archived' rather than 'ready': a ready plan must satisfy the
            # four-item/sixteen-image publication invariant, which a check-in
            # thread does not depend on.
            "VALUES (:id, :user_id, :job_id, :local_date, 'UTC', 1, false, "
            "'archived', '{}'::jsonb, '{}'::jsonb)"
        ),
        {
            "id": plan_id,
            "user_id": user_id,
            "job_id": job_id,
            "local_date": date(2026, 8, 8),
        },
    )
    return user_id, subject, plan_id


def _insert_thread(connection, user_id, plan_id, conversation_id=None):
    from sqlalchemy import text

    conversation_id = conversation_id or uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.conversations "
            "(id, user_id, thread_type, subject_type, subject_id) "
            "VALUES (:id, :user_id, 'care_plan', 'action_plan', :plan_id)"
        ),
        {"id": conversation_id, "user_id": user_id, "plan_id": plan_id},
    )
    return conversation_id


def test_only_one_care_plan_thread_can_exist_per_plan() -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            user_id, _, plan_id = _seed_user_with_plan(connection)
            _insert_thread(connection, user_id, plan_id)
            _insert_thread(connection, user_id, plan_id)


def test_a_thread_cannot_cite_another_users_plan() -> None:
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="same user"):
        with _engine().begin() as connection:
            _, _, their_plan = _seed_user_with_plan(connection)
            owner_id, _, _ = _seed_user_with_plan(connection)
            _insert_thread(connection, owner_id, their_plan)


def test_a_subject_type_without_an_id_is_rejected() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            user_id, _, _ = _seed_user_with_plan(connection)
            connection.execute(
                text(
                    "INSERT INTO app.conversations "
                    "(id, user_id, thread_type, subject_type) "
                    "VALUES (:id, :user_id, 'care_plan', 'action_plan')"
                ),
                {"id": uuid.uuid4(), "user_id": user_id},
            )


def test_an_unknown_subject_type_is_rejected() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            user_id, _, plan_id = _seed_user_with_plan(connection)
            connection.execute(
                text(
                    "INSERT INTO app.conversations "
                    "(id, user_id, thread_type, subject_type, subject_id) "
                    "VALUES (:id, :user_id, 'general', 'horoscope', :plan_id)"
                ),
                {"id": uuid.uuid4(), "user_id": user_id, "plan_id": plan_id},
            )


def test_ordinary_threads_still_need_no_subject() -> None:
    from sqlalchemy import text

    with _engine().begin() as connection:
        user_id, _, _ = _seed_user_with_plan(connection)
        connection.execute(
            text(
                "INSERT INTO app.conversations (id, user_id, thread_type) "
                "VALUES (:id, :user_id, 'general')"
            ),
            {"id": uuid.uuid4(), "user_id": user_id},
        )


@pytest.mark.anyio
async def test_opening_a_plan_checkin_twice_returns_one_thread() -> None:
    from app.v2.application.checkins import open_plan_checkin
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        _, subject, plan_id = _seed_user_with_plan(connection)
    principal = _principal(subject)

    async with SqlAlchemyUnitOfWork() as uow:
        first = await open_plan_checkin(
            uow,
            principal=principal,
            plan_id=plan_id,
            revision=1,
            key=f"k-{uuid.uuid4()}",
        )
    async with SqlAlchemyUnitOfWork() as uow:
        second = await open_plan_checkin(
            uow,
            principal=principal,
            plan_id=plan_id,
            revision=1,
            key=f"k-{uuid.uuid4()}",
        )
    assert first.conversation_id == second.conversation_id


@pytest.mark.anyio
async def test_a_stale_plan_revision_is_rejected() -> None:
    from app.v2.application.checkins import open_plan_checkin
    from app.v2.application.errors import ApplicationProblem
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        _, subject, plan_id = _seed_user_with_plan(connection)

    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await open_plan_checkin(
                uow,
                principal=_principal(subject),
                plan_id=plan_id,
                revision=99,
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.code == "plan_revision_conflict"


@pytest.mark.anyio
async def test_a_plan_checkin_on_another_users_plan_is_not_found() -> None:
    from app.v2.application.checkins import open_plan_checkin
    from app.v2.application.errors import ApplicationProblem
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        _, _, their_plan = _seed_user_with_plan(connection)
        _, subject, _ = _seed_user_with_plan(connection)

    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await open_plan_checkin(
                uow,
                principal=_principal(subject),
                plan_id=their_plan,
                revision=1,
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.status == 404


@pytest.mark.anyio
async def test_a_partially_answered_checkin_can_be_reread_by_id() -> None:
    """The gap this closes: /due was the only way to reach a check-in."""
    from sqlalchemy import text

    from app.v2.application.checkins import get_weekly_checkin, list_weekly_checkins
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        user_id, subject, _ = _seed_user_with_plan(connection)
        conversation_id = uuid.uuid4()
        checkin_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO app.conversations (id, user_id, thread_type) "
                "VALUES (:id, :user_id, 'weekly_checkin')"
            ),
            {"id": conversation_id, "user_id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO app.weekly_checkins (id, user_id, week_start, "
                " timezone, definition_version, conversation_id) "
                "VALUES (:id, :user_id, DATE '2026-08-03', 'UTC', "
                "'weekly-checkin.v1', :conversation_id)"
            ),
            {
                "id": checkin_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
            },
        )
        question_id = connection.execute(
            text(
                "SELECT id FROM app.weekly_checkin_questions "
                "WHERE version = 'weekly-checkin.v1' AND answer_type = 'scale' "
                "ORDER BY ordinal LIMIT 1"
            )
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO app.weekly_checkin_responses "
                "(id, weekly_checkin_id, question_id, answer) "
                "VALUES (:id, :checkin_id, :question_id, CAST(:answer AS jsonb))"
            ),
            {
                "id": uuid.uuid4(),
                "checkin_id": checkin_id,
                "question_id": question_id,
                "answer": json.dumps({"value": 4}),
            },
        )

    principal = _principal(subject)
    async with SqlAlchemyUnitOfWork() as uow:
        checkin = await get_weekly_checkin(uow, principal=principal, checkin_id=checkin_id)
    assert checkin.checkin_id == checkin_id
    assert checkin.completed_at is None
    assert len(checkin.answers) == 1
    assert len(checkin.questions) == 4

    async with SqlAlchemyUnitOfWork() as uow:
        page = await list_weekly_checkins(uow, principal=principal)
    assert [c.checkin_id for c in page.checkins] == [checkin_id]
    assert page.next_cursor is None


@pytest.mark.anyio
async def test_another_users_checkin_is_reported_absent_not_forbidden() -> None:
    from sqlalchemy import text

    from app.v2.application.checkins import get_weekly_checkin
    from app.v2.application.errors import ApplicationProblem
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        owner_id, _, _ = _seed_user_with_plan(connection)
        _, intruder_subject, _ = _seed_user_with_plan(connection)
        conversation_id, checkin_id = uuid.uuid4(), uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO app.conversations (id, user_id, thread_type) "
                "VALUES (:id, :user_id, 'weekly_checkin')"
            ),
            {"id": conversation_id, "user_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO app.weekly_checkins (id, user_id, week_start, "
                " timezone, definition_version, conversation_id) "
                "VALUES (:id, :user_id, DATE '2026-08-03', 'UTC', "
                "'weekly-checkin.v1', :conversation_id)"
            ),
            {
                "id": checkin_id,
                "user_id": owner_id,
                "conversation_id": conversation_id,
            },
        )

    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await get_weekly_checkin(
                uow, principal=_principal(intruder_subject), checkin_id=checkin_id
            )
    assert problem.value.status == 404
