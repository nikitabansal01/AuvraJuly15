"""add text feedback fields to action plan feedback

Revision ID: add_text_feedback_fields
Revises: 
Create Date: 2025-12-26

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_text_feedback_fields'
down_revision = '223a06753539'  # Points to merge_migration_heads
branch_labels = None
depends_on = None


def upgrade():
    """Add new fields to action_plan_feedback table for text feedback."""
    
    # Add feedback_text column
    op.add_column('action_plan_feedback', 
        sa.Column('feedback_text', sa.Text(), nullable=True)
    )
    
    # Add replacement_category column
    op.add_column('action_plan_feedback', 
        sa.Column('replacement_category', sa.String(50), nullable=True)
    )
    
    # Add feedback_source column with default 'home'
    op.add_column('action_plan_feedback', 
        sa.Column('feedback_source', sa.String(20), server_default='home', nullable=True)
    )
    
    # Add index for feedback_source
    op.create_index('idx_feedback_source', 'action_plan_feedback', ['feedback_source'])


def downgrade():
    """Remove the text feedback fields."""
    
    op.drop_index('idx_feedback_source', table_name='action_plan_feedback')
    op.drop_column('action_plan_feedback', 'feedback_source')
    op.drop_column('action_plan_feedback', 'replacement_category')
    op.drop_column('action_plan_feedback', 'feedback_text')
