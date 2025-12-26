"""Add freeze_used_dates JSONB column to user_streak_data

Revision ID: add_freeze_used_dates
Revises: add_text_feedback_fields
Create Date: 2025-12-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = 'add_freeze_used_dates'
down_revision = 'add_text_feedback_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Add freeze_used_dates JSONB column for multi-day freeze tracking
    op.add_column(
        'user_streak_data',
        sa.Column('freeze_used_dates', JSONB, nullable=True, default=[])
    )


def downgrade():
    op.drop_column('user_streak_data', 'freeze_used_dates')
