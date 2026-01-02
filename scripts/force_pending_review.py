import asyncio
import logging
import sys
import os
from datetime import timedelta, datetime
from sqlalchemy import text

# Add parent directory to path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal, ActionPlan, ActionPlanItem, UserProfile
from app.utils.timezone_utils import get_user_current_date

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TARGET_UID = "NT8iTNpsSKfBAkQVXKmsyDxMcKv2"

def force_pending_review():
    db = SessionLocal()
    try:
        logger.info(f"Targeting user: {TARGET_UID}")
        
        # Verify user exists
        user = db.query(UserProfile).filter(UserProfile.uid == TARGET_UID).first()
        if not user:
            logger.error(f"User {TARGET_UID} not found!")
            return

        # Get dates
        today = get_user_current_date(TARGET_UID, db)
        yesterday = today - timedelta(days=1)
        
        logger.info(f"User Timezone: {user.current_timezone}")
        logger.info(f"Today (User Local): {today}")
        logger.info(f"Yesterday (User Local): {yesterday}")
        
        # Check for existing plan for yesterday
        existing_plan = db.query(ActionPlan).filter(
            ActionPlan.uid == TARGET_UID,
            ActionPlan.plan_date == yesterday
        ).first()
        
        if existing_plan:
            logger.info(f"Found existing plan for yesterday (ID: {existing_plan.id}).")
            if existing_plan.review_completed:
                logger.info("Plan was marked as reviewed. Resetting to unreviewed...")
                existing_plan.review_completed = False
                db.commit()
                logger.info("✅ Reset complete. Pending review should now appear.")
            else:
                logger.info("✅ Plan is already unreviewed. Pending review should be visible.")
            return

        # Create new plan for yesterday
        logger.info("No plan found for yesterday. Creating dummy plan...")
        
        new_plan = ActionPlan(
            uid=TARGET_UID,
            plan_date=yesterday,
            primary_hormone="insulin",
            secondary_hormones=["cortisol"],
            cycle_day=14,
            cycle_phase="Ovulation",
            lifestyle_focus=["eat", "move"],
            generation_cost="$0.00",
            generation_time_ms=1000,
            gpt_model_used="manual-override",
            is_regenerated=False,
            feedback_collected=False,
            review_completed=False  # CRITICAL: This triggers the review
        )
        
        db.add(new_plan)
        db.flush() # Get ID
        
        logger.info(f"Created plan ID: {new_plan.id}")
        
        # Add items
        items = [
            {
                "slot": 1, "time_slot": "morning", "category": "food", 
                "title": "Oatmeal with Berries", 
                "specific_action": "Eat a bowl of oatmeal with blueberries.",
                "target_hormone": "insulin",
                "is_completed": True # Completed
            },
            {
                "slot": 2, "time_slot": "afternoon", "category": "movement", 
                "title": "15 Min Walk", 
                "specific_action": "Take a brisk walk after lunch.",
                "target_hormone": "insulin",
                "is_completed": False # Incomplete (triggers review questions)
            },
            {
                "slot": 3, "time_slot": "evening", "category": "mindfulness", 
                "title": "Deep Breathing", 
                "specific_action": "Practice 4-7-8 breathing for 5 mins.",
                "target_hormone": "cortisol",
                "is_completed": False # Incomplete
            },
            {
                "slot": 4, "time_slot": "anytime", "category": "food", 
                "title": "Herbal Tea", 
                "specific_action": "Drink chamomile tea.",
                "target_hormone": "cortisol",
                "is_completed": True # Completed
            }
        ]
        
        for item_data in items:
            item = ActionPlanItem(
                plan_id=new_plan.id,
                uid=TARGET_UID,
                slot=item_data["slot"],
                time_slot=item_data["time_slot"],
                category=item_data["category"],
                title=item_data["title"],
                specific_action=item_data["specific_action"],
                target_hormone=item_data["target_hormone"],
                is_completed=item_data["is_completed"],
                completed_at=datetime.utcnow() if item_data["is_completed"] else None
            )
            db.add(item)
            
        db.commit()
        logger.info("✅ Successfully created dummy plan for yesterday with 4 items.")
        logger.info("Pending review should now appear in the app.")

    except Exception as e:
        logger.error(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    force_pending_review()
