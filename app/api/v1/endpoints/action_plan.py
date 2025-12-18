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
    FeedbackResponse,
    ReplacementResponse,
    CompletionResponse,
    PlanSatisfactionResponse,
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
    format: str = Query("legacy", description="Response format: 'legacy' or 'new'")
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
        
        # Get user timezone
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
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
        
        # Return in requested format
        if format == "legacy":
            return _convert_to_legacy_format(result)
        else:
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
    Submit feedback (like/dislike) for an action.
    
    Requirements:
    - feedback_type must be 'like' or 'dislike'
    - time_shown is when the action was first displayed to user
    - After 30 seconds, user can request replacement for disliked actions
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        if request.feedback_type not in ["like", "dislike"]:
            raise HTTPException(status_code=400, detail="Invalid feedback type")
        
        # Get async session
        async_db = await get_async_db_session()
        
        generator = get_action_plan_generator()
        result = await generator.record_feedback(
            user_id=uid,
            item_id=request.item_id,
            feedback_type=request.feedback_type,
            time_shown=request.time_shown,
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
        can_replace = time_elapsed >= FEEDBACK_MINIMUM_TIME and request.feedback_type == "dislike"
        
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
        item.completed_at = datetime.now(timezone.utc)
        db.commit()
        
        logger.info(f"Action completed: item_id={item_id}, uid={uid}")
        
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
    Submit overall plan satisfaction (triggered 30 seconds after plan renders).
    
    This endpoint captures:
    - 'yes' - Plan works well for user
    - 'no' - Plan doesn't work, user can specify issues
    - 'partial' - Some actions work, some don't
    
    The feedback is used to improve future plan generation.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        if request.satisfaction not in ["yes", "no", "partial"]:
            raise HTTPException(status_code=400, detail="Invalid satisfaction value")
        
        from app.core.database import ActionPlan
        
        # Get the plan
        plan = db.query(ActionPlan).filter(
            and_(ActionPlan.id == request.plan_id, ActionPlan.uid == uid)
        ).first()
        
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        
        # Store satisfaction feedback in plan metadata
        plan_metadata = plan.generation_metadata or {}
        plan_metadata["user_satisfaction"] = request.satisfaction
        plan_metadata["satisfaction_feedback"] = request.feedback_text
        plan_metadata["satisfaction_issues"] = request.specific_issues
        plan_metadata["satisfaction_submitted_at"] = datetime.now(timezone.utc).isoformat()
        plan.generation_metadata = plan_metadata
        
        db.commit()
        
        logger.info(f"Plan satisfaction submitted: plan_id={request.plan_id}, satisfaction={request.satisfaction}, uid={uid}")
        
        # Return appropriate message
        if request.satisfaction == "yes":
            message = "Great! We'll keep creating similar plans for you! 💜"
        elif request.satisfaction == "no":
            message = "Thanks for letting us know! We'll adjust your future plans. 💜"
        else:
            message = "Got it! We'll fine-tune your future plans. 💜"
        
        return PlanSatisfactionResponse(
            success=True,
            message=message,
            will_adjust_future_plans=request.satisfaction != "yes"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit plan satisfaction: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit satisfaction")


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
        ActionPlanItem.plan_id == plan.id
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
        
        # Category-specific fields
        if item.category == "food":
            action_data["food_items"] = item.food_items
            action_data["food_amounts"] = item.food_amounts
        elif item.category == "movement":
            action_data["exercise_types"] = item.exercise_types
            action_data["exercise_durations"] = item.exercise_durations
            action_data["exercise_intensities"] = item.exercise_intensities
        elif item.category == "mindfulness":
            action_data["mindfulness_techniques"] = item.mindfulness_techniques
            action_data["mindfulness_durations"] = item.mindfulness_durations
        
        actions.append(action_data)
        
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


def _convert_to_legacy_format(result: dict) -> dict:
    """
    Convert new action plan format to legacy assignment format.
    
    This ensures backward compatibility with existing mobile app.
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
            "generation_source": "action_plan"
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
            "conditions": [],
            "symptoms": [],
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
            
            # Category-specific
            "food_amounts": action.get("food_amounts"),
            "food_items": action.get("food_items"),
            "exercise_durations": action.get("exercise_durations"),
            "exercise_types": action.get("exercise_types"),
            "exercise_intensities": action.get("exercise_intensities"),
            "mindfulness_durations": action.get("mindfulness_durations"),
            "mindfulness_techniques": action.get("mindfulness_techniques")
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
        "generation_source": "action_plan"
    }
