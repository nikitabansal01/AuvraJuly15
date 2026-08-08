"""Add action plan system tables

Revision ID: add_action_plan_tables
Revises: safe_lifestyle_focus
Create Date: 2025-12-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_action_plan_tables'
down_revision = 'safe_lifestyle_focus'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create action_plans table
    op.create_table(
        'action_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uid', sa.String(length=255), nullable=False),
        sa.Column('plan_date', sa.Date(), nullable=False),
        sa.Column('primary_hormone', sa.String(length=50), nullable=True),
        sa.Column('secondary_hormones', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('cycle_day', sa.Integer(), nullable=True),
        sa.Column('cycle_phase', sa.String(length=50), nullable=True),
        sa.Column('lifestyle_focus', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('generation_cost', sa.String(length=50), nullable=True),
        sa.Column('generation_time_ms', sa.Integer(), nullable=True),
        sa.Column('gpt_model_used', sa.String(length=50), nullable=True, default='gpt-4o-mini'),
        sa.Column('is_regenerated', sa.Boolean(), nullable=True, default=False),
        sa.Column('feedback_collected', sa.Boolean(), nullable=True, default=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['uid'], ['user_profiles.uid'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_action_plan_user_date', 'action_plans', ['uid', 'plan_date'], unique=True)
    
    # Create action_plan_items table
    op.create_table(
        'action_plan_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('uid', sa.String(length=255), nullable=False),
        sa.Column('slot', sa.Integer(), nullable=False),
        sa.Column('time_slot', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('specific_action', sa.Text(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=True),
        sa.Column('target_hormone', sa.String(length=50), nullable=False),
        sa.Column('hormone_persona_intro', sa.Text(), nullable=True),
        sa.Column('food_amounts', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('food_items', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('exercise_durations', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('exercise_types', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('exercise_intensities', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('mindfulness_durations', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('mindfulness_techniques', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('conditions', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('symptoms', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('hero_image_url', sa.String(length=500), nullable=True),
        sa.Column('hero_image_prompt', sa.Text(), nullable=True),
        sa.Column('research_studies', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_completed', sa.Boolean(), nullable=True, default=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('is_replaced', sa.Boolean(), nullable=True, default=False),
        sa.Column('replaced_at', sa.DateTime(), nullable=True),
        sa.Column('replacement_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['plan_id'], ['action_plans.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_action_item_plan', 'action_plan_items', ['plan_id'])
    op.create_index('idx_action_item_user_date', 'action_plan_items', ['uid', 'created_at'])
    
    # Create action_plan_item_variants table
    op.create_table(
        'action_plan_item_variants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('variant_type', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(length=500), nullable=True),
        sa.Column('image_prompt', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['action_plan_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_variant_item', 'action_plan_item_variants', ['item_id'])
    
    # Create action_plan_feedback table
    op.create_table(
        'action_plan_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uid', sa.String(length=255), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('feedback_type', sa.String(length=20), nullable=False),
        sa.Column('action_title', sa.String(length=255), nullable=True),
        sa.Column('action_category', sa.String(length=20), nullable=True),
        sa.Column('target_hormone', sa.String(length=50), nullable=True),
        sa.Column('replacement_reason', sa.Text(), nullable=True),
        sa.Column('was_replaced', sa.Boolean(), nullable=True, default=False),
        sa.Column('cycle_day', sa.Integer(), nullable=True),
        sa.Column('cycle_phase', sa.String(length=50), nullable=True),
        sa.Column('action_shown_at', sa.DateTime(), nullable=True),
        sa.Column('feedback_given_at', sa.DateTime(), nullable=True),
        sa.Column('time_to_feedback_seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['uid'], ['user_profiles.uid'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plan_id'], ['action_plans.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['action_plan_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_feedback_user', 'action_plan_feedback', ['uid', 'created_at'])
    op.create_index('idx_feedback_type', 'action_plan_feedback', ['feedback_type'])
    
    # Create image_library table
    op.create_table(
        'image_library',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('image_url', sa.String(length=500), nullable=False),
        sa.Column('prompt_text', sa.Text(), nullable=False),
        sa.Column('prompt_embedding', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('variant_type', sa.String(length=20), nullable=True),
        sa.Column('generation_model', sa.String(length=50), nullable=True, default='flux-schnell'),
        sa.Column('generation_cost', sa.String(length=50), nullable=True),
        sa.Column('generation_time_ms', sa.Integer(), nullable=True),
        sa.Column('image_width', sa.Integer(), nullable=True, default=512),
        sa.Column('image_height', sa.Integer(), nullable=True, default=512),
        sa.Column('usage_count', sa.Integer(), nullable=True, default=1),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('used_by_users', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_image_library_category', 'image_library', ['category', 'variant_type'])
    op.create_index('idx_image_library_usage', 'image_library', ['usage_count'])


def downgrade() -> None:
    op.drop_table('image_library')
    op.drop_table('action_plan_feedback')
    op.drop_table('action_plan_item_variants')
    op.drop_table('action_plan_items')
    op.drop_table('action_plans')
