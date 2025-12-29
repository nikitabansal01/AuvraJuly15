import sys
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import WeeklyCheckIn

def reset_checkin(uid="Sneha"):
    """Reset weekly check-in for a user."""
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        print(f"Resetting check-in for user: {uid}")
        
        # Delete all check-ins for this user
        # In a real scenario, we might want to just delete the incomplete one,
        # but for "start from first" testing, we'll clear recent ones or all.
        # Let's just delete the incomplete one and the most recent one if it was today.
        
        # Delete incomplete check-ins
        db.execute(
            text("DELETE FROM weekly_checkins WHERE uid = :uid AND is_complete = false"),
            {"uid": uid}
        )
        
        # Delete check-ins from today (in case they just finished one and want to restart)
        db.execute(
            text("DELETE FROM weekly_checkins WHERE uid = :uid AND check_in_date = CURRENT_DATE"),
            {"uid": uid}
        )
        
        db.commit()
        print("✅ Check-in reset successfully.")
        
    except Exception as e:
        print(f"❌ Error resetting check-in: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    # You can pass a UID as an argument, otherwise defaults to "Sneha" (from logs)
    uid = sys.argv[1] if len(sys.argv) > 1 else "Sneha"
    reset_checkin(uid)
