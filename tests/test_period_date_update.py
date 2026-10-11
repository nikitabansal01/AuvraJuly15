"""Owned period-date correction without fabricated calendar events."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql

from app.core.database import UserProfile, UserResponse
from app.models.cycle_models import PeriodStartUpdate
from app.services.cycle_service import CycleService


def service_for(monkeypatch, zone="Asia/Kolkata", today=date(2026, 10, 11)):
    profile = SimpleNamespace(uid="qa-one", name="QA", current_timezone=zone)
    answers = SimpleNamespace(uid="qa-one", id=21, last_period_date_utc=datetime(2026, 9, 30, 18, 30),
                              survey_timezone="Asia/Kolkata", cycle_length="26-30 days",
                              period_description="Regular", diagnosed_conditions=[])
    statements = []
    class Query:
        def __init__(self, model): self.model, self.query = model, Session().query(model)
        def filter(self, *where): self.query = self.query.filter(*where); return self
        def order_by(self, *order): self.query = self.query.order_by(*order); return self
        def with_for_update(self): self.query = self.query.with_for_update(); return self
        def first(self):
            compiled = self.query.statement.compile(dialect=postgresql.dialect())
            statements.append(str(self.query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
            if compiled.params.get('uid_1') != 'qa-one': return None
            return profile if self.model is UserProfile else answers
    db = Mock()
    db.query = lambda model: Query(model)
    service = CycleService(db)
    monkeypatch.setattr(service, "_get_current_date_in_timezone", lambda _: today)
    return service, db, profile, answers, statements


def test_date_contract_rejects_timestamps_numeric_values_and_invalid_dates():
    for value in ["2026-02-30", "2026-10-01T00:00:00Z", 1790812800, datetime(2026, 10, 1), "10/01/2026"]:
        with pytest.raises(ValidationError): PeriodStartUpdate(last_period_start=value)
    assert PeriodStartUpdate(last_period_start="2026-10-01").last_period_start == date(2026, 10, 1)
    with pytest.raises(ValidationError): PeriodStartUpdate(last_period_start="2026-10-01", uid="another-user")


@pytest.mark.parametrize("zone,reported,expected", [
    ("Asia/Kolkata", date(2026, 10, 1), datetime(2026, 9, 30, 18, 30)),
    ("America/Los_Angeles", date(2026, 3, 8), datetime(2026, 3, 8, 8)),
    ("America/Los_Angeles", date(2026, 3, 9), datetime(2026, 3, 9, 7)),
])
def test_reported_date_round_trips_through_utc_including_daylight_saving(monkeypatch, zone, reported, expected):
    service, db, _, answers, _ = service_for(monkeypatch, zone)
    result = service.update_last_period_start("qa-one", reported)
    assert answers.last_period_date_utc == expected and answers.survey_timezone == zone
    assert result.last_period_start == reported and result.last_period_timezone == zone
    assert result.can_update_period is True
    db.commit.assert_called_once()


def test_future_validation_uses_the_accounts_calendar_date_not_the_servers_date(monkeypatch):
    service, db, _, answers, _ = service_for(monkeypatch, "America/Los_Angeles", today=date(2026, 10, 10))
    before = answers.last_period_date_utc
    with pytest.raises(ValueError, match="future"):
        service.update_last_period_start("qa-one", date(2026, 10, 11))
    assert answers.last_period_date_utc == before
    db.commit.assert_not_called(); db.rollback.assert_called_once()


def test_update_cannot_modify_a_different_accounts_survey(monkeypatch):
    service, db, _, answers, _ = service_for(monkeypatch)
    before = answers.last_period_date_utc
    with pytest.raises(LookupError): service.update_last_period_start("qa-two", date(2026, 10, 2))
    assert answers.last_period_date_utc == before
    db.commit.assert_not_called(); db.rollback.assert_called_once()


def test_update_locks_owned_rows_and_selects_the_latest_survey_with_a_stable_tie_break(monkeypatch):
    service, _, _, _, statements = service_for(monkeypatch)
    service.update_last_period_start("qa-one", date(2026, 10, 2))
    assert all("uid = 'qa-one'" in statement and "FOR UPDATE" in statement for statement in statements)
    assert "created_at DESC" in statements[1] and "id DESC" in statements[1]


def test_a_travel_timezone_change_does_not_relabel_the_original_reported_date(monkeypatch):
    service, _, profile, answers, _ = service_for(monkeypatch, "America/Los_Angeles")
    result = service._build_cycle_phase_info("qa-one", profile, answers)
    assert result.last_period_start == date(2026, 10, 1)
    assert result.last_period_timezone == "Asia/Kolkata"
    assert result.timezone == "America/Los_Angeles"


def test_failed_commit_rolls_back_and_never_returns_a_success_snapshot(monkeypatch):
    service, db, _, _, _ = service_for(monkeypatch)
    db.commit.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError): service.update_last_period_start("qa-one", date(2026, 10, 2))
    db.rollback.assert_called_once()


def test_saving_a_date_without_cycle_length_does_not_invent_a_phase(monkeypatch):
    service, _, _, answers, _ = service_for(monkeypatch)
    answers.cycle_length = None
    result = service.update_last_period_start("qa-one", date(2026, 10, 2))
    assert result.last_period_start == date(2026, 10, 2)
    assert result.cycle_day is None and result.phase is None
