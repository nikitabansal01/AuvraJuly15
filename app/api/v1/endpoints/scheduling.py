from datetime import date, datetime
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.services.scheduling_service import SchedulingService
from app.models.scheduling_models import CompletionRequest

router = APIRouter()

@router.get("/schedule/today", response_model=Dict[str, Any])
async def get_today_schedule(
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get today's schedule.
    Schedule based on frequency_detail and duration_weeks, classified by optimal_times.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        today = date.today()
        scheduling_service = SchedulingService(db)
        schedule = scheduling_service.generate_user_schedule(uid, today)
        
        return schedule
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get schedule: {str(e)}")

@router.get("/schedule/{target_date}", response_model=Dict[str, Any])
async def get_schedule_by_date(
    target_date: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get schedule for a specific date."""
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Parse date
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        scheduling_service = SchedulingService(db)
        schedule = scheduling_service.generate_user_schedule(uid, parsed_date)
        
        return schedule
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get schedule: {str(e)}")

@router.post("/schedule/complete", response_model=Dict[str, Any])
async def mark_recommendation_completed(
    completion_request: CompletionRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark recommendation as completed."""
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Parse date (default: today)
        completion_date = completion_request.completion_date or date.today()
        
        scheduling_service = SchedulingService(db)
        success = scheduling_service.mark_recommendation_completed(
            uid=uid,
            recommendation_id=completion_request.recommendation_id,
            completion_date=completion_date,
            notes=completion_request.notes
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to mark recommendation as completed")
        
        return {
            "message": "Recommendation marked as completed",
            "recommendation_id": completion_request.recommendation_id,
            "completion_date": completion_date.isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to mark completion: {str(e)}")

@router.get("/schedule/stats", response_model=Dict[str, Any])
async def get_schedule_stats(
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get schedule statistics."""
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        today = date.today()
        scheduling_service = SchedulingService(db)
        
        # Get today's schedule
        schedule = scheduling_service.generate_user_schedule(uid, today)
        
        # Statistics are already calculated in schedule
        stats = {
            "date": today.isoformat(),
            "total_recommendations": schedule['hormone_completion_stats']['overall']['total_recommendations'],
            "completed_recommendations": schedule['hormone_completion_stats']['overall']['completed_recommendations'],
            "completion_rate": schedule['hormone_completion_stats']['overall']['completion_rate'],
            "hormone_completion_stats": schedule.get('hormone_completion_stats', {})
        }
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")
