"""Debug script to trace get_missed_days logic for user"""
import os
import sys
sys.path.insert(0, os.getcwd())

from datetime import date, timedelta
from sqlalchemy import create_engine, and_, func
from sqlalchemy.orm import sessionmaker
from app.models.action_plan import ActionPlan, ActionPlanItem
from app.models.user_streak_data import UserStreakData
from zoneinfo import ZoneInfo

# Database connection
DATABASE_URL = "postgresql://postgres.dculqiokbqnwuhqpdret:HlsJUbre21mItNrw@aws-0-us-east-2.pooler.supabase.com:5432/postgres"
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

uid = "QEAS0DwsVDfi4VvdulM6yvHWp3C3"

# Get streak data
streak_data = db.query(UserStreakData).filter(UserStreakData.uid == uid).first()
print(f"=== STREAK DATA ===")
print(f"freeze_count: {streak_data.freeze_count if streak_data else 'N/A'}")
print(f"freeze_used_dates: {streak_data.freeze_used_dates if streak_data else 'N/A'}")
print(f"current_streak: {streak_data.current_streak if streak_data else 'N/A'}")

# Get user timezone from profile
from app.models.user_profile import UserProfile
profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
user_tz = profile.timezone if profile and profile.timezone else "UTC"
print(f"\nUser timezone: {user_tz}")

# Calculate today in user's timezone
from datetime import datetime
tz = ZoneInfo(user_tz)
now_utc = datetime.utcnow()
now_local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
today = now_local.date()
print(f"Today (user local): {today}")
print(f"Yesterday: {today - timedelta(days=1)}")

# Get frozen dates
frozen_dates = []
if streak_data and streak_data.freeze_used_dates:
    try:
        frozen_dates = [date.fromisoformat(d) for d in streak_data.freeze_used_dates if d]
    except:
        pass
print(f"Frozen dates: {frozen_dates}")

# Now trace through get_missed_days logic
print(f"\n=== TRACING get_missed_days ===")
missed_days = []
check_date = today - timedelta(days=1)  # Start from yesterday
days_checked = 0

while True:
    days_checked += 1
    print(f"\n--- Checking {check_date} (day {days_checked}) ---")
    
    # Get the plan for this date
    plan = db.query(ActionPlan).filter(
        and_(
            ActionPlan.uid == uid,
            ActionPlan.plan_date == check_date
        )
    ).first()
    
    # Check if this date is already frozen
    is_frozen = check_date in frozen_dates
    print(f"  Plan exists: {plan is not None}")
    print(f"  Is frozen: {is_frozen}")
    
    if plan:
        # Count total items in this plan (excluding replaced)
        total_items = db.query(func.count(ActionPlanItem.id)).filter(
            and_(
                ActionPlanItem.plan_id == plan.id,
                ActionPlanItem.is_replaced.isnot(True)
            )
        ).scalar() or 0
        
        # Count completed items
        completed_count = db.query(func.count(ActionPlanItem.id)).filter(
            and_(
                ActionPlanItem.plan_id == plan.id,
                ActionPlanItem.is_completed == True,
                ActionPlanItem.is_replaced.isnot(True)
            )
        ).scalar() or 0
        
        print(f"  Total items: {total_items}")
        print(f"  Completed: {completed_count}")
        
        # Fully completed = stop, not missed (streak is intact from here back)
        if total_items > 0 and completed_count == total_items:
            print(f"  -> FULLY COMPLETED - STOP")
            break
        
        # Frozen day = not missed, but continue checking for older missed days
        elif is_frozen:
            print(f"  -> FROZEN - skip, continue checking older days")
            check_date -= timedelta(days=1)
            continue
        
        # NOT frozen and NOT complete = missed
        else:
            print(f"  -> MISSED (not complete, not frozen)")
            missed_days.append(check_date)
            check_date -= timedelta(days=1)
    else:
        # No plan for this date
        if is_frozen:
            print(f"  -> NO PLAN but FROZEN - skip")
            check_date -= timedelta(days=1)
        else:
            print(f"  -> NO PLAN and NOT frozen - MISSED")
            missed_days.append(check_date)
            check_date -= timedelta(days=1)
    
    # Safety limit - don't check more than 7 days total (not just missed days)
    if days_checked >= 7:
        print(f"  -> SAFETY LIMIT REACHED")
        break

print(f"\n=== RESULT ===")
print(f"Missed days: {missed_days}")
print(f"Count: {len(missed_days)}")

# Expected:
# - Dec 28: NOT frozen, 0/4 completed -> MISSED
# - Dec 27: FROZEN -> continue
# - Dec 26-22: NO PLAN, NOT frozen -> MISSED (but safety limit may kick in)

# Now test risk status calculation
print(f"\n=== RISK STATUS ===")
missed_days_count = len(missed_days)
freeze_count = streak_data.freeze_count if streak_data else 0
print(f"missed_days_count: {missed_days_count}")
print(f"freeze_count: {freeze_count}")

streak_at_risk = missed_days_count > 0
freezes_needed = missed_days_count
can_freeze = freeze_count >= freezes_needed and freezes_needed > 0

print(f"streak_at_risk: {streak_at_risk}")
print(f"can_freeze: {can_freeze}")
print(f"freezes_needed: {freezes_needed}")

if streak_at_risk and can_freeze:
    print(f"\n✅ ALERT SHOULD SHOW!")
else:
    print(f"\n❌ ALERT WILL NOT SHOW!")
    if not streak_at_risk:
        print(f"   Reason: streak_at_risk is False (no missed days?)")
    if not can_freeze:
        print(f"   Reason: can_freeze is False (freeze_count={freeze_count} < freezes_needed={freezes_needed})")

db.close()
