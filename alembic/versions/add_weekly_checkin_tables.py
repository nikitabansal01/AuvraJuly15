"""Add weekly check-in tables and columns

Revision ID: add_weekly_checkin
Revises: add_preference_compliance
Create Date: 2025-01-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = 'add_weekly_checkin'
down_revision = 'add_preference_compliance'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create weekly_checkins table
    op.create_table(
        'weekly_checkins',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('uid', sa.String(255), sa.ForeignKey('user_profiles.uid', ondelete='CASCADE'), nullable=False, index=True),
        
        # Week identification
        sa.Column('week_number', sa.Integer, nullable=False),  # ISO week number (1-53)
        sa.Column('year', sa.Integer, nullable=False),
        sa.Column('check_in_date', sa.Date, nullable=False),
        
        # Primary concern tracking
        sa.Column('top_concern', sa.String(100), nullable=True),  # e.g., "acne", "bloating", "mood"
        sa.Column('concern_severity', sa.Integer, nullable=True),  # 1-9 scale
        sa.Column('overall_wellbeing', sa.Integer, nullable=True),  # 1-9 scale
        
        # Factor analysis (what affected symptoms this week)
        sa.Column('factors_positive', JSONB, default=list),  # ["more_sleep", "less_stress", ...]
        sa.Column('factors_negative', JSONB, default=list),  # ["ate_out_more", "missed_workouts", ...]
        
        # Action plan reflection
        sa.Column('action_reflections', JSONB, default=dict),  # {action_id: {completed: bool, helpfulness: 1-5, notes: str}}
        
        # Forward looking
        sa.Column('concerns_next_week', sa.Text, nullable=True),  # User's main worry for next week
        
        # Cycle context at check-in time
        sa.Column('cycle_day_at_checkin', sa.Integer, nullable=True),
        sa.Column('phase_at_checkin', sa.String(30), nullable=True),
        
        # Conversation data
        sa.Column('conversation_summary', sa.Text, nullable=True),  # LLM summary of the check-in
        sa.Column('raw_messages', JSONB, default=list),  # Full conversation messages
        
        # Progress tracking
        sa.Column('is_complete', sa.Boolean, default=False, nullable=False),
        sa.Column('current_question_index', sa.Integer, default=0),
        
        # Timestamps
        sa.Column('started_at', sa.DateTime, default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
    )
    
    # Create indexes
    op.create_index('idx_weekly_checkins_user_date', 'weekly_checkins', ['uid', 'check_in_date'])
    op.create_index('idx_weekly_checkins_user_week', 'weekly_checkins', ['uid', 'year', 'week_number'])
    
    # Create weekly_checkin_questions table (for dynamic question flow)
    op.create_table(
        'weekly_checkin_questions',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('question_key', sa.String(50), nullable=False, unique=True),  # e.g., "top_concern", "concern_severity"
        sa.Column('question_type', sa.String(30), nullable=False),  # "slider", "tap_choice", "multi_select", "free_text"
        sa.Column('question_template', sa.Text, nullable=False),  # Template with {placeholders}
        sa.Column('default_tap_options', JSONB, default=list),  # Default options if LLM doesn't generate
        sa.Column('concern_type', sa.String(50), nullable=True),  # If question is specific to a concern type
        sa.Column('question_order', sa.Integer, nullable=False),  # Order in the flow
        sa.Column('is_required', sa.Boolean, default=True),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('show_condition', JSONB, nullable=True),  # JSON condition for when to show this question
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.now()),
    )
    
    # Add weekly_checkin_id to symptom_logs
    op.add_column('symptom_logs', 
        sa.Column('weekly_checkin_id', sa.String(36), 
                  sa.ForeignKey('weekly_checkins.id', ondelete='SET NULL'), 
                  nullable=True, index=True)
    )
    
    # Add weekly check-in tracking to user_profiles
    op.add_column('user_profiles',
        sa.Column('weekly_checkin_due_date', sa.Date, nullable=True)
    )
    op.add_column('user_profiles',
        sa.Column('last_weekly_checkin_id', sa.String(36), nullable=True)
    )
    
    # Seed default questions
    op.execute("""
        INSERT INTO weekly_checkin_questions (question_key, question_type, question_template, default_tap_options, question_order, is_required, is_active)
        VALUES 
        ('greeting', 'free_text', 'Hey! Time for your weekly check-in 💜 How are you feeling this week?', '[]', 1, false, true),
        ('top_concern', 'tap_choice', 'What has been bothering you the most this week?', '["Acne", "Bloating", "Mood swings", "Fatigue", "Cramps", "Headaches", "Other"]', 2, true, true),
        ('concern_severity', 'slider', 'On a scale of 1-9, how severe has your {top_concern} been?', '[]', 3, true, true),
        ('factors_negative', 'multi_select', 'Did any of these make it worse?', '["Ate out more", "Less sleep", "More stress", "Missed workouts", "Sugary foods", "Skipped supplements"]', 4, true, true),
        ('factors_positive', 'multi_select', 'What helped you feel better?', '["Regular meals", "Good sleep", "Exercise", "Less stress", "Healthy eating", "Supplements"]', 5, true, true),
        ('action_reflection', 'tap_choice', 'How did you find this week''s action plan?', '["Really helpful", "Somewhat helpful", "Neutral", "Didn''t follow it", "Too difficult"]', 6, true, true),
        ('overall_wellbeing', 'slider', 'Overall, how would you rate your wellbeing this week? (1=worst, 9=best)', '[]', 7, true, true),
        ('concerns_next_week', 'free_text', 'Anything you''re worried about for next week?', '[]', 8, false, true),
        ('closing', 'free_text', 'Thanks for checking in! I''ll use this to personalize your action plan. 💜', '[]', 9, false, true)
        ON CONFLICT (question_key) DO NOTHING;
    """)


def downgrade() -> None:
    # Remove columns from user_profiles
    op.drop_column('user_profiles', 'last_weekly_checkin_id')
    op.drop_column('user_profiles', 'weekly_checkin_due_date')
    
    # Remove column from symptom_logs
    op.drop_column('symptom_logs', 'weekly_checkin_id')
    
    # Drop tables
    op.drop_table('weekly_checkin_questions')
    op.drop_index('idx_weekly_checkins_user_week', table_name='weekly_checkins')
    op.drop_index('idx_weekly_checkins_user_date', table_name='weekly_checkins')
    op.drop_table('weekly_checkins')
