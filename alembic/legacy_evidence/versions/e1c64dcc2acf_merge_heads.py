"""merge heads

Revision ID: e1c64dcc2acf
Revises: 63b5f93e3606, add_daily_review_system
Create Date: 2025-12-30 12:46:06.032727

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1c64dcc2acf'
down_revision: Union[str, None] = ('63b5f93e3606', 'add_daily_review_system')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
