"""
AUVRA Action Plan API Endpoints

New action plan system that replaces the old scheduling endpoints.
Generates 4 personalized daily actions with AI-generated images.

Endpoints:
- GET /assignments/today - Get or generate today's action plan
- GET /assignments/{date} - Get action plan for specific date
- POST /feedback - Submit like/dislike feedback
- POST /replace - Replace a disliked action
- POST /complete - Mark action as completed
"""

import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, update

from app.core.database import get_db
from app.core.security import get_current_user
from app.services.action_plan_generator import get_action_plan_generator
from app.models.action_plan_models import (
    ActionPlanResponse,
    ActionPlanFeedbackRequest,
    ActionReplacementRequest,
    ActionCompletionRequest,
    PlanSatisfactionRequest,
    BatchReplacementRequest,
    FeedbackResponse,
    ReplacementResponse,
    CompletionResponse,
    PlanSatisfactionResponse,
    BatchReplacementResponse,
    LegacyAssignmentResponse,
    LegacyAssignmentInfo,
    VariantInfo
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Minimum time before user can replace an action (in seconds)
FEEDBACK_MINIMUM_TIME = 30


@router.get("/assignments/today")
async def get_today_assignments(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    format: str = Query("legacy", description="Response format: 'legacy' or 'new'"),
    timezone: Optional[str] = Query(None, description="User's current local timezone (IANA format)")
):
    """
    Get today's action plan (or generate if doesn't exist).
    
    This endpoint replaces the old scheduling system completely.
    On first call of the day, generates:
    - 4 personalized actions (2 for primary hormone, 2 for secondary)
    - 16 AI-generated images (hero + 3 variants per action)
    - Research citations for each action
    
    Returns:
        Legacy format (default): Compatible with existing mobile app
        New format: Full action plan with all features
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Get user profile
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        
        # If timezone provided, update profile and use it
        if timezone and user_profile:
            if user_profile.current_timezone != timezone:
                logger.info(f"Updating timezone for user {uid}: {user_profile.current_timezone} -> {timezone}")
                user_profile.current_timezone = timezone
                db.commit()
        
        user_timezone = timezone or (user_profile.current_timezone if user_profile else "Asia/Seoul")
        
        # Get async session for generator
        async_db = await get_async_db_session()
        
        # Get or generate action plan
        generator = get_action_plan_generator()
        result = await generator.get_or_generate_today_plan(
            user_id=uid,
            user_timezone=user_timezone,
            db=async_db
        )
        
        await async_db.close()
        
        if not result.get("success"):
            raise HTTPException(
                status_code=500, 
                detail=result.get("error", "Failed to generate action plan")
            )
        
        logger.info(f"Action plan retrieved: uid={uid}, plan_id={result.get('plan_id')}")
        
        # Get weekly check-in status for dynamic card
        weekly_checkin_status = None
        try:
            from app.services.weekly_checkin_service import WeeklyCheckInService
            checkin_service = WeeklyCheckInService(db)
            weekly_checkin_status = checkin_service.get_checkin_status(uid)
        except Exception as e:
            logger.warning(f"Failed to get weekly check-in status: {e}")
        
        # Return in requested format
        if format == "legacy":
            return _convert_to_legacy_format(result, weekly_checkin_status)
        else:
            result["weekly_checkin"] = weekly_checkin_status
            return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get today's assignments: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get action plan")


@router.get("/assignments/{target_date}")
async def get_assignments_for_date(
    target_date: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    format: str = Query("legacy", description="Response format: 'legacy' or 'new'")
):
    """
    Get action plan for a specific date.
    
    Unlike today's endpoint, this does NOT generate a new plan.
    Only returns existing plans or empty response.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Parse date
        try:
            parsed_date = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        # Check if plan exists
        from app.core.database import ActionPlan, ActionPlanItem, ActionPlanItemVariant
        
        plan = db.query(ActionPlan).filter(
            and_(ActionPlan.uid == uid, ActionPlan.plan_date == parsed_date)
        ).first()
        
        if not plan:
            # Return empty response
            if format == "legacy":
                return {
                    "date": target_date,
                    "assignments": {
                        "morning": [],
                        "afternoon": [],
                        "evening": [],
                        "completed": []
                    },
                    "total_assignments": 0,
                    "completed_assignments": 0,
                    "completion_rate": 0.0,
                    "hormone_stats": {},
                    "generation_source": "action_plan"
                }
            else:
                return {
                    "success": True,
                    "plan_id": None,
                    "plan_date": target_date,
                    "actions": [],
                    "total_actions": 0,
                    "completed_actions": 0
                }
        
        # Build response from existing plan
        result = _build_plan_response(plan, db)
        
        if format == "legacy":
            return _convert_to_legacy_format(result)
        else:
            return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get assignments for date: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get action plan")


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    request: ActionPlanFeedbackRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Submit feedback for an action.
    
    Supports both home screen (30-sec modal) and ActionDetailScreen feedback.
    
    Feedback types:
    - 'like' / 'dislike': Home screen modal
    - 'loved' / 'completed' / 'skipped' / 'not_for_me': ActionDetailScreen
    
    For 'dislike' or 'not_for_me', user can request replacement after 30 seconds.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        valid_types = ["like", "dislike", "loved", "completed", "skipped", "not_for_me"]
        if request.feedback_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"Invalid feedback type. Must be one of: {valid_types}")
        
        # Get async session
        async_db = await get_async_db_session()
        
        generator = get_action_plan_generator()
        result = await generator.record_feedback(
            user_id=uid,
            item_id=request.item_id,
            feedback_type=request.feedback_type,
            time_shown=request.time_shown,
            feedback_text=request.feedback_text,  # NEW: Text feedback
            feedback_source=request.feedback_source,  # NEW: Source (home or detail)
            db=async_db
        )
        
        await async_db.close()
        
        if not result.get("success"):
            raise HTTPException(
                status_code=404,
                detail=result.get("error", "Failed to record feedback")
            )
        
        # Check if enough time has passed for replacement
        time_elapsed = result.get("time_to_feedback_seconds", 0)
        can_replace_types = ["dislike", "not_for_me"]
        can_replace = time_elapsed >= FEEDBACK_MINIMUM_TIME and request.feedback_type in can_replace_types
        
        return FeedbackResponse(
            success=True,
            feedback_id=result.get("feedback_id"),
            time_to_feedback_seconds=time_elapsed,
            can_replace=can_replace
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to submit feedback")


@router.post("/replace", response_model=ReplacementResponse)
async def replace_action(
    request: ActionReplacementRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Replace a disliked action with a new one.
    
    Requirements:
    - User must have given 'dislike' feedback first
    - At least 30 seconds must have passed since action was shown
    - Replacement will target the SAME hormone (but can be different category)
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Check refresh limit (2x Plan Refresh reward gives 2/day, default is 1/day)
        from app.services.reward_service import RewardService
        reward_service = RewardService(db)
        refresh_status = reward_service.get_refresh_status(uid)
        
        if not refresh_status["can_refresh"]:
            raise HTTPException(
                status_code=429,
                detail=f"Daily refresh limit reached ({refresh_status['limit']}/day). Try again tomorrow!"
            )
        
        # Get async session
        async_db = await get_async_db_session()
        
        generator = get_action_plan_generator()
        result = await generator.replace_action(
            user_id=uid,
            item_id=request.item_id,
            reason=request.reason,
            db=async_db
        )
        
        await async_db.close()
        
        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Failed to replace action")
            )
        
        # Consume a refresh on success
        refresh_result = reward_service.use_refresh(uid)
        
        return ReplacementResponse(
            success=True,
            original_id=result.get("original_id"),
            replacement_id=result.get("replacement_id"),
            replacement_action=result.get("replacement_action")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to replace action: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to replace action")


@router.post("/assignments/{item_id}/complete", response_model=CompletionResponse)
async def complete_action(
    item_id: int,
    request: ActionCompletionRequest = None,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mark an action as completed.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        from app.core.database import ActionPlanItem
        
        # Get the action
        item = db.query(ActionPlanItem).filter(
            and_(ActionPlanItem.id == item_id, ActionPlanItem.uid == uid)
        ).first()
        
        if not item:
            raise HTTPException(status_code=404, detail="Action not found")
        
        # Mark as completed
        item.is_completed = True
        item.completed_at = datetime.utcnow()
        db.commit()
        
        # Update user's streak data
        from app.services.streak_service import StreakService
        streak_service = StreakService(db)
        current_streak, longest_streak = streak_service.update_streak_on_completion(uid)
        
        logger.info(f"Action completed: item_id={item_id}, uid={uid}, streak={current_streak}")
        
        return CompletionResponse(
            success=True,
            item_id=item_id,
            completed_at=item.completed_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to complete action: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to complete action")


@router.post("/plan-satisfaction", response_model=PlanSatisfactionResponse)
async def submit_plan_satisfaction(
    request: PlanSatisfactionRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    30-Second Feedback Flow - Main endpoint.
    
    Triggered 30 seconds after action plan renders.
    Modal asks: "How does your action plan look today?"
    
    Options:
    - 'works_for_me' (👍) → Store all 4 actions as LIKED, close modal
    - 'want_to_change' (👎) → Replace specified items
    
    If want_to_change:
    - Frontend shows checkboxes for each action (only items NOT marked complete)
    - User selects items to replace
    - Backend stores selected items as DISLIKED
    - Calls GPT-4o-mini for replacements targeting SAME hormones
    - Generates images for new items
    - Updates action plan in database
    - Returns new actions for frontend refresh
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        if request.satisfaction not in ["works_for_me", "want_to_change"]:
            raise HTTPException(status_code=400, detail="Invalid satisfaction value. Use 'works_for_me' or 'want_to_change'")
        
        from app.core.database import ActionPlan, ActionPlanItem, ActionPlanFeedback
        
        # Get the plan
        plan = db.query(ActionPlan).filter(
            and_(ActionPlan.id == request.plan_id, ActionPlan.uid == uid)
        ).first()
        
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        
        # Get all items in the plan
        items = db.query(ActionPlanItem).filter(
            ActionPlanItem.plan_id == request.plan_id
        ).all()
        
        if request.satisfaction == "works_for_me":
            # 👍 User likes the plan - mark ALL non-completed items as LIKED
            for item in items:
                if not item.is_completed:
                    # Check if feedback already exists for this action (prevent duplicates)
                    existing_feedback = db.query(ActionPlanFeedback).filter(
                        and_(
                            ActionPlanFeedback.uid == uid,
                            ActionPlanFeedback.action_title == item.title,
                            ActionPlanFeedback.feedback_type == "like"
                        )
                    ).first()
                    
                    if existing_feedback:
                        # Update timestamp instead of creating duplicate
                        existing_feedback.created_at = datetime.utcnow()
                        existing_feedback.plan_id = request.plan_id
                        existing_feedback.item_id = item.id
                        logger.info(f"Updated existing 'like' feedback for: {item.title}")
                    else:
                        # Record as liked in feedback table
                        feedback = ActionPlanFeedback(
                            uid=uid,
                            plan_id=request.plan_id,
                            item_id=item.id,
                            feedback_type="like",
                            action_title=item.title,
                            action_category=item.category,
                            target_hormone=item.target_hormone,
                            created_at=datetime.utcnow()
                        )
                        db.add(feedback)
                        logger.info(f"Created new 'like' feedback for: {item.title}")
            
            
            # Mark feedback as collected (feedback_collected column exists on ActionPlan model)
            plan.feedback_collected = True
            
            db.commit()
            
            logger.info(f"Plan satisfaction: ALL LIKED, plan_id={request.plan_id}, uid={uid}")
            
            return PlanSatisfactionResponse(
                success=True,
                message="Great! We'll keep creating similar plans for you! 💜",
                will_adjust_future_plans=False
            )
        
        else:
            # 👎 User wants to change some items
            items_to_replace = request.items_to_replace or []
            
            if not items_to_replace:
                raise HTTPException(status_code=400, detail="Please specify items_to_replace when satisfaction='want_to_change'")
            
            # Check refresh limit (2x Plan Refresh reward gives 2/day, default is 1/day)
            from app.services.reward_service import RewardService
            reward_service = RewardService(db)
            refresh_status = reward_service.get_refresh_status(uid)
            
            if not refresh_status["can_refresh"]:
                raise HTTPException(
                    status_code=429,
                    detail=f"Daily refresh limit reached ({refresh_status['limit']}/day). Try again tomorrow!"
                )
            
            # Mark selected items as DISLIKED
            for item in items:
                if item.id in items_to_replace and not item.is_completed:
                    # Check for ANY existing feedback (could be "like" or "dislike")
                    existing_feedback = db.query(ActionPlanFeedback).filter(
                        and_(
                            ActionPlanFeedback.uid == uid,
                            ActionPlanFeedback.action_title == item.title
                        )
                    ).first()
                    
                    if existing_feedback:
                        # Update existing feedback to "dislike" (might change from "like")
                        existing_feedback.feedback_type = "dislike"
                        existing_feedback.created_at = datetime.utcnow()
                        existing_feedback.plan_id = request.plan_id
                        existing_feedback.item_id = item.id
                        existing_feedback.replacement_reason = request.feedback_text
                        logger.info(f"Updated feedback to 'dislike' for: {item.title}")
                    else:
                        # No previous feedback - create new dislike
                        feedback = ActionPlanFeedback(
                            uid=uid,
                            plan_id=request.plan_id,
                            item_id=item.id,
                            feedback_type="dislike",
                            action_title=item.title,
                            action_category=item.category,
                            target_hormone=item.target_hormone,
                            replacement_reason=request.feedback_text,
                            created_at=datetime.utcnow()
                        )
                        db.add(feedback)
                        logger.info(f"Created new 'dislike' feedback for: {item.title}")
                # NOTE: Non-selected items are intentionally NOT updated here
                # They should already have "like" feedback from previous "works_for_me"
                # This prevents duplicate feedback records
            
            db.commit()
            
            # Now generate replacements via async
            async_db = await get_async_db_session()
            
            generator = get_action_plan_generator()
            replacement_result = await generator.batch_replace_actions(
                user_id=uid,
                plan_id=request.plan_id,
                item_ids=items_to_replace,
                db=async_db
            )
            
            await async_db.close()
            
            if not replacement_result.get("success"):
                raise HTTPException(
                    status_code=500,
                    detail=replacement_result.get("error", "Failed to generate replacements")
                )
            
            
            # Mark feedback as collected
            plan.feedback_collected = True
            
            # Use 1 refresh (counts as single refresh regardless of replaced count)
            reward_service.use_refresh(uid)
            
            db.commit()
            
            logger.info(f"Plan satisfaction: REPLACED {len(items_to_replace)} items, plan_id={request.plan_id}, uid={uid}")
            
            return PlanSatisfactionResponse(
                success=True,
                message=f"Done! We've replaced {len(items_to_replace)} action(s) for you. 💜",
                will_adjust_future_plans=True,
                replaced_items=items_to_replace,
                new_actions=replacement_result.get("new_actions", [])
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit plan satisfaction: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit satisfaction")


@router.post("/batch-replace", response_model=BatchReplacementResponse)
async def batch_replace_actions(
    request: BatchReplacementRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Replace multiple actions at once.
    
    This is an alternative to the plan-satisfaction flow for direct batch replacement.
    Each replaced action targets the SAME hormone as the original.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        if not request.item_ids_to_replace:
            raise HTTPException(status_code=400, detail="No items specified for replacement")
        
        # Get async session
        async_db = await get_async_db_session()
        
        generator = get_action_plan_generator()
        result = await generator.batch_replace_actions(
            user_id=uid,
            plan_id=request.plan_id,
            item_ids=request.item_ids_to_replace,
            reasons=request.reasons,
            db=async_db
        )
        
        await async_db.close()
        
        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to replace actions")
            )
        
        return BatchReplacementResponse(
            success=True,
            replaced_count=result.get("replaced_count", 0),
            replacements=result.get("replacements", []),
            generation_cost=result.get("generation_cost")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to batch replace actions: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to replace actions")


@router.post("/refresh-all-incomplete")
async def refresh_all_incomplete_actions(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Refresh all incomplete actions for today.
    
    Uses the daily refresh limit (1 default, 2 with plan_refresh_2x reward).
    Counts as 1 refresh regardless of how many actions are replaced.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Check refresh limit
        from app.services.reward_service import RewardService
        reward_service = RewardService(db)
        refresh_status = reward_service.get_refresh_status(uid)
        
        if not refresh_status["can_refresh"]:
            raise HTTPException(
                status_code=429,
                detail=f"Daily refresh limit reached ({refresh_status['limit']}/day). Try again tomorrow!"
            )
        
        # Get today's incomplete items
        from app.core.database import ActionPlan, ActionPlanItem
        from datetime import date
        from app.utils.timezone_utils import get_user_current_date
        
        today = get_user_current_date(uid, db)
        plan = db.query(ActionPlan).filter(
            ActionPlan.uid == uid,
            ActionPlan.plan_date == today
        ).order_by(ActionPlan.created_at.desc()).first()
        
        if not plan:
            raise HTTPException(status_code=404, detail="No active action plan found")
        
        incomplete_items = db.query(ActionPlanItem).filter(
            ActionPlanItem.plan_id == plan.id,
            ActionPlanItem.is_completed == False,
            ActionPlanItem.is_replaced.isnot(True)  # Only active items, not already replaced
        ).all()
        
        if not incomplete_items:
            return {
                "success": True,
                "message": "No incomplete actions to refresh",
                "replaced_count": 0,
                "refresh_status": refresh_status
            }
        
        item_ids = [item.id for item in incomplete_items]
        
        # Get async session and regenerate
        async_db = await get_async_db_session()
        generator = get_action_plan_generator()
        
        result = await generator.batch_replace_actions(
            user_id=uid,
            plan_id=plan.id,
            item_ids=item_ids,
            reasons={item_id: "User requested refresh all" for item_id in item_ids},
            db=async_db
        )
        
        await async_db.close()
        
        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to refresh actions")
            )
        
        # Use 1 refresh (counts as single refresh regardless of count)
        reward_service.use_refresh(uid)
        
        # Get updated refresh status
        new_refresh_status = reward_service.get_refresh_status(uid)
        
        return {
            "success": True,
            "message": f"Refreshed {result.get('replaced_count', 0)} actions",
            "replaced_count": result.get("replaced_count", 0),
            "replacements": result.get("replacements", []),
            "refresh_status": new_refresh_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to refresh all incomplete: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to refresh actions")



# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def get_async_db_session() -> AsyncSession:
    """Get an async database session."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    import os
    
    # Get database URL and convert to async
    db_url = os.getenv("DATABASE_URL", "")
    
    # Handle different URL formats
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    return async_session()


def _build_plan_response(plan, db) -> dict:
    """Build response dictionary from plan object."""
    from app.core.database import ActionPlanItem, ActionPlanItemVariant
    
    items = db.query(ActionPlanItem).filter(
        and_(
            ActionPlanItem.plan_id == plan.id,
            ActionPlanItem.is_replaced.isnot(True)
        )
    ).order_by(ActionPlanItem.slot).all()
    

    actions = []
    completed_count = 0
    
    for item in items:
        variants = db.query(ActionPlanItemVariant).filter(
            ActionPlanItemVariant.item_id == item.id
        ).all()
        
        action_data = {
            "id": item.id,
            "slot": item.slot,
            "time_slot": item.time_slot,
            "category": item.category,
            "title": item.title,
            "specific_action": item.specific_action,
            "purpose": item.purpose,
            "target_hormone": item.target_hormone,
            "hormone_persona_intro": item.hormone_persona_intro,
            "hero_image_url": item.hero_image_url,
            "research_studies": item.research_studies or [],
            "conditions": item.conditions or [],
            "symptoms": item.symptoms or [],
            "is_completed": item.is_completed,
            "is_replaced": item.is_replaced,
            "variants": [
                {
                    "variant_type": v.variant_type,
                    "title": v.title,
                    "description": v.description,
                    "image_url": v.image_url
                }
                for v in variants
            ]
        }
        
        # Category-specific fields (case-insensitive + null safety)
        cat = (item.category or "").lower()
        if cat == "food":
            action_data["food_items"] = item.food_items or []
            action_data["food_amounts"] = item.food_amounts or []
        elif cat == "movement":
            action_data["exercise_types"] = item.exercise_types or []
            action_data["exercise_durations"] = item.exercise_durations or []
            action_data["exercise_intensities"] = item.exercise_intensities or []
        elif cat == "mindfulness":
            action_data["mindfulness_techniques"] = item.mindfulness_techniques or []
            action_data["mindfulness_durations"] = item.mindfulness_durations or []
        
        actions.append(action_data)
        
        # Debug: Log category-specific fields to verify they are being returned
        if item.category == "food":
            logger.info(f"API Response - Action {item.id} '{item.title}' [FOOD]: "
                       f"food_amounts={item.food_amounts}, food_items={item.food_items}")
        elif item.category == "movement":
            logger.info(f"API Response - Action {item.id} '{item.title}' [MOVEMENT]: "
                       f"exercise_durations={item.exercise_durations}")
        elif item.category == "mindfulness":
            logger.info(f"API Response - Action {item.id} '{item.title}' [MINDFULNESS]: "
                       f"mindfulness_durations={item.mindfulness_durations}")
        
        if item.is_completed:
            completed_count += 1
    
    return {
        "success": True,
        "plan_id": plan.id,
        "plan_date": plan.plan_date.isoformat(),
        "primary_hormone": plan.primary_hormone,
        "secondary_hormones": plan.secondary_hormones,
        "cycle_day": plan.cycle_day,
        "cycle_phase": plan.cycle_phase,
        "actions": actions,
        "total_actions": len(actions),
        "completed_actions": completed_count,
        "generation_cost": plan.generation_cost,
        "generation_time_ms": plan.generation_time_ms
    }


def _convert_to_legacy_format(result: dict, weekly_checkin_status: dict = None) -> dict:
    """
    Convert new action plan format to legacy assignment format.
    
    This ensures backward compatibility with existing mobile app.
    
    Args:
        result: The action plan result dict
        weekly_checkin_status: Optional check-in status to include
    """
    if not result.get("success"):
        return {
            "date": result.get("plan_date", ""),
            "assignments": {
                "morning": [],
                "afternoon": [],
                "evening": [],
                "completed": []
            },
            "total_assignments": 0,
            "completed_assignments": 0,
            "completion_rate": 0.0,
            "hormone_stats": {},
            "generation_source": "action_plan",
            "weekly_checkin": weekly_checkin_status
        }
    
    # Group actions by time slot
    assignments = {
        "morning": [],
        "afternoon": [],
        "evening": [],
        "completed": []
    }
    
    hormone_stats = {}
    
    for action in result.get("actions", []):
        # Convert to legacy format
        legacy_item = {
            "id": action["id"],
            "recommendation_id": action["id"],  # Same as id in new system
            "title": action["title"],
            "purpose": action.get("purpose"),
            "specific_action": action.get("specific_action"),
            "category": action["category"],
            "conditions": action.get("conditions", []),
            "symptoms": action.get("symptoms", []),
            "hormones": [action["target_hormone"]],
            "research_summary": None,
            "research_studies": action.get("research_studies", []),
            "is_completed": action.get("is_completed", False),
            "completed_at": None,
            "advices": [],
            
            # New fields (extension)
            "hero_image_url": action.get("hero_image_url"),
            "hormone_persona_intro": action.get("hormone_persona_intro"),
            "variants": action.get("variants", []),
            
            # Category-specific (Null safety for frontend)
            "food_amounts": action.get("food_amounts") or [],
            "food_items": action.get("food_items") or [],
            "exercise_durations": action.get("exercise_durations") or [],
            "exercise_types": action.get("exercise_types") or [],
            "exercise_intensities": action.get("exercise_intensities") or [],
            "mindfulness_durations": action.get("mindfulness_durations") or [],
            "mindfulness_techniques": action.get("mindfulness_techniques") or []
        }
        
        # Add to appropriate time slot
        if action.get("is_completed"):
            assignments["completed"].append(legacy_item)
        else:
            time_slot = action.get("time_slot", "morning")
            if time_slot in assignments:
                assignments[time_slot].append(legacy_item)
            else:
                assignments["morning"].append(legacy_item)
        
        # Track hormone stats
        hormone = action["target_hormone"]
        if hormone not in hormone_stats:
            hormone_stats[hormone] = {"total": 0, "completed": 0}
        hormone_stats[hormone]["total"] += 1
        if action.get("is_completed"):
            hormone_stats[hormone]["completed"] += 1
    
    total = len(result.get("actions", []))
    completed = sum(1 for a in result.get("actions", []) if a.get("is_completed"))
    
    return {
        "date": result.get("plan_date", ""),
        "assignments": assignments,
        "total_assignments": total,
        "completed_assignments": completed,
        "completion_rate": completed / total if total > 0 else 0.0,
        "hormone_stats": hormone_stats,
        "plan_id": result.get("plan_id"),
        "primary_hormone": result.get("primary_hormone"),
        "cycle_phase": result.get("cycle_phase"),
        "show_feedback_prompt_after_seconds": 30,  # 30-second feedback prompt
        "generation_source": "action_plan",
        "weekly_checkin": weekly_checkin_status  # Dynamic check-in card status
    }
