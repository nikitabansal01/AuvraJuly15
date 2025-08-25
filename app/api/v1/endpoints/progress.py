from datetime import date, datetime
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.services.progress_service import ProgressService

router = APIRouter()

@router.get("/progress/weekly", response_model=Dict[str, Any])
async def get_weekly_progress(
    target_date: str = Query(None, description="Target date in YYYY-MM-DD format (default: today)"),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    주간 진행상황 통계를 가져옵니다.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # 날짜 파싱
        parsed_date = None
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        progress_service = ProgressService(db)
        progress = progress_service.get_weekly_progress(uid, parsed_date)
        
        return progress
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get weekly progress: {str(e)}")

@router.get("/progress/monthly", response_model=Dict[str, Any])
async def get_monthly_progress(
    target_date: str = Query(None, description="Target date in YYYY-MM-DD format (default: today)"),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    월간 진행상황 통계를 가져옵니다.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # 날짜 파싱
        parsed_date = None
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        progress_service = ProgressService(db)
        progress = progress_service.get_monthly_progress(uid, parsed_date)
        
        return progress
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get monthly progress: {str(e)}")

@router.get("/progress/recommendation/{recommendation_id}", response_model=Dict[str, Any])
async def get_recommendation_progress(
    recommendation_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    특정 추천의 진행상황을 가져옵니다.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        progress_service = ProgressService(db)
        progress = progress_service.get_recommendation_progress(uid, recommendation_id)
        
        if "error" in progress:
            raise HTTPException(status_code=404, detail=progress["error"])
        
        return progress
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get recommendation progress: {str(e)}")

@router.get("/progress/overall", response_model=Dict[str, Any])
async def get_overall_progress(
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    전체 진행상황 통계를 가져옵니다.
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        progress_service = ProgressService(db)
        progress = progress_service.get_overall_progress(uid)
        
        if "error" in progress:
            raise HTTPException(status_code=500, detail=progress["error"])
        
        return progress
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get overall progress: {str(e)}")

