"""Add actionable_insights column to weekly_checkins

Revision ID: add_actionable_insights
Revises: e1c64dcc2acf
Create Date: 2025-01-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = 'add_actionable_insights'
down_revision = 'e1c64dcc2acf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add actionable_insights JSONB column to weekly_checkins table."""
    op.add_column(
        'weekly_checkins',
        sa.Column(
            'actionable_insights',
            JSONB,
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
            comment='Structured insights for action plan generation: triggers_identified, relief_factors_identified, severity_trend, suggested_additions, suggested_removals, priority_focus, key_insight'
        )
    )


def downgrade() -> None:
    """Remove actionable_insights column from weekly_checkins table."""
    op.drop_column('weekly_checkins', 'actionable_insights')
