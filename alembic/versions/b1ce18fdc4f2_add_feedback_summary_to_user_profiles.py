"""add_feedback_summary_to_user_profiles

Revision ID: b1ce18fdc4f2
Revises: fix_image_url_sizes
Create Date: 2025-12-22 19:43:05.632171

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1ce18fdc4f2'
down_revision: Union[str, None] = 'fix_image_url_sizes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add feedback summary fields to user_profiles
    op.add_column('user_profiles', sa.Column('feedback_summary', sa.Text(), nullable=True))
    op.add_column('user_profiles', sa.Column('feedback_summary_updated_at', sa.DateTime(), nullable=True))
    op.add_column('user_profiles', sa.Column('feedback_last_count', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    # Remove feedback summary fields
    op.drop_column('user_profiles', 'feedback_last_count')
    op.drop_column('user_profiles', 'feedback_summary_updated_at')
    op.drop_column('user_profiles', 'feedback_summary')
