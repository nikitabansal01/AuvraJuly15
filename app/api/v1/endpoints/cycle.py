import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.cycle_service import CycleService
from app.models.cycle_models import CyclePhaseResponse, PeriodStartUpdate, CycleCalendarResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/calendar", response_model=CycleCalendarResponse)
async def get_cycle_calendar(
    year: int = Query(..., ge=1900, le=2100),
    month: int = Query(..., ge=1, le=12),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Sign in to view your cycle information")
    try:
        return CycleService(db).get_calendar_month(uid, year, month)
    except Exception as error:
        logger.error("Cycle calendar read failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Your cycle calendar could not be loaded") from error

@router.get("/phase", response_model=CyclePhaseResponse)
async def get_cycle_phase(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get current menstrual cycle information.
    
    Returns:
    - User name
    - Current Cycle Day
    - Menstrual cycle phase (Menses, Follicular, Ovulation, Luteal)
    - Guidance message if data is insufficient
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        service = CycleService(db)
        cycle_info = service.get_cycle_phase_info(uid)
        
        logger.info(f"Cycle phase info retrieved: uid={uid}, cycle_day={cycle_info.cycle_day}, phase={cycle_info.phase}")
        return CyclePhaseResponse(cycle_info=cycle_info)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cycle phase info: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get cycle phase information")


@router.put("/period", response_model=CyclePhaseResponse)
async def update_period_start(
    payload: PeriodStartUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save only the authenticated user's reported last period start."""
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Sign in to update your period information")
    try:
        info = CycleService(db).update_last_period_start(uid, payload.last_period_start)
        return CyclePhaseResponse(cycle_info=info)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.error("Period date update failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Your period information could not be saved") from error
