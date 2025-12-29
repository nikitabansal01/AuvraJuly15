#!/usr/bin/env python3
"""
Fake a 30-day streak by creating action plans with completed items for past days.
The streak calculation looks at ActionPlanItem completions.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
from app.core.database import SessionLocal, ActionPlan, ActionPlanItem, UserStreakData
from sqlalchemy.orm.attributes import flag_modified

USER_UID = "QEAS0DwsVDfi4VvdulM6yvHWp3C3"

def fake_30_day_streak():
    db = SessionLocal()
    try:
        # Get the user's streak data
        streak_data = db.query(UserStreakData).filter(UserStreakData.uid == USER_UID).first()
        
        if not streak_data:
            print(f"❌ No streak data found for {USER_UID}")
            return
            
        print(f"Before: freeze_count={streak_data.freeze_count}, current={streak_data.current_streak}")
        
        # Create action plans for the past 30 days with completed items
        today = date(2025, 12, 29)
        
        for i in range(1, 31):  # 30 days back
            plan_date = today - timedelta(days=i)
            
            # Check if plan exists
            existing_plan = db.query(ActionPlan).filter(
                ActionPlan.uid == USER_UID,
                ActionPlan.plan_date == plan_date
            ).first()
            
            if existing_plan:
                # Mark all items in this plan as completed
                items = db.query(ActionPlanItem).filter(
                    ActionPlanItem.plan_id == existing_plan.id
                ).all()
                for item in items:
                    item.is_completed = True
                print(f"  ✅ Marked {len(items)} items completed for existing plan on {plan_date}")
            else:
                # Create a new fake plan with 4 completed items
                print(f"  Creating fake plan for {plan_date}")
                new_plan = ActionPlan(
                    uid=USER_UID,
                    plan_date=plan_date,
                    cycle_phase="follicular",
                    primary_hormone="estrogen",
                )
                db.add(new_plan)
                db.flush()  # Get the plan ID
                
                # Add 4 completed items
                for slot in range(1, 5):
                    item = ActionPlanItem(
                        plan_id=new_plan.id,
                        uid=USER_UID,
                        slot=slot,
                        time_slot=["morning", "afternoon", "evening", "anytime"][slot - 1],
                        category="food",
                        title=f"Fake Item {slot}",
                        specific_action="Fake action for testing",
                        target_hormone="estrogen",
                        is_completed=True,  # Already completed!
                    )
                    db.add(item)
                print(f"  ✅ Created plan with 4 completed items for {plan_date}")
        
        # Clear any frozen dates so the completions count
        streak_data.freeze_used_dates = []
        flag_modified(streak_data, 'freeze_used_dates')
        
        db.commit()
        
        # Verify
        db.refresh(streak_data)
        print(f"\n✅ Created/updated 30 days of completed plans!")
        print(f"After: freeze_count={streak_data.freeze_count}, frozen_dates cleared")
        print(f"\nRefresh the app to see streak = 30")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    fake_30_day_streak()
