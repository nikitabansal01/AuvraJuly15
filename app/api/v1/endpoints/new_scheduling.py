import logging
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.new_scheduling_service import NewSchedulingService
from app.models.scheduling_models import AssignmentResponse, AssignmentCompletionRequest
from sqlalchemy import and_

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/assignments/today", response_model=AssignmentResponse)
async def get_today_assignments(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get today's assignments (time-based).
    
    - Calculate today's date based on UserProfile's current_timezone
    - Ensure immediate schedule emission if needed
    - Return assignments grouped by time periods
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Get current_timezone from UserProfile
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        service = NewSchedulingService(db)
        
        # Today's date (user timezone based)
        today = date.today()
        
        # Get assignments (with adjustment)
        result = service.get_user_assignments_for_date(uid, today, user_timezone)
        
        logger.info(f"Today's assignments retrieved: uid={uid}, timezone={user_timezone}, count={result['total_assignments']}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to get today's assignments: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get assignments")

@router.get("/assignments/{target_date}", response_model=AssignmentResponse)
async def get_assignments_for_date(
    target_date: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get assignments for a specific date.
    
    - target_date: YYYY-MM-DD format
    - Adjusted based on UserProfile's current_timezone
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Get current_timezone from UserProfile
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        # Parse date
        try:
            parsed_date = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")
        
        service = NewSchedulingService(db)
        
        # Get assignments
        result = service.get_user_assignments_for_date(uid, parsed_date, user_timezone)
        
        logger.info(f"Assignments for specific date retrieved: uid={uid}, date={target_date}, timezone={user_timezone}, count={result['total_assignments']}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get assignments for specific date: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get assignments")

@router.post("/assignments/{assignment_id}/complete")
async def complete_assignment(
    assignment_id: int,
    request: AssignmentCompletionRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mark assignment as completed.
    
    - assignment_id: ID of assignment to complete
    - notes: Completion notes (optional)
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        service = NewSchedulingService(db)
        
        # Mark assignment as completed
        success = service.mark_assignment_completed(
            assignment_id=assignment_id,
            uid=uid,
            notes=request.notes
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Assignment not found")
        
        logger.info(f"Assignment completed: assignment_id={assignment_id}, uid={uid}")
        return {"message": "Assignment completed", "assignment_id": assignment_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to complete assignment: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to complete assignment")

@router.post("/schedules/{recommendation_id}/create")
async def create_schedule_from_recommendation(
    recommendation_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Convert existing recommendation to new schedule.
    
    - recommendation_id: ID of recommendation to convert
    - Uses UserProfile's current_timezone
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Get current_timezone from UserProfile
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        # Verify recommendation
        from app.core.database import RecommendationRecord
        recommendation = db.query(RecommendationRecord).filter(
            and_(
                RecommendationRecord.id == recommendation_id,
                RecommendationRecord.uid == uid
            )
        ).first()
        
        if not recommendation:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        
        service = NewSchedulingService(db)
        
        # Create schedule
        schedule = service.create_schedule_from_recommendation(recommendation, user_timezone)
        
        logger.info(f"Schedule created: recommendation_id={recommendation_id}, schedule_id={schedule.id}, timezone={user_timezone}")
        return {
            "message": "Schedule created",
            "schedule_id": schedule.id,
            "recommendation_id": recommendation_id,
            "timezone": user_timezone
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create schedule: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create schedule")

@router.get("/schedules/active")
async def get_active_schedules(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get active schedules list."""
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        from app.core.database import RecommendationSchedule, RecommendationRecord
        
        # Get active schedules
        active_schedules = db.query(RecommendationSchedule).filter(
            RecommendationSchedule.uid == uid
        ).all()
        
        result = []
        for schedule in active_schedules:
            # Get recommendation information
            recommendation = db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if recommendation:
                result.append({
                    "schedule_id": schedule.id,
                    "recommendation_id": schedule.recommendation_id,
                    "title": recommendation.title,
                    "category": recommendation.category,
                    "rrule": schedule.rrule,
                    "start_date": schedule.start_date_utc.isoformat(),
                    "end_date": schedule.end_date_utc.isoformat() if schedule.end_date_utc else None,
                    "next_fire_at_utc": schedule.next_fire_at_utc.isoformat() if schedule.next_fire_at_utc else None
                })
        
        logger.info(f"Active schedules retrieved: uid={uid}, count={len(result)}")
        return {"schedules": result}
        
    except Exception as e:
        logger.error(f"Failed to get active schedules: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get schedules")

@router.delete("/schedules/{schedule_id}")
async def deactivate_schedule(
    schedule_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Deactivate schedule."""
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        from app.core.database import RecommendationSchedule
        
        # Verify and deactivate schedule
        schedule = db.query(RecommendationSchedule).filter(
            and_(
                RecommendationSchedule.id == schedule_id,
                RecommendationSchedule.uid == uid
            )
        ).first()
        
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")
        
        # Delete schedule (actual deletion instead of is_active)
        db.delete(schedule)
        db.commit()
        
        logger.info(f"Schedule deleted: schedule_id={schedule_id}, uid={uid}")
        return {"message": "Schedule deleted", "schedule_id": schedule_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to deactivate schedule: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to deactivate schedule")

