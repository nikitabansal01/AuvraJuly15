"""Fix image URL column sizes for base64 data URLs

Revision ID: fix_image_url_sizes
Revises: fe16aed4dbbb
Create Date: 2025-12-18 17:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fix_image_url_sizes'
down_revision = None  # This runs independently
branch_labels = None
depends_on = None


def upgrade():
    """
    Change image URL columns from VARCHAR(500) to TEXT to support base64 data URLs.
    Base64-encoded placeholder images are ~6000+ characters.
    """
    
    # Fix action_plan_items.hero_image_url
    op.execute("""
        ALTER TABLE action_plan_items 
        ALTER COLUMN hero_image_url TYPE TEXT
    """)
    
    # Fix action_plan_item_variants.image_url
    op.execute("""
        ALTER TABLE action_plan_item_variants 
        ALTER COLUMN image_url TYPE TEXT
    """)
    
    # Fix image_library.image_url
    op.execute("""
        ALTER TABLE image_library 
        ALTER COLUMN image_url TYPE TEXT
    """)
    
    print("✅ Successfully changed image URL columns to TEXT type")


def downgrade():
    """Revert back to VARCHAR(500) - WARNING: may truncate data!"""
    
    op.execute("""
        ALTER TABLE action_plan_items 
        ALTER COLUMN hero_image_url TYPE VARCHAR(500)
    """)
    
    op.execute("""
        ALTER TABLE action_plan_item_variants 
        ALTER COLUMN image_url TYPE VARCHAR(500)
    """)
    
    op.execute("""
        ALTER TABLE image_library 
        ALTER COLUMN image_url TYPE VARCHAR(500)
    """)
