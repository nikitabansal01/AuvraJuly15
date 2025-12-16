"""add_lifestyle_focus_simple

Revision ID: add_lifestyle_focus_simple
Revises: c5b30e69e462
Create Date: 2025-12-16 15:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_lifestyle_focus_simple'
down_revision: Union[str, None] = 'c5b30e69e462'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add lifestyle_focus column if it doesn't exist
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='question_sessions' AND column_name='lifestyle_focus'
    """))
    if result.fetchone() is None:
        op.add_column('question_sessions', sa.Column('lifestyle_focus', sa.ARRAY(sa.String()), nullable=True))


def downgrade() -> None:
    op.drop_column('question_sessions', 'lifestyle_focus')
