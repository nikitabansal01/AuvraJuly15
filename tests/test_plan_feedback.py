"""Feedback remains attached to the action/day that produced it; retries are reads."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api.v1.endpoints.action_plan import submit_plan_satisfaction, _convert_to_legacy_format
from app.models.action_plan_models import PlanSatisfactionRequest, ActionPlanResponse
from app.services.action_plan_generator import ActionPlanGenerator


class Query:
    def __init__(self, model, value, statements):
        self.query = Session().query(model)
        self.value, self.statements = value, statements

    def filter(self, *conditions):
        self.query = self.query.filter(*conditions)
        return self

    def with_for_update(self, **kwargs):
        self.query = self.query.with_for_update(**kwargs)
        return self

    def first(self):
        self.statements.append(str(self.query.statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
        return self.value

    all = first


class Database:
    def __init__(self, *values):
        self.values, self.statements = iter(values), []
        self.add, self.commit, self.rollback = Mock(), Mock(), Mock()

    def query(self, model):
        return Query(model, next(self.values), self.statements)


def plan(acknowledged=False):
    return SimpleNamespace(id=20, uid="qa-user", feedback_collected=acknowledged)


def item(item_id=49, **changes):
    return SimpleNamespace(**({"id": item_id, "title": "Brisk Walking", "category": "movement",
        "target_hormone": "insulin", "is_completed": False, "is_replaced": False} | changes))


def request():
    return PlanSatisfactionRequest(plan_id=20, satisfaction="works_for_me")


async def test_positive_feedback_is_scoped_to_the_owned_plan_and_active_item():
    owned = plan()
    db = Database(owned, [item(), item(50, is_completed=True), item(51, is_replaced=True)], None)
    result = await submit_plan_satisfaction(request(), {"uid": "qa-user"}, db)
    assert result.success and owned.feedback_collected
    feedback = db.add.call_args.args[0]
    assert feedback.uid == "qa-user" and feedback.plan_id == 20 and feedback.item_id == 49
    db.add.assert_called_once()
    db.commit.assert_called_once()
    assert "FOR UPDATE OF action_plans" in db.statements[0]
    assert "action_plans.uid = 'qa-user'" in db.statements[0]
    # Same-title actions on another day must retain their own historical record.
    assert "plan_id = 20" in db.statements[2] and "item_id = 49" in db.statements[2]


async def test_an_acknowledged_plan_retry_does_not_rewrite_history_or_create_feedback():
    db = Database(plan(True))
    result = await submit_plan_satisfaction(request(), {"uid": "qa-user"}, db)
    assert result.success
    db.add.assert_not_called()
    db.commit.assert_not_called()
    assert len(db.statements) == 1


async def test_partial_prior_feedback_keeps_its_first_timestamp_and_item_ownership():
    original = datetime(2026, 10, 9, 10)
    prior = SimpleNamespace(plan_id=20, item_id=49, created_at=original)
    db = Database(plan(), [item()], prior)
    assert (await submit_plan_satisfaction(request(), {"uid": "qa-user"}, db)).success
    assert (prior.plan_id, prior.item_id, prior.created_at) == (20, 49, original)
    db.add.assert_not_called()


async def test_another_users_plan_cannot_be_acknowledged():
    db = Database(None)
    with pytest.raises(HTTPException) as error:
        await submit_plan_satisfaction(request(), {"uid": "qa-user"}, db)
    assert error.value.status_code == 404
    db.add.assert_not_called()


@pytest.mark.parametrize("acknowledged", [True, False])
async def test_feedback_fact_survives_generator_model_and_legacy_assignment_conversion(acknowledged):
    class EmptyItems:
        def scalars(self): return self
        def all(self): return []

    class EmptyDatabase:
        async def execute(self, _query): return EmptyItems()

    stored = SimpleNamespace(id=20, plan_date=date(2026, 10, 11), primary_hormone="insulin",
        secondary_hormones=[], cycle_day=11, cycle_phase="follicular",
        generation_cost="0", generation_time_ms=1, feedback_collected=acknowledged)
    generator = ActionPlanGenerator.__new__(ActionPlanGenerator)
    result = await generator._format_plan_response(stored, EmptyDatabase())
    assert result["success"] and result["feedback_collected"] is acknowledged
    assert ActionPlanResponse(**result).feedback_collected is acknowledged
    assert _convert_to_legacy_format(result)["feedback_collected"] is acknowledged
