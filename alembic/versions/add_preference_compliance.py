"""Add preference_compliance_score to action_plan_evaluations

Revision ID: add_preference_compliance
Revises: add_freeze_used_dates
Create Date: 2025-12-26

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_preference_compliance'
down_revision = 'add_freeze_used_dates'
branch_labels = None
depends_on = None


def upgrade():
    # Add preference_compliance_score column to action_plan_evaluations table
    # This tracks diet/allergy/cuisine compliance (0-100)
    op.add_column(
        'action_plan_evaluations',
        sa.Column('preference_compliance_score', sa.Integer, nullable=True)
    )


def downgrade():
    op.drop_column('action_plan_evaluations', 'preference_compliance_score')
