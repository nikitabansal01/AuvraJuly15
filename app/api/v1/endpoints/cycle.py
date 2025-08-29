import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.cycle_service import CycleService
from app.models.cycle_models import CyclePhaseResponse

logger = logging.getLogger(__name__)

router = APIRouter()

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
        
    except Exception as e:
        logger.error(f"Failed to get cycle phase info: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get cycle phase information")

