"""The original calendar receives estimates from Home's existing calculation."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql

from app.core.database import UserProfile, UserResponse
from app.services.cycle_service import CycleService


def fixture(monkeypatch, start=datetime(2026, 9, 30, 18, 30), today=date(2026, 10, 11)):
    profile = SimpleNamespace(uid="qa-one", name="QA", current_timezone="Asia/Kolkata")
    answers = SimpleNamespace(uid="qa-one", last_period_date_utc=start, survey_timezone="Asia/Kolkata",
        cycle_length="26-30 days", period_description="Regular", diagnosed_conditions=[])
    statements = []
    class Query:
        def __init__(self, model): self.model, self.query = model, Session().query(model)
        def filter(self, *where): self.query = self.query.filter(*where); return self
        def order_by(self, *order): self.query = self.query.order_by(*order); return self
        def first(self):
            compiled = self.query.statement.compile(dialect=postgresql.dialect())
            statements.append(str(self.query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
            if compiled.params.get('uid_1') != 'qa-one': return None
            return profile if self.model is UserProfile else answers
    db = Mock(); db.query = lambda model: Query(model)
    service = CycleService(db)
    monkeypatch.setattr(service, '_get_current_date_in_timezone', lambda _: today)
    return service, db, profile, answers, statements


def test_todays_calendar_phase_matches_home_and_does_not_mutate_any_records(monkeypatch):
    service, db, profile, answers, _ = fixture(monkeypatch)
    home = service._build_cycle_phase_info('qa-one', profile, answers)
    result = service.get_calendar_month('qa-one', 2026, 10)
    today = next(day for day in result.days if day.date == home.as_of_date)
    assert (today.cycle_day, today.phase) == (home.cycle_day, home.phase)
    assert len(result.days) == 31 and result.month == '2026-10'
    db.commit.assert_not_called(); db.add.assert_not_called(); db.delete.assert_not_called()


def test_only_a_saved_start_gets_a_reported_marker_not_every_estimated_period(monkeypatch):
    service, _, _, _, _ = fixture(monkeypatch)
    result = service.get_calendar_month('qa-one', 2026, 10)
    assert [day.date for day in result.days if day.reported_period_start] == [date(2026, 10, 1)]
    november = service.get_calendar_month('qa-one', 2026, 11)
    assert not any(day.reported_period_start for day in november.days)


def test_future_estimates_are_explicit_projections_and_do_not_become_observations(monkeypatch):
    service, _, _, _, _ = fixture(monkeypatch)
    result = service.get_calendar_month('qa-one', 2026, 10)
    assert all(day.is_projection == (day.date > date(2026, 10, 11)) for day in result.days)
    assert not any(day.reported_period_start for day in result.days if day.is_projection)


def test_days_before_the_saved_start_have_no_invented_phase_history(monkeypatch):
    service, _, _, _, _ = fixture(monkeypatch)
    result = service.get_calendar_month('qa-one', 2026, 9)
    assert all(day.phase is None and day.cycle_day is None for day in result.days)


def test_a_leap_month_has_the_exact_calendar_dates(monkeypatch):
    service, _, _, _, _ = fixture(monkeypatch, start=datetime(2024, 1, 31, 18, 30))
    result = service.get_calendar_month('qa-one', 2024, 2)
    assert len(result.days) == 29 and result.days[-1].date == date(2024, 2, 29)


def test_unknown_answers_remain_unknown_and_other_accounts_are_never_read(monkeypatch):
    service, _, _, _, statements = fixture(monkeypatch)
    result = service.get_calendar_month('qa-two', 2026, 10)
    assert all(day.phase is None and not day.reported_period_start for day in result.days)
    assert result.cycle_info.can_update_period is False
    assert all("uid = 'qa-two'" in statement for statement in statements)


def test_missing_cycle_length_does_not_fill_a_month_with_default_phases(monkeypatch):
    service, _, _, answers, _ = fixture(monkeypatch)
    answers.cycle_length = None
    result = service.get_calendar_month('qa-one', 2026, 10)
    assert all(day.phase is None for day in result.days)
    assert result.days[0].reported_period_start is True
