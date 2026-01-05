"""Merge actionable_insights and symptom check-in threads heads.

Revision ID: merge_actionable_insights_and_symptom_threads
Revises: add_actionable_insights, add_symptom_checkin_threads
Create Date: 2026-01-05

"""

# This is an Alembic merge revision to resolve multiple heads.

from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision = "merge_actionable_insights_and_symptom_threads"
down_revision = ("add_actionable_insights", "add_symptom_checkin_threads")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge revision; no schema changes.
    pass


def downgrade() -> None:
    # Downgrade is a no-op for merge revisions.
    pass
