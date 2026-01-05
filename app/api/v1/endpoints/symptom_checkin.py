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

from uuid import uuid4

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.ui_blocks import UIBlock, UIBlockAction, UIEventRequest
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
    ui_blocks: List[UIBlock] = []


class RespondSymptomCheckInRequest(BaseModel):
    thread_id: str
    message_text: str


class RespondSymptomCheckInResponse(BaseModel):
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


def _baseline_symptom_tap_options() -> List[Dict[str, str]]:
    """Stable, app-consistent taps for Symptom Check-in.

    The LLM can add additional taps, but these should always be present.
    """
    tap_options = [
        {"id": "improving", "text": "😊 Feeling better"},
        {"id": "stable", "text": "😕 About the same"},
        {"id": "worsening", "text": "😣 Feeling worse"},
        {"id": "wins", "text": "🏆 Share a win"},
        {"id": "difficulties", "text": "😮‍💨 Share a difficulty"},
    ]
    tap_options = _ensure_tap_option(tap_options, "track_symptom", "📊 Track a symptom")
    tap_options = _ensure_tap_option(tap_options, "show_patterns", "🔍 Show my patterns")
    tap_options = _ensure_tap_option(tap_options, "manage_symptoms", "🧩 Manage symptoms")
    return tap_options


def _dr_auvra_progress_tap_options() -> List[Dict[str, str]]:
    """Dr. Auvra style: Better/Same/Worse (for users with yesterday's data)."""
    return [
        {"id": "better", "text": "😊 Better than yesterday"},
        {"id": "same", "text": "😐 About the same"},
        {"id": "worse", "text": "😟 Worse than yesterday"},
    ]


def _dr_auvra_first_time_tap_options() -> List[Dict[str, str]]:
    """Dr. Auvra style: Common symptoms (for first-time users)."""
    return [
        {"id": "choose_symptom::bloating", "text": "🫄 Bloating"},
        {"id": "choose_symptom::cramps", "text": "😣 Cramps"},
        {"id": "choose_symptom::fatigue", "text": "😴 Fatigue"},
        {"id": "choose_symptom::headache", "text": "🤕 Headache"},
        {"id": "choose_symptom::mood", "text": "😔 Mood changes"},
        # Free-form path: user can type what they're feeling.
        {"id": "other", "text": "✍️ Something else"},
    ]


def _default_ui_blocks_for_start(top_symptoms: Optional[List[str]] = None) -> List[UIBlock]:
    top = [s for s in (top_symptoms or []) if (s or "").strip()][:3]

    actions: List[UIBlockAction] = [
        UIBlockAction(
            id="open_symptom_manager",
            title="Manage symptoms",
            action_type="open_modal",
            payload={"modal": "SymptomManagerModal"},
            style="primary",
        )
    ]

    # Add quick-log shortcuts for the user's most common recent symptoms.
    for s in top:
        actions.append(
            UIBlockAction(
                id=f"choose_symptom::{s}",
                title=f"Log {s}",
                action_type="submit_event",
                payload={"symptom_type": s},
                style="secondary",
            )
        )

    return [
        UIBlock(
            id=str(uuid4()),
            type="quick_actions",
            title="Symptoms",
            subtitle="Quick log a symptom or open manager",
            actions=actions,
            dismissible=True,
            priority="low",
            analytics={"surface": "symptom_checkin", "reason": "entry_point"},
        )
    ]


def _symptom_slider_block(symptom_type: str) -> UIBlock:
    st = (symptom_type or "").strip()
    label = st[:1].upper() + st[1:] if st else "Symptom"
    return UIBlock(
        id=str(uuid4()),
        type="slider_1_9",
        title=f"How intense was {label} today?",
        subtitle="Tap 1 (none) to 9 (very strong)",
        payload={"symptom_type": st},
        dismissible=True,
        priority="normal",
        analytics={"surface": "symptom_checkin", "reason": "quick_log"},
    )


class TranscribeResponse(BaseModel):
    text: str


def _open_symptom_manager_block() -> UIBlock:
    return UIBlock(
        id=str(uuid4()),
        type="open_modal",
        title="Manage symptoms",
        payload={"modal": "SymptomManagerModal"},
        actions=[
            UIBlockAction(
                id="confirm_open",
                title="Open",
                action_type="open_modal",
                payload={"modal": "SymptomManagerModal"},
            )
        ],
        dismissible=True,
        priority="normal",
        analytics={"surface": "symptom_checkin", "reason": "ui_event"},
    )


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

        # Dr. Auvra style: Context-aware tap options
        # Check if user has yesterday's symptom data
        yesterday_symptom = service._get_yesterday_symptom(uid)
        
        if yesterday_symptom:
            # User has recent data - show Better/Same/Worse
            tap_options = _dr_auvra_progress_tap_options()
        else:
            # First time - show common symptoms to pick from
            tap_options = _dr_auvra_first_time_tap_options()

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "ui_blocks": [],
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

        # Use AI-generated tap options directly (Dr. Auvra style)
        # Only fall back to baseline if AI didn't provide any AND we're not complete.
        # If the AI marks the flow complete, the UI should not show unrelated taps.
        if getattr(ai_response, "is_complete", False):
            tap_options = []
        elif ai_response.tap_options:
            tap_options = [{"id": t.id, "text": t.text} for t in ai_response.tap_options]
        else:
            tap_options = _baseline_symptom_tap_options()

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


@router.post("/event", response_model=RespondSymptomCheckInResponse)
async def symptom_ui_event(
    payload: UIEventRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Handle structured UI events.

    MVP routing:
    - open modal actions return a UI block instructing the client to open a modal.
    - slider submissions can be converted into a conservative "log X N/9" message.
    - any explicit send_text in metadata is routed through the existing `/respond` flow.
    """
    try:
        uid = current_user["uid"]
        if not payload.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")

        service = SymptomCheckInService(db)

        action_id = (payload.action_id or "").strip()
        meta = payload.metadata or {}

        # Quick symptom picker -> ask for severity via an inline slider block.
        if action_id.startswith("choose_symptom::"):
            st = (meta.get("symptom_type") or "").strip() or action_id.split("::", 1)[1].strip()
            thread = service.get_thread_by_id(uid, payload.thread_id)
            display_text = (meta.get("display_text") or "").strip() or (f"Log {st}" if st else "Track a symptom")
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "meta": {"kind": "ui_symptom_pick", "symptom": st},
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
                db.add(thread)
                db.commit()
                db.refresh(thread)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option([], "track_symptom", "📊 Track a symptom")
            tap_options = _ensure_tap_option(tap_options, "show_patterns", "🔍 Show my patterns")
            tap_options = _ensure_tap_option(tap_options, "manage_symptoms", "🧩 Manage symptoms")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_symptom_slider_block(st)],
            }

        if action_id in {"open_symptom_manager", "manage_symptoms"}:
            thread = service.get_thread_by_id(uid, payload.thread_id)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option([], "manage_symptoms", "🧩 Manage symptoms")
            tap_options = _ensure_tap_option(tap_options, "track_symptom", "📊 Track a symptom")
            tap_options = _ensure_tap_option(tap_options, "show_patterns", "🔍 Show my patterns")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_open_symptom_manager_block()],
            }

        if payload.event_type == "slider_submit":
            symptom_type = (meta.get("symptom_type") or "").strip()
            sev = payload.value
            if symptom_type and isinstance(sev, int) and 1 <= sev <= 9:
                message_text = f"log {symptom_type} {sev}/9"
                thread, ai_response = await service.respond(uid, payload.thread_id, message_text)

                # Replace the internal command-style user text with a friendly UI display text
                # so the user sees what they tapped (not the parser-friendly command).
                display_text = (meta.get("display_text") or "").strip() or f"{symptom_type} {sev}/9"
                if display_text:
                    raw = list(thread.raw_messages or [])
                    for i in range(len(raw) - 1, -1, -1):
                        m = raw[i] or {}
                        if m.get("role") == "user" and (m.get("content") or "").strip() == message_text:
                            m2 = dict(m)
                            m2["content"] = display_text
                            raw[i] = m2
                            thread.raw_messages = raw
                            db.add(thread)
                            db.commit()
                            db.refresh(thread)
                            break

                history = service.format_history_for_mobile(thread)
                tap_options = [t.model_dump() for t in (ai_response.tap_options or [])]
                tap_options = _ensure_tap_option(tap_options, "track_symptom", "📊 Track a symptom")
                tap_options = _ensure_tap_option(tap_options, "show_patterns", "🔍 Show my patterns")
                tap_options = _ensure_tap_option(tap_options, "manage_symptoms", "🧩 Manage symptoms")
                return {
                    "thread_id": thread.id,
                    "local_date": thread.local_date.isoformat(),
                    "history": history,
                    "tap_options": tap_options,
                    "actionable_insights": thread.actionable_insights or {},
                    "ui_blocks": [],
                }

        send_text = (meta.get("send_text") or "").strip()
        if send_text:
            thread, ai_response = await service.respond(uid, payload.thread_id, send_text)
            history = service.format_history_for_mobile(thread)
            tap_options = [t.model_dump() for t in (ai_response.tap_options or [])]
            tap_options = _ensure_tap_option(tap_options, "track_symptom", "📊 Track a symptom")
            tap_options = _ensure_tap_option(tap_options, "show_patterns", "🔍 Show my patterns")
            tap_options = _ensure_tap_option(tap_options, "manage_symptoms", "🧩 Manage symptoms")
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
        tap_options = _ensure_tap_option([], "track_symptom", "📊 Track a symptom")
        tap_options = _ensure_tap_option(tap_options, "show_patterns", "🔍 Show my patterns")
        tap_options = _ensure_tap_option(tap_options, "manage_symptoms", "🧩 Manage symptoms")
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
