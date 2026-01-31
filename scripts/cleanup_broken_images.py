"""
Cleanup script for broken image cache entries.

This script:
1. Finds and deletes ImageLibrary records with non-Cloudinary URLs (expired RunPod URLs)
2. Clears hero_image_url on ActionPlanItem records that have non-Cloudinary URLs
   (so they get regenerated on next request)

Run with: python scripts/cleanup_broken_images.py
"""

import asyncio
import os
import sys

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, will use environment variables

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update, delete, and_, or_, not_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def cleanup_broken_images():
    """Clean up broken image cache entries."""
    
    # Get database URL from environment
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ DATABASE_URL environment variable not set")
        return
    
    # Convert to async URL if needed
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    print(f"🔗 Connecting to database...")
    
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Import models
            from app.core.database import ImageLibrary, ActionPlanItem, ActionPlanItemVariant
            
            # ========================================================
            # 1. Find and delete broken ImageLibrary records
            # ========================================================
            print("\n📊 Checking ImageLibrary table for broken URLs...")
            
            # Find records with non-Cloudinary URLs
            broken_query = select(ImageLibrary).where(
                and_(
                    ImageLibrary.image_url.isnot(None),
                    ImageLibrary.image_url != "",
                    not_(ImageLibrary.image_url.like("%res.cloudinary.com%"))
                )
            )
            
            result = await session.execute(broken_query)
            broken_records = result.scalars().all()
            
            print(f"   Found {len(broken_records)} broken ImageLibrary records")
            
            if broken_records:
                # Show some examples
                for record in broken_records[:5]:
                    print(f"   - ID {record.id}: {record.image_url[:60]}...")
                
                if len(broken_records) > 5:
                    print(f"   ... and {len(broken_records) - 5} more")
                
                # Delete broken records
                delete_query = delete(ImageLibrary).where(
                    and_(
                        ImageLibrary.image_url.isnot(None),
                        ImageLibrary.image_url != "",
                        not_(ImageLibrary.image_url.like("%res.cloudinary.com%"))
                    )
                )
                await session.execute(delete_query)
                print(f"   ✅ Deleted {len(broken_records)} broken ImageLibrary records")
            else:
                print("   ✅ No broken ImageLibrary records found")
            
            # ========================================================
            # 2. Find and clear broken ActionPlanItem hero_image_url
            # ========================================================
            print("\n📊 Checking ActionPlanItem table for broken hero URLs...")
            
            broken_items_query = select(ActionPlanItem).where(
                and_(
                    ActionPlanItem.hero_image_url.isnot(None),
                    ActionPlanItem.hero_image_url != "",
                    not_(ActionPlanItem.hero_image_url.like("%res.cloudinary.com%"))
                )
            )
            
            result = await session.execute(broken_items_query)
            broken_items = result.scalars().all()
            
            print(f"   Found {len(broken_items)} ActionPlanItem records with broken hero URLs")
            
            if broken_items:
                for item in broken_items[:5]:
                    print(f"   - ID {item.id}: '{item.title[:30]}...' -> {item.hero_image_url[:50] if item.hero_image_url else 'None'}...")
                
                if len(broken_items) > 5:
                    print(f"   ... and {len(broken_items) - 5} more")
                
                # Clear broken URLs (set to NULL so they get regenerated)
                update_query = update(ActionPlanItem).where(
                    and_(
                        ActionPlanItem.hero_image_url.isnot(None),
                        ActionPlanItem.hero_image_url != "",
                        not_(ActionPlanItem.hero_image_url.like("%res.cloudinary.com%"))
                    )
                ).values(hero_image_url=None)
                await session.execute(update_query)
                print(f"   ✅ Cleared {len(broken_items)} broken hero_image_url values")
            else:
                print("   ✅ No broken ActionPlanItem hero URLs found")
            
            # ========================================================
            # 3. Find and clear broken ActionPlanItemVariant image_url
            # ========================================================
            print("\n📊 Checking ActionPlanItemVariant table for broken URLs...")
            
            broken_variants_query = select(ActionPlanItemVariant).where(
                and_(
                    ActionPlanItemVariant.image_url.isnot(None),
                    ActionPlanItemVariant.image_url != "",
                    not_(ActionPlanItemVariant.image_url.like("%res.cloudinary.com%"))
                )
            )
            
            result = await session.execute(broken_variants_query)
            broken_variants = result.scalars().all()
            
            print(f"   Found {len(broken_variants)} ActionPlanItemVariant records with broken URLs")
            
            if broken_variants:
                for variant in broken_variants[:5]:
                    print(f"   - ID {variant.id}: '{variant.title[:30]}...' -> {variant.image_url[:50] if variant.image_url else 'None'}...")
                
                if len(broken_variants) > 5:
                    print(f"   ... and {len(broken_variants) - 5} more")
                
                # Clear broken URLs
                update_query = update(ActionPlanItemVariant).where(
                    and_(
                        ActionPlanItemVariant.image_url.isnot(None),
                        ActionPlanItemVariant.image_url != "",
                        not_(ActionPlanItemVariant.image_url.like("%res.cloudinary.com%"))
                    )
                ).values(image_url=None)
                await session.execute(update_query)
                print(f"   ✅ Cleared {len(broken_variants)} broken variant image_url values")
            else:
                print("   ✅ No broken ActionPlanItemVariant URLs found")
            
            # Commit all changes
            await session.commit()
            print("\n🎉 Cleanup complete! All changes committed.")
            
        except Exception as e:
            await session.rollback()
            print(f"\n❌ Error during cleanup: {e}")
            raise
        finally:
            await engine.dispose()


if __name__ == "__main__":
    print("=" * 60)
    print("🧹 Image Cache Cleanup Script")
    print("=" * 60)
    asyncio.run(cleanup_broken_images())
