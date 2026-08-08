"""Correct provider-event grain and isolate plan media by generation job.

Revision ID: 20260808_0008
Revises: 20260808_0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "20260808_0008"
down_revision = "20260808_0007"
branch_labels = depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("uq_ai_invocations_generation_job"),
        "ai_invocations",
        schema="app",
        type_="unique",
    )
    op.create_index(
        "ix_ai_invocations_generation_job_id",
        "ai_invocations",
        ["generation_job_id"],
        schema="app",
    )
    op.drop_constraint(
        op.f("uq_media_assets_content_sha256"),
        "media_assets",
        schema="app",
        type_="unique",
    )
    op.add_column(
        "media_assets",
        sa.Column("generation_job_id", UUID(as_uuid=True), nullable=True),
        schema="app",
    )
    op.create_foreign_key(
        "fk_media_assets_generation_job",
        "media_assets",
        "generation_jobs",
        ["generation_job_id"],
        ["id"],
        source_schema="app",
        referent_schema="ops",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_media_assets_generation_job_id",
        "media_assets",
        ["generation_job_id"],
        schema="app",
    )
    op.create_check_constraint(
        "ck_generation_jobs_request_object",
        "generation_jobs",
        "jsonb_typeof(request_payload) = 'object'",
        schema="ops",
    )
    _freeze_generation_assessments()


def _freeze_generation_assessments() -> None:
    op.execute(
        """
        CREATE FUNCTION app.guard_generation_assessment_snapshot() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM ops.generation_jobs job
            WHERE job.job_type = 'plan_generation'
              AND job.request_payload->>'assessment_id' = OLD.id::text
          ) THEN
            RAISE EXCEPTION 'assessment referenced by a plan generation job is immutable'
              USING ERRCODE = '23514';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$;
        CREATE TRIGGER guard_generation_assessment_snapshot
        BEFORE UPDATE OR DELETE ON app.onboarding_assessments
        FOR EACH ROW EXECUTE FUNCTION app.guard_generation_assessment_snapshot();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS guard_generation_assessment_snapshot ON app.onboarding_assessments"
    )
    op.execute("DROP FUNCTION IF EXISTS app.guard_generation_assessment_snapshot()")
    op.drop_constraint(
        op.f("ck_generation_jobs_ck_generation_jobs_request_object"),
        "generation_jobs",
        schema="ops",
        type_="check",
    )
    op.drop_index("ix_media_assets_generation_job_id", "media_assets", schema="app")
    op.drop_constraint(
        op.f("fk_media_assets_generation_job"),
        "media_assets",
        schema="app",
        type_="foreignkey",
    )
    op.drop_column("media_assets", "generation_job_id", schema="app")
    op.create_unique_constraint(
        "uq_media_assets_content_sha256",
        "media_assets",
        ["content_sha256"],
        schema="app",
    )
    op.drop_index("ix_ai_invocations_generation_job_id", "ai_invocations", schema="app")
    op.create_unique_constraint(
        "uq_ai_invocations_generation_job",
        "ai_invocations",
        ["generation_job_id"],
        schema="app",
    )
