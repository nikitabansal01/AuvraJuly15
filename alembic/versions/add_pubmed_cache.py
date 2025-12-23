"""add pubmed cache table

Revision ID: add_pubmed_cache
Revises: fix_image_url_sizes
Create Date: 2025-12-23

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_pubmed_cache'
down_revision = 'fix_image_url_sizes'  # Chain after the latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Create pubmed_cache table
    op.create_table(
        'pubmed_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(32), nullable=False),
        sa.Column('pubmed_id', sa.String(20), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('journal', sa.String(255), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('authors', sa.Text(), nullable=True),
        sa.Column('participants', sa.String(100), nullable=True),
        sa.Column('finding', sa.Text(), nullable=True),
        sa.Column('access_count', sa.Integer(), default=1),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_accessed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('ix_pubmed_cache_id', 'pubmed_cache', ['id'])
    op.create_index('ix_pubmed_cache_cache_key', 'pubmed_cache', ['cache_key'], unique=True)
    op.create_index('idx_pubmed_cache_key', 'pubmed_cache', ['cache_key'])
    op.create_index('idx_pubmed_cache_access', 'pubmed_cache', ['access_count'])


def downgrade():
    op.drop_index('idx_pubmed_cache_access', 'pubmed_cache')
    op.drop_index('idx_pubmed_cache_key', 'pubmed_cache')
    op.drop_index('ix_pubmed_cache_cache_key', 'pubmed_cache')
    op.drop_index('ix_pubmed_cache_id', 'pubmed_cache')
    op.drop_table('pubmed_cache')
