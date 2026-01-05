"""add symptom checkin threads

Revision ID: add_symptom_checkin_threads
Revises: add_care_plan_checkin_threads
Create Date: 2026-01-05

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "add_symptom_checkin_threads"
down_revision = "add_care_plan_checkin_threads"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "symptom_checkin_threads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("uid", sa.String(length=255), sa.ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=True),
        sa.Column("raw_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rolling_summary", sa.Text(), nullable=True),
        sa.Column("summarized_message_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_summarized_at", sa.DateTime(), nullable=True),
        sa.Column("actionable_insights", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("uid", "local_date", name="uq_symptom_thread_user_date"),
    )

    op.create_index("idx_symptom_thread_uid", "symptom_checkin_threads", ["uid"], unique=False)
    op.create_index("idx_symptom_thread_local_date", "symptom_checkin_threads", ["local_date"], unique=False)
    op.create_index("idx_symptom_thread_user_closed", "symptom_checkin_threads", ["uid", "is_closed"], unique=False)


def downgrade():
    op.drop_index("idx_symptom_thread_user_closed", table_name="symptom_checkin_threads")
    op.drop_index("idx_symptom_thread_local_date", table_name="symptom_checkin_threads")
    op.drop_index("idx_symptom_thread_uid", table_name="symptom_checkin_threads")
    op.drop_table("symptom_checkin_threads")
