"""Exercise the real HTTP contract without a database or Firebase network call."""
from datetime import date
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport

from app.api.v1.endpoints import cycle
from app.core import security
from app.core.database import get_db
from app.models.cycle_models import CycleCalendarResponse, CyclePhaseInfo


@pytest.fixture
async def api(monkeypatch):
    service = Mock()
    info = CyclePhaseInfo(user_name="QA", as_of_date=date(2026, 10, 11),
                          last_period_start=date(2026, 10, 1), can_update_period=True)
    service.get_calendar_month.return_value = CycleCalendarResponse(month="2026-10", cycle_info=info, days=[])
    service.update_last_period_start.return_value = info
    monkeypatch.setattr(cycle, "CycleService", lambda _db: service)

    async def verify(token):
        if token != "fixture-token":
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"uid": "qa-one"}

    monkeypatch.setattr(security, "verify_firebase_token", verify)
    app = FastAPI()
    app.include_router(cycle.router, prefix="/api/v1/cycle")
    app.dependency_overrides[get_db] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client, service


async def test_calendar_and_edit_require_bearer_authentication(api):
    client, service = api
    for method, path, kwargs in [
        (client.get, "/api/v1/cycle/calendar?year=2026&month=10", {}),
        (client.put, "/api/v1/cycle/period", {"json": {"last_period_start": "2026-10-01"}}),
    ]:
        assert (await method(path, **kwargs)).status_code in (401, 403)
        assert (await method(path, headers={"Authorization": "Bearer invalid"}, **kwargs)).status_code == 401
    service.get_calendar_month.assert_not_called()
    service.update_last_period_start.assert_not_called()


async def test_month_read_uses_only_the_verified_accounts_identity(api):
    client, service = api
    response = await client.get("/api/v1/cycle/calendar?year=2026&month=10&uid=qa-two",
                          headers={"Authorization": "Bearer fixture-token"})
    assert response.status_code == 200
    assert response.json()["cycle_info"]["last_period_start"] == "2026-10-01"
    service.get_calendar_month.assert_called_once_with("qa-one", 2026, 10)


@pytest.mark.parametrize("query", ["year=2026&month=13", "year=1899&month=1", "year=2101&month=1"])
async def test_out_of_range_months_do_not_reach_the_service(api, query):
    client, service = api
    assert (await client.get(f"/api/v1/cycle/calendar?{query}",
                      headers={"Authorization": "Bearer fixture-token"})).status_code == 422
    service.get_calendar_month.assert_not_called()


async def test_edit_accepts_only_a_date_and_returns_the_saved_server_acknowledgement(api):
    client, service = api
    response = await client.put("/api/v1/cycle/period", json={"last_period_start": "2026-10-01"},
                          headers={"Authorization": "Bearer fixture-token"})
    assert response.status_code == 200
    assert response.json()["cycle_info"]["last_period_start"] == "2026-10-01"
    service.update_last_period_start.assert_called_once_with("qa-one", date(2026, 10, 1))
    response = await client.put("/api/v1/cycle/period", json={"last_period_start": "2026-10-01", "uid": "qa-two"},
                          headers={"Authorization": "Bearer fixture-token"})
    assert response.status_code == 422
    assert service.update_last_period_start.call_count == 1


@pytest.mark.parametrize("failure,code", [(LookupError("Complete setup"), 404), (ValueError("Future date"), 422), (RuntimeError("private database detail"), 500)])
async def test_failed_writes_are_never_returned_as_success(api, failure, code):
    client, service = api
    service.update_last_period_start.side_effect = failure
    response = await client.put("/api/v1/cycle/period", json={"last_period_start": "2026-10-01"},
                          headers={"Authorization": "Bearer fixture-token"})
    assert response.status_code == code
    assert "private database detail" not in response.text
