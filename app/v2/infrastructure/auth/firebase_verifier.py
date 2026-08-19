"""Firebase Admin adapter behind the provider-neutral identity port."""

from __future__ import annotations

import firebase_admin
from datetime import UTC, datetime
from fastapi.concurrency import run_in_threadpool
from firebase_admin import auth, exceptions

from app.v2.domain.identity import (
    AuthenticationUnavailable,
    InvalidIdentityToken,
    VerifiedPrincipal,
)


class FirebaseIdentityTokenVerifier:
    async def verify(self, token: str) -> VerifiedPrincipal:
        if not firebase_admin._apps:
            raise AuthenticationUnavailable("Firebase is not initialized")
        try:
            claims = await run_in_threadpool(
                lambda: auth.verify_id_token(token, check_revoked=True)
            )
        except _INVALID_TOKEN_ERRORS:
            raise InvalidIdentityToken("Firebase rejected the token") from None
        except _PROVIDER_UNAVAILABLE_ERRORS:
            raise AuthenticationUnavailable("Firebase token verification is unavailable") from None
        except Exception:
            # An unclassified provider failure must not be presented as a bad
            # token. Do not log the exception: Firebase errors can include
            # provider response content and this adapter receives credentials.
            raise AuthenticationUnavailable("Firebase token verification failed") from None

        subject = claims.get("uid") or claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise InvalidIdentityToken("Firebase token has no subject")
        return VerifiedPrincipal(
            auth_provider="firebase",
            subject=subject,
            email=claims.get("email"),
            email_verified=bool(claims.get("email_verified", False)),
            display_name=claims.get("name"),
            authenticated_at=_authentication_time(claims.get("auth_time")),
        )


def _authentication_time(value: object) -> datetime | None:
    """Translate Firebase's signed Unix auth_time claim without guessing."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


_INVALID_TOKEN_ERRORS = (
    auth.InvalidIdTokenError,
    auth.ExpiredIdTokenError,
    auth.RevokedIdTokenError,
    auth.UserDisabledError,
    exceptions.UnauthenticatedError,
)
_PROVIDER_UNAVAILABLE_ERRORS = (
    auth.CertificateFetchError,
    auth.UnexpectedResponseError,
    exceptions.AbortedError,
    exceptions.DeadlineExceededError,
    exceptions.InternalError,
    exceptions.ResourceExhaustedError,
    exceptions.UnavailableError,
    exceptions.UnknownError,
)
