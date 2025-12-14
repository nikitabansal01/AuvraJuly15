"""Add chatbot tables

Revision ID: add_chatbot_tables
Revises: fe16aed4dbbb
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = 'add_chatbot_tables'
down_revision = 'fe16aed4dbbb'  # Chain from latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Add chatbot_memory to user_profiles if not exists
    try:
        op.add_column('user_profiles', sa.Column('chatbot_memory', JSONB, nullable=True))
    except Exception:
        pass  # Column may already exist
    
    # Create chat_sessions table
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False, index=True),
        sa.Column('conversation_context', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), default='active'),
        sa.Column('current_step', sa.String(100), nullable=True),
        sa.Column('current_flow_data', JSONB, nullable=True),
        sa.Column('started_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('last_message_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('metadata', JSONB, nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_chat_sessions_user_context', 'chat_sessions', ['user_id', 'conversation_context'])
    op.create_index('ix_chat_sessions_user_status', 'chat_sessions', ['user_id', 'status'])
    op.create_index('ix_chat_sessions_last_msg', 'chat_sessions', ['last_message_at'])
    
    # Create chat_messages table
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36), sa.ForeignKey('chat_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('input_mode', sa.String(10), nullable=True),
        sa.Column('selected_choice', sa.String(255), nullable=True),
        sa.Column('slider_value', sa.Integer(), nullable=True),
        sa.Column('response_type', sa.String(20), nullable=True),
        sa.Column('choices', JSONB, nullable=True),
        sa.Column('slider_config', JSONB, nullable=True),
        sa.Column('actions', JSONB, nullable=True),
        sa.Column('tools_called', JSONB, nullable=True),
        sa.Column('retrieval_context', JSONB, nullable=True),
        sa.Column('audio_url', sa.String(500), nullable=True),
        sa.Column('transcription_confidence', sa.Integer(), nullable=True),
        sa.Column('model_used', sa.String(50), nullable=True),
        sa.Column('tokens_input', sa.Integer(), nullable=True),
        sa.Column('tokens_output', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('evaluation_scores', JSONB, nullable=True),
        sa.Column('message_metadata', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_chat_messages_session', 'chat_messages', ['session_id'])
    op.create_index('ix_chat_messages_session_created', 'chat_messages', ['session_id', 'created_at'])
    
    # Create symptom_logs table
    op.create_table(
        'symptom_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False, index=True),
        sa.Column('symptom_type', sa.String(50), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('factors', JSONB, nullable=True),
        sa.Column('cycle_day', sa.Integer(), nullable=True),
        sa.Column('phase', sa.String(30), nullable=True),
        sa.Column('logged_via', sa.String(30), default='chatbot'),
        sa.Column('chat_message_id', sa.String(36), nullable=True),
        sa.Column('logged_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('logged_date', sa.Date(), nullable=False),
    )
    op.create_index('ix_symptom_logs_user_type_date', 'symptom_logs', ['user_id', 'symptom_type', 'logged_date'])
    op.create_index('ix_symptom_logs_user_date', 'symptom_logs', ['user_id', 'logged_date'])
    
    # Create conversation_summaries table
    op.create_table(
        'conversation_summaries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False, index=True),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('summary_type', sa.String(20), nullable=False),
        sa.Column('summary_data', JSONB, nullable=False),
        sa.Column('generated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_conv_summaries_user_period', 'conversation_summaries', ['user_id', 'summary_type', 'period_start'], unique=True)
    
    # Create assignment_skip_logs table
    op.create_table(
        'assignment_skip_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False, index=True),
        sa.Column('assignment_id', sa.BigInteger(), nullable=False),
        sa.Column('recommendation_id', sa.Integer(), nullable=False),
        sa.Column('skip_reason', sa.String(100), nullable=True),
        sa.Column('reason_notes', sa.Text(), nullable=True),
        sa.Column('alternative_offered', sa.Boolean(), default=False),
        sa.Column('alternative_taken_id', sa.Integer(), nullable=True),
        sa.Column('cycle_day', sa.Integer(), nullable=True),
        sa.Column('phase', sa.String(30), nullable=True),
        sa.Column('chat_session_id', sa.String(36), nullable=True),
        sa.Column('skipped_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('skip_date', sa.Date(), nullable=False),
    )
    op.create_index('ix_skip_logs_user_date', 'assignment_skip_logs', ['user_id', 'skip_date'])
    op.create_index('ix_skip_logs_recommendation', 'assignment_skip_logs', ['recommendation_id'])


def downgrade():
    op.drop_table('assignment_skip_logs')
    op.drop_table('conversation_summaries')
    op.drop_table('symptom_logs')
    op.drop_table('chat_messages')
    op.drop_table('chat_sessions')
    
    try:
        op.drop_column('user_profiles', 'chatbot_memory')
    except Exception:
        pass
