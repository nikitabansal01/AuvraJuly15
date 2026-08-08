"""One fail-closed Firebase principal dependency for all private v2 routes."""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.v2.application.errors import service_unavailable, unauthorized
from app.v2.application.ports import IdentityTokenVerifier
from app.v2.domain.identity import (
    AuthenticationUnavailable,
    InvalidIdentityToken,
    VerifiedPrincipal,
)
from app.v2.infrastructure.auth.firebase_verifier import FirebaseIdentityTokenVerifier

bearer_scheme = HTTPBearer(auto_error=False)


def get_identity_token_verifier() -> IdentityTokenVerifier:
    return FirebaseIdentityTokenVerifier()


async def get_verified_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    verifier: IdentityTokenVerifier = Depends(get_identity_token_verifier),
) -> VerifiedPrincipal:
    """Verify a non-revoked Firebase ID token without development bypasses."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        return await verifier.verify(credentials.credentials)
    except AuthenticationUnavailable:
        raise service_unavailable(
            "authentication_unavailable",
            "Authentication is temporarily unavailable.",
        ) from None
    except InvalidIdentityToken:
        raise unauthorized("The Firebase ID token is invalid or revoked.") from None
