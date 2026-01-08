import asyncio
import logging
import sys
import uuid
from datetime import date
from sqlalchemy import text

# Add project root to path
import os
sys.path.append(os.getcwd())

from app.core.database import SessionLocal, AsyncSessionLocal, ActionPlan, ActionPlanItem, QuestionSession
from app.services.action_plan_generator import ActionPlanGenerator
from app.services.question_service import QuestionService
from app.models.question_models import SessionDataCreate, SessionLinkRequest, UserProfileCreate, SessionData

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def verify_guest_flow():
    logger.info("🧪 STARTING GUEST FLOW VERIFICATION")
    
    # 1. Setup Data
    device_id = f"test_device_{uuid.uuid4()}"
    session_id = None
    user_uid = f"test_user_{uuid.uuid4()}"
    test_email = f"test_{uuid.uuid4()}@example.com"
    
    db = SessionLocal()
    
    try:
        # ------------------------------------------------------------------
        # STEP 1: Create Guest Session & Add Data
        # ------------------------------------------------------------------
        logger.info("\n--- STEP 1: Creating Guest Session ---")
        q_service = QuestionService(db)
        session_id = q_service.create_session(device_id)
        logger.info(f"✅ Created session: {session_id}")
        
        # Add minimal data required for generation
        session_data = SessionData(
            age=30,
            period_description="Regular",
            cycle_length="26-30 days",
            primary_goal="energy",
            lifestyle_focus=["food", "movement", "mindfulness"],
            diagnosed_conditions=["PCOS"], # Trigger PCOS logic
            top_concern="Fatigue"
        )
        q_service.save_session_data(session_id, session_data)
        logger.info("✅ Saved session data")

        # ------------------------------------------------------------------
        # STEP 2: Generate Full Action Plan (Guest Mode)
        # ------------------------------------------------------------------
        logger.info("\n--- STEP 2: Generating Full Action Plan (Guest Mode) ---")
        
        # We manually call what the background task calls
        from app.services.action_plan_generator import ActionPlanGenerator
        
        async with AsyncSessionLocal() as async_db:
            generator = ActionPlanGenerator() # sync db for init
            
            logger.info("🚀 Triggering generate_new_plan for guest...")
            response = await generator.generate_new_plan(
                user_id=None,
                plan_date=date.today(),
                user_timezone="UTC",
                db=async_db,
                image_mode="full", # We want to test image generation logic (mocked or real)
                session_id=session_id
            )
            
            if not response.get("success"):
                logger.error(f"❌ Generation failed: {response}")
                return
            
            plan = response
            logger.info(f"✅ Plan Generated! ID: {plan['plan_id']}")
            
            # Verify it's linked to session_id in DB
            from sqlalchemy import select
            result = await async_db.execute(select(ActionPlan).where(ActionPlan.id == plan['plan_id']))
            db_plan = result.scalar_one()
            
            if db_plan.session_id == session_id and db_plan.uid is None:
                logger.info(f"✅ Verified: Plan {db_plan.id} has session_id={session_id} and uid=None")
            else:
                logger.error(f"❌ Mismatch: session_id={db_plan.session_id}, uid={db_plan.uid}")
                return

        # ------------------------------------------------------------------
        # STEP 3: simulate User Signup & Link
        # ------------------------------------------------------------------
        logger.info("\n--- STEP 3: Linking Session to New User ---")
        
        # Mock user object for link_session_to_user
        current_user = {"uid": user_uid, "email": test_email}
        
        link_req = SessionLinkRequest(
            user_profile=UserProfileCreate(name="Test User", email=test_email),
            current_timezone="UTC",
            lifestyle_focus=["food"]
        )
        
        # Run linking logic
        logger.info(f"🔗 Linking session {session_id} to user {user_uid}...")
        
        # We need to run this in a way that allows it to use its internal session commit
        # The service commits to 'db', so we can reuse our 'db' session
        
        # Mock the run_in_threadpool if needed, or just call the sync method directly
        # link_session_to_user is synchronous (def, not async def) in QuestionService?
        # Let me check... QuestionService.link_session_to_user IS synchronous.
        # But in the endpoint it's called via run_in_threadpool.
        
        result = q_service.link_session_to_user(
            session_id,
            user_uid,
            "Test User",
            test_email,
            "UTC",
            ["food"]
        )
        
        if result:
            logger.info("✅ Link function returned True")
        else:
            logger.error("❌ Link function returned False")
            return

        # ------------------------------------------------------------------
        # STEP 4: Verification
        # ------------------------------------------------------------------
        logger.info("\n--- STEP 4: Final Verification ---")
        
        # Check ActionPlan ownership
        linked_plan = db.query(ActionPlan).filter(ActionPlan.uid == user_uid).first()
        
        if linked_plan:
            logger.info(f"✅ Found plan for user {user_uid}: ID={linked_plan.id}")
            
            if linked_plan.session_id is None:
                 logger.info("✅ Verified: Plan session_id is None (cleared)")
            else:
                 logger.warning(f"⚠️ Plan session_id is {linked_plan.session_id} (not cleared, but acceptable if logic kept it)")
                 
            # Check items
            items = db.query(ActionPlanItem).filter(ActionPlanItem.plan_id == linked_plan.id).all()
            if items and all(i.uid == user_uid for i in items):
                logger.info(f"✅ Verified: All {len(items)} items have uid={user_uid}")
            else:
                logger.error("❌ Some items do not have correct uid")
                
        else:
            logger.error(f"❌ No plan found for user {user_uid}!")

        # Verify session is deleted
        session = db.query(QuestionSession).filter(QuestionSession.session_id == session_id).first()
        if not session:
             logger.info("✅ Verified: Session data deleted")
        else:
             logger.error("❌ Session data still exists")
             
        # Cleanup
        if linked_plan:
            db.delete(linked_plan) # this should cascade items
            logger.info("🧹 Cleaned up test plan")
        db.commit()

    except Exception as e:
        logger.error(f"💥 Exception: {e}", exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(verify_guest_flow())
