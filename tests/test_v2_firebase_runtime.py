"""Firebase initialization for the v2 API composition root.

This decides whether token verification is possible at all, so its failure
modes matter: production must refuse to start without credentials rather than
run with authentication quietly disabled, and it must never silently attach to
an app belonging to a different Firebase project.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.v2.infrastructure.auth.firebase_runtime as runtime


def _settings(**overrides):
    values = {
        "ENVIRONMENT": "production",
        "FIREBASE_PROJECT_ID": "auvra-test",
        "FIREBASE_PRIVATE_KEY_ID": "",
        "FIREBASE_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\\nLINE\\n-----END PRIVATE KEY-----\\n",
        "FIREBASE_CLIENT_EMAIL": "svc@auvra-test.iam.gserviceaccount.com",
        "FIREBASE_CLIENT_ID": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def captured(monkeypatch):
    """Record what would have been sent to Firebase without calling it."""

    state = {}

    def fake_certificate(payload):
        state["certificate"] = payload
        return "credential-object"

    def fake_initialize_app(credential):
        state["initialized_with"] = credential

    def fake_get_app():
        raise ValueError("no app")

    monkeypatch.setattr(runtime.credentials, "Certificate", fake_certificate)
    monkeypatch.setattr(runtime.firebase_admin, "initialize_app", fake_initialize_app)
    monkeypatch.setattr(runtime.firebase_admin, "get_app", fake_get_app)
    return state


def test_production_refuses_to_start_without_the_credential(monkeypatch, captured):
    """Starting without authentication is worse than not starting."""

    monkeypatch.setattr(runtime, "settings", _settings(FIREBASE_PRIVATE_KEY=""))
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime.initialize_v2_firebase()
    assert "initialized_with" not in captured


@pytest.mark.parametrize("missing", ["FIREBASE_PROJECT_ID", "FIREBASE_CLIENT_EMAIL"])
def test_production_refuses_to_start_without_project_or_client_email(
    monkeypatch, captured, missing
):
    monkeypatch.setattr(runtime, "settings", _settings(**{missing: ""}))
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime.initialize_v2_firebase()


def test_staging_is_held_to_the_same_standard_as_production(monkeypatch, captured):
    """Staging exercises the real auth path, so it must not silently skip it."""

    monkeypatch.setattr(
        runtime, "settings", _settings(ENVIRONMENT="staging", FIREBASE_PRIVATE_KEY="")
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime.initialize_v2_firebase()


def test_development_may_start_without_firebase(monkeypatch, captured):
    """Local runs exercise public onboarding; authenticated calls still fail
    closed inside the verifier, which refuses when no app is initialized."""

    monkeypatch.setattr(
        runtime,
        "settings",
        _settings(ENVIRONMENT="development", FIREBASE_PRIVATE_KEY=""),
    )
    runtime.initialize_v2_firebase()
    assert "initialized_with" not in captured


def test_optional_metadata_is_not_required_to_initialize(monkeypatch, captured):
    """private_key_id and client_id are optional Admin SDK metadata; the Google
    auth library needs only client_email, token_uri and private_key."""

    monkeypatch.setattr(runtime, "settings", _settings())
    runtime.initialize_v2_firebase()
    assert captured["initialized_with"] == "credential-object"


def test_the_certificate_is_built_with_escaped_newlines_restored(monkeypatch, captured):
    """Environment variables carry the PEM with literal backslash-n; a key that
    keeps them is not parseable and every token verification would fail."""

    monkeypatch.setattr(runtime, "settings", _settings())
    runtime.initialize_v2_firebase()

    certificate = captured["certificate"]
    assert certificate["type"] == "service_account"
    assert certificate["project_id"] == "auvra-test"
    assert certificate["token_uri"] == "https://oauth2.googleapis.com/token"
    assert "\\n" not in certificate["private_key"]
    assert certificate["private_key"].startswith("-----BEGIN PRIVATE KEY-----\n")


def test_an_existing_app_for_the_same_project_is_reused(monkeypatch, captured):
    """Re-entrant startup must not raise or initialize twice."""

    monkeypatch.setattr(runtime, "settings", _settings())
    monkeypatch.setattr(
        runtime.firebase_admin,
        "get_app",
        lambda: SimpleNamespace(project_id="auvra-test"),
    )
    runtime.initialize_v2_firebase()
    assert "initialized_with" not in captured


def test_an_existing_app_for_a_different_project_is_refused(monkeypatch, captured):
    """Attaching to another project's app would verify tokens against the
    wrong user directory."""

    monkeypatch.setattr(runtime, "settings", _settings())
    monkeypatch.setattr(
        runtime.firebase_admin,
        "get_app",
        lambda: SimpleNamespace(project_id="someone-elses-project"),
    )
    with pytest.raises(RuntimeError, match="different project"):
        runtime.initialize_v2_firebase()


def test_whitespace_only_credentials_count_as_missing(monkeypatch, captured):
    monkeypatch.setattr(runtime, "settings", _settings(FIREBASE_CLIENT_EMAIL="   "))
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime.initialize_v2_firebase()
