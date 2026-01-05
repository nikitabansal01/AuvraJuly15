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


def _default_tap_options() -> List[Dict[str, str]]:
    return [
        {"id": "want-to-change", "text": "👎 I want to change it"},
        {"id": "alternate-suggestions", "text": "🔁 I want alternate suggestions"},
    ]


def _should_exclude_tap_option(option_id: str, text: str) -> bool:
    oid = (option_id or "").strip().lower()
    t = (text or "").strip().lower()
    # Product decision: do not show "skip actions" in care plan check-in.
    if oid == "skip-actions":
        return True
    if "skip" in t and "action" in t:
        return True
    return False


def _looks_like_confirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return t in {"ok", "okay", "okk", "yes", "y", "yep", "yeah", "sure", "do it", "go ahead", "confirm"}


def _looks_like_change_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ["change", "replace", "swap", "skip", "alternate", "another option", "not for me"])


def _pick_replace_block(items: List[Dict[str, Any]]) -> UIBlock:
    actions: List[UIBlockAction] = []
    for it in items[:8]:
        item_id = it.get("item_id")
        title = (it.get("title") or "").strip()
        if not item_id or not title:
            continue
        actions.append(
            UIBlockAction(
                id=f"care_plan_replace_pick_{item_id}",
                title=f"Replace: {title}",
                action_type="submit_event",
                payload={"item_id": int(item_id)},
                style="secondary",
            )
        )

    actions.append(
        UIBlockAction(
            id="open_plan_manager",
            title="Open full plan manager",
            action_type="open_modal",
            payload={"modal": "PlanManagerModal"},
            style="ghost",
        )
    )

    return UIBlock(
        id=str(uuid4()),
        type="quick_actions",
        title="Which action should we change?",
        subtitle="Pick one — I’ll replace it with a fresh alternative.",
        actions=actions,
        dismissible=True,
        priority="normal",
        analytics={"surface": "care_plan_checkin", "reason": "change_intent"},
    )


def _confirm_replace_block(item_id: int) -> UIBlock:
    return UIBlock(
        id=str(uuid4()),
        type="quick_actions",
        title="Replace this action?",
        subtitle="I’ll swap it with a similar alternative (uses a plan refresh token if available).",
        actions=[
            UIBlockAction(
                id="care_plan_replace_confirm",
                title="Yes, replace it",
                action_type="submit_event",
                payload={"item_id": int(item_id), "reason": "User requested change via chat"},
                style="primary",
            ),
            UIBlockAction(
                id="care_plan_replace_cancel",
                title="No, keep it",
                action_type="submit_event",
                payload={},
                style="secondary",
            ),
        ],
        dismissible=True,
        priority="high",
        analytics={"surface": "care_plan_checkin", "reason": "confirm_replace"},
    )


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
        tap_options = _default_tap_options()
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

        # If there's a pending replace and the user confirms naturally, execute it.
        thread = service.get_thread_by_id(uid, payload.thread_id)
        pending = (thread.actionable_insights or {}).get("pending_replace") if thread else None
        if pending and _looks_like_confirmation(payload.message_text):
            item_id = int(pending.get("item_id") or 0)
            reason = (pending.get("reason") or "User confirmed replace").strip()
            if item_id:
                # Append the user's confirmation
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": payload.message_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
                # Clear pending
                ai = dict(thread.actionable_insights or {})
                ai.pop("pending_replace", None)
                thread.actionable_insights = ai
                db.add(thread)
                db.commit()
                db.refresh(thread)

                result = await service.replace_action_item(uid, item_id, reason)
                # Append bot response
                raw = list(thread.raw_messages or [])
                if result.get("success"):
                    repl = result.get("replacement_action") or {}
                    repl_title = (repl.get("title") or repl.get("specific_action") or "a fresh alternative").strip()
                    raw.append(
                        {
                            "id": str(uuid4()),
                            "role": "bot",
                            "content": f"Done — I replaced it with: {repl_title}. Want to change anything else?",
                            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                        }
                    )
                else:
                    raw.append(
                        {
                            "id": str(uuid4()),
                            "role": "bot",
                            "content": result.get("error") or "Sorry — I couldn't replace that right now.",
                            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                        }
                    )
                thread.raw_messages = raw
                db.add(thread)
                db.commit()
                db.refresh(thread)

                history = service.format_history_for_mobile(thread)
                tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                return {
                    "thread_id": thread.id,
                    "local_date": thread.local_date.isoformat(),
                    "history": history,
                    "tap_options": tap_options,
                    "actionable_insights": thread.actionable_insights or {},
                    "ui_blocks": [_open_plan_manager_block()],
                }

        thread, ai_response = await service.respond(uid, payload.thread_id, payload.message_text)
        history = service.format_history_for_mobile(thread)

        tap_options = _default_tap_options()
        for t in (ai_response.tap_options or []):
            if _should_exclude_tap_option(t.id, t.text):
                continue
            tap_options = _ensure_tap_option(tap_options, t.id, t.text)
        tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")

        ui_blocks: List[UIBlock] = []
        if _looks_like_change_intent(payload.message_text) or (ai_response.insights and ai_response.insights.plan_changes_requested):
            items = service.get_plan_items_for_ui(uid, limit=8)
            if items:
                ui_blocks.append(_pick_replace_block(items))

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": ui_blocks,
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

        # Replace flow (picker -> confirm -> execute)
        if action_id.startswith("care_plan_replace_pick_"):
            try:
                item_id = int(action_id.split("care_plan_replace_pick_", 1)[1])
            except Exception:
                item_id = 0
            thread = service.get_thread_by_id(uid, payload.thread_id)
            display_text = (meta.get("display_text") or "").strip() or "Replace this action"
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
            if item_id:
                ai = dict(thread.actionable_insights or {})
                ai["pending_replace"] = {"item_id": item_id, "reason": "User requested change via UI"}
                thread.actionable_insights = ai
            db.add(thread)
            db.commit()
            db.refresh(thread)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_confirm_replace_block(item_id)] if item_id else [],
            }

        if action_id == "care_plan_replace_cancel":
            thread = service.get_thread_by_id(uid, payload.thread_id)
            display_text = (meta.get("display_text") or "").strip() or "Cancel"
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
            ai = dict(thread.actionable_insights or {})
            ai.pop("pending_replace", None)
            thread.actionable_insights = ai
            db.add(thread)
            db.commit()
            db.refresh(thread)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [],
            }

        if action_id == "care_plan_replace_confirm":
            thread = service.get_thread_by_id(uid, payload.thread_id)
            item_id = int((meta.get("item_id") or 0) or 0)
            reason = (meta.get("reason") or "User requested change via UI").strip()
            result = await service.replace_action_item(uid, item_id, reason) if item_id else {"success": False, "error": "Invalid item"}

            raw = list(thread.raw_messages or [])
            display_text = (meta.get("display_text") or "").strip() or "Yes"
            if display_text:
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
            if result.get("success"):
                repl = result.get("replacement_action") or {}
                repl_title = (repl.get("title") or repl.get("specific_action") or "a fresh alternative").strip()
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": f"Done — I replaced it with: {repl_title}.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                # Clear pending on success
                ai = dict(thread.actionable_insights or {})
                ai.pop("pending_replace", None)
                thread.actionable_insights = ai
            else:
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": result.get("error") or "Sorry — I couldn't replace that right now.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )

            thread.raw_messages = raw
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
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
            tap_options = _default_tap_options()
            for t in (ai_response.tap_options or []):
                if _should_exclude_tap_option(t.id, t.text):
                    continue
                tap_options = _ensure_tap_option(tap_options, t.id, t.text)
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
        tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
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
