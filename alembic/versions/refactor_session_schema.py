"""Refactor session schema

Revision ID: refactor_session_schema
Revises: 7bddf1339f88
Create Date: 2024-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'refactor_session_schema'
down_revision = '7bddf1339f88'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create new user_profiles table
    op.create_table('user_profiles',
        sa.Column('uid', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('uid')
    )
    op.create_index(op.f('ix_user_profiles_uid'), 'user_profiles', ['uid'], unique=False)

    # 2. Modify question_sessions table
    op.add_column('question_sessions', sa.Column('expires_at', sa.DateTime(), nullable=False))
    op.add_column('question_sessions', sa.Column('age', sa.Integer(), nullable=True))
    op.add_column('question_sessions', sa.Column('period_description', sa.String(length=100), nullable=True))
    op.add_column('question_sessions', sa.Column('birth_control', postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('question_sessions', sa.Column('last_period_date', sa.String(length=50), nullable=True))
    op.add_column('question_sessions', sa.Column('cycle_length', sa.String(length=50), nullable=True))
    op.add_column('question_sessions', sa.Column('period_concerns', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('question_sessions', sa.Column('body_concerns', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('question_sessions', sa.Column('skin_hair_concerns', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('question_sessions', sa.Column('mental_health_concerns', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('question_sessions', sa.Column('other_concerns', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('question_sessions', sa.Column('top_concern', sa.String(length=255), nullable=True))
    op.add_column('question_sessions', sa.Column('diagnosed_conditions', postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('question_sessions', sa.Column('family_history', postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('question_sessions', sa.Column('workout_intensity', sa.String(length=50), nullable=True))
    op.add_column('question_sessions', sa.Column('sleep_duration', sa.String(length=50), nullable=True))
    op.add_column('question_sessions', sa.Column('stress_level', sa.String(length=50), nullable=True))
    
    # Set default expires_at for existing sessions
    op.execute("UPDATE question_sessions SET expires_at = created_at + INTERVAL '24 hours' WHERE expires_at IS NULL")
    
    # 3. Modify user_responses table
    op.add_column('user_responses', sa.Column('age', sa.Integer(), nullable=True))
    
    # Copy existing age data (no conversion needed)
    op.execute("UPDATE user_responses SET age = age WHERE age IS NOT NULL")
    
    # Remove old columns from user_responses
    op.drop_column('user_responses', 'session_id')
    op.drop_column('user_responses', 'name')
    op.drop_column('user_responses', 'last_period_date')
    
    # Remove old columns from question_sessions
    op.drop_column('question_sessions', 'uid')
    op.drop_column('question_sessions', 'completed_at')
    
    # Update status values
    op.execute("UPDATE question_sessions SET status = 'active' WHERE status = 'in_progress'")
    op.execute("UPDATE question_sessions SET status = 'completed' WHERE status = 'linked'")


def downgrade() -> None:
    # 1. Drop user_profiles table
    op.drop_index(op.f('ix_user_profiles_uid'), table_name='user_profiles')
    op.drop_table('user_profiles')

    # 2. Restore question_sessions table
    op.add_column('question_sessions', sa.Column('uid', sa.String(length=255), nullable=True))
    op.add_column('question_sessions', sa.Column('completed_at', sa.DateTime(), nullable=True))
    
    # Remove new columns from question_sessions
    op.drop_column('question_sessions', 'expires_at')
    op.drop_column('question_sessions', 'age')
    op.drop_column('question_sessions', 'period_description')
    op.drop_column('question_sessions', 'birth_control')
    op.drop_column('question_sessions', 'last_period_date')
    op.drop_column('question_sessions', 'cycle_length')
    op.drop_column('question_sessions', 'period_concerns')
    op.drop_column('question_sessions', 'body_concerns')
    op.drop_column('question_sessions', 'skin_hair_concerns')
    op.drop_column('question_sessions', 'mental_health_concerns')
    op.drop_column('question_sessions', 'other_concerns')
    op.drop_column('question_sessions', 'top_concern')
    op.drop_column('question_sessions', 'diagnosed_conditions')
    op.drop_column('question_sessions', 'family_history')
    op.drop_column('question_sessions', 'workout_intensity')
    op.drop_column('question_sessions', 'sleep_duration')
    op.drop_column('question_sessions', 'stress_level')

    # 3. Restore user_responses table
    op.add_column('user_responses', sa.Column('session_id', sa.String(length=255), nullable=True))
    op.add_column('user_responses', sa.Column('name', sa.String(length=255), nullable=True))
    op.add_column('user_responses', sa.Column('last_period_date', sa.String(length=50), nullable=True))
    
    # Remove age column (keep existing age data)
    op.drop_column('user_responses', 'age')
    
    # Restore status values
    op.execute("UPDATE question_sessions SET status = 'in_progress' WHERE status = 'active'")
    op.execute("UPDATE question_sessions SET status = 'linked' WHERE status = 'completed'")
