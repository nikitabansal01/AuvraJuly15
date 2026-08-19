"""Regression proofs for ORM metadata parity with the migrated v2 schema."""
from __future__ import annotations

from app.v2.persistence import V2Base
from app.v2.persistence.models import OnboardingAssessment


def test_onboarding_assessment_current_default_matches_partial_current_index_semantics():
    column = OnboardingAssessment.__table__.c.is_current
    assert column.default is not None and column.default.arg is True
    assert column.server_default is not None
    assert str(column.server_default.arg) == "true"


def test_drift_metadata_keeps_database_uuid_and_intentional_nonunique_invocations():
    metadata = V2Base.metadata
    assert str(metadata.tables["app.users"].c.id.server_default.arg) == "gen_random_uuid()"
    assert not any(
        constraint.unique
        for constraint in metadata.tables["app.ai_invocations"].indexes
        if "generation_job_id" in constraint.columns
    )
    assert not any(
        constraint.unique
        for constraint in metadata.tables["app.media_assets"].constraints
        if {column.name for column in constraint.columns} == {"content_sha256"}
    )
