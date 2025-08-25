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
    현재 생리 주기 정보 조회
    
    - 사용자 이름
    - 현재 Cycle Day
    - 생리 주기 Phase (Menses, Follicular, Ovulation, Luteal)
    - 데이터 부족 시 안내 메시지
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="사용자 ID 없음")
        
        service = CycleService(db)
        cycle_info = service.get_cycle_phase_info(uid)
        
        logger.info(f"생리 주기 정보 조회: uid={uid}, cycle_day={cycle_info.cycle_day}, phase={cycle_info.phase}")
        return CyclePhaseResponse(cycle_info=cycle_info)
        
    except Exception as e:
        logger.error(f"생리 주기 정보 조회 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="생리 주기 정보 조회 실패")

