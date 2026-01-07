"""add_images_to_recommendation_records

Revision ID: e9b5f0a13e6e
Revises: merge_action_ins_symptom
Create Date: 2026-01-08 00:58:39.734692

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9b5f0a13e6e'
down_revision: Union[str, None] = 'merge_action_ins_symptom'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from sqlalchemy.dialects import postgresql

def upgrade() -> None:
    # Add columns to recommendation_records to support full Action Plan generation features
    op.add_column('recommendation_records', sa.Column('hero_image_url', sa.Text(), nullable=True))
    op.add_column('recommendation_records', sa.Column('hero_image_prompt', sa.Text(), nullable=True))
    op.add_column('recommendation_records', sa.Column('variants_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('recommendation_records', sa.Column('hormone_persona_intro', sa.Text(), nullable=True))
    op.add_column('recommendation_records', sa.Column('target_hormone', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('recommendation_records', 'target_hormone')
    op.drop_column('recommendation_records', 'hormone_persona_intro')
    op.drop_column('recommendation_records', 'variants_data')
    op.drop_column('recommendation_records', 'hero_image_prompt')
    op.drop_column('recommendation_records', 'hero_image_url')
