"""Insights report retained observations rather than filling absent measurements."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api.v1.endpoints.weekly_checkin import TrendDataPoint, FactorImpact
from app.core.database import UserProfile, UserResponse
from app.services.cycle_service import CycleService
from app.services.weekly_checkin_service import WeeklyCheckInService
from app.utils.checkin_insights import factor_mentions


def checkin(**changes):
    return SimpleNamespace(**({"id": "qa-checkin", "week_number": 41,
        "check_in_date": date(2026, 10, 11), "top_concern": "Adult Acne",
        "concern_severity": 3, "overall_wellbeing": None,
        "factors_positive": ["Improved sleep"], "factors_negative": [],
        "phase_at_checkin": "Follicular phase"} | changes))


def test_the_current_checkin_flow_keeps_a_helpful_factor_without_a_wellbeing_score():
    result = factor_mentions([checkin()])
    assert result["helps"] == [{"factor": "Improved sleep", "occurrences": 1,
                                "rated_occurrences": 0, "avg_wellbeing": None}]
    assert FactorImpact(**result["helps"][0]).avg_wellbeing is None


def test_mentions_and_the_number_of_ratings_use_distinct_denominators():
    rows = [checkin(), checkin(overall_wellbeing=8)]
    factor = factor_mentions(rows)["helps"][0]
    assert factor["occurrences"] == 2 and factor["rated_occurrences"] == 1
    assert factor["avg_wellbeing"] == 8


def test_duplicate_factor_tags_count_once_per_checkin_and_missing_scores_are_not_zero():
    result = factor_mentions(iter([checkin(
        factors_positive=[" Good_sleep ", "good sleep", "None of the above", None],
        factors_negative=["Work stress", "work_stress"], concern_severity=None)]))
    assert result["helps"][0]["occurrences"] == 1
    assert result["hurts"] == [{"factor": "Work stress", "occurrences": 1,
                                "rated_occurrences": 0, "avg_severity": None}]


def test_trend_rows_keep_identity_date_concern_and_structured_factors_from_the_same_record():
    service = WeeklyCheckInService.__new__(WeeklyCheckInService)
    service.get_checkin_history = Mock(return_value=[checkin()])
    result = service.get_severity_trends("qa-user", weeks=104)
    service.get_checkin_history.assert_called_once_with("qa-user", limit=104)
    row = TrendDataPoint(**result[0])
    assert (row.id, row.date, row.concern, row.severity) == ("qa-checkin", "2026-10-11", "Adult Acne", 3)
    assert row.factors_positive == ["Improved sleep"] and row.wellbeing is None


def test_cycle_snapshot_date_uses_the_saved_timezone_even_without_cycle_answers(monkeypatch):
    instant = datetime(2026, 10, 11, 0, 15, tzinfo=timezone.utc)
    service = CycleService()
    monkeypatch.setattr(service, "_get_current_date_in_timezone", lambda zone: instant.astimezone(ZoneInfo(zone)).date())
    profile = SimpleNamespace(name="QA", current_timezone="America/Los_Angeles")
    result = service._build_cycle_phase_info("qa-user", profile, None)
    assert result.as_of_date == date(2026, 10, 10) and result.timezone == profile.current_timezone
    assert result.phase is None and result.cycle_day is None


def test_phase_and_plan_snapshot_can_share_one_calendar_date(monkeypatch):
    service = CycleService()
    today = date(2026, 10, 11)
    monkeypatch.setattr(service, "_get_current_date_in_timezone", lambda _: today)
    calculate = Mock(return_value=(11, "Follicular phase"))
    monkeypatch.setattr(service, "_calculate_cycle_phase", calculate)
    profile = SimpleNamespace(name="QA", current_timezone="Asia/Kolkata")
    answers = SimpleNamespace(last_period_date_utc=datetime(2026, 10, 1), cycle_length="26-30 days",
                              period_description="Regular", diagnosed_conditions=[])
    result = service._build_cycle_phase_info("qa-user", profile, answers)
    assert result.as_of_date == today and calculate.call_args.args[-1] == today
    assert result.cycle_length_days == 28


def test_sync_cycle_lookup_matches_the_profiles_latest_survey_policy(monkeypatch):
    statements = []
    profile, latest = object(), object()
    class Query:
        def __init__(self, model): self.model, self.query = model, Session().query(model)
        def filter(self, *where): self.query = self.query.filter(*where); return self
        def order_by(self, *order): self.query = self.query.order_by(*order); return self
        def first(self):
            statements.append(str(self.query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
            return profile if self.model is UserProfile else latest
    service = CycleService(SimpleNamespace(query=lambda model: Query(model)))
    build = Mock(return_value=service._default_cycle_info())
    monkeypatch.setattr(service, "_build_cycle_phase_info", build)
    service.get_cycle_phase_info("qa-user")
    build.assert_called_once_with("qa-user", profile, latest)
    assert "created_at DESC" in statements[1] and "uid = 'qa-user'" in statements[1]
