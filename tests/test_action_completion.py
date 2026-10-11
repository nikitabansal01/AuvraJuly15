"""Completion retries must preserve action history and its ownership boundary.

The database results and streak writer are isolated; SQLAlchemy still builds
the actual PostgreSQL queries. These tests never contact a database or an LLM.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api.v1.endpoints.action_plan import complete_action
from app.services.streak_service import StreakService


class QueryResult:
    def __init__(self, model, result, statements):
        self.query = Session().query(model)
        self.result = result
        self.statements = statements

    def filter(self, *conditions):
        self.query = self.query.filter(*conditions)
        return self

    def join(self, *args):
        self.query = self.query.join(*args)
        return self

    def with_for_update(self, **kwargs):
        self.query = self.query.with_for_update(**kwargs)
        return self

    def first(self):
        self.statements.append(str(self.query.statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )))
        return self.result


class DatabaseResults:
    def __init__(self, *results):
        self.results = iter(results)
        self.statements = []
        self.commit = Mock()
        self.rollback = Mock()

    def query(self, model):
        return QueryResult(model, next(self.results), self.statements)


@pytest.fixture
def streak_update(monkeypatch):
    update = Mock(return_value=(1, 1))
    monkeypatch.setattr(StreakService, "update_streak_on_completion", update)
    return update


def action(**changes):
    return SimpleNamespace(
        **({"id": 42, "plan_id": 20, "uid": "qa-user", "session_id": None,
            "is_completed": False, "completed_at": None} | changes)
    )


async def test_retry_preserves_first_completion_and_does_not_repeat_streak(streak_update):
    item = action()
    db = DatabaseResults(item, item)
    first = await complete_action(42, current_user={"uid": "qa-user"}, db=db)
    # Simulate a later request, independently of the wall-clock speed of this test.
    original_time = datetime(2026, 10, 9, 15, 30)
    item.completed_at = original_time
    retry = await complete_action(42, current_user={"uid": "qa-user"}, db=db)

    assert first.success and retry.success
    assert item.completed_at == retry.completed_at == original_time
    streak_update.assert_called_once_with("qa-user")
    assert all("FOR UPDATE OF action_plan_items" in sql for sql in db.statements)
    assert all("action_plan_items.uid = 'qa-user'" in sql for sql in db.statements)


async def test_legacy_completed_action_without_timestamp_is_not_redated(streak_update):
    item = action(is_completed=True)
    db = DatabaseResults(item)
    result = await complete_action(42, current_user={"uid": "qa-user"}, db=db)

    assert result.success and result.completed_at is None
    assert item.completed_at is None
    streak_update.assert_not_called()


@pytest.mark.parametrize("already_completed", [False, True])
async def test_guest_item_adoption_and_completion_share_one_commit(streak_update, already_completed):
    original_time = datetime(2026, 10, 9, 15, 30) if already_completed else None
    item = action(uid=None, session_id="guest-session", is_completed=already_completed,
                  completed_at=original_time)
    db = DatabaseResults(item)
    result = await complete_action(42, current_user={"uid": "qa-user"}, db=db)

    assert result.success and item.uid == "qa-user" and item.session_id is None
    db.commit.assert_called_once()
    orphan_sql = db.statements[0]
    assert "action_plan_items.uid IS NULL" in orphan_sql
    assert "action_plans.uid = 'qa-user'" in orphan_sql
    assert "FOR UPDATE OF action_plan_items" in orphan_sql
    if already_completed:
        assert item.completed_at == original_time
        streak_update.assert_not_called()
    else:
        assert item.is_completed and item.completed_at is not None
        streak_update.assert_called_once_with("qa-user")


async def test_inaccessible_item_returns_404_without_writes(streak_update):
    db = DatabaseResults(None)
    with pytest.raises(HTTPException) as error:
        await complete_action(42, current_user={"uid": "other-user"}, db=db)

    assert error.value.status_code == 404
    db.commit.assert_not_called()
    streak_update.assert_not_called()


async def test_failed_commit_rolls_back_without_awarding_streak(streak_update):
    db = DatabaseResults(action())
    db.commit.side_effect = RuntimeError("private database detail")
    with pytest.raises(HTTPException) as error:
        await complete_action(42, current_user={"uid": "qa-user"}, db=db)

    assert error.value.status_code == 500
    assert error.value.detail == "Failed to complete action"
    db.rollback.assert_called_once()
    streak_update.assert_not_called()
