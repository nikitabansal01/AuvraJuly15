"""Care Plan Check-in API endpoints (Refactored to use LangGraph)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
from uuid import uuid4
import logging
import datetime

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.ui_blocks import UIBlock, UIBlockAction
from app.services.care_plan_checkin_service import CarePlanCheckInService
# Import Graph
from app.langgraph.graphs.care_plan_checkin import process_care_plan_message, CarePlanCheckInState

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Models ---
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

class TranscribeResponse(BaseModel):
    text: str

# --- Helpers ---
def _ensure_tap_option(tap_options: List[Dict[str, str]], option_id: str, text: str) -> List[Dict[str, str]]:
    existing_ids = {t.get("id") for t in (tap_options or [])}
    if option_id in existing_ids:
        return tap_options
    return list(tap_options or []) + [{"id": option_id, "text": text}]

def _default_ui_blocks_for_start() -> List[UIBlock]:
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
            analytics={"surface": "care_plan_checkin", "reason": "entry_point"}
        )
    ]

def _default_tap_options() -> List[Dict[str, str]]:
    return [
        {"id": "want-to-change", "text": "👎 I want to change it"},
        {"id": "alternate-suggestions", "text": "🔁 I want alternate suggestions"},
    ]

# --- Endpoints ---

@router.post("/start", response_model=StartCarePlanCheckInResponse)
async def start_care_plan_checkin(
    force_new: bool = False,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)
        if force_new:
            thread = service.create_new_thread(uid)
        else:
            thread = service.get_or_create_today_thread(uid)
            
        history = service.format_history_for_mobile(thread)
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
        logger.error(f"Start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/respond", response_model=RespondCarePlanCheckInResponse)
async def respond_care_plan_checkin(
    payload: RespondCarePlanCheckInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Process message using LangGraph engine.
    Replaces old manual logic with graph-based state machine.
    """
    try:
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)
        
        # 1. Load Thread
        thread = service.get_thread_by_id(uid, payload.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
            
        # 2. Prepare LangGraph State
        items = service.get_plan_items_for_ui(uid, limit=12) # Fetch active items
        saved_context = thread.actionable_insights or {}
        
        # Helper to map legacy DB message format to Graph format
        graph_messages = []
        for m in (thread.raw_messages or []):
            role = m.get("role")
            if role == "bot": role = "assistant" # Graph usually uses 'assistant'
            graph_messages.append({"role": role, "content": m.get("content")})

        state: CarePlanCheckInState = {
            "user_id": uid,
            "thread_id": thread.id,
            "message_id": str(uuid4()),
            "action_items": [i for i in items], # Copy
            "messages": graph_messages,
            
            # Restore persistent context
            "workflow_stage": saved_context.get("workflow_stage"),
            "targeted_action_index": saved_context.get("targeted_action_index"),
            "barrier_type": saved_context.get("barrier_type"),
            "change_reason": saved_context.get("change_reason"),
            "alternate_candidates": saved_context.get("alternate_candidates", []),
            "current_streak": 0, # Should load real streak if needed
            "refresh_tokens_available": 2, # simplified
            "refresh_tokens_unlocked": True,
            # Defaults
            "plan_id": None, "plan_date": None, "cycle_day": None, "cycle_phase": None, "primary_hormone": None,
            "current_intent": None, "user_message": None, "targeted_action_id": None,
            "selected_alternate_index": None, "selected_alternate": None,
            "ui_blocks": [], "bot_response": "", "actions_to_execute": [], "phase": "loaded", "error": None
        }

        # 3. Invoke Graph
        final_state = await process_care_plan_message(state, payload.message_text)

        # 4. Save Result to DB
        # Identify new messages
        new_msgs_count = len(final_state["messages"]) - len(graph_messages)
        if new_msgs_count > 0:
            new_msgs = final_state["messages"][-new_msgs_count:]
            raw = list(thread.raw_messages or [])
            for msg in new_msgs:
                role = "bot" if msg["role"] == "assistant" else msg["role"]
                raw.append({
                    "id": str(uuid4()),
                    "role": role,
                    "content": msg["content"],
                    "created_at": datetime.datetime.utcnow().isoformat()
                })
            thread.raw_messages = raw

        # Persist context
        new_insights = dict(thread.actionable_insights or {})
        new_insights.update({
            "workflow_stage": final_state.get("workflow_stage"),
            "targeted_action_index": final_state.get("targeted_action_index"),
            "barrier_type": final_state.get("barrier_type"),
            "change_reason": final_state.get("change_reason"),
            "alternate_candidates": final_state.get("alternate_candidates"),
        })
        thread.actionable_insights = new_insights
        
        db.add(thread)
        db.commit()
        db.refresh(thread)

        # 5. Build Response
        history = service.format_history_for_mobile(thread)
        tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
        
        # Convert Graph UI Blocks to Pydantic
        resp_ui_blocks = []
        for block in final_state.get("ui_blocks", []):
            actions = []
            for act in block.get("actions", []):
                actions.append(UIBlockAction(
                    id=act.get("id"), title=act.get("title"), 
                    action_type="submit_event", payload=act.get("payload", {}),
                    style=act.get("style", "primary")
                ))
            resp_ui_blocks.append(UIBlock(
                id=block.get("id", str(uuid4())),
                type="quick_actions", # Force quick actions for mobile compatibility
                title=block.get("title"),
                subtitle=block.get("description") or block.get("subtitle"),
                actions=actions,
                dismissible=True, priority="high", 
                analytics={"surface": "care_plan_checkin", "source": "langgraph"}
            ))

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": resp_ui_blocks,
        }

    except Exception as e:
        logger.error(f"Respond error: {e}")
        # Return fallback error message to user via 500? Or smooth error?
        raise HTTPException(status_code=500, detail=str(e))
