"""The Firebase adapter that establishes who a request belongs to.

Every authenticated route depends on this module and it had no tests. The
distinction it draws is the important one: a *rejected token* and an
*unavailable provider* must never be confused. Treating an outage as a bad
token would sign real users out during an incident; treating a bad token as an
outage would let a caller retry into a route they must not reach.
"""

from __future__ import annotations

from datetime import UTC, datetime

import firebase_admin
import pytest
from firebase_admin import auth, exceptions

from app.v2.domain.identity import (
    AuthenticationUnavailable,
    InvalidIdentityToken,
)
from app.v2.infrastructure.auth.firebase_verifier import (
    FirebaseIdentityTokenVerifier,
    _authentication_time,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def initialized_firebase(monkeypatch):
    """Pretend an app is initialized without contacting Firebase."""

    monkeypatch.setattr(firebase_admin, "_apps", {"[DEFAULT]": object()})


def _claims(**overrides):
    base = {
        "uid": "firebase-subject-1",
        "email": "person@example.test",
        "email_verified": True,
        "name": "A Person",
        "auth_time": 1_754_000_000,
    }
    base.update(overrides)
    return base


def _verify_returns(monkeypatch, claims):
    captured = {}

    def fake_verify(token, check_revoked=False):
        captured["token"] = token
        captured["check_revoked"] = check_revoked
        return claims

    monkeypatch.setattr(auth, "verify_id_token", fake_verify)
    return captured


def _verify_raises(monkeypatch, error):
    def fake_verify(token, check_revoked=False):
        raise error

    monkeypatch.setattr(auth, "verify_id_token", fake_verify)


@pytest.mark.anyio
async def test_a_valid_token_becomes_a_provider_neutral_principal(
    monkeypatch, initialized_firebase
):
    captured = _verify_returns(monkeypatch, _claims())
    principal = await FirebaseIdentityTokenVerifier().verify("a-token")

    assert principal.auth_provider == "firebase"
    assert principal.subject == "firebase-subject-1"
    assert principal.email == "person@example.test"
    assert principal.email_verified is True
    assert principal.display_name == "A Person"
    assert principal.authenticated_at == datetime.fromtimestamp(1_754_000_000, UTC)
    assert captured["token"] == "a-token"


@pytest.mark.anyio
async def test_revocation_is_always_checked(monkeypatch, initialized_firebase):
    """A signed-out or compromised session must not keep working.

    Firebase only consults the revocation list when asked, so this flag is the
    difference between honouring a sign-out and ignoring it.
    """
    captured = _verify_returns(monkeypatch, _claims())
    await FirebaseIdentityTokenVerifier().verify("a-token")
    assert captured["check_revoked"] is True


@pytest.mark.anyio
async def test_an_uninitialized_firebase_is_unavailable_not_a_bad_token(monkeypatch):
    monkeypatch.setattr(firebase_admin, "_apps", {})
    with pytest.raises(AuthenticationUnavailable):
        await FirebaseIdentityTokenVerifier().verify("a-token")


@pytest.mark.parametrize(
    "error",
    [
        auth.InvalidIdTokenError("bad"),
        auth.ExpiredIdTokenError("expired", cause=None),
        auth.RevokedIdTokenError("revoked"),
        auth.UserDisabledError("disabled"),
        exceptions.UnauthenticatedError("unauthenticated"),
    ],
)
@pytest.mark.anyio
async def test_a_rejected_token_is_reported_as_an_invalid_token(
    monkeypatch, initialized_firebase, error
):
    _verify_raises(monkeypatch, error)
    with pytest.raises(InvalidIdentityToken):
        await FirebaseIdentityTokenVerifier().verify("a-token")


@pytest.mark.parametrize(
    "error",
    [
        auth.CertificateFetchError("certs", cause=None),
        exceptions.AbortedError("aborted"),
        exceptions.DeadlineExceededError("deadline"),
        exceptions.InternalError("internal"),
        exceptions.ResourceExhaustedError("exhausted"),
        exceptions.UnavailableError("unavailable"),
        exceptions.UnknownError("unknown"),
    ],
)
@pytest.mark.anyio
async def test_a_provider_outage_is_never_reported_as_a_bad_token(
    monkeypatch, initialized_firebase, error
):
    """Otherwise every user is signed out during a Firebase incident."""

    _verify_raises(monkeypatch, error)
    with pytest.raises(AuthenticationUnavailable):
        await FirebaseIdentityTokenVerifier().verify("a-token")


@pytest.mark.anyio
async def test_an_unclassified_failure_fails_safe_as_unavailable(
    monkeypatch, initialized_firebase
):
    """An unrecognised provider error must not be presented as a bad token."""

    _verify_raises(monkeypatch, RuntimeError("something unexpected"))
    with pytest.raises(AuthenticationUnavailable):
        await FirebaseIdentityTokenVerifier().verify("a-token")


@pytest.mark.anyio
async def test_provider_error_detail_never_reaches_the_raised_exception(
    monkeypatch, initialized_firebase
):
    """Firebase errors can carry response content, and this adapter sees tokens."""

    secret = "token=ya29.SECRET-VALUE user=person@example.test"
    _verify_raises(monkeypatch, RuntimeError(secret))
    with pytest.raises(AuthenticationUnavailable) as raised:
        await FirebaseIdentityTokenVerifier().verify("a-token")

    assert "SECRET-VALUE" not in str(raised.value)
    # `from None` suppresses the cause, so the original text cannot be
    # re-exposed by a traceback handler further up the stack.
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("subject", [None, "", "   ", 123, True, []])
@pytest.mark.anyio
async def test_a_token_without_a_usable_subject_is_invalid(
    monkeypatch, initialized_firebase, subject
):
    """Every owned row keys off this value; it must be a real string."""

    claims = _claims()
    claims.pop("uid")
    claims["sub"] = subject
    _verify_returns(monkeypatch, claims)
    with pytest.raises(InvalidIdentityToken):
        await FirebaseIdentityTokenVerifier().verify("a-token")


@pytest.mark.anyio
async def test_sub_is_accepted_when_uid_is_absent(monkeypatch, initialized_firebase):
    claims = _claims()
    claims.pop("uid")
    claims["sub"] = "subject-from-sub"
    _verify_returns(monkeypatch, claims)
    principal = await FirebaseIdentityTokenVerifier().verify("a-token")
    assert principal.subject == "subject-from-sub"


@pytest.mark.anyio
async def test_optional_claims_absent_yields_a_usable_principal(
    monkeypatch, initialized_firebase
):
    _verify_returns(monkeypatch, {"uid": "subject-only"})
    principal = await FirebaseIdentityTokenVerifier().verify("a-token")

    assert principal.subject == "subject-only"
    assert principal.email is None
    assert principal.display_name is None
    # Absent auth_time must not be treated as "authenticated just now"; the
    # recent-authentication policy for deletion and export depends on that.
    assert principal.authenticated_at is None
    assert principal.email_verified is False


@pytest.mark.parametrize(
    "value",
    [None, True, False, "1754000000", [], {}, float("nan"), float("inf"), 10**20],
)
def test_a_malformed_auth_time_becomes_none_rather_than_a_guess(value) -> None:
    """None means 'not recently authenticated', which fails closed."""

    assert _authentication_time(value) is None


def test_a_valid_auth_time_is_translated_to_utc() -> None:
    assert _authentication_time(1_754_000_000) == datetime.fromtimestamp(
        1_754_000_000, UTC
    )
    assert _authentication_time(1_754_000_000).tzinfo is UTC
