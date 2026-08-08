"""Permit the initial onboarding assessment revision to be zero.

Revision ID: 20260808_0004
Revises: 20260808_0003
"""

from alembic import op


revision = "20260808_0004"
down_revision = "20260808_0003"
branch_labels = depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_onboarding_assessments_positive_version",
        "onboarding_assessments",
        schema="app",
        type_="check",
    )
    op.create_check_constraint(
        "ck_onboarding_assessments_nonnegative_version",
        "onboarding_assessments",
        "version >= 0",
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_onboarding_assessments_nonnegative_version",
        "onboarding_assessments",
        schema="app",
        type_="check",
    )
    op.create_check_constraint(
        "ck_onboarding_assessments_positive_version",
        "onboarding_assessments",
        "version > 0",
        schema="app",
    )
