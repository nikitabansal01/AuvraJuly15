"""Add durable plan-generation evidence and provider-telemetry linkage.

Revision ID: 20260808_0006
Revises: 20260808_0005
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "20260808_0006"
down_revision = "20260808_0005"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_sources",
        sa.Column("source_type", sa.String(32), nullable=True),
        schema="app",
    )
    op.add_column(
        "research_sources",
        sa.Column("source_external_id", sa.String(64), nullable=True),
        schema="app",
    )
    op.execute(
        """
        UPDATE app.research_sources
        SET source_type = 'pubmed',
            source_external_id = substring(canonical_url from
              '^https://pubmed[.]ncbi[.]nlm[.]nih[.]gov/([0-9]+)/$')
        WHERE source_type IS NULL AND source_external_id IS NULL;
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM app.research_sources
            WHERE source_type IS NULL OR source_external_id IS NULL
          ) THEN
            RAISE EXCEPTION 'research source cannot be classified as canonical PubMed evidence'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$;
        """
    )
    op.alter_column("research_sources", "source_type", nullable=False, schema="app")
    op.alter_column("research_sources", "source_external_id", nullable=False, schema="app")
    op.create_unique_constraint(
        "uq_research_sources_type_external",
        "research_sources",
        ["source_type", "source_external_id"],
        schema="app",
    )
    op.add_column(
        "ai_invocations",
        sa.Column("generation_job_id", UUID(as_uuid=True), nullable=True),
        schema="app",
    )
    op.create_foreign_key(
        "fk_ai_invocations_generation_job",
        "ai_invocations",
        "generation_jobs",
        ["generation_job_id"],
        ["id"],
        source_schema="app",
        referent_schema="ops",
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_ai_invocations_generation_job",
        "ai_invocations",
        ["generation_job_id"],
        schema="app",
    )
    op.add_column(
        "ai_invocations",
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="USD"),
        schema="app",
    )
    op.add_column(
        "ai_invocations",
        sa.Column("price_version", sa.String(64), nullable=False, server_default="unknown"),
        schema="app",
    )
    op.create_check_constraint(
        "ck_ai_invocations_currency",
        "ai_invocations",
        "currency_code ~ '^[A-Z]{3}$'",
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_invocations_ck_ai_invocations_currency"),
        "ai_invocations",
        schema="app",
        type_="check",
    )
    op.drop_column("ai_invocations", "price_version", schema="app")
    op.drop_column("ai_invocations", "currency_code", schema="app")
    op.drop_constraint(
        "uq_ai_invocations_generation_job",
        "ai_invocations",
        schema="app",
        type_="unique",
    )
    op.drop_constraint(
        "fk_ai_invocations_generation_job",
        "ai_invocations",
        schema="app",
        type_="foreignkey",
    )
    op.drop_column("ai_invocations", "generation_job_id", schema="app")
    op.drop_constraint(
        "uq_research_sources_type_external",
        "research_sources",
        schema="app",
        type_="unique",
    )
    op.drop_column("research_sources", "source_external_id", schema="app")
    op.drop_column("research_sources", "source_type", schema="app")
