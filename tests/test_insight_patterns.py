from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api.v1.endpoints import insights
from app.utils import timezone_utils


class Query:
    def __init__(self, models, values, statements):
        self.query, self.values, self.statements = Session().query(*models), values, statements
    def join(self, *args): self.query = self.query.join(*args); return self
    def filter(self, *args): self.query = self.query.filter(*args); return self
    def order_by(self, *args): self.query = self.query.order_by(*args); return self
    def all(self):
        self.statements.append(str(self.query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
        return self.values


class Database:
    def __init__(self, *results): self.results, self.statements = iter(results), []
    def query(self, *models): return Query(models, next(self.results), self.statements)


def action(item_id, category="food", completed=True):
    # ActionPlanItem intentionally has no plan_date field.
    return SimpleNamespace(id=item_id, category=category, is_completed=completed)


@pytest.fixture
def unlocked(monkeypatch):
    monkeypatch.setattr(insights.RewardService, "is_reward_unlocked", lambda *_: True)
    monkeypatch.setattr(timezone_utils, "get_user_current_date", lambda *_: date(2026, 10, 11))


async def test_pattern_weeks_use_parent_plan_dates_and_preserve_every_recorded_category(unlocked):
    db = Database([(action(1), date(2026, 10, 9)), (action(2, "movement", False), date(2026, 10, 10)),
                   (action(3, "mindfulness"), date(2026, 10, 2)), (action(4, "unknown"), date(2026, 9, 20))], [])
    result = await insights.get_symptom_patterns({"uid": "qa-user"}, db)
    assert result.period_start == "2026-09-12" and result.period_end == "2026-10-11"
    assert result.period_days == 30
    assert [week.total_completed for week in result.weekly_trends] == [1, 0, 1, 1]
    assert result.weekly_trends[-1].total_assigned == 2
    assert sum(row.completed for row in result.category_breakdown) == 3
    assert "action_plans.uid = 'qa-user'" in db.statements[0]
    assert "action_plan_items.uid IS NULL" in db.statements[0]
    assert "is_replaced IS NOT true" in db.statements[0]
    assert "2026-09-12" in db.statements[0]


async def test_action_preference_counts_use_latest_owned_feedback_for_each_item(unlocked):
    latest = SimpleNamespace(item_id=1, feedback_type="dislike")
    older = SimpleNamespace(item_id=1, feedback_type="like")
    db = Database([(action(1), date(2026, 10, 9))], [latest, older])
    result = await insights.get_symptom_patterns({"uid": "qa-user"}, db)
    assert result.category_breakdown[0].liked == 0 and result.category_breakdown[0].disliked == 1
    assert "action_plan_feedback.uid = 'qa-user'" in db.statements[1]
    assert "coalesce(" in db.statements[1] and " DESC" in db.statements[1]


async def test_a_new_users_missing_previous_week_is_not_called_an_improvement(unlocked):
    db = Database([(action(1), date(2026, 10, 11))], [])
    result = await insights.get_symptom_patterns({"uid": "qa-user"}, db)
    assert not any("more actions this week" in value for value in result.insights)
    assert result.weekly_trends[-2].total_assigned == 0


async def test_empty_pattern_history_is_not_a_chart_of_fabricated_zero_observations(unlocked):
    result = await insights.get_symptom_patterns({"uid": "qa-user"}, Database([]))
    assert result.category_breakdown == [] and result.weekly_trends == []
    assert result.period_start == "2026-09-12"


async def test_the_existing_reward_gate_still_runs_before_any_analytics_read(monkeypatch):
    monkeypatch.setattr(insights.RewardService, "is_reward_unlocked", lambda *_: False)
    db = SimpleNamespace(query=Mock())
    with pytest.raises(HTTPException) as error:
        await insights.get_symptom_patterns({"uid": "qa-user"}, db)
    assert error.value.status_code == 403
    db.query.assert_not_called()
