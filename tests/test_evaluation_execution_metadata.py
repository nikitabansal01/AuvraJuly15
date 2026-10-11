import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from app.services import evaluation_service as module

SCORES={'personalization_score':80,'condition_appropriateness':75,'feedback_alignment_score':90,'preference_compliance_score':85,'citation_relevance_score':70,'reasoning':{}}
def response(status=200, *, scores=SCORES, model='fixture-model', usage=None):
    return SimpleNamespace(status_code=status,text='fixture response',json=lambda:{'id':'fixture-request','model':model,
        'usage':usage or {},'choices':[{'message':{'content':json.dumps(scores)}}]})
def fixture(monkeypatch, responses, *, groq=False):
    service=module.ActionPlanEvaluator.__new__(module.ActionPlanEvaluator)
    service.openai_api_key='fixture-key'
    service.client=SimpleNamespace(post=AsyncMock(side_effect=responses))
    monkeypatch.setattr(module,'GROQ_API_KEY','fixture-fallback-key' if groq else None)
    db=SimpleNamespace(add=Mock(),commit=AsyncMock(),rollback=AsyncMock())
    return service,db


@pytest.mark.asyncio
async def test_successful_evaluation_records_the_response_model_and_usage_not_just_the_primary_setting(monkeypatch):
    service,db=fixture(monkeypatch,[response(model='actual-openai-model',usage={'prompt_tokens':10,'completion_tokens':20})])
    result=await service.evaluate_plan(7,None,[],{},True,db,session_id='fixture')
    saved=db.add.call_args.args[0]
    assert saved.evaluator_model=='actual-openai-model' and result['evaluation_complete'] is True
    assert saved.llm_evaluation_response['_execution']['request_id']=='fixture-request'
    assert saved.evaluation_cost is not None


@pytest.mark.asyncio
async def test_fallback_records_the_provider_that_succeeded_and_does_not_label_unmetered_cost_free(monkeypatch):
    service,db=fixture(monkeypatch,[response(503),response(model='actual-groq-model')],groq=True)
    result=await service.evaluate_plan(7,None,[],{},True,db,session_id='fixture')
    saved=db.add.call_args.args[0]
    assert saved.evaluator_model=='actual-groq-model'
    assert saved.llm_evaluation_response['_execution']['provider']=='groq'
    assert saved.evaluation_cost is None and result['cost'] is None


@pytest.mark.asyncio
async def test_failed_or_incomplete_evaluation_cannot_become_a_perfect_quality_score(monkeypatch):
    service,db=fixture(monkeypatch,[response(scores={'personalization_score':100})])
    result=await service.evaluate_plan(7,None,[],{},True,db,session_id='fixture')
    saved=db.add.call_args.args[0]
    assert saved.overall_quality_score is None and saved.evaluator_model == 'unavailable'
    assert result['evaluation_complete'] is False and result['overall_quality_score'] is None


@pytest.mark.asyncio
async def test_an_unreported_usage_total_is_unknown_instead_of_zero_cost(monkeypatch):
    service,db=fixture(monkeypatch,[response()])
    await service.evaluate_plan(7,None,[],{},True,db,session_id='fixture')
    assert db.add.call_args.args[0].evaluation_cost is None


@pytest.mark.asyncio
async def test_failed_evaluation_commit_rolls_back(monkeypatch):
    service,db=fixture(monkeypatch,[response()]);db.commit.side_effect=RuntimeError('database unavailable')
    assert await service.evaluate_plan(7,None,[],{},True,db,session_id='fixture') is None
    db.rollback.assert_awaited_once()
