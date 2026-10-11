from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.services.guest_plan_transfer import transfer_guest_plan


@pytest.mark.asyncio
async def test_rejected_guest_adoption_does_not_change_actions_or_commit():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
    assert await transfer_guest_plan(db, 7, 'qa-one', date(2026, 10, 11)) is False
    assert db.execute.await_count == 1
    sql, parameters = db.execute.call_args.args
    assert 'assigned.plan_date = :plan_date' in str(sql) and 'item.uid <> :uid' in str(sql)
    assert parameters['uid'] == 'qa-one'


@pytest.mark.asyncio
async def test_owned_adoption_updates_only_matching_children_in_the_callers_transaction():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 7)))
    assert await transfer_guest_plan(db, 7, 'qa-one', date(2026, 10, 11)) is True
    assert db.execute.await_count == 2
    sql, parameters = db.execute.call_args.args
    assert 'plan_id = :plan_id' in str(sql) and '(uid IS NULL OR uid = :uid)' in str(sql)
    assert parameters == {'uid': 'qa-one', 'plan_id': 7}
