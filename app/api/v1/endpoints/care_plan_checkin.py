"""Care Plan Check-in API endpoints.

Daily threaded chat that stores one thread per user per local date.
Used for:
- daily adherence / blockers / plan-change requests
- generating condensed insights for action plan updates & replacements

Mobile:
- start returns history immediately
- respond returns updated history + tap options
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.services.care_plan_checkin_service import CarePlanCheckInService

router = APIRouter()


class TapOption(BaseModel):
    id: str
    text: str


class ChatMessage(BaseModel):
    id: str
    text: str
    isBot: bool


class StartCarePlanCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []


class RespondCarePlanCheckInRequest(BaseModel):
    thread_id: str
    message_text: str


class RespondCarePlanCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []
    actionable_insights: Dict[str, Any] = {}


class TranscribeResponse(BaseModel):
    text: str


@router.post("/start", response_model=StartCarePlanCheckInResponse)
async def start_care_plan_checkin(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)
        thread = service.get_or_create_today_thread(uid)
        history = service.format_history_for_mobile(thread)

        # Default tap options (LLM can override on respond)
        tap_options = [
            {"id": "want-to-change", "text": "👎 I want to change it"},
            {"id": "skip-actions", "text": "⏩ I want to skip some actions for today"},
            {"id": "alternate-suggestions", "text": "🔁 I want alternate suggestions"},
        ]

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/respond", response_model=RespondCarePlanCheckInResponse)
async def respond_care_plan_checkin(
    payload: RespondCarePlanCheckInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)
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
        service = CarePlanCheckInService(db)
        text = await service.transcribe_audio(uid, file)
        return {"text": text}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
