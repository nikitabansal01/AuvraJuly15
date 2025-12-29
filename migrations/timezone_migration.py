"""
AUVRA Timezone Implementation - Database Migration Script

This migration ensures all existing users have a default timezone set.
For users without timezone data, we default to UTC (safe universal default).

IMPORTANT: This should be run once to initialize timezone data.
"""

from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable not set")
    exit(1)

# Create engine and session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def migrate_timezone_defaults():
    """
    Set default timezone for users who don't have one.
    
    Strategy:
    1. All existing users without timezone → UTC (safest default)
    2. Log any users updated for verification
    """
    db = SessionLocal()
    
    try:
        logger.info("Starting timezone migration...")
        
        # Update user profiles where current_timezone is NULL
        result = db.execute(
            text("""
                UPDATE user_profiles 
                SET current_timezone = 'UTC'
                WHERE current_timezone IS NULL
                RETURNING uid, name, email
            """)
        )
        
        updated_users = result.fetchall()
        
        if updated_users:
            logger.info(f"Updated {len(updated_users)} users to UTC timezone:")
            for user in updated_users[:10]:  # Show first 10
                logger.info(f"  - UID: {user.uid}, Name: {user.name}, Email: {user.email}")
            
            if len(updated_users) > 10:
                logger.info(f"  ... and {len(updated_users) - 10} more users")
        else:
            logger.info("No users needed timezone updates (all already have timezone set)")
        
        db.commit()
        logger.info("✅ Timezone migration completed successfully")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Timezone migration failed: {e}")
        db.rollback()
        return False
        
    finally:
        db.close()


def verify_timezone_coverage():
    """
    Verify that all users now have timezone set.
    """
    db = SessionLocal()
    
    try:
        # Count users without timezone
        result = db.execute(
            text("""
                SELECT COUNT(*) 
                FROM user_profiles 
                WHERE current_timezone IS NULL
            """)
        )
        
        null_count = result.scalar()
        
        if null_count == 0:
            logger.info("✅ All users have timezone set")
            return True
        else:
            logger.warning(f"⚠️  {null_count} users still have NULL timezone")
            return False
            
    except Exception as e:
        logger.error(f"❌ Verification failed: {e}")
        return False
        
    finally:
        db.close()


def add_timezone_indexes():
    """
    Add indexes for timezone-related queries.
    """
    db = SessionLocal()
    
    try:
        logger.info("Adding timezone-related indexes...")
        
        # Index on user_profiles.current_timezone for analytics
        db.execute(
            text("""
                CREATE INDEX IF NOT EXISTS idx_user_profiles_timezone 
                ON user_profiles(current_timezone)
            """)
        )
        
        db.commit()
        logger.info("✅ Indexes created successfully")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Index creation failed: {e}")
        db.rollback()
        return False
        
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("="*80)
    logger.info("AUVRA TIMEZONE MIGRATION")
    logger.info("="*80)
    
    # Step 1: Migrate timezone defaults
    if not migrate_timezone_defaults():
        logger.error("Migration failed, exiting")
        exit(1)
    
    # Step 2: Verify coverage
    if not verify_timezone_coverage():
        logger.error("Verification failed, exiting")
        exit(1)
    
    # Step 3: Add indexes
    if not add_timezone_indexes():
        logger.error("Index creation failed, exiting")
        exit(1)
    
    logger.info("="*80)
    logger.info("✅ TIMEZONE MIGRATION COMPLETED SUCCESSFULLY")
    logger.info("="*80)
    logger.info("")
    logger.info("Next steps:")
    logger.info("1. All users now have timezone set (defaulted to UTC)")
    logger.info("2. Users can update their timezone via /api/v1/timezone/update")
    logger.info("3. All date calculations now use user's timezone")
    logger.info("4. Streaks, action plans, and schedules are timezone-aware")
