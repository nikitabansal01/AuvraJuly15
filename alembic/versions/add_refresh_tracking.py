"""Add refresh tracking to user_streak_data

Revision ID: add_refresh_tracking
Revises: add_user_rewards_tables
Create Date: 2025-12-26
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = 'add_refresh_tracking'
down_revision = 'add_user_rewards_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add refresh tracking columns to user_streak_data
    op.add_column('user_streak_data', 
                  sa.Column('daily_refresh_count', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('user_streak_data', 
                  sa.Column('last_refresh_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_streak_data', 'last_refresh_date')
    op.drop_column('user_streak_data', 'daily_refresh_count')
