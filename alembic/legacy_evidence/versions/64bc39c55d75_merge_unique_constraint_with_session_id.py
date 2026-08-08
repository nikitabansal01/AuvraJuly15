"""merge_unique_constraint_with_session_id

Revision ID: 64bc39c55d75
Revises: 78911d5faa9d, add_unique_user_date
Create Date: 2026-01-22 06:23:43.172501

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64bc39c55d75'
down_revision: Union[str, None] = ('78911d5faa9d', 'add_unique_user_date')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
