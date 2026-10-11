from typing import Optional, List
from pydantic import BaseModel, ConfigDict, field_validator
from datetime import date, datetime
import re

class CyclePhaseInfo(BaseModel):
    """Menstrual cycle information"""
    user_name: str
    cycle_day: Optional[int] = None
    phase: Optional[str] = None
    as_of_date: Optional[date] = None
    timezone: Optional[str] = None
    cycle_length_days: Optional[int] = None
    last_period_start: Optional[date] = None
    last_period_timezone: Optional[str] = None
    can_update_period: bool = False
    
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


class CycleCalendarDay(BaseModel):
    date: date
    cycle_day: Optional[int] = None
    phase: Optional[str] = None
    is_projection: bool = False
    reported_period_start: bool = False


class CycleCalendarResponse(BaseModel):
    month: str
    cycle_info: CyclePhaseInfo
    days: List[CycleCalendarDay]


class PeriodStartUpdate(BaseModel):
    """One reported calendar date, never a timestamp or a predicted period."""
    last_period_start: date
    model_config = ConfigDict(extra="forbid")

    @field_validator("last_period_start", mode="before")
    @classmethod
    def calendar_date_only(cls, value):
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use a calendar date in YYYY-MM-DD format")
        return date.fromisoformat(value)
