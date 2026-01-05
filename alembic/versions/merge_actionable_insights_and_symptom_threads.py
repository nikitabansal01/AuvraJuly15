"""Merge actionable_insights and symptom check-in threads heads.

NOTE: This revision id must remain <= 32 characters because the production
`alembic_version.version_num` column is VARCHAR(32). A longer revision id will
cause deployment-time migrations to fail when Alembic updates that table.

Revision ID: merge_action_ins_symptom
Revises: add_actionable_insights, add_symptom_checkin_threads
Create Date: 2026-01-05

"""

# This is an Alembic merge revision to resolve multiple heads.

from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision = "merge_action_ins_symptom"
down_revision = ("add_actionable_insights", "add_symptom_checkin_threads")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge revision; no schema changes.
    pass


def downgrade() -> None:
    # Downgrade is a no-op for merge revisions.
    pass
