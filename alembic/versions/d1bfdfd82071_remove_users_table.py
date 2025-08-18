"""remove_users_table

Revision ID: d1bfdfd82071
Revises: 2c0cc15be40b
Create Date: 2025-08-18 14:59:02.863461

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1bfdfd82071'
down_revision: Union[str, None] = '2c0cc15be40b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop users table as it's redundant with user_profiles
    op.drop_table('users')


def downgrade() -> None:
    # Recreate users table if needed to rollback
    op.create_table('users',
        sa.Column('uid', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('uid')
    )
