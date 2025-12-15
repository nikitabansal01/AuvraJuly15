"""Ensure chatbot tables have all required columns

Revision ID: ensure_chatbot_tables
Revises: 55ac255ddc53
Create Date: 2025-12-15 12:30:00.000000

This migration ensures all chatbot tables exist with correct schema.
It's safe to run multiple times (uses IF NOT EXISTS / IF EXISTS).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'ensure_chatbot_tables'
down_revision: Union[str, None] = '55ac255ddc53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ensure chatbot tables exist with all required columns"""
    
    # First check if tables exist, if not create them
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()
    
    if 'chat_sessions' not in tables:
        # Create chat_sessions table
        op.create_table(
            'chat_sessions',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('user_id', sa.String(255), sa.ForeignKey('user_profiles.uid', ondelete='CASCADE'), nullable=False),
            sa.Column('conversation_context', sa.String(50), nullable=False),
            sa.Column('status', sa.String(20), server_default='active'),
            sa.Column('current_step', sa.String(100), nullable=True),
            sa.Column('current_flow_data', JSONB, nullable=True),
            sa.Column('started_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('last_message_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('metadata', JSONB, nullable=True),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('ended_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_chat_sessions_user_id', 'chat_sessions', ['user_id'])
        op.create_index('idx_chat_sessions_user_status', 'chat_sessions', ['user_id', 'status'])
        op.create_index('idx_chat_sessions_last_msg', 'chat_sessions', ['last_message_at'])
    else:
        # Table exists, ensure created_at column exists
        columns = [col['name'] for col in inspector.get_columns('chat_sessions')]
        if 'created_at' not in columns:
            op.add_column('chat_sessions', sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()))
        if 'updated_at' not in columns:
            op.add_column('chat_sessions', sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()))
        if 'summary' not in columns:
            op.add_column('chat_sessions', sa.Column('summary', sa.Text(), nullable=True))
    
    if 'chat_messages' not in tables:
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
    
    if 'symptom_logs' not in tables:
        # Create symptom_logs table
        op.create_table(
            'symptom_logs',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('user_id', sa.String(255), sa.ForeignKey('user_profiles.uid', ondelete='CASCADE'), nullable=False),
            sa.Column('symptom_type', sa.String(50), nullable=False),
            sa.Column('severity', sa.Integer(), nullable=False),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('factors', JSONB, nullable=True),
            sa.Column('cycle_day', sa.Integer(), nullable=True),
            sa.Column('phase', sa.String(30), nullable=True),
            sa.Column('logged_via', sa.String(30), server_default='chatbot'),
            sa.Column('chat_message_id', sa.String(36), nullable=True),
            sa.Column('logged_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('logged_date', sa.Date(), nullable=False),
        )
        op.create_index('ix_symptom_logs_user_id', 'symptom_logs', ['user_id'])
        op.create_index('ix_symptom_logs_user_type_date', 'symptom_logs', ['user_id', 'symptom_type', 'logged_date'])
    
    if 'conversation_summaries' not in tables:
        # Create conversation_summaries table
        op.create_table(
            'conversation_summaries',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('user_id', sa.String(255), sa.ForeignKey('user_profiles.uid', ondelete='CASCADE'), nullable=False),
            sa.Column('period_start', sa.Date(), nullable=False),
            sa.Column('period_end', sa.Date(), nullable=False),
            sa.Column('summary_text', sa.Text(), nullable=False),
            sa.Column('key_topics', JSONB, nullable=True),
            sa.Column('symptoms_mentioned', JSONB, nullable=True),
            sa.Column('decisions_made', JSONB, nullable=True),
            sa.Column('session_count', sa.Integer(), server_default='0'),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index('ix_conversation_summaries_user_id', 'conversation_summaries', ['user_id'])
        op.create_index('ix_conversation_summaries_period', 'conversation_summaries', ['user_id', 'period_start', 'period_end'])


def downgrade() -> None:
    """Drop chatbot tables if needed"""
    op.drop_table('conversation_summaries', if_exists=True)
    op.drop_table('symptom_logs', if_exists=True)
    op.drop_table('chat_messages', if_exists=True)
    op.drop_table('chat_sessions', if_exists=True)
