"""create research_papers table

Revision ID: create_research_papers
Revises: fe16aed4dbbb
Create Date: 2024-12-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

# revision identifiers, used by Alembic.
revision = 'create_research_papers'
down_revision = 'fe16aed4dbbb'  # Points to lifestyle_focus migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create research_papers table
    op.create_table(
        'research_papers',
        sa.Column('id', sa.Integer(), nullable=False),
        
        # Paper Identity
        sa.Column('pmid', sa.String(20), nullable=True),
        sa.Column('doi', sa.String(100), nullable=True),
        
        # Paper Content
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('journal', sa.String(255), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('authors', sa.Text(), nullable=True),
        sa.Column('participants', sa.Integer(), nullable=True),
        sa.Column('finding', sa.Text(), nullable=True),
        sa.Column('abstract', sa.Text(), nullable=True),
        
        # Semantic Matching
        sa.Column('paper_embedding', JSONB, nullable=True),
        
        # Categorization
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('hormones', ARRAY(sa.String()), nullable=True),
        sa.Column('topics', ARRAY(sa.String()), nullable=True),
        
        # Quality & Curation
        sa.Column('quality_score', sa.Integer(), server_default='50'),
        sa.Column('verified', sa.Boolean(), server_default='false'),
        sa.Column('source', sa.String(50), server_default='pubmed'),
        
        # Usage Tracking
        sa.Column('usage_count', sa.Integer(), server_default='1'),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('NOW()')),
        sa.Column('last_used_at', sa.DateTime(), server_default=sa.text('NOW()')),
        
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('idx_research_papers_pmid', 'research_papers', ['pmid'], unique=True)
    op.create_index('idx_research_papers_category', 'research_papers', ['category'])
    op.create_index('idx_research_papers_quality', 'research_papers', ['quality_score'])
    op.create_index('idx_research_papers_usage', 'research_papers', ['usage_count'])
    
    # Migrate data from pubmed_cache to research_papers (if pubmed_cache exists)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'pubmed_cache' in inspector.get_table_names():
        op.execute("""
            INSERT INTO research_papers (pmid, title, journal, year, authors, participants, finding, source, usage_count, created_at, last_used_at)
            SELECT 
                pubmed_id,
                title,
                journal,
                year,
                authors,
                CASE 
                    WHEN participants ~ '^[0-9]+$' THEN CAST(participants AS INTEGER)
                    ELSE NULL
                END,
                finding,
                'pubmed',
                access_count,
                created_at,
                last_accessed_at
            FROM pubmed_cache
            WHERE pubmed_id IS NOT NULL
            ON CONFLICT (pmid) DO NOTHING
        """)


def downgrade() -> None:
    op.drop_index('idx_research_papers_usage', table_name='research_papers')
    op.drop_index('idx_research_papers_quality', table_name='research_papers')
    op.drop_index('idx_research_papers_category', table_name='research_papers')
    op.drop_index('idx_research_papers_pmid', table_name='research_papers')
    op.drop_table('research_papers')
