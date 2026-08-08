"""add_preference_weights_to_user_tables

Revision ID: b521a53ac247
Revises: 55ac255ddc53
Create Date: 2025-12-12 18:22:02.744272

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b521a53ac247'
down_revision: Union[str, None] = '55ac255ddc53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
