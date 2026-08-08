"""merge_migration_heads

Revision ID: 223a06753539
Revises: add_pubmed_cache, b1ce18fdc4f2
Create Date: 2025-12-24 13:11:54.930967

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '223a06753539'
down_revision: Union[str, None] = ('add_pubmed_cache', 'b1ce18fdc4f2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
