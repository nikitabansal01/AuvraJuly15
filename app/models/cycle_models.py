from typing import Optional
from pydantic import BaseModel
from datetime import date

class CyclePhaseInfo(BaseModel):
    """Menstrual cycle information"""
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
    """Menstrual cycle response"""
    cycle_info: CyclePhaseInfo
