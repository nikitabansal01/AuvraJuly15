"""add_lifestyle_focus_to_question_sessions_safe

Revision ID: safe_lifestyle_focus
Revises: 5c6207e75696
Create Date: 2025-12-16 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'safe_lifestyle_focus'
down_revision: Union[str, None] = '5c6207e75696'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add lifestyle_focus column to question_sessions if it doesn't exist
    conn = op.get_bind()
    
    # Check if column exists
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='question_sessions' AND column_name='lifestyle_focus'
    """))
    if result.fetchone() is None:
        op.add_column('question_sessions', sa.Column('lifestyle_focus', sa.ARRAY(sa.String()), nullable=True))
    
    # Add chatbot_memory to user_profiles if it doesn't exist
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='user_profiles' AND column_name='chatbot_memory'
    """))
    if result.fetchone() is None:
        from sqlalchemy.dialects import postgresql
        op.add_column('user_profiles', sa.Column('chatbot_memory', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    
    # Drop chatbot_memory if exists
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='user_profiles' AND column_name='chatbot_memory'
    """))
    if result.fetchone() is not None:
        op.drop_column('user_profiles', 'chatbot_memory')
    
    # Drop lifestyle_focus if exists
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='question_sessions' AND column_name='lifestyle_focus'
    """))
    if result.fetchone() is not None:
        op.drop_column('question_sessions', 'lifestyle_focus')
