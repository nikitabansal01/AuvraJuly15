"""Static proofs that the v2 deployment package cannot absorb legacy code."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_v2_runtime_dependency_inputs_exclude_archived_provider_stacks() -> None:
    runtime_requirements = (ROOT / "requirements-v2.txt").read_text(encoding="utf-8")
    forbidden = (
        "celery",
        "langchain",
        "langgraph",
        "openai",
        "pinecone",
        "cloudinary",
        "groq",
        "supabase",
    )
    lowered = runtime_requirements.lower()
    assert not any(package in lowered for package in forbidden)


def test_v2_container_uses_explicit_source_allowlist() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-v2.lock" in dockerfile
    assert "COPY --chown=auvra:auvra app/v2/ app/v2/" in dockerfile
    assert "COPY --chown=auvra:auvra alembic/recovery_versions/" in dockerfile
    assert "COPY --chown=auvra:auvra contracts/ contracts/" in dockerfile
    assert "COPY --chown=auvra:auvra . ." not in dockerfile


def test_build_context_excludes_legacy_runtime_directories() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for legacy_path in (
        "app/api",
        "app/core",
        "app/langgraph",
        "app/models",
        "app/services",
        "alembic/versions",
    ):
        assert legacy_path in ignored
