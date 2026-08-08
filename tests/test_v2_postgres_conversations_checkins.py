"""PostgreSQL 17 proofs for conversation/check-in ownership and definition invariants."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="PostgreSQL trigger tests require AUVRA_TEST_DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _clear_disposable_conversation_facts_after_test():
    """Keep shared PostgreSQL migration tests order-independent without touching definitions."""
    yield
    if not os.getenv("AUVRA_TEST_DATABASE_URL"):
        return
    from sqlalchemy import text

    with _engine().begin() as connection:
        connection.execute(
            text(
                "TRUNCATE app.weekly_checkin_responses, "
                "app.weekly_checkins, app.conversations CASCADE"
            )
        )


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])


def _insert_user(connection) -> uuid.UUID:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    connection.execute(
        text("INSERT INTO app.users (id, auth_subject) VALUES (:id, :subject)"),
        {"id": user_id, "subject": f"conversation-test-{user_id}"},
    )
    return user_id


def _insert_conversation(connection, user_id: uuid.UUID, thread_type: str) -> uuid.UUID:
    from sqlalchemy import text

    conversation_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.conversations (id, user_id, thread_type, status, revision) "
            "VALUES (:id, :user_id, :thread_type, 'active', 1)"
        ),
        {"id": conversation_id, "user_id": user_id, "thread_type": thread_type},
    )
    return conversation_id


def _insert_checkin(
    connection,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    version="weekly-checkin.v1",
):
    from sqlalchemy import text

    checkin_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.weekly_checkins "
            "(id, user_id, week_start, definition_version, timezone, conversation_id, revision) "
            "VALUES (:id, :user_id, :week_start, :version, 'America/Los_Angeles', "
            ":conversation_id, 1)"
        ),
        {
            "id": checkin_id,
            "user_id": user_id,
            "week_start": date(2026, 12, 28),
            "version": version,
            "conversation_id": conversation_id,
        },
    )
    return checkin_id


def test_weekly_checkin_requires_same_owner_typed_conversation() -> None:
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with pytest.raises(DBAPIError, match="same-user weekly-checkin conversation"):
        with engine.begin() as connection:
            user_id = _insert_user(connection)
            conversation_id = _insert_conversation(connection, user_id, "general")
            _insert_checkin(connection, user_id, conversation_id)


def test_weekly_response_must_match_definition_and_completion_requires_all_answers() -> (
    None
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with engine.begin() as connection:
        v1_question_ids = (
            connection.execute(
                text(
                    "SELECT id FROM app.weekly_checkin_questions "
                    "WHERE version = 'weekly-checkin.v1' ORDER BY ordinal"
                )
            )
            .scalars()
            .all()
        )
        assert len(v1_question_ids) == 4

    with pytest.raises(DBAPIError, match="definition version"):
        with engine.begin() as connection:
            user_id = _insert_user(connection)
            conversation_id = _insert_conversation(
                connection, user_id, "weekly_checkin"
            )
            checkin_id = _insert_checkin(connection, user_id, conversation_id)
            wrong_question_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO app.weekly_checkin_questions "
                    "(id, version, ordinal, prompt, answer_type, answer_schema, required) "
                    "VALUES (:id, 'weekly-checkin.v2', 1, 'Wrong version', 'scale', "
                    "CAST(:schema AS jsonb), true)"
                ),
                {
                    "id": wrong_question_id,
                    "schema": json.dumps({"minimum": 0, "maximum": 10}),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO app.weekly_checkin_responses "
                    "(id, weekly_checkin_id, question_id, answer) "
                    "VALUES (:id, :checkin_id, :question_id, CAST(:answer AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "checkin_id": checkin_id,
                    "question_id": wrong_question_id,
                    "answer": json.dumps({"value": 5}),
                },
            )

    with pytest.raises(DBAPIError, match="answer every required definition question"):
        with engine.begin() as connection:
            user_id = _insert_user(connection)
            conversation_id = _insert_conversation(
                connection, user_id, "weekly_checkin"
            )
            checkin_id = _insert_checkin(connection, user_id, conversation_id)
            connection.execute(
                text(
                    "INSERT INTO app.weekly_checkin_responses "
                    "(id, weekly_checkin_id, question_id, answer) "
                    "VALUES (:id, :checkin_id, :question_id, CAST(:answer AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "checkin_id": checkin_id,
                    "question_id": v1_question_ids[0],
                    "answer": json.dumps({"value": 5}),
                },
            )
            connection.execute(
                text(
                    "UPDATE app.weekly_checkins SET completed_at = now() WHERE id = :id"
                ),
                {"id": checkin_id},
            )


def test_completed_checkins_responses_and_referenced_questions_are_immutable() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with engine.begin() as connection:
        question_ids = (
            connection.execute(
                text(
                    "SELECT id FROM app.weekly_checkin_questions "
                    "WHERE version = 'weekly-checkin.v1' AND required ORDER BY ordinal"
                )
            )
            .scalars()
            .all()
        )
        user_id = _insert_user(connection)
        conversation_id = _insert_conversation(connection, user_id, "weekly_checkin")
        checkin_id = _insert_checkin(connection, user_id, conversation_id)
        response_id = uuid.uuid4()
        for ordinal, question_id in enumerate(question_ids):
            connection.execute(
                text(
                    "INSERT INTO app.weekly_checkin_responses "
                    "(id, weekly_checkin_id, question_id, answer) "
                    "VALUES (:id, :checkin_id, :question_id, CAST(:answer AS jsonb))"
                ),
                {
                    "id": response_id if ordinal == 0 else uuid.uuid4(),
                    "checkin_id": checkin_id,
                    "question_id": question_id,
                    "answer": json.dumps({"value": 5}),
                },
            )
        connection.execute(
            text("UPDATE app.weekly_checkins SET completed_at = now() WHERE id = :id"),
            {"id": checkin_id},
        )

    with pytest.raises(DBAPIError, match="weekly check-in response is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE app.weekly_checkin_responses SET answer = :answer WHERE id = :id"
                ),
                {"id": response_id, "answer": json.dumps({"value": 6})},
            )
    with pytest.raises(DBAPIError, match="completed weekly check-in is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE app.weekly_checkins SET timezone = 'UTC' WHERE id = :id"),
                {"id": checkin_id},
            )
    with pytest.raises(
        DBAPIError, match="definition referenced by a check-in is immutable"
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE app.weekly_checkin_questions SET prompt = 'Changed' WHERE id = :id"
                ),
                {"id": question_ids[0]},
            )


def test_question_definition_freezes_when_checkin_starts_before_any_answer() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = _engine()
    with pytest.raises(
        DBAPIError, match="definition referenced by a check-in is immutable"
    ):
        with engine.begin() as connection:
            user_id = _insert_user(connection)
            conversation_id = _insert_conversation(
                connection, user_id, "weekly_checkin"
            )
            question_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO app.weekly_checkin_questions "
                    "(id, version, ordinal, prompt, answer_type, answer_schema, required) "
                    "VALUES (:id, 'weekly-checkin.freeze-test.v1', 1, 'Original', 'boolean', "
                    "CAST(:schema AS jsonb), true)"
                ),
                {"id": question_id, "schema": json.dumps({})},
            )
            _insert_checkin(
                connection,
                user_id,
                conversation_id,
                version="weekly-checkin.freeze-test.v1",
            )
            connection.execute(
                text(
                    "UPDATE app.weekly_checkin_questions SET prompt = 'Changed' WHERE id = :id"
                ),
                {"id": question_id},
            )
