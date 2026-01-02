"""
Cleanup fake plans and mark all old plans as reviewed.

This script marks all plans older than today as review_completed=True
so users don't get stuck in an endless loop of pending reviews.

Usage:
    python scripts/cleanup_fake_plans.py [user_uid]
    
If no user_uid is provided, it will clean up ALL users.
"""

import sys
import os
from datetime import date, datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def cleanup_fake_plans(uid: str = None):
    """Mark all old plans as reviewed and optionally delete fake items."""
    
    session = Session()
    
    try:
        today = date.today()
        print(f"📅 Today: {today}")
        
        # Build the query based on whether we're cleaning up a specific user or all
        if uid:
            print(f"🧹 Cleaning up plans for user: {uid}")
            
            # Count plans that need fixing
            result = session.execute(text("""
                SELECT COUNT(*) FROM action_plans 
                WHERE uid = :uid 
                AND plan_date < :today 
                AND review_completed = false
            """), {"uid": uid, "today": today})
            pending_count = result.scalar()
            
            print(f"📋 Found {pending_count} plans with pending reviews")
            
            if pending_count > 0:
                # Mark all old plans as reviewed
                session.execute(text("""
                    UPDATE action_plans 
                    SET review_completed = true 
                    WHERE uid = :uid 
                    AND plan_date < :today 
                    AND review_completed = false
                """), {"uid": uid, "today": today})
                
                session.commit()
                print(f"✅ Marked {pending_count} plans as reviewed")
            
            # Also show stats for this user
            result = session.execute(text("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN review_completed = true THEN 1 ELSE 0 END) as reviewed,
                    SUM(CASE WHEN review_completed = false THEN 1 ELSE 0 END) as pending
                FROM action_plans WHERE uid = :uid
            """), {"uid": uid})
            row = result.fetchone()
            print(f"\n📊 User stats: {row[0]} total plans, {row[1]} reviewed, {row[2]} pending")
            
        else:
            print("🧹 Cleaning up ALL users...")
            
            # Count all plans that need fixing
            result = session.execute(text("""
                SELECT COUNT(*) FROM action_plans 
                WHERE plan_date < :today 
                AND review_completed = false
            """), {"today": today})
            pending_count = result.scalar()
            
            print(f"📋 Found {pending_count} plans with pending reviews across all users")
            
            if pending_count > 0:
                # Mark all old plans as reviewed for all users
                session.execute(text("""
                    UPDATE action_plans 
                    SET review_completed = true 
                    WHERE plan_date < :today 
                    AND review_completed = false
                """), {"today": today})
                
                session.commit()
                print(f"✅ Marked {pending_count} plans as reviewed")
            
            # Show per-user breakdown
            result = session.execute(text("""
                SELECT 
                    uid,
                    COUNT(*) as total,
                    SUM(CASE WHEN plan_date < :today AND review_completed = false THEN 1 ELSE 0 END) as still_pending
                FROM action_plans 
                GROUP BY uid
                ORDER BY total DESC
                LIMIT 10
            """), {"today": today})
            
            print("\n📊 Top 10 users by plan count:")
            for row in result:
                print(f"   {row[0][:20]}... - {row[1]} plans, {row[2]} still pending")
        
        print("\n✅ Cleanup complete! Users should now be able to use the app normally.")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
        return False
    finally:
        session.close()


if __name__ == "__main__":
    uid = sys.argv[1] if len(sys.argv) > 1 else None
    
    if uid:
        print(f"🔧 Cleaning up fake plans for user: {uid}")
    else:
        print("🔧 Cleaning up fake plans for ALL users")
    
    print("=" * 50)
    
    success = cleanup_fake_plans(uid)
    
    if not success:
        sys.exit(1)
