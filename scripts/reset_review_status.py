import asyncio
import logging
from sqlalchemy import text
from app.core.database import SessionLocal
from app.utils.timezone_utils import get_user_current_date
from datetime import timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def reset_review_status():
    db = SessionLocal()
    try:
        # Get a user ID to work with (from the most recent plan)
        user_query = text("SELECT uid FROM action_plans ORDER BY created_at DESC LIMIT 1")
        user_result = db.execute(user_query).first()
        
        if not user_result:
            logger.error("No users found with action plans.")
            return
            
        uid = user_result.uid
        today = get_user_current_date(uid, db)
        yesterday = today - timedelta(days=1)
        
        logger.info(f"Targeting user: {uid}")
        logger.info(f"Today: {today}, Yesterday: {yesterday}")
        
        # Check if there is a plan for yesterday
        yesterday_plan_query = text("""
            SELECT id, review_completed 
            FROM action_plans 
            WHERE uid = :uid AND plan_date = :yesterday
        """)
        yesterday_plan = db.execute(yesterday_plan_query, {"uid": uid, "yesterday": yesterday}).first()
        
        if yesterday_plan:
            logger.info(f"Found plan for yesterday (ID: {yesterday_plan.id}). Resetting review status...")
            
            # Reset review status
            update_query = text("""
                UPDATE action_plans 
                SET review_completed = false 
                WHERE id = :id
            """)
            db.execute(update_query, {"id": yesterday_plan.id})
            
            # Delete any existing review records
            delete_review = text("""
                DELETE FROM action_plan_daily_reviews 
                WHERE plan_id = :id
            """)
            db.execute(delete_review, {"id": yesterday_plan.id})
            
            db.commit()
            logger.info("Successfully reset review status! Restart the app to see the popup.")
            
        else:
            logger.info("No plan found for yesterday. Looking for ANY recent plan to move to yesterday...")
            
            # Find the most recent plan that is NOT today
            recent_plan_query = text("""
                SELECT id, plan_date 
                FROM action_plans 
                WHERE uid = :uid AND plan_date < :today
                ORDER BY plan_date DESC 
                LIMIT 1
            """)
            recent_plan = db.execute(recent_plan_query, {"uid": uid, "today": today}).first()
            
            if recent_plan:
                logger.info(f"Moving plan {recent_plan.id} (from {recent_plan.plan_date}) to yesterday ({yesterday})...")
                
                update_query = text("""
                    UPDATE action_plans 
                    SET plan_date = :yesterday, review_completed = false 
                    WHERE id = :id
                """)
                db.execute(update_query, {"yesterday": yesterday, "id": recent_plan.id})
                
                # Delete any existing review records
                delete_review = text("""
                    DELETE FROM action_plan_daily_reviews 
                    WHERE plan_id = :id
                """)
                db.execute(delete_review, {"id": recent_plan.id})
                
                db.commit()
                logger.info("Successfully moved plan and reset review status! Restart the app.")
            else:
                logger.warning("No suitable plan found to use for review testing.")

    except Exception as e:
        logger.error(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(reset_review_status())
