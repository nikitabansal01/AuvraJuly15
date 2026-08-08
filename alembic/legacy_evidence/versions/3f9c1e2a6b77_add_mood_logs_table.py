"""add_mood_logs_table

Revision ID: 3f9c1e2a6b77
Revises: 64bc39c55d75
Create Date: 2026-02-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f9c1e2a6b77"
down_revision: Union[str, None] = "64bc39c55d75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mood_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("mood_level", sa.Integer(), nullable=False),
        sa.Column("energy_level", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("logged_at", sa.DateTime(), nullable=False),
        sa.Column("logged_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.uid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mood_logs_logged_date"), "mood_logs", ["logged_date"], unique=False)
    op.create_index(op.f("ix_mood_logs_user_id"), "mood_logs", ["user_id"], unique=False)
    op.create_index("idx_mood_logs_user_date_unique", "mood_logs", ["user_id", "logged_date"], unique=True)
    op.create_index("idx_mood_logs_user_logged_at", "mood_logs", ["user_id", "logged_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_mood_logs_user_logged_at", table_name="mood_logs")
    op.drop_index("idx_mood_logs_user_date_unique", table_name="mood_logs")
    op.drop_index(op.f("ix_mood_logs_user_id"), table_name="mood_logs")
    op.drop_index(op.f("ix_mood_logs_logged_date"), table_name="mood_logs")
    op.drop_table("mood_logs")

