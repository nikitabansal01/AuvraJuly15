"""No live Firebase reads/writes; check account isolation with verified-claim fixtures."""
import pytest
from fastapi import HTTPException
from unittest.mock import Mock
from app.api.v1.endpoints import users


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [None, False, "true", 1])
async def test_email_verification_does_not_grant_global_account_listing(monkeypatch, admin):
    provider = Mock()
    monkeypatch.setattr(users, "list_users", provider)
    with pytest.raises(HTTPException) as denied:
        await users.get_users({"uid": "qa-one", "email_verified": True, "admin": admin})
    assert denied.value.status_code == 403
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_only_the_explicit_boolean_admin_claim_allows_listing(monkeypatch):
    provider = Mock(return_value=[])
    monkeypatch.setattr(users, "list_users", provider)
    assert await users.get_users({"uid": "fixture-admin", "email_verified": True, "admin": True}) == []
    provider.assert_called_once()


@pytest.mark.asyncio
async def test_a_user_cannot_read_another_accounts_record(monkeypatch):
    provider = Mock()
    monkeypatch.setattr(users, "get_user_by_uid", provider)
    with pytest.raises(HTTPException) as denied:
        await users.get_user("qa-two", {"uid": "qa-one", "email_verified": True})
    assert denied.value.status_code == 403
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_an_owned_record_can_be_read(monkeypatch):
    provider = Mock(return_value={"uid": "qa-one", "email": "fixture@example.test"})
    monkeypatch.setattr(users, "get_user_by_uid", provider)
    assert (await users.get_user("qa-one", {"uid": "qa-one", "email_verified": True})).uid == "qa-one"
    provider.assert_called_once_with("qa-one")


@pytest.mark.asyncio
async def test_a_forbidden_update_preserves_403_and_never_calls_firebase(monkeypatch):
    provider = Mock()
    monkeypatch.setattr(users, "update_user", provider)
    with pytest.raises(HTTPException) as denied:
        await users.update_user_info("qa-two", users.UserUpdate(display_name="Fixture"), {"uid": "qa-one"})
    assert denied.value.status_code == 403
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_a_forbidden_delete_preserves_403_and_never_calls_firebase(monkeypatch):
    provider = Mock()
    monkeypatch.setattr(users, "delete_user", provider)
    with pytest.raises(HTTPException) as denied:
        await users.delete_user_account("qa-two", {"uid": "qa-one"})
    assert denied.value.status_code == 403
    provider.assert_not_called()
