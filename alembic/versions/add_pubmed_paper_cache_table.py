"""add_pubmed_paper_cache_table

Revision ID: add_pubmed_cache_01
Revises: 
Create Date: 2024-12-23

Creates the pubmed_paper_cache table for storing real PubMed research papers.
This replaces GPT-hallucinated research citations with verified papers.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_pubmed_cache_01'
down_revision = None  # Will be set by Alembic based on current head
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create pubmed_paper_cache table."""
    op.create_table(
        'pubmed_paper_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pmid', sa.String(20), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('authors', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('journal', sa.String(500), nullable=False),
        sa.Column('publication_year', sa.Integer(), nullable=False),
        sa.Column('abstract', sa.Text(), nullable=True),
        sa.Column('participant_count', sa.Integer(), nullable=True),
        sa.Column('female_only', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('finding_summary', sa.Text(), nullable=True),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('target_hormones', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('search_keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('mesh_terms', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('relevance_score', sa.Integer(), server_default='50', nullable=True),
        sa.Column('doi', sa.String(200), nullable=True),
        sa.Column('pubmed_url', sa.String(500), nullable=True),
        sa.Column('use_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('fetch_date', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('idx_paper_cache_pmid', 'pubmed_paper_cache', ['pmid'], unique=True)
    op.create_index('idx_paper_cache_category', 'pubmed_paper_cache', ['category'])
    op.create_index('idx_paper_cache_year', 'pubmed_paper_cache', ['publication_year'])
    op.create_index('idx_paper_cache_relevance', 'pubmed_paper_cache', ['relevance_score'])
    
    # GIN indexes for JSONB columns
    op.create_index(
        'idx_paper_cache_hormones', 
        'pubmed_paper_cache', 
        ['target_hormones'],
        postgresql_using='gin'
    )
    op.create_index(
        'idx_paper_cache_keywords', 
        'pubmed_paper_cache', 
        ['search_keywords'],
        postgresql_using='gin'
    )


def downgrade() -> None:
    """Drop pubmed_paper_cache table."""
    op.drop_index('idx_paper_cache_keywords', table_name='pubmed_paper_cache')
    op.drop_index('idx_paper_cache_hormones', table_name='pubmed_paper_cache')
    op.drop_index('idx_paper_cache_relevance', table_name='pubmed_paper_cache')
    op.drop_index('idx_paper_cache_year', table_name='pubmed_paper_cache')
    op.drop_index('idx_paper_cache_category', table_name='pubmed_paper_cache')
    op.drop_index('idx_paper_cache_pmid', table_name='pubmed_paper_cache')
    op.drop_table('pubmed_paper_cache')
