"""Add unique constraint for action plan user/date combination

Revision ID: add_unique_user_date
Revises: add_freeze_used_dates
Create Date: 2025-01-21

This migration adds a unique constraint to prevent duplicate action plans
for the same user on the same date. This is CRITICAL to prevent race conditions
where multiple concurrent requests could create duplicate plans.

BUG FIX: Previously, when a user signed up:
1. Session generation created plan 348 (uid=None, session_id=xxx)
2. Plan 348 was transferred to user (uid=abc, session_id=None)
3. Frontend polling triggered ANOTHER generation
4. Due to different advisory lock keys (session vs user), plan 349 was created
5. Result: User had TWO plans for same day, queries could return either

This constraint ensures only ONE plan per user per day.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_unique_user_date'
down_revision = 'add_freeze_used_dates'
branch_labels = None
depends_on = None


def upgrade():
    """Add unique constraints to prevent duplicate action plans."""
    
    # Add unique constraint for user + plan_date combination
    # Use partial index - only for non-null uid values
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_action_plan_user_date 
        ON action_plans (uid, plan_date) 
        WHERE uid IS NOT NULL
    """)
    
    # Add unique constraint for session + plan_date combination
    # For guest users who only have session_id
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_action_plan_session_date 
        ON action_plans (session_id, plan_date) 
        WHERE session_id IS NOT NULL AND uid IS NULL
    """)


def downgrade():
    """Remove the unique constraints."""
    op.execute("DROP INDEX IF EXISTS uq_action_plan_user_date")
    op.execute("DROP INDEX IF EXISTS uq_action_plan_session_date")
