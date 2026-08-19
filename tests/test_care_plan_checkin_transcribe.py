import io
import os
import sys

import anyio
import pytest
from starlette.datastructures import UploadFile

# Add project root to path (consistent with other tests)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# These exercise legacy transcription services, which depend on the `openai`
# package from requirements.txt. The v2 image deliberately excludes legacy code
# and its dependencies, and CI installs only the v2 sets -- so without this the
# import error aborted collection for the *entire* suite. Skipping keeps CI
# running the whole tests/ directory (never a glob, so a new file cannot be
# silently excluded) while these run wherever the legacy stack is installed.
pytest.importorskip("openai")

from app.services.care_plan_checkin_service import CarePlanCheckInService


class _DummyTranscript:
    def __init__(self, text: str):
        self.text = text


class _DummyTranscriptions:
    async def create(self, model: str, file):  # noqa: ANN001
        assert model == "whisper-1"
        assert getattr(file, "read", None) is not None
        return _DummyTranscript("  hello from care plan yap  ")


class _DummyAudio:
    def __init__(self):
        self.transcriptions = _DummyTranscriptions()


class _DummyAsyncOpenAI:
    def __init__(self, api_key: str):
        assert api_key
        self.audio = _DummyAudio()


def test_transcribe_audio_requires_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")

    service = CarePlanCheckInService(db=None)  # type: ignore[arg-type]
    upload = UploadFile(filename="yap.m4a", file=io.BytesIO(b"abc"))

    with pytest.raises(ValueError, match="OPENAI_API_KEY is not configured"):
        anyio.run(service.transcribe_audio, "user_1", upload)


def test_transcribe_audio_rejects_empty_upload(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    service = CarePlanCheckInService(db=None)  # type: ignore[arg-type]
    upload = UploadFile(filename="yap.m4a", file=io.BytesIO(b""))

    with pytest.raises(ValueError, match="Empty audio upload"):
        anyio.run(service.transcribe_audio, "user_1", upload)


def test_transcribe_audio_returns_stripped_text(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    import app.services.care_plan_checkin_service as mod

    monkeypatch.setattr(mod, "AsyncOpenAI", _DummyAsyncOpenAI)

    service = CarePlanCheckInService(db=None)  # type: ignore[arg-type]
    upload = UploadFile(filename="yap.m4a", file=io.BytesIO(b"abc"))

    text = anyio.run(service.transcribe_audio, "user_1", upload)
    assert text == "hello from care plan yap"
