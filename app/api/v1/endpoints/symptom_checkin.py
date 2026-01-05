"""Symptom Check-in API endpoints.

Daily threaded chat for symptom progress.
Mobile contract:
- start returns history immediately
- respond returns updated history + tap options
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.services.symptom_checkin_service import SymptomCheckInService

router = APIRouter()


class TapOption(BaseModel):
    id: str
    text: str


class ChatMessage(BaseModel):
    id: str
    text: str
    isBot: bool


class StartSymptomCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []


class RespondSymptomCheckInRequest(BaseModel):
    thread_id: str
    message_text: str


class RespondSymptomCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []
    actionable_insights: Dict[str, Any] = {}


class TranscribeResponse(BaseModel):
    text: str


class SymptomLogCreateRequest(BaseModel):
    symptom_type: str
    severity: int  # 1-9
    notes: Optional[str] = None
    factors: List[str] = []
    logged_via: str = "symptom_checkin_ui"


class SymptomLogItem(BaseModel):
    symptom_type: str
    severity: int
    logged_at: str
    notes: Optional[str] = None
    factors: List[str] = []


class SymptomTypeAggregate(BaseModel):
    symptom_type: str
    count: int
    avg_severity: float
    last_severity: Optional[int] = None
    trend: str  # improving|stable|worsening|unknown


class SymptomOverviewResponse(BaseModel):
    period_days: int
    logs: List[SymptomLogItem]
    aggregates: List[SymptomTypeAggregate]
    top_symptoms: List[str]


@router.post("/start", response_model=StartSymptomCheckInResponse)
async def start_symptom_checkin(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = SymptomCheckInService(db)
        thread = service.get_or_create_today_thread(uid)
        history = service.format_history_for_mobile(thread)

        tap_options = [
            {"id": "improving", "text": "😊 Feeling better"},
            {"id": "stable", "text": "😕 About the same"},
            {"id": "worsening", "text": "😣 Feeling worse"},
            {"id": "wins", "text": "🏆 Share a win"},
        ]

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/respond", response_model=RespondSymptomCheckInResponse)
async def respond_symptom_checkin(
    payload: RespondSymptomCheckInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = SymptomCheckInService(db)
        thread, ai_response = await service.respond(uid, payload.thread_id, payload.message_text)
        history = service.format_history_for_mobile(thread)

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": [t.model_dump() for t in (ai_response.tap_options or [])],
            "actionable_insights": thread.actionable_insights or {},
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = SymptomCheckInService(db)
        text = await service.transcribe_audio(uid, file)
        return {"text": text}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/log")
async def log_symptom(
    payload: SymptomLogCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a symptom log entry.

    Used by the Symptom Manager UI to record a concrete severity score (1-9) with optional factors.
    """
    try:
        uid = current_user["uid"]
        service = SymptomCheckInService(db)
        result = service.create_symptom_log(
            uid=uid,
            symptom_type=payload.symptom_type,
            severity=payload.severity,
            notes=payload.notes,
            factors=payload.factors,
            logged_via=payload.logged_via,
        )
        return {"success": True, "log": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/overview", response_model=SymptomOverviewResponse)
async def get_overview(
    period_days: int = Query(14, ge=3, le=60),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get recent symptom logs + aggregates for charts in Symptom Manager UI."""
    try:
        uid = current_user["uid"]
        service = SymptomCheckInService(db)
        return service.get_symptom_overview(uid=uid, period_days=period_days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
