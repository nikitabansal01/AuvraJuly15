"""Add daily review system tables and columns

Revision ID: add_daily_review_system
Revises: safe_lifestyle_focus
Create Date: 2024-12-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_daily_review_system'
down_revision = 'safe_lifestyle_focus'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add review_completed column to action_plans table
    op.add_column('action_plans', sa.Column('review_completed', sa.Boolean(), nullable=True, server_default='false'))
    
    # Add carried_forward_from column to action_plan_items table (for carry forward tracking)
    op.add_column('action_plan_items', sa.Column('carried_forward_from', sa.Integer(), nullable=True))
    
    # Create action_plan_daily_reviews table
    # op.create_table(
    #     'action_plan_daily_reviews',
    #     sa.Column('id', sa.Integer(), nullable=False),
    #     sa.Column('uid', sa.String(length=255), nullable=False),
    #     sa.Column('plan_id', sa.Integer(), nullable=False),
    #     sa.Column('review_date', sa.Date(), nullable=False),
    #     sa.Column('review_completed_at', sa.DateTime(), nullable=True),
    #     sa.Column('items_review_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
    #     sa.Column('streak_action', sa.String(length=20), nullable=True),
    #     sa.Column('freezes_used_count', sa.Integer(), nullable=True, server_default='0'),
    #     sa.Column('items_carried_forward', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
    #     sa.Column('items_marked_complete', sa.Integer(), nullable=True, server_default='0'),
    #     sa.Column('items_replaced', sa.Integer(), nullable=True, server_default='0'),
    #     sa.Column('items_skipped', sa.Integer(), nullable=True, server_default='0'),
    #     sa.Column('created_at', sa.DateTime(), nullable=True),
    #     sa.ForeignKeyConstraint(['plan_id'], ['action_plans.id'], ondelete='CASCADE'),
    #     sa.ForeignKeyConstraint(['uid'], ['user_profiles.uid'], ondelete='CASCADE'),
    #     sa.PrimaryKeyConstraint('id')
    # )
    
    # Create indexes
    # op.create_index('idx_daily_review_user_date', 'action_plan_daily_reviews', ['uid', 'review_date'], unique=False)
    # op.create_index('idx_daily_review_plan', 'action_plan_daily_reviews', ['plan_id'], unique=False)
    # op.create_index(op.f('ix_action_plan_daily_reviews_id'), 'action_plan_daily_reviews', ['id'], unique=False)
    # op.create_index(op.f('ix_action_plan_daily_reviews_uid'), 'action_plan_daily_reviews', ['uid'], unique=False)
    # op.create_index(op.f('ix_action_plan_daily_reviews_plan_id'), 'action_plan_daily_reviews', ['plan_id'], unique=False)
    # op.create_index(op.f('ix_action_plan_daily_reviews_review_date'), 'action_plan_daily_reviews', ['review_date'], unique=False)
    
    # Add index for review_completed on action_plans for fast pending review queries
    op.create_index('idx_action_plan_review_completed', 'action_plans', ['uid', 'plan_date', 'review_completed'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_action_plan_review_completed', table_name='action_plans')
    op.drop_index(op.f('ix_action_plan_daily_reviews_review_date'), table_name='action_plan_daily_reviews')
    op.drop_index(op.f('ix_action_plan_daily_reviews_plan_id'), table_name='action_plan_daily_reviews')
    op.drop_index(op.f('ix_action_plan_daily_reviews_uid'), table_name='action_plan_daily_reviews')
    op.drop_index(op.f('ix_action_plan_daily_reviews_id'), table_name='action_plan_daily_reviews')
    op.drop_index('idx_daily_review_plan', table_name='action_plan_daily_reviews')
    op.drop_index('idx_daily_review_user_date', table_name='action_plan_daily_reviews')
    
    # Drop table
    op.drop_table('action_plan_daily_reviews')
    
    # Remove columns
    op.drop_column('action_plan_items', 'carried_forward_from')
    op.drop_column('action_plans', 'review_completed')
