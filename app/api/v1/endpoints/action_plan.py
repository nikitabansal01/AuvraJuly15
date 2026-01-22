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
import time
from datetime import date, datetime, timezone
from typing import Optional, List

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
    VariantInfo,
    # Daily Review models
    DailyReviewRequest,
    DailyReviewResponse,
    PendingReviewResponse,
    PendingReviewItemInfo,
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
    timezone: Optional[str] = Query(None, description="User's current local timezone (IANA format)"),
    image_mode: str = Query(
        "auto",
        description="Image generation mode: auto (default), full, hero_only, none",
    ),
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
    t0 = time.perf_counter()
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

        t_profile_ms = int((time.perf_counter() - t0) * 1000)
        
        # CRITICAL: Check for pending daily review BEFORE generating new plan
        # User must complete review of their LAST plan before getting today's plan
        # No time limit - even if user comes back after weeks, they must review their last plan
        from app.core.database import ActionPlan
        from app.utils.timezone_utils import get_user_current_date
        
        today_date = get_user_current_date(uid, db)

        # Timing metadata (new vs repeat user, and whether a plan already existed)
        plan_exists_for_today = (
            db.query(ActionPlan.id)
            .filter(and_(ActionPlan.uid == uid, ActionPlan.plan_date == today_date))
            .first()
            is not None
        )
        has_any_plan_ever = (
            db.query(ActionPlan.id)
            .filter(ActionPlan.uid == uid)
            .first()
            is not None
        )
        is_first_plan_for_user = not has_any_plan_ever
        
        # Find the LAST plan that hasn't been reviewed
        # Query automatically handles signup day: plan_date < today_date finds nothing on day 1
        total_plan_count = db.query(ActionPlan).filter(ActionPlan.uid == uid).count()
        pending_review = None

        if total_plan_count > 1:
            # CRITICAL FIX: Force absolutely fresh DB read using raw SQL
            # db.expire_all() isn't enough because Supabase pooler can return stale connections
            # Using raw SQL with text() bypasses SQLAlchemy's identity map AND forces fresh read
            from sqlalchemy import text
            
            raw_result = db.execute(
                text("""
                    SELECT id, plan_date, review_completed 
                    FROM action_plans 
                    WHERE uid = :uid AND plan_date < :today AND review_completed = false
                    ORDER BY plan_date DESC
                    LIMIT 1
                """),
                {"uid": uid, "today": today_date}
            ).fetchone()
            
            if raw_result:
                # Found pending plan via raw SQL, now get the ORM object
                pending_plan_id = raw_result[0]
                logger.info(f"[PENDING_CHECK] Raw SQL found pending plan id={pending_plan_id}, date={raw_result[1]}, review_completed={raw_result[2]}")
                
                # Force expire any cached version and fetch fresh
                db.expire_all()
                pending_review = db.query(ActionPlan).filter(
                    ActionPlan.id == pending_plan_id
                ).first()
                
                # Double-check the review status after ORM load
                if pending_review:
                    logger.info(f"[PENDING_CHECK] ORM loaded plan {pending_review.id}: review_completed={pending_review.review_completed}")
                    if pending_review.review_completed:
                        logger.info(f"[PENDING_CHECK] ORM says review_completed=True! Raw SQL was stale. Clearing pending_review.")
                        pending_review = None
            else:
                logger.info(f"[PENDING_CHECK] Raw SQL found no pending plans for {uid}")
        
        if pending_review:
            logger.info(f"Blocking plan generation for {uid} due to pending review for {pending_review.plan_date}")
            logger.info(
                "[TIMING] action_plan_today uid=%s blocked=pending_review total_ms=%s pending_review_date=%s",
                uid,
                int((time.perf_counter() - t0) * 1000),
                pending_review.plan_date,
            )
            # Return 428 Precondition Required to indicate review is needed
            raise HTTPException(
                status_code=428, 
                detail=f"Daily review pending for {pending_review.plan_date}. Please complete review first."
            )

        t_review_ms = int((time.perf_counter() - t0) * 1000)
        
        # Get async session for generator
        async_db = await get_async_db_session()

        # Decide image generation mode.
        # FORCE "full" mode to ensure 16 images (4 hero + 12 variants) are always generated
        # This overrides any client-side "hero_only" requests which cause user complaints
        effective_image_mode = "full" 

        if effective_image_mode not in {"full", "hero_only", "none"}:
            raise HTTPException(status_code=400, detail="Invalid image_mode. Use auto, full, hero_only, or none.")

        # Skip quality check for first-time users (signup flow) to reduce latency by ~10-15s
        skip_quality_check = is_first_plan_for_user

        # Get or generate action plan
        generator = get_action_plan_generator()
        t_gen_start = time.perf_counter()
        try:
            result = await generator.get_or_generate_today_plan(
                user_id=uid,
                user_timezone=user_timezone,
                db=async_db,
                image_mode=effective_image_mode,
                skip_quality_check=skip_quality_check,
            )
        finally:
            await async_db.close()

        t_generator_ms = int((time.perf_counter() - t_gen_start) * 1000)
        
        # ═══════════════════════════════════════════════════════════════════════════════════
        # OPTION 3+4 FIX: Handle "generating" status
        # If a session plan is being generated for this user, don't block or start duplicate.
        # Return a 202 Accepted with progress info so frontend can poll.
        # ═══════════════════════════════════════════════════════════════════════════════════
        if result.get("generating"):
            logger.info(f"[ACTION_PLAN_TODAY] Plan is being generated for {uid}, returning 202 with progress info")
            
            # Return 202 Accepted - indicates request accepted but not yet complete
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=202,
                content={
                    "success": True,
                    "generating": True,
                    "plan_exists": False,
                    "session_id": result.get("session_id"),
                    "processing_status": result.get("processing_status", "in_progress"),
                    "progress": result.get("progress", 0),
                    "phase": result.get("phase", "Generating"),
                    "elapsed_seconds": result.get("elapsed_seconds", 0),
                    "estimated_remaining_seconds": result.get("estimated_remaining_seconds", 180),
                    "message": result.get("message", "Your personalized plan is being generated. Please wait..."),
                    "plan_source": result.get("plan_source"),
                    "poll_endpoint": "/action-plan/assignments/today/status",
                    "poll_interval_ms": 3000,  # Suggest frontend poll every 3 seconds
                }
            )
        
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

        timings_ms = {
            "server_total_ms": int((time.perf_counter() - t0) * 1000),
            "server_profile_ms": t_profile_ms,
            "server_review_check_ms": max(0, t_review_ms - t_profile_ms),
            "server_generator_call_ms": t_generator_ms,
            "plan_generation_time_ms": result.get("generation_time_ms"),
            "plan_existed_before_call": plan_exists_for_today,
            "is_first_plan_for_user": is_first_plan_for_user,
            "plan_source": result.get("plan_source"),
        }

        logger.info(
            "[TIMING] action_plan_today uid=%s date=%s total_ms=%s generator_ms=%s plan_existed=%s first_plan=%s plan_generation_time_ms=%s",
            uid,
            result.get("plan_date"),
            timings_ms.get("server_total_ms"),
            timings_ms.get("server_generator_call_ms"),
            timings_ms.get("plan_existed_before_call"),
            timings_ms.get("is_first_plan_for_user"),
            timings_ms.get("plan_generation_time_ms"),
        )
        
        # Return in requested format
        if format == "legacy":
            legacy = _convert_to_legacy_format(result, weekly_checkin_status)
            legacy["timings_ms"] = timings_ms
            # Keep a top-level convenience field for quick inspection (backwards compatible)
            legacy["plan_generation_time_ms"] = result.get("generation_time_ms")
            legacy["plan_source"] = result.get("plan_source")
            return legacy
        else:
            result["weekly_checkin"] = weekly_checkin_status
            result["timings_ms"] = timings_ms
            result["plan_source"] = result.get("plan_source")
            return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get today's assignments: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get action plan")


@router.get("/assignments/today/status")
async def get_today_plan_status(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    timezone: Optional[str] = Query(None, description="User's current local timezone (IANA format)"),
):
    """
    Check if today's plan exists WITHOUT generating one.
    
    Use this endpoint for polling to check if a plan has been created,
    without triggering a new plan generation. This prevents race conditions
    where multiple generation requests could create duplicate plans.
    
    OPTION 3+4 ENHANCEMENT: Also checks for linked session generation in progress.
    
    Returns:
        - plan_exists: True if plan exists for today
        - generating: True if a session plan is being generated (poll again)
        - plan_id: ID of the plan (if exists)
        - plan_date: Date of the plan (if exists)
        - ready: True if plan exists and has assignments
        - total_assignments: Number of assignments (if exists)
        - progress: Generation progress if generating (0-100)
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Get user profile for timezone
        from app.core.database import UserProfile, ActionPlan, ActionPlanItem, QuestionSession, SessionProcessingStatus
        from app.utils.timezone_utils import get_user_current_date
        
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        
        # If timezone provided, update profile and use it
        if timezone and user_profile:
            if user_profile.current_timezone != timezone:
                logger.info(f"Updating timezone for user {uid}: {user_profile.current_timezone} -> {timezone}")
                user_profile.current_timezone = timezone
                db.commit()
        
        user_timezone = timezone or (user_profile.current_timezone if user_profile else "Asia/Seoul")
        today = get_user_current_date(uid, db)
        
        # Check if plan exists for today - DO NOT GENERATE
        plan = db.query(ActionPlan).filter(
            ActionPlan.uid == uid,
            ActionPlan.plan_date == today
        ).first()
        
        if plan:
            # Plan exists! Return it
            item_count = db.query(ActionPlanItem).filter(
                ActionPlanItem.plan_id == plan.id
            ).count()
            
            return {
                "plan_exists": True,
                "generating": False,
                "plan_id": plan.id,
                "plan_date": str(plan.plan_date),
                "ready": item_count > 0,
                "total_assignments": item_count,
                "cycle_phase": plan.cycle_phase,
                "primary_hormone": plan.primary_hormone
            }
        
        # ═══════════════════════════════════════════════════════════════════════════════════
        # OPTION 3+4 ENHANCEMENT: Check if a linked session is generating for this user
        # This lets frontend show progress while waiting for session completion
        # ═══════════════════════════════════════════════════════════════════════════════════
        linked_session = db.query(QuestionSession).filter(
            QuestionSession.status == f"linked:{uid}"
        ).order_by(QuestionSession.created_at.desc()).first()
        
        if linked_session:
            # Check if session is still being processed
            processing = db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == linked_session.session_id
            ).first()
            
            if processing and processing.processing_status in ["queued", "in_progress"]:
                # Session is still generating - return progress info
                elapsed = 0
                if processing.started_at:
                    elapsed = (datetime.utcnow() - processing.started_at).total_seconds()
                
                progress = processing.progress or 0
                estimated_total = 180  # ~3 minutes typical
                if progress > 0:
                    estimated_total = (elapsed / progress) * 100
                estimated_remaining = max(0, estimated_total - elapsed)
                
                logger.info(f"[STATUS] Session {linked_session.session_id} generating for {uid}: {progress}% complete")
                
                return {
                    "plan_exists": False,
                    "generating": True,  # KEY FLAG
                    "plan_id": None,
                    "plan_date": str(today),
                    "ready": False,
                    "total_assignments": 0,
                    "session_id": linked_session.session_id,
                    "processing_status": processing.processing_status,
                    "progress": progress,
                    "phase": processing.phase or "Generating",
                    "elapsed_seconds": int(elapsed),
                    "estimated_remaining_seconds": int(estimated_remaining),
                    "message": f"Your personalized plan is {progress}% complete..."
                }
            
            # ═══════════════════════════════════════════════════════════════════════════════════
            # CRITICAL FIX: If processing just completed, the plan might not be visible yet due to
            # transaction timing. Re-check for plan with a fresh query and commit.
            # This fixes the race condition where processing=completed but plan query returns None.
            # ═══════════════════════════════════════════════════════════════════════════════════
            if processing and processing.processing_status == "completed":
                # Force a fresh read from database (flush any pending transactions)
                db.expire_all()
                
                # Re-check for plan - it should exist now after auto-transfer
                plan_recheck = db.query(ActionPlan).filter(
                    ActionPlan.uid == uid,
                    ActionPlan.plan_date == today
                ).first()
                
                if plan_recheck:
                    logger.info(f"[STATUS] Plan {plan_recheck.id} found after session completion for {uid}")
                    item_count = db.query(ActionPlanItem).filter(
                        ActionPlanItem.plan_id == plan_recheck.id
                    ).count()
                    
                    return {
                        "plan_exists": True,
                        "generating": False,
                        "plan_id": plan_recheck.id,
                        "plan_date": str(plan_recheck.plan_date),
                        "ready": item_count > 0,
                        "total_assignments": item_count,
                        "cycle_phase": plan_recheck.cycle_phase,
                        "primary_hormone": plan_recheck.primary_hormone
                    }
                else:
                    # Processing completed but plan not found - this is unexpected
                    # Log warning and return "still generating" to keep frontend polling
                    logger.warning(f"[STATUS] Processing completed for session {linked_session.session_id} but no plan found for {uid}. Waiting...")
                    return {
                        "plan_exists": False,
                        "generating": True,  # Keep polling - plan transfer might be in progress
                        "plan_id": None,
                        "plan_date": str(today),
                        "ready": False,
                        "total_assignments": 0,
                        "session_id": linked_session.session_id,
                        "processing_status": "completing",
                        "progress": 95,
                        "phase": "Finalizing",
                        "elapsed_seconds": 0,
                        "estimated_remaining_seconds": 10,
                        "message": "Finalizing your personalized plan..."
                    }
        
        # No plan and no active session - plan doesn't exist
        return {
            "plan_exists": False,
            "generating": False,
            "plan_id": None,
            "plan_date": str(today),
            "ready": False,
            "total_assignments": 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to check plan status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check plan status")


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
            
            # Log result for debugging
            logger.info(f"[PLAN-SATISFACTION] batch_replace_actions returned: success={replacement_result.get('success')}, replaced_count={replacement_result.get('replaced_count', 0)}")
            
            if not replacement_result.get("success"):
                logger.error(f"[PLAN-SATISFACTION] Replacement failed: {replacement_result.get('error')}")
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
        
        # Log result for debugging
        logger.info(f"[REFRESH-ALL] batch_replace_actions returned: success={result.get('success')}, replaced_count={result.get('replaced_count', 0)}")
        
        if not result.get("success"):
            logger.error(f"[REFRESH-ALL] Replacement failed: {result.get('error')}")
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
# DAILY REVIEW ENDPOINTS - Next-day action plan review flow
# ============================================================================

@router.get("/pending-review", response_model=PendingReviewResponse)
async def get_pending_review(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Check if user has a pending daily review for yesterday's (or earlier) action plan.
    
    Returns pending review data if:
    1. There's a plan from yesterday (or earlier) that hasn't been reviewed
    2. That plan has at least one incomplete item
    
    Also handles frozen days - still offers review for GPT learning but no streak penalty.
    
    CRITICAL: Skip review for user's FIRST EVER plan (new signup protection).
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        from app.core.database import ActionPlan, ActionPlanItem, UserStreakData
        from app.utils.timezone_utils import get_user_current_date
        from datetime import timedelta
        
        today = get_user_current_date(uid, db)
        
        # CRITICAL FIX: Check how many plans this user has
        # If user only has ONE plan ever, skip review (it's their first day / signup)
        total_plan_count = db.query(ActionPlan).filter(ActionPlan.uid == uid).count()
        
        if total_plan_count <= 1:
            logger.info(f"Skipping review for {uid} - user only has {total_plan_count} plan(s) (new user protection)")
            return PendingReviewResponse(needs_review=False)
        
        # CRITICAL FIX: Use raw SQL to absolutely ensure fresh DB read
        # db.expire_all() isn't enough because Supabase pooler can return stale connections
        from sqlalchemy import text
        
        raw_result = db.execute(
            text("""
                SELECT id, plan_date, review_completed 
                FROM action_plans 
                WHERE uid = :uid AND plan_date < :today AND review_completed = false
                ORDER BY plan_date DESC
                LIMIT 1
            """),
            {"uid": uid, "today": today}
        ).fetchone()
        
        if not raw_result:
            logger.info(f"[PENDING_REVIEW] Raw SQL found no pending plans for {uid}")
            return PendingReviewResponse(needs_review=False)
        
        pending_plan_id = raw_result[0]
        logger.info(f"[PENDING_REVIEW] Raw SQL found pending plan id={pending_plan_id}, date={raw_result[1]}, review_completed={raw_result[2]}")
        
        # Expire cache and get ORM object
        db.expire_all()
        plan_needing_review = db.query(ActionPlan).filter(
            ActionPlan.id == pending_plan_id
        ).first()
        
        if not plan_needing_review:
            logger.error(f"[PENDING_REVIEW] ORM could not find plan {pending_plan_id} that raw SQL found!")
            return PendingReviewResponse(needs_review=False)
        
        # Double-check: if ORM shows review_completed=True, raw SQL was stale
        if plan_needing_review.review_completed:
            logger.info(f"[PENDING_REVIEW] ORM shows review_completed=True for plan {pending_plan_id}. Raw SQL was stale. No review needed.")
            return PendingReviewResponse(needs_review=False)
        
        if not plan_needing_review:
            return PendingReviewResponse(needs_review=False)
        
        # Get only active items (exclude replaced ones)
        # Users should only review actions they actually had, not replaced ones
        items = db.query(ActionPlanItem).filter(
            ActionPlanItem.plan_id == plan_needing_review.id,
            ActionPlanItem.is_replaced != True  # noqa: E712
        ).order_by(ActionPlanItem.slot).all()
        
        if not items:
            # No items means plan was empty - mark as reviewed and skip
            plan_needing_review.review_completed = True
            db.commit()
            return PendingReviewResponse(needs_review=False)
        
        # Count completion status (treat all returned items as reviewable actions)
        total_items = len(items)
        completed_count = sum(1 for item in items if item.is_completed)
        incomplete_count = total_items - completed_count
        
        # NOTE: We now ALWAYS show review, even if all items are completed
        # This allows users to:
        # 1. Confirm they actually completed items (not just marked by mistake)
        # 2. Change status if they marked complete but actually skipped
        # 3. Provide additional feedback for GPT learning
        # The review modal handles both completed and incomplete items
        
        # Check if this day was already frozen
        streak_data = db.query(UserStreakData).filter(UserStreakData.uid == uid).first()
        was_frozen = False
        if streak_data and streak_data.freeze_used_dates:
            was_frozen = plan_needing_review.plan_date.isoformat() in streak_data.freeze_used_dates
        
        # Get streak status
        from app.services.streak_service import StreakService
        streak_service = StreakService(db)
        streak_status = streak_service.get_full_streak_status(uid)
        
        # Build item info list (all items including replaced ones)
        item_infos = [
            PendingReviewItemInfo(
                id=item.id,
                title=item.title,
                category=item.category,
                time_slot=item.time_slot,
                target_hormone=item.target_hormone,
                is_completed=item.is_completed,
                is_replaced=item.is_replaced or False,
                hero_image_url=item.hero_image_url
            )
            for item in items
        ]
        
        return PendingReviewResponse(
            needs_review=True,
            review_date=plan_needing_review.plan_date.isoformat(),
            plan_id=plan_needing_review.id,
            items=item_infos,
            total_items=total_items,
            completed_count=completed_count,
            incomplete_count=incomplete_count,
            streak_at_risk=streak_status.get("streak_at_risk", False) and not was_frozen,
            freezes_available=streak_status.get("freeze_count", 0),
            was_frozen=was_frozen
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get pending review: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check pending review")


@router.post("/submit-daily-review", response_model=DailyReviewResponse)
async def submit_daily_review(
    request: DailyReviewRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Submit the daily review for a previous day's action plan.
    
    For each item, user specifies:
    - 'forgot_to_mark': Actually completed, forgot to mark -> mark as completed
    - 'replaced': Did something else instead -> mark as completed with replacement info
    - 'skipped': Didn't do anything -> carry forward to today
    - 'was_completed': Already marked complete (just confirming)
    
    Streak logic:
    - If after review, ALL items are marked complete (including forgot_mark + replaced) -> streak maintained
    - If NOT all complete and use_freeze=True -> apply freeze, streak maintained
    - If NOT all complete and use_freeze=False (or no freezes) -> streak breaks
    
    Skipped items get carried forward to today's plan.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        from app.core.database import ActionPlan, ActionPlanItem, ActionPlanDailyReview, ActionPlanFeedback
        from app.utils.timezone_utils import get_user_current_date
        from datetime import timedelta
        from sqlalchemy.orm.attributes import flag_modified
        
        today = get_user_current_date(uid, db)
        
        # Get the plan being reviewed
        plan = db.query(ActionPlan).filter(
            and_(
                ActionPlan.id == request.plan_id,
                ActionPlan.uid == uid
            )
        ).first()
        
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        
        if plan.review_completed:
            return DailyReviewResponse(
                success=True,
                message="This plan has already been reviewed",
                streak_maintained=True
            )
        
        # Get all active items for this plan
        items = db.query(ActionPlanItem).filter(
            and_(
                ActionPlanItem.plan_id == plan.id,
                ActionPlanItem.is_replaced.isnot(True)
            )
        ).all()
        
        items_by_id = {item.id: item for item in items}
        
        # Process each item status from the review
        items_marked_complete = 0
        items_replaced = 0
        items_skipped = 0
        items_to_carry_forward = []
        
        for item_status in request.items:
            item = items_by_id.get(item_status.item_id)
            if not item:
                continue
            
            if item_status.status == 'forgot_to_mark':
                # Mark as completed retroactively
                item.is_completed = True
                item.completed_at = datetime.utcnow()
                items_marked_complete += 1
                
                # Record feedback for GPT memory
                feedback = ActionPlanFeedback(
                    uid=uid,
                    plan_id=plan.id,
                    item_id=item.id,
                    feedback_type="completed",
                    action_title=item.title,
                    action_category=item.category,
                    target_hormone=item.target_hormone,
                    feedback_source="daily_review",
                    feedback_text="Completed but forgot to mark in app",
                    created_at=datetime.utcnow()
                )
                db.add(feedback)
                
            elif item_status.status == 'replaced':
                # Mark as completed with replacement info
                item.is_completed = True
                item.completed_at = datetime.utcnow()
                item.is_replaced = True
                item.replaced_at = datetime.utcnow()
                item.replacement_reason = item_status.replacement_text or "Replaced with alternative"
                items_replaced += 1
                
                # Record feedback for GPT memory
                feedback = ActionPlanFeedback(
                    uid=uid,
                    plan_id=plan.id,
                    item_id=item.id,
                    feedback_type="not_for_me",
                    action_title=item.title,
                    action_category=item.category,
                    target_hormone=item.target_hormone,
                    feedback_source="daily_review",
                    feedback_text=f"Replaced with: {item_status.replacement_text or 'alternative'}",
                    replacement_reason=item_status.replacement_text,
                    replacement_category=item_status.replacement_category,
                    was_replaced=True,
                    created_at=datetime.utcnow()
                )
                db.add(feedback)
                
            elif item_status.status == 'skipped':
                # Mark as incomplete (even if previously completed - user correcting mistake)
                # Will be carried forward to today's plan
                if item.is_completed:
                    # User is saying they marked complete by mistake
                    item.is_completed = False
                    item.completed_at = None
                    logger.info(f"User corrected item {item.id} - was marked complete by mistake, now skipped")
                
                items_skipped += 1
                items_to_carry_forward.append(item.id)
                
                # Record skip feedback for GPT memory
                feedback = ActionPlanFeedback(
                    uid=uid,
                    plan_id=plan.id,
                    item_id=item.id,
                    feedback_type="skipped",
                    action_title=item.title,
                    action_category=item.category,
                    target_hormone=item.target_hormone,
                    feedback_source="daily_review",
                    feedback_text="Skipped - carrying forward",
                    created_at=datetime.utcnow()
                )
                db.add(feedback)
            
            # 'was_completed' status: item was already complete, just confirming
            elif item_status.status == 'was_completed':
                if item.is_completed:
                    items_marked_complete += 1
                    # Record confirmation feedback for GPT memory (user liked it enough to confirm)
                    feedback = ActionPlanFeedback(
                        uid=uid,
                        plan_id=plan.id,
                        item_id=item.id,
                        feedback_type="completed",
                        action_title=item.title,
                        action_category=item.category,
                        target_hormone=item.target_hormone,
                        feedback_source="daily_review",
                        feedback_text="Confirmed completed during daily review",
                        created_at=datetime.utcnow()
                    )
                    db.add(feedback)
        
        # Calculate final completion count
        total_items = len(items)
        final_completed = sum(1 for item in items if item.is_completed)
        
        # Determine streak outcome
        from app.services.streak_service import StreakService
        streak_service = StreakService(db)
        streak_data = streak_service.get_or_create_streak_data(uid)
        
        streak_maintained = False
        streak_broken = False
        freezes_used = 0
        streak_action = "maintained"
        
        # Check if day was already frozen
        was_already_frozen = False
        if streak_data.freeze_used_dates:
            was_already_frozen = plan.plan_date.isoformat() in streak_data.freeze_used_dates
        
        if was_already_frozen:
            # Day was frozen - streak is safe regardless of completion
            streak_maintained = True
            streak_action = "maintained"
        elif (total_items == 0) or (total_items > 0 and final_completed == total_items):
            # All completed (or empty plan) - streak maintained
            streak_maintained = True
            streak_action = "maintained"
        elif request.use_freeze and streak_data.freeze_count > 0:
            # Not enough completed but user wants to use freeze
            streak_data.freeze_count -= 1
            frozen_dates = list(streak_data.freeze_used_dates or [])
            if plan.plan_date.isoformat() not in frozen_dates:
                frozen_dates.append(plan.plan_date.isoformat())
                streak_data.freeze_used_dates = frozen_dates
                flag_modified(streak_data, 'freeze_used_dates')
            freezes_used = 1
            streak_maintained = True
            streak_action = "used_freeze"
        else:
            # Not enough completed and no freeze used
            streak_broken = True
            streak_action = "broken"
            streak_data.current_streak = 0
        
        # Recalculate streak
        if streak_maintained:
            # Flush changes to DB so streak service can see them
            db.flush()
            
            # User requested logic: Get streak BEFORE this plan, then add 1
            # This avoids race conditions with the current plan's status in DB
            # We calculate streak assuming "today" is the plan date
            # This gives us the streak count UP TO (but not including) the plan date
            prior_streak = streak_service.calculate_streak_from_actions(
                uid, 
                reference_date=plan.plan_date
            )
            
            # Since we know streak is maintained for THIS plan, we just add 1
            new_streak = prior_streak + 1
            
            logger.info(f"Streak calculation override: Prior streak (up to {plan.plan_date}) = {prior_streak}. New streak = {new_streak}")
            
            streak_data.current_streak = new_streak
            streak_data.longest_streak = max(streak_data.longest_streak, new_streak)
        
        # Mark plan as reviewed
        plan.review_completed = True
        
        # Create daily review record
        review_record = ActionPlanDailyReview(
            uid=uid,
            plan_id=plan.id,
            review_date=today,
            review_completed_at=datetime.utcnow(),
            items_review_data=[
                {
                    "item_id": item_status.item_id,
                    "status": item_status.status,
                    "replacement_text": item_status.replacement_text,
                    "replacement_category": item_status.replacement_category
                }
                for item_status in request.items
            ],
            streak_action=streak_action,
            freezes_used_count=freezes_used,
            items_carried_forward=items_to_carry_forward,
            items_marked_complete=items_marked_complete + sum(1 for item in items if item.is_completed and item.id not in [s.item_id for s in request.items]),
            items_replaced=items_replaced,
            items_skipped=items_skipped,
            created_at=datetime.utcnow()
        )
        db.add(review_record)
        
        logger.info(f"[SUBMIT_REVIEW] About to commit review for plan {plan.id}. review_completed={plan.review_completed}")
        db.commit()
        
        # CRITICAL: Verify the commit actually persisted using raw SQL
        # This catches any issues with Supabase pooler not committing properly
        from sqlalchemy import text
        verify_result = db.execute(
            text("SELECT id, review_completed FROM action_plans WHERE id = :plan_id"),
            {"plan_id": plan.id}
        ).fetchone()
        
        if verify_result:
            logger.info(f"[SUBMIT_REVIEW] Post-commit verification: plan {verify_result[0]} review_completed={verify_result[1]}")
            if not verify_result[1]:
                logger.error(f"[SUBMIT_REVIEW] CRITICAL: Commit failed! Plan {plan.id} still has review_completed=False in DB!")
                # Force another commit attempt
                plan.review_completed = True
                db.commit()
                logger.info(f"[SUBMIT_REVIEW] Forced second commit for plan {plan.id}")
        
        # Carry forward skipped items to today's plan
        today_plan_updated = False
        if items_to_carry_forward:
            try:
                today_plan_updated = await _carry_forward_items_to_today(
                    uid=uid, 
                    skipped_item_ids=items_to_carry_forward,
                    source_plan=plan,
                    db=db
                )
            except Exception as carry_error:
                logger.warning(f"Failed to carry forward items: {carry_error}")
                # Don't fail the whole request - just log and continue
        
        # Build response message
        if streak_maintained and freezes_used > 0:
            message = f"Review complete! Used 1 freeze to protect your streak. 🧊"
        elif streak_maintained:
            message = f"Great job! Your streak continues! 🔥"
        else:
            message = f"Review complete. Let's start a new streak today! 💪"
        
        # Add info about carried forward items
        if items_to_carry_forward and today_plan_updated:
            message += f" {len(items_to_carry_forward)} action(s) moved to today."
        
        logger.info(f"Daily review submitted: uid={uid}, plan_id={plan.id}, "
                   f"completed={final_completed}/{total_items}, streak_action={streak_action}, "
                   f"carried_forward={len(items_to_carry_forward)}")
        
        return DailyReviewResponse(
            success=True,
            streak_maintained=streak_maintained,
            streak_broken=streak_broken,
            freezes_used=freezes_used,
            new_streak_count=streak_data.current_streak,
            items_carried_forward=items_to_carry_forward,
            today_plan_updated=today_plan_updated,
            message=message
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit daily review: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit daily review")


async def _carry_forward_items_to_today(
    uid: str,
    skipped_item_ids: List[int],
    source_plan,
    db: Session
) -> bool:
    """
    Carry forward skipped items from a previous day's plan to today's plan.
    
    IMPORTANT: This function REPLACES some items in today's plan with carried items,
    maintaining the total of 4 items and proper hormone balance (2 primary + 2 secondary).
    
    Logic:
    1. Get carried items and their hormones
    2. If today's plan doesn't exist, GENERATE IT FIRST
    3. Count hormone distribution of carried items
    4. Delete excess items from today's plan (keeping hormone balance)
    5. Add carried items to today's plan
    6. Result: Always 4 items total with proper hormone balance
    
    Returns True if successfully modified today's plan.
    """
    from app.core.database import ActionPlan, ActionPlanItem, ActionPlanItemVariant, UserResponse, UserProfile
    from app.utils.timezone_utils import get_user_current_date
    from app.services.action_plan_generator import get_action_plan_generator
    
    TARGET_ITEMS = 4  # Standard plan size
    TARGET_PER_HORMONE = 2  # 2 primary + 2 secondary
    
    if not skipped_item_ids:
        return False
    
    today = get_user_current_date(uid, db)
    
    # Find today's plan
    today_plan = db.query(ActionPlan).filter(
        and_(
            ActionPlan.uid == uid,
            ActionPlan.plan_date == today
        )
    ).first()
    
    # Get the source items to copy FIRST (before generating)
    source_items = db.query(ActionPlanItem).filter(
        ActionPlanItem.id.in_(skipped_item_ids)
    ).all()
    
    if not source_items:
        return False
    
    # Limit carried items to max 4
    source_items = source_items[:TARGET_ITEMS]
    num_carried = len(source_items)
    
    if not today_plan:
        # OPTIMIZED: Pass carry-forward items to generator so it only generates what's needed
        # This saves GPT + image generation costs when all 4 items are carried forward
        logger.info(f"No plan exists for today ({today}) - generating plan WITH {num_carried} carry-forward items")
        
        try:
            # Get user's timezone
            user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
            user_timezone = user_profile.current_timezone if user_profile and user_profile.current_timezone else "Asia/Seoul"
            
            # Build carryforward_items list for generator
            carryforward_items = []
            for item in source_items:
                # Get variants for this item
                variants = db.query(ActionPlanItemVariant).filter(
                    ActionPlanItemVariant.item_id == item.id
                ).all()
                
                carryforward_items.append({
                    "source_item": item,
                    "source_variants": variants,
                    "original_id": item.id
                })
            
            # Get async session for generator
            async_db = await get_async_db_session()
            
            # Generate today's plan with carry-forward items
            # The generator will only generate (4 - len(carryforward_items)) new actions
            generator = get_action_plan_generator()
            result = await generator.get_or_generate_today_plan(
                user_id=uid,
                user_timezone=user_timezone,
                db=async_db,
                carryforward_items=carryforward_items  # Pass carry-forward items
            )
            
            await async_db.close()
            
            if not result.get("success"):
                logger.error(f"Failed to generate today's plan for carry forward: {result.get('error')}")
                return False
            
            logger.info(f"Generated today's plan (id={result.get('plan_id')}) with {num_carried} carried items already included")
            
            # Items are already in the plan - no need to add them again
            # Just log and return success
            return True
                
        except Exception as gen_error:
            logger.error(f"Failed to generate plan for carry forward: {gen_error}")
            return False
    
    # CRITICAL FIX: Only carry forward from YESTERDAY (or the plan being reviewed)
    # We should not carry forward items from older plans if they were already carried forward before
    # But here source_plan is the plan being reviewed, so we are safe.
    # The issue is likely that we are appending to today's plan which might already have items.
    
    # Get user's primary and secondary hormones for balance from UserResponse (not UserProfile)
    user_response = db.query(UserResponse).filter(UserResponse.uid == uid).first()
    primary_hormone = (user_response.primary_hormone or "cortisol").lower() if user_response else "cortisol"
    # secondary_hormones is an array, get first one if exists
    secondary_hormones = user_response.secondary_hormones if user_response and user_response.secondary_hormones else []
    secondary_hormone = secondary_hormones[0].lower() if secondary_hormones else "progesterone"
    
    # Count hormone distribution of carried items
    carried_primary = sum(1 for item in source_items if (item.target_hormone or "").lower() == primary_hormone)
    carried_secondary = num_carried - carried_primary
    
    logger.info(f"Carrying forward {num_carried} items: {carried_primary} primary ({primary_hormone}), {carried_secondary} secondary ({secondary_hormone})")
    
    # Get all current items in today's plan
    today_items = db.query(ActionPlanItem).filter(
        and_(
            ActionPlanItem.plan_id == today_plan.id,
            ActionPlanItem.is_replaced.isnot(True)
        )
    ).order_by(ActionPlanItem.slot).all()
    
    # Calculate how many items to KEEP from today (not delete)
    # We want exactly 4 items total.
    # If we carry 2, we keep 2. If we carry 4, we keep 0.
    items_to_keep_count = TARGET_ITEMS - num_carried
    
    if items_to_keep_count < 0:
        # If somehow we are carrying more than 4 (should be capped above), cap it
        items_to_keep_count = 0
        
    # If today's plan already has items, we need to make space
    # But we must be careful not to duplicate items if this runs multiple times
    
    # Check if we already have carried items in today's plan from the SAME source items
    # This prevents "8 items" bug if the function is called twice
    existing_carried = db.query(ActionPlanItem).filter(
        and_(
            ActionPlanItem.plan_id == today_plan.id,
            ActionPlanItem.carried_forward_from.in_(skipped_item_ids)
        )
    ).count()
    
    if existing_carried > 0:
        logger.info(f"Items already carried forward to today's plan. Skipping to avoid duplicates.")
        return True
    
    # Calculate needed hormone balance for remaining items
    needed_primary = max(0, TARGET_PER_HORMONE - carried_primary)
    needed_secondary = max(0, TARGET_PER_HORMONE - carried_secondary)
    
    # Group today's items by hormone
    today_primary = [item for item in today_items if (item.target_hormone or "").lower() == primary_hormone]
    today_secondary = [item for item in today_items if (item.target_hormone or "").lower() != primary_hormone]
    
    # Select items to keep - prioritize maintaining hormone balance
    items_to_keep = []
    
    # Keep needed primary items
    for item in today_primary[:needed_primary]:
        items_to_keep.append(item.id)
    
    # Keep needed secondary items
    for item in today_secondary[:needed_secondary]:
        items_to_keep.append(item.id)
    
    # Fill remaining slots with any items if still needed
    remaining_needed = items_to_keep_count - len(items_to_keep)
    if remaining_needed > 0:
        for item in today_items:
            if item.id not in items_to_keep and remaining_needed > 0:
                items_to_keep.append(item.id)
                remaining_needed -= 1
    
    logger.info(f"Today has {len(today_items)} items, keeping {len(items_to_keep)}, will have {num_carried + len(items_to_keep)} total")
    
    # Delete items NOT in keep list
    items_to_delete = [item for item in today_items if item.id not in items_to_keep]
    
    for item in items_to_delete:
        # Delete variants first
        db.query(ActionPlanItemVariant).filter(ActionPlanItemVariant.item_id == item.id).delete()
        # Delete item
        db.query(ActionPlanItem).filter(ActionPlanItem.id == item.id).delete()
    
    logger.info(f"Deleted {len(items_to_delete)} items from today's plan to make room for carried items")
    
    # Renumber remaining items (slots 0 to N-1)
    remaining_items = db.query(ActionPlanItem).filter(
        ActionPlanItem.plan_id == today_plan.id
    ).order_by(ActionPlanItem.slot).all()
    
    for idx, item in enumerate(remaining_items):
        item.slot = idx
    
    # Add carried items starting from next slot
    start_slot = len(remaining_items)
    items_added = 0
    
    for idx, source_item in enumerate(source_items):
        new_slot = start_slot + idx
        
        # Create new item as a copy
        new_item = ActionPlanItem(
            plan_id=today_plan.id,
            uid=uid,
            slot=new_slot,
            time_slot=source_item.time_slot,
            category=source_item.category,
            target_hormone=source_item.target_hormone,
            title=source_item.title,  # REMOVED [Carried Forward] prefix per user request
            specific_action=source_item.specific_action,
            purpose=source_item.purpose,
            hormone_persona_intro=source_item.hormone_persona_intro,
            food_amounts=source_item.food_amounts,
            food_items=source_item.food_items,
            exercise_durations=source_item.exercise_durations,
            exercise_types=source_item.exercise_types,
            exercise_intensities=source_item.exercise_intensities,
            mindfulness_durations=source_item.mindfulness_durations,
            mindfulness_techniques=source_item.mindfulness_techniques,
            conditions=source_item.conditions,
            symptoms=source_item.symptoms,
            hero_image_url=source_item.hero_image_url,
            hero_image_prompt=source_item.hero_image_prompt,
            research_studies=source_item.research_studies,
            is_completed=False,
            is_replaced=False,
            carried_forward_from=source_item.id,
            created_at=datetime.utcnow()
        )
        db.add(new_item)
        db.flush()
        
        # Copy variants if they exist
        source_variants = db.query(ActionPlanItemVariant).filter(
            ActionPlanItemVariant.item_id == source_item.id
        ).all()
        
        for variant in source_variants:
            new_variant = ActionPlanItemVariant(
                item_id=new_item.id,
                variant_type=variant.variant_type,
                title=variant.title,
                description=variant.description,
                image_url=variant.image_url,
                image_prompt=variant.image_prompt,
                created_at=datetime.utcnow()
            )
            db.add(new_variant)
        
        items_added += 1
        logger.info(f"Carried forward item {source_item.id} -> new item {new_item.id} in today's plan (slot {new_slot})")
    
    db.commit()
    
    # Final count
    final_count = db.query(ActionPlanItem).filter(
        ActionPlanItem.plan_id == today_plan.id
    ).count()
    
    logger.info(f"Successfully carried forward {items_added} items. Today's plan now has {final_count} items total.")
    return items_added > 0


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
    
    # Query ALL items first for debugging
    all_items = db.query(ActionPlanItem).filter(
        ActionPlanItem.plan_id == plan.id
    ).order_by(ActionPlanItem.slot).all()
    
    # Query non-replaced items
    items = db.query(ActionPlanItem).filter(
        and_(
            ActionPlanItem.plan_id == plan.id,
            ActionPlanItem.is_replaced.isnot(True)
        )
    ).order_by(ActionPlanItem.slot).all()
    
    # Debug logging
    logger.info(f"🔍 _build_plan_response for plan_id={plan.id}:")
    logger.info(f"   Total items in DB: {len(all_items)}")
    logger.info(f"   Non-replaced items: {len(items)}")
    for item in all_items:
        logger.info(f"   - Item {item.id}: '{item.title}' | is_replaced={item.is_replaced} | target_hormone={item.target_hormone}")

    

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
        
        # Track hormone stats - normalize to lowercase to prevent duplicates
        hormone = (action.get("target_hormone") or "unknown").lower()
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
