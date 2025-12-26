"""Add user rewards and streak tracking tables

Revision ID: add_user_rewards_tables
Revises: add_text_feedback_fields
Create Date: 2025-12-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_user_rewards_tables'
down_revision = 'add_text_feedback_fields'  # Latest migration
branch_labels = None
depends_on = None


def upgrade():
    # User Rewards table - tracks claimed rewards
    op.create_table(
        'user_rewards',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uid', sa.String(255), nullable=False),
        sa.Column('reward_id', sa.String(50), nullable=False),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['uid'], ['user_profiles.uid'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_user_reward_uid', 'user_rewards', ['uid'])
    op.create_index('ix_user_rewards_id', 'user_rewards', ['id'])
    
    # User Streak Data table - persistent streak tracking with freeze support
    op.create_table(
        'user_streak_data',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uid', sa.String(255), nullable=False),
        sa.Column('current_streak', sa.Integer(), server_default='0'),
        sa.Column('longest_streak', sa.Integer(), server_default='0'),
        sa.Column('last_activity_date', sa.Date(), nullable=True),
        sa.Column('freeze_count', sa.Integer(), server_default='0'),
        sa.Column('freeze_used_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['uid'], ['user_profiles.uid'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uid', name='uq_user_streak_data_uid')
    )
    op.create_index('idx_streak_uid', 'user_streak_data', ['uid'])
    op.create_index('ix_user_streak_data_id', 'user_streak_data', ['id'])


def downgrade():
    op.drop_index('ix_user_streak_data_id', table_name='user_streak_data')
    op.drop_index('idx_streak_uid', table_name='user_streak_data')
    op.drop_table('user_streak_data')
    
    op.drop_index('ix_user_rewards_id', table_name='user_rewards')
    op.drop_index('idx_user_reward_uid', table_name='user_rewards')
    op.drop_table('user_rewards')
