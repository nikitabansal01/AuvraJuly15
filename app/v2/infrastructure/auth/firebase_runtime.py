"""Firebase setup used only by the v2 API composition root."""

from __future__ import annotations

import firebase_admin
from firebase_admin import credentials

from app.v2.runtime.config import settings


def initialize_v2_firebase() -> None:
    """Initialize Firebase from explicit environment credentials only.

    There is intentionally no file fallback and no anonymous/default-app path.
    Development may start without Firebase so public onboarding tests can run;
    authenticated calls still fail closed through the token verifier.
    """

    fields = (
        settings.FIREBASE_PROJECT_ID,
        settings.FIREBASE_PRIVATE_KEY_ID,
        settings.FIREBASE_PRIVATE_KEY,
        settings.FIREBASE_CLIENT_EMAIL,
        settings.FIREBASE_CLIENT_ID,
    )
    credentials_complete = all(value.strip() for value in fields)
    if not credentials_complete:
        if settings.ENVIRONMENT in {"staging", "production"}:
            raise RuntimeError("Firebase credentials are incomplete")
        return
    try:
        existing = firebase_admin.get_app()
    except ValueError:
        existing = None
    if existing is not None:
        if existing.project_id != settings.FIREBASE_PROJECT_ID:
            raise RuntimeError(
                "The existing Firebase app belongs to a different project"
            )
        return
    firebase_admin.initialize_app(
        credentials.Certificate(
            {
                "type": "service_account",
                "project_id": settings.FIREBASE_PROJECT_ID,
                "private_key_id": settings.FIREBASE_PRIVATE_KEY_ID,
                "private_key": settings.FIREBASE_PRIVATE_KEY.replace("\\n", "\n"),
                "client_email": settings.FIREBASE_CLIENT_EMAIL,
                "client_id": settings.FIREBASE_CLIENT_ID,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
