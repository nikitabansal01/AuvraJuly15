from typing import Optional
from pydantic import BaseModel
from datetime import date

class CyclePhaseInfo(BaseModel):
    """생리 주기 정보"""
    user_name: str
    cycle_day: Optional[int] = None
    phase: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_name": "Jessica",
                "cycle_day": 19,
                "phase": "Luteal phase"
            }
        }

class CyclePhaseResponse(BaseModel):
    """생리 주기 응답"""
    cycle_info: CyclePhaseInfo
