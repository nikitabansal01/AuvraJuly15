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

from uuid import uuid4

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.ui_blocks import UIBlock, UIBlockAction, UIEventRequest
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
    ui_blocks: List[UIBlock] = []


class RespondCarePlanCheckInRequest(BaseModel):
    thread_id: str
    message_text: str


class RespondCarePlanCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []
    actionable_insights: Dict[str, Any] = {}
    ui_blocks: List[UIBlock] = []


def _ensure_tap_option(tap_options: List[Dict[str, str]], option_id: str, text: str) -> List[Dict[str, str]]:
    existing_ids = {t.get("id") for t in (tap_options or [])}
    if option_id in existing_ids:
        return tap_options
    return list(tap_options or []) + [{"id": option_id, "text": text}]


def _default_ui_blocks_for_start() -> List[UIBlock]:
    """Minimal dynamic UI blocks for Gemini-like behavior.

    These are optional and can be empty; mobile should render if present.
    """
    return [
        UIBlock(
            id=str(uuid4()),
            type="quick_actions",
            title="Care plan",
            subtitle="Only shows when it helps",
            actions=[
                UIBlockAction(
                    id="open_plan_manager",
                    title="Manage plan",
                    action_type="open_modal",
                    payload={"modal": "PlanManagerModal"},
                )
            ],
            dismissible=True,
            priority="low",
            analytics={"surface": "care_plan_checkin", "reason": "entry_point"},
        )
    ]


class TranscribeResponse(BaseModel):
    text: str


def _open_plan_manager_block() -> UIBlock:
    return UIBlock(
        id=str(uuid4()),
        type="open_modal",
        title="Manage plan",
        payload={"modal": "PlanManagerModal"},
        actions=[
            UIBlockAction(
                id="confirm_open",
                title="Open",
                action_type="open_modal",
                payload={"modal": "PlanManagerModal"},
            )
        ],
        dismissible=True,
        priority="normal",
        analytics={"surface": "care_plan_checkin", "reason": "ui_event"},
    )


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
        tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "ui_blocks": _default_ui_blocks_for_start(),
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

        tap_options = [t.model_dump() for t in (ai_response.tap_options or [])]
        tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": [],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/event", response_model=RespondCarePlanCheckInResponse)
async def care_plan_ui_event(
    payload: UIEventRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Handle structured UI events.

    MVP routing:
    - open modal actions return a UI block instructing the client to open a modal.
    - send_text-style events are converted into the existing `/respond` flow.
    """
    try:
        uid = current_user["uid"]
        if not payload.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")

        service = CarePlanCheckInService(db)

        action_id = (payload.action_id or "").strip()
        meta = payload.metadata or {}

        if action_id == "open_plan_manager":
            thread = service.get_thread_by_id(uid, payload.thread_id)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option([], "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_open_plan_manager_block()],
            }

        send_text = (meta.get("send_text") or "").strip()
        if send_text:
            thread, ai_response = await service.respond(uid, payload.thread_id, send_text)
            history = service.format_history_for_mobile(thread)
            tap_options = [t.model_dump() for t in (ai_response.tap_options or [])]
            tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [],
            }

        # Default: no-op
        thread = service.get_thread_by_id(uid, payload.thread_id)
        history = service.format_history_for_mobile(thread)
        tap_options = _ensure_tap_option([], "manage_plan", "🧩 Manage plan")
        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": [],
        }
    except HTTPException:
        raise
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
