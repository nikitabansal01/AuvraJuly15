from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql
from app.core.database import UserProfile, UserResponse
from app.services.profile_service import ProfileService, RootCauseEngine, SCORING_FIELDS, health_record
from app.api.v1.endpoints.users import UserProfileUpdate


def fixture(monkeypatch):
    profile = SimpleNamespace(uid='qa-one', name='Original QA')
    answers = SimpleNamespace(**dict.fromkeys(SCORING_FIELDS))
    answers.uid = 'qa-one'; answers.top_concern = 'Adult Acne'; answers.skin_hair_concerns = ['Adult Acne', 'Thinning of hair']
    answers.diagnosed_conditions = []; answers.family_history = ['PCOS']; answers.cycle_length = '26-30 days'
    answers.primary_hormone = 'androgens'; answers.secondary_hormones = ['insulin']
    statements = []
    class Query:
        def __init__(self, model): self.model, self.query = model, Session().query(model)
        def filter(self, *where): self.query = self.query.filter(*where); return self
        def order_by(self, *order): self.query = self.query.order_by(*order); return self
        def with_for_update(self): self.query = self.query.with_for_update(); return self
        def first(self):
            compiled = self.query.statement.compile(dialect=postgresql.dialect())
            statements.append(str(self.query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True})))
            return (profile if self.model is UserProfile else answers) if compiled.params.get('uid_1') == 'qa-one' else None
    db = Mock(); db.query = lambda model: Query(model)
    analyze = Mock(return_value={'primary_imbalance': 'insulin', 'secondary_imbalances': ['androgens']})
    monkeypatch.setattr(RootCauseEngine, 'analyze_hormone_imbalance', analyze)
    return ProfileService(db), db, profile, answers, analyze, statements


@pytest.mark.parametrize('payload', [{}, {'name': ''}, {'name': ' '}, {'name': None}, {'name': 'X'*81}, {'name':'QA\nName'},
                                     {'name': 'QA', 'uid': 'qa-two'}, {'name':'QA','password':'private'}, {'diagnosis':None}])
def test_profile_contract_rejects_invalid_or_identity_changes(payload):
    with pytest.raises(ValidationError): UserProfileUpdate(**payload)


def test_name_only_edit_is_owned_and_does_not_call_the_assessment_provider(monkeypatch):
    service, db, profile, answers, analyze, statements = fixture(monkeypatch)
    payload = UserProfileUpdate(name='  Updated QA  ')
    saved = service.update('qa-one', payload.model_dump(exclude_unset=True))
    assert saved['name'] == profile.name == 'Updated QA'
    assert answers.top_concern == 'Adult Acne' and answers.primary_hormone == 'androgens'
    analyze.assert_not_called(); db.commit.assert_called_once()
    assert all("uid = 'qa-one'" in statement and 'FOR UPDATE' in statement for statement in statements)


def test_health_edit_recalculates_from_all_current_inputs_and_preserves_old_plan_and_checkin_facts(monkeypatch):
    service, db, _, answers, analyze, statements = fixture(monkeypatch)
    saved = service.update('qa-one', {'top_concern':'Thinning of hair', 'diagnosis':['PCOS']})
    context = analyze.call_args.args[0]
    assert set(context) == set(SCORING_FIELDS)
    assert context['family_history'] == ['PCOS'] and context['skin_hair_concerns'] == ['Adult Acne','Thinning of hair']
    assert context['top_concern'] == 'Thinning of hair' and context['diagnosed_conditions'] == ['PCOS']
    assert answers.primary_hormone == 'insulin' and answers.secondary_hormones == ['androgens']
    assert saved['diagnosis'] == ['PCOS']
    assert 'created_at DESC' in statements[1] and 'id DESC' in statements[1]
    db.add.assert_not_called(); db.delete.assert_not_called(); db.commit.assert_called_once()
    assert len(statements) == 2


def test_unknown_priority_or_unlisted_diagnosis_cannot_change_saved_context(monkeypatch):
    service, db, profile, answers, analyze, _ = fixture(monkeypatch)
    for changes in [{'top_concern':'A different symptom'}, {'diagnosis':['Unlisted condition']}]:
        with pytest.raises(ValueError): service.update('qa-one', changes)
    assert profile.name == 'Original QA' and answers.top_concern == 'Adult Acne' and answers.diagnosed_conditions == []
    analyze.assert_not_called(); db.commit.assert_not_called()


def test_scoring_failure_does_not_apply_partial_name_or_health_edits(monkeypatch):
    service, db, profile, answers, analyze, _ = fixture(monkeypatch)
    analyze.side_effect = RuntimeError('provider failed')
    with pytest.raises(RuntimeError): service.update('qa-one', {'name':'Wrong','diagnosis':['PCOS']})
    assert profile.name == 'Original QA' and answers.diagnosed_conditions == []
    db.commit.assert_not_called(); db.rollback.assert_called_once()


def test_other_accounts_records_are_never_used_for_an_edit(monkeypatch):
    service, db, profile, _, analyze, _ = fixture(monkeypatch)
    with pytest.raises(LookupError): service.update('qa-two', {'name':'Wrong'})
    assert profile.name == 'Original QA'
    analyze.assert_not_called(); db.commit.assert_not_called()


def test_saving_unchanged_health_is_an_idempotent_no_provider_call(monkeypatch):
    service, db, _, _, analyze, _ = fixture(monkeypatch)
    saved = service.update('qa-one', {'top_concern':'Adult Acne', 'diagnosis':[]})
    assert saved['top_concern'] == 'Adult Acne'
    analyze.assert_not_called(); db.commit.assert_called_once()


def test_other_reported_diagnosis_uses_the_existing_complete_assessment_context(monkeypatch):
    service, db, _, answers, analyze, _ = fixture(monkeypatch)
    saved = service.update('qa-one', {'diagnosis': ['Others: Clinician-reported condition']})
    assert saved['diagnosis'] == ['Others: Clinician-reported condition']
    assert analyze.call_args.args[0]['family_history'] == ['PCOS']
    assert answers.diagnosed_conditions == saved['diagnosis']
    db.commit.assert_called_once()


@pytest.mark.parametrize('value', ['Others: ', 'Others: X', 'Others: ' + 'X' * 151])
def test_invalid_other_diagnoses_cannot_mutate_the_saved_profile(monkeypatch, value):
    service, db, _, answers, analyze, _ = fixture(monkeypatch)
    with pytest.raises(ValueError): service.update('qa-one', {'diagnosis': [value]})
    assert answers.diagnosed_conditions == []
    db.commit.assert_not_called(); analyze.assert_not_called()


def test_record_reader_keeps_more_than_five_concerns_and_removes_placeholder_values():
    answers = SimpleNamespace(period_concerns=['a','b','None of the above'], body_concerns=['c','d'],
        skin_hair_concerns=['e','f','a'], mental_health_concerns=[],other_concerns=[],diagnosed_conditions=['None of the above'],top_concern='f')
    assert health_record(answers)['concerns'] == ['a','b','c','d','e','f']
    assert health_record(answers)['diagnosis'] == []
