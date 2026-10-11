"""Atomic owned setup linking, with no live database/provider calls."""
from datetime import datetime, timedelta, date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql

from app.core.database import QuestionSession, UserProfile, UserResponse, ActionPlan, ActionPlanItem
from app.services.question_service import QuestionService
from app.services import processing_status_service
from app.api.v1.endpoints import questions
from app.models.question_models import SessionData
from app.utils.assessment_data import SavedSetupConflict, assessment_facts


def fixture(monkeypatch, *, has_plan=True, has_items=True, processing=False):
    session = SimpleNamespace(status="active", expires_at=datetime.utcnow() + timedelta(hours=1), age=28, period_description="Regular", primary_hormone="insulin", secondary_hormones=["androgens"])
    profile = SimpleNamespace(uid="qa-one", name="QA", email="fixture@example.test", current_timezone="UTC", lifestyle_focus=[])
    answers = SimpleNamespace(uid="qa-one", age=28, period_description="Regular")
    plan = SimpleNamespace(id=7, uid=None, session_id="guest-one", plan_date=date.today(), review_completed=False)
    item = SimpleNamespace(uid=None, session_id="guest-one")
    records = {QuestionSession: session, UserProfile: profile, UserResponse: answers, ActionPlan: plan if has_plan else None,
               ActionPlanItem: item if has_items else None}
    statements = []
    class Query:
        def __init__(self, model): self.model, self.query = model, Session().query(model)
        def filter(self, *where): self.query = self.query.filter(*where); return self
        def order_by(self, *order): self.query = self.query.order_by(*order); return self
        def with_for_update(self): self.query = self.query.with_for_update(); return self
        def first(self):
            statements.append(str(self.query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
            compiled = self.query.statement.compile(dialect=postgresql.dialect())
            if self.model is ActionPlan and 'uid_1' in compiled.params:
                candidate = records.get(ActionPlan)
                if candidate is None or candidate.uid != compiled.params['uid_1']: return None
            return records.get(self.model)
        def all(self):
            self.first()
            return [records[self.model]] if records.get(self.model) else []
    db = Mock(); db.query = lambda model: Query(model)
    status = SimpleNamespace(processing_status="in_progress") if processing else None
    monkeypatch.setattr(processing_status_service, "ProcessingStatusService", lambda _db: SimpleNamespace(get_processing_status=lambda _id: status))
    return QuestionService(db), db, session, profile, answers, plan, item, records, statements


def test_completed_guest_plan_and_profile_are_committed_together_once(monkeypatch):
    service, db, session, profile, _, plan, item, _, statements = fixture(monkeypatch)
    assert service.link_session_to_user("guest-one", "qa-one", "Updated QA", "fixture@example.test", "UTC", ["eat"])
    db.flush.assert_called_once(); db.commit.assert_called_once()
    assert profile.name == "Updated QA" and plan.uid == item.uid == "qa-one"
    assert plan.session_id is None and item.session_id is None
    db.delete.assert_called_once_with(session)
    assert any("FOR UPDATE" in statement for statement in statements)


def test_failed_commit_cannot_leave_an_early_committed_profile(monkeypatch):
    service, db, *_ = fixture(monkeypatch)
    db.commit.side_effect = RuntimeError("database unavailable")
    assert service.link_session_to_user("guest-one", "qa-one", "QA", "fixture@example.test", "UTC") is False
    assert db.commit.call_count == 1
    assert db.rollback.called


def test_another_accounts_claimed_survey_is_rejected_before_profile_changes(monkeypatch):
    service, db, session, profile, *_ = fixture(monkeypatch)
    session.status = "linked:qa-two"
    with pytest.raises(PermissionError): service.link_session_to_user("guest-one", "qa-one", "Wrong", "fixture@example.test")
    assert profile.name == "QA"
    db.commit.assert_not_called(); db.flush.assert_not_called()


@pytest.mark.parametrize("has_items", [True, False])
def test_even_an_empty_plan_owned_by_another_account_is_not_adopted(monkeypatch, has_items):
    service, db, _, _, _, plan, item, *_ = fixture(monkeypatch, has_items=has_items)
    plan.uid = "qa-two"
    with pytest.raises(PermissionError): service.link_session_to_user("guest-one", "qa-one", "QA", "fixture@example.test")
    assert plan.uid == "qa-two" and item.uid is None
    db.commit.assert_not_called(); db.delete.assert_not_called()


def test_foreign_owned_actions_abort_the_entire_transfer_before_mutation(monkeypatch):
    service, db, _, _, _, plan, item, *_ = fixture(monkeypatch)
    item.uid = "qa-two"
    with pytest.raises(PermissionError): service.link_session_to_user("guest-one", "qa-one", "QA", "fixture@example.test")
    assert plan.uid is None and item.uid == "qa-two"
    db.commit.assert_not_called()


def test_in_flight_generation_keeps_the_claim_and_does_not_delete_or_adopt_an_empty_plan(monkeypatch):
    service, db, session, _, _, plan, _, _, _ = fixture(monkeypatch, has_items=False, processing=True)
    assert service.link_session_to_user("guest-one", "qa-one", "QA", "fixture@example.test", "UTC")
    assert session.status == "linked:qa-one" and plan.uid is None
    db.delete.assert_not_called(); db.commit.assert_called_once()


def test_legacy_fallback_claims_the_session_for_one_account_without_generating_another_plan(monkeypatch):
    service, db, session, *_ = fixture(monkeypatch, has_plan=False)
    assert service.link_session_to_user("guest-one", "qa-one", "QA", "fixture@example.test", "UTC")
    assert session.status == "linked:qa-one"
    db.commit.assert_called_once()


def test_replaying_an_existing_owned_link_does_not_overwrite_profile_metadata(monkeypatch):
    service, db, session, profile, *_ = fixture(monkeypatch)
    session.status = "linked:qa-one"
    assert service.link_session_to_user("guest-one", "qa-one", "Wrong", "fixture@example.test")
    assert profile.name == "QA"
    db.flush.assert_not_called(); db.commit.assert_called_once()


def test_transaction_locks_are_bound_and_acquired_in_one_stable_order(monkeypatch):
    service, db, *_ = fixture(monkeypatch)
    service.link_session_to_user("guest-one", "qa-one", "QA", "fixture@example.test", "UTC")
    calls = db.execute.call_args_list
    assert len(calls) == 2 and all(str(call.args[0]) == "SELECT pg_advisory_xact_lock(:key)" for call in calls)
    keys = [call.args[1]["key"] for call in calls]
    assert len(set(keys)) == 2 and keys == sorted(keys)


def test_expired_linked_sessions_are_not_kept_publicly_readable_forever(monkeypatch):
    service, _, session, *_ = fixture(monkeypatch)
    session.status = "linked:qa-one"
    session.expires_at = datetime.utcnow() - timedelta(hours=1)
    assert service.get_session("guest-one") is None
    with pytest.raises(LookupError): service.link_session_to_user("guest-one", "qa-one", "QA", "fixture@example.test")


def test_partial_lifestyle_save_preserves_omitted_health_concerns(monkeypatch):
    service, db, session, *_ = fixture(monkeypatch)
    session.other_concerns = ['Existing concern']
    session.survey_timezone = 'Asia/Kolkata'
    assert service.save_session_data('guest-one', SessionData(lifestyle_focus=['eat']))
    assert session.other_concerns == ['Existing concern']
    assert session.lifestyle_focus == ['eat']
    assert session.survey_timezone == 'Asia/Kolkata'
    db.commit.assert_called_once()


def test_a_guest_callback_cannot_overwrite_health_after_linking(monkeypatch):
    service, db, session, *_ = fixture(monkeypatch)
    session.status = 'linked:qa-one'
    with pytest.raises(PermissionError): service.save_session_data('guest-one', SessionData(age=35))
    assert session.age == 28
    db.commit.assert_not_called()


def test_a_different_existing_assessment_requires_explicit_saved_profile_recovery(monkeypatch):
    service, db, _, profile, answers, *_ = fixture(monkeypatch)
    answers.age = 35
    with pytest.raises(SavedSetupConflict): service.link_session_to_user('guest-one', 'qa-one', 'Wrong', 'fixture@example.test')
    assert profile.name == 'QA' and answers.age == 35
    db.flush.assert_not_called(); db.commit.assert_not_called()


def test_assessment_array_order_and_utc_representation_do_not_create_false_conflicts():
    from datetime import timezone
    one = SimpleNamespace(age=28, other_concerns=['One', 'Two'], last_period_date_utc=datetime(2026, 10, 1))
    two = SimpleNamespace(age=28, other_concerns=['Two', 'One'], last_period_date_utc=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert assessment_facts(one) == assessment_facts(two)


@pytest.mark.asyncio
async def test_recovery_status_reads_only_owned_facts_and_no_health_payload(monkeypatch):
    captured = []
    class Query:
        def __init__(self, column): self.query = Session().query(column)
        def order_by(self, *order): self.query = self.query.order_by(*order); return self
        def filter(self, *where): self.query = self.query.filter(*where); return self
        def first(self):
            captured.append(str(self.query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
            return SimpleNamespace(age=28, period_description="Regular")
    result = await questions.get_setup_status(SimpleNamespace(query=Query), {"uid": "qa-one"})
    assert result == {"has_profile": True, "has_answers": True}
    assert len(captured) == 2 and all("uid = 'qa-one'" in statement for statement in captured)


@pytest.mark.asyncio
async def test_expired_session_failure_remains_404_for_client_recovery(monkeypatch):
    service = Mock(); service.link_session_to_user.side_effect = LookupError("expired")
    monkeypatch.setattr(questions, "QuestionService", lambda _db: service)
    payload = SimpleNamespace(user_profile=SimpleNamespace(name="QA", email="fixture@example.test"), current_timezone="UTC", lifestyle_focus=[])
    with pytest.raises(questions.HTTPException) as error:
        await questions._link_session_to_user_internal("guest-one", payload, Mock(), {"uid": "qa-one", "email": "fixture@example.test"})
    assert error.value.status_code == 404
