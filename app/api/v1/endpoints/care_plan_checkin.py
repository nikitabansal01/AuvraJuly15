"""Care Plan Check-in API endpoints (Refactored to use LangGraph)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
from uuid import uuid4
import logging
import datetime

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db, CarePlanCheckInThread
from app.models.ui_blocks import UIBlock, UIBlockAction, UIEventRequest
from app.services.care_plan_checkin_service import CarePlanCheckInService
# Import Graph
from app.langgraph.graphs.care_plan_checkin import (
    process_care_plan_message, 
    process_alternate_selection,
    CarePlanCheckInState
)

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
def _reconstruct_state(thread: CarePlanCheckInThread, uid: str, service: CarePlanCheckInService) -> CarePlanCheckInState:
    """Helper to reconstruct full LangGraph state from thread and service context."""
    saved_context = thread.actionable_insights or {}
    
    # Build message history (subset for context)
    graph_messages = []
    for rm in (thread.raw_messages or [])[-8:]:
        graph_messages.append({
            "role": "assistant" if rm["role"] == "bot" else rm["role"], 
            "content": rm["content"]
        })

    # Get current plan items
    items = service.get_plan_items_for_ui(uid=uid)
    
    # Reconstruct state
    return {
        "user_id": uid,
        "thread_id": thread.id,
        "message_id": str(uuid4()),
        "action_items": [i for i in items],
        "messages": graph_messages,
        
        # Restore persistent context
        "workflow_stage": saved_context.get("workflow_stage"),
        "targeted_action_index": saved_context.get("targeted_action_index"),
        "barrier_type": saved_context.get("barrier_type"),
        "change_reason": saved_context.get("change_reason"),
        "alternate_candidates": saved_context.get("alternate_candidates", []),
        
        # Defaults/Context (could be loaded from streak_service/plan_generator if needed)
        "current_streak": 0, 
        "refresh_tokens_available": 2,
        "refresh_tokens_unlocked": True,
        "plan_id": None, "plan_date": None, "cycle_day": None, "cycle_phase": None, "primary_hormone": None,
        "current_intent": None, "user_message": None, "targeted_action_id": None,
        "selected_alternate_index": None, "selected_alternate": None,
        "ui_blocks": [], "bot_response": "", "actions_to_execute": [], 
        "phase": "loaded", "error": None
    }

def _persist_state(thread: CarePlanCheckInThread, final_state: CarePlanCheckInState):
    """Update thread actionable_insights with persistent LangGraph state."""
    new_insights = dict(thread.actionable_insights or {})
    
    # Serialize Pydantic objects to dicts
    def serialize(obj):
        if hasattr(obj, "model_dump"): return obj.model_dump()
        if hasattr(obj, "dict"): return obj.dict()
        return obj

    raw_candidates = final_state.get("alternate_candidates", [])
    serialized_candidates = [serialize(c) for c in raw_candidates]

    new_insights.update({
        "workflow_stage": final_state.get("workflow_stage"),
        "targeted_action_index": final_state.get("targeted_action_index"),
        "barrier_type": final_state.get("barrier_type"),
        "change_reason": final_state.get("change_reason"),
        "alternate_candidates": serialized_candidates,
    })
    thread.actionable_insights = new_insights

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
        logger.info(f"CARE_PLAN_RESPOND_V2 for uid={uid}")
        
        # 1. Load Thread
        thread = service.get_thread_by_id(uid, payload.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
            
        # 2. Reconstruct State
        state = _reconstruct_state(thread, uid, service)

        # 3. Invoke Graph
        final_state = await process_care_plan_message(state, payload.message_text)

        # 4. Save Result to DB
        # Identify new messages
        new_msgs_count = len(final_state["messages"]) - len(state["messages"])
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
        _persist_state(thread, final_state)
        
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


@router.post("/event", response_model=RespondCarePlanCheckInResponse)
async def care_plan_ui_event(
    payload: UIEventRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Handle UI block events like button clicks.
    
    Routes:
    - select_alt_N: User selected alternate action N, process replacement
    - confirm_skip: User confirmed skip
    - show_alternates: User wants to see alternatives
    """
    try:
        uid = current_user["uid"]
        if not payload.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")
        
        service = CarePlanCheckInService(db)
        thread = service.get_thread_by_id(uid, payload.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        action_id = (payload.action_id or "").strip()
        meta = payload.metadata or {}
        
        logger.info(f"CARE_PLAN_EVENT_V2: action_id={action_id}, thread={payload.thread_id}")
        logger.info(f"THREAD_TYPE: {type(thread)}, ATTRS: {dir(thread)}")
        
        # Handle alternate selection (select_alt_0, select_alt_1, etc.)
        if action_id.startswith("select_alt_"):
            try:
                selected_idx = int(action_id.replace("select_alt_", ""))
            except ValueError:
                selected_idx = 0
            
            # Load current state from thread
            stored_state = _reconstruct_state(thread, uid, service)
            
            # Process the selection through LangGraph
            result = await process_alternate_selection(
                state=stored_state,
                selected_index=selected_idx
            )
            
            # Update thread with bot response
            bot_response = result.get("bot_response", "I've made the change for you!")
            raw_messages = list(thread.raw_messages or [])
            raw_messages.append({
                "id": str(uuid4()),
                "role": "assistant",
                "content": bot_response,
                "created_at": datetime.datetime.utcnow().isoformat(),
            })
            thread.raw_messages = raw_messages
            
            # Persist state correctly
            _persist_state(thread, result)
            
            db.add(thread)
            db.commit()
            db.refresh(thread)
            
            # Format response
            history = service.format_history_for_mobile(thread)
            
            # Check for refresh actions
            actions_to_execute = result.get("actions_to_execute", [])
            actionable_insights = thread.actionable_insights or {}
            if any(a.get("type") == "refresh_plan" for a in actions_to_execute):
                actionable_insights["refresh_plan"] = True
            
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": [],  # Clear CTAs after selection
                "actionable_insights": actionable_insights,
                "ui_blocks": [],  # Clear UI blocks after selection
            }
        
        # Handle confirm_skip
        if action_id == "confirm_skip":
            stored_state = _reconstruct_state(thread, uid, service)
            # Process as a skip confirmation through regular respond
            result = await process_care_plan_message(
                state=stored_state,
                user_message="Yes, skip it"
            )
            
            bot_response = result.get("bot_response", "I've skipped that action for you.")
            raw_messages = list(thread.raw_messages or [])
            raw_messages.append({
                "id": str(uuid4()),
                "role": "assistant",
                "content": bot_response,
                "created_at": datetime.datetime.utcnow().isoformat(),
            })
            thread.raw_messages = raw_messages
            
            # Persist state
            _persist_state(thread, result)
            
            db.add(thread)
            db.commit()
            db.refresh(thread)
            
            history = service.format_history_for_mobile(thread)
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": [],
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [],
            }
        
        # Handle show_alternates
        if action_id == "show_alternates":
            stored_state = _reconstruct_state(thread, uid, service)
            result = await process_care_plan_message(
                state=stored_state,
                user_message="Show me alternatives"
            )
            
            bot_response = result.get("bot_response", "Here are some alternatives:")
            raw_messages = list(thread.raw_messages or [])
            raw_messages.append({
                "id": str(uuid4()),
                "role": "assistant",
                "content": bot_response,
                "created_at": datetime.datetime.utcnow().isoformat(),
            })
            thread.raw_messages = raw_messages
            
            # Persist state
            _persist_state(thread, result)
            
            db.add(thread)
            db.commit()
            db.refresh(thread)
            
            history = service.format_history_for_mobile(thread)
            
            # Format UI blocks from result
            resp_ui_blocks = []
            for block in result.get("ui_blocks", []):
                actions = []
                for a in block.get("actions", []):
                    actions.append(UIBlockAction(
                        id=a.get("id", str(uuid4())),
                        title=a.get("title", "Action"),
                        action_type=a.get("action_type", "submit_event"),
                        style=a.get("style", "primary")
                    ))
                resp_ui_blocks.append(UIBlock(
                    id=block.get("id", str(uuid4())),
                    type="quick_actions",
                    title=block.get("title"),
                    subtitle=block.get("subtitle"),
                    actions=actions,
                    dismissible=True, priority="high",
                    analytics={"surface": "care_plan_checkin", "source": "langgraph_event"}
                ))
            
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": [],
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": resp_ui_blocks,
            }
        
        # Unknown action - return empty
        logger.warning(f"Unknown UI event action: {action_id}")
        history = service.format_history_for_mobile(thread)
        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": [],
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": [],
        }
        
    except Exception as e:
        logger.error(f"UI event error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
