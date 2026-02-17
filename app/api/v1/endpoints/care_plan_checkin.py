"""Care Plan Check-in API endpoints (Refactored to use LangGraph)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
from uuid import uuid4
import logging
import datetime

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db, CarePlanCheckInThread, ActionPlan, ActionPlanItem, ActionPlanFeedback
from app.models.ui_blocks import UIBlock, UIBlockAction, UIEventRequest
from app.services.care_plan_checkin_service import CarePlanCheckInService
from app.services.reward_service import RewardService
from app.services.streak_service import StreakService
from app.utils.timezone_utils import get_user_current_date
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

    # Get plan_id and plan_date for today (fallback to latest)
    plan_id = saved_context.get("plan_id")
    plan_date = None
    if not plan_id:
        today = get_user_current_date(uid, service.db)
        plan = (
            service.db.query(ActionPlan)
            .filter(ActionPlan.uid == uid, ActionPlan.plan_date == today)
            .order_by(ActionPlan.created_at.desc())
            .first()
        )
        if not plan:
            plan = (
                service.db.query(ActionPlan)
                .filter(ActionPlan.uid == uid)
                .order_by(ActionPlan.plan_date.desc(), ActionPlan.created_at.desc())
                .first()
            )
        if plan:
            plan_id = plan.id
            plan_date = plan.plan_date
    else:
        plan_date = thread.local_date

    # Get refresh tokens + streak
    reward_service = RewardService(service.db)
    refresh_status = reward_service.get_refresh_status(uid)
    streak_status = StreakService(service.db).get_full_streak_status(uid)
    
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
        "targeted_action_id": saved_context.get("targeted_action_id"),
        "plan_id": plan_id,
        "barrier_type": saved_context.get("barrier_type"),
        "change_reason": saved_context.get("change_reason"),
        "alternate_candidates": saved_context.get("alternate_candidates", []),
        
        # Defaults/Context (could be loaded from streak_service/plan_generator if needed)
        "current_streak": streak_status.get("current_streak", 0),
        "refresh_tokens_available": refresh_status.get("remaining", 0),
        "refresh_tokens_unlocked": refresh_status.get("limit", 0) > 0,
        "plan_date": plan_date, "cycle_day": None, "cycle_phase": None, "primary_hormone": None,
        "current_intent": None, "user_message": None,
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
        "targeted_action_id": final_state.get("targeted_action_id"),  # CRITICAL: needed for replacement
        "plan_id": final_state.get("plan_id"),  # Needed for DB operations
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
        
        # Sanitize message - remove null bytes and other problematic characters
        message_text = payload.message_text
        if message_text:
            original_len = len(message_text)
            message_text = message_text.replace("\x00", "").replace("\u0000", "")
            # Remove other control characters except newlines/tabs
            message_text = "".join(c for c in message_text if c == "\n" or c == "\t" or ord(c) >= 32)
            if len(message_text) != original_len:
                logger.info(f"[SANITIZE] Removed {original_len - len(message_text)} problematic characters from message")
        
        # 1. Load Thread
        thread = service.get_thread_by_id(uid, payload.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        # CRITICAL FIX: Save user message to raw_messages IMMEDIATELY
        # This ensures user messages always appear in chat history, even if processing fails
        raw = list(thread.raw_messages or [])
        raw.append({
            "id": str(uuid4()),
            "role": "user",
            "content": message_text,
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        thread.raw_messages = raw
        db.add(thread)
        db.commit()
        db.refresh(thread)
            
        # 2. Reconstruct State
        state = _reconstruct_state(thread, uid, service)

        # 3. Invoke Graph
        final_state = await process_care_plan_message(state, message_text, thread_id=thread.id)

        # 4. Save Result to DB
        # NOTE: User message was already saved above, so we only save bot messages here
        raw = list(thread.raw_messages or [])
        
        # Only append new bot/assistant messages from the graph (skip user messages - already saved)
        new_msgs_count = len(final_state["messages"]) - len(state["messages"])
        if new_msgs_count > 0:
            new_msgs = final_state["messages"][-new_msgs_count:]
            for msg in new_msgs:
                # Skip user messages - already saved at the start
                if msg.get("role") == "user":
                    continue
                role = "bot" if msg["role"] == "assistant" else msg["role"]
                raw.append({
                    "id": str(uuid4()),
                    "role": role,
                    "content": msg["content"],
                    "created_at": datetime.datetime.utcnow().isoformat()
                })

        # Always append bot_response if provided
        bot_response = (final_state.get("bot_response") or "").strip()
        if bot_response:
            raw.append({
                "id": str(uuid4()),
                "role": "bot",
                "content": bot_response,
                "created_at": datetime.datetime.utcnow().isoformat()
            })

        if raw:
            thread.raw_messages = raw

        # Persist context
        _persist_state(thread, final_state)
        
        db.add(thread)
        db.commit()
        db.refresh(thread)

        # 5. Build Response
        history = service.format_history_for_mobile(thread)
        # If UI blocks are present, suppress most tap options to reduce conflicting CTAs
        # BUT always include "manage_plan" so users can access PlanManagerModal at any time
        if final_state.get("ui_blocks"):
            tap_options = [{"id": "manage_plan", "text": "🧩 Manage plan"}]
        else:
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
        
        # Convert Graph UI Blocks to Pydantic with STRICT VALIDATION
        resp_ui_blocks = []
        for block in final_state.get("ui_blocks", []):
            # Validate actions - filter out empty/invalid ones
            valid_actions = []
            for act in block.get("actions", []):
                title = act.get("title", "").strip()
                if not title:  # Skip actions with no title
                    logger.warning(f"Skipping action with empty title in block {block.get('id')}")
                    continue
                valid_actions.append(UIBlockAction(
                    id=act.get("id"), 
                    title=title, 
                    action_type=act.get("action_type", "submit_event"), 
                    payload=act.get("payload", {}),
                    style=act.get("style", "primary")
                ))
            
            # Validate block itself - must have title OR subtitle OR valid actions
            block_title = (block.get("title") or "").strip()
            block_subtitle = (block.get("description") or block.get("subtitle") or "").strip()
            
            # Skip completely empty blocks
            if not block_title and not block_subtitle and not valid_actions:
                logger.warning(f"Skipping empty UI block: {block.get('id')}")
                continue
            
            resp_ui_blocks.append(UIBlock(
                id=block.get("id", str(uuid4())),
                type=block.get("type") or "quick_actions",
                title=block_title if block_title else None,
                subtitle=block_subtitle if block_subtitle else None,
                payload=block.get("payload", {}),
                actions=valid_actions,
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
        
        # Helper: Add user tap action as a visible message in chat history
        def _add_user_tap_message(thread_obj, user_text: str):
            """Add user's tap/click action as a user message so it appears in chat."""
            raw = list(thread_obj.raw_messages or [])
            raw.append({
                "id": str(uuid4()),
                "role": "user",
                "content": user_text,
                "created_at": datetime.datetime.utcnow().isoformat(),
            })
            thread_obj.raw_messages = raw
        
        # Map action IDs to human-readable tap messages for chat display
        action_display_text = {
            "want-to-change": "I want to change my plan",
            "alternate-suggestions": "Show me alternate suggestions", 
            "manage_plan": "I want to manage my plan",
            "confirm_skip": "Yes, skip it",
            "confirm_replace": "Yes, confirm the replacement",
            "cancel_replace": "No, keep my current plan",
            "keep_as_is": "Keep it as is",
            "show_alternates": "Show me alternatives",
            "mark_done": "I completed it",
            "swap_action": "I want to swap this action",
            "ask_why": "Why is this action in my plan?",
        }
        logger.info(f"THREAD_TYPE: {type(thread)}, ATTRS: {dir(thread)}")

        # Handle action selection from change-action UI block
        if action_id.startswith("select_action_"):
            stored_state = _reconstruct_state(thread, uid, service)
            action_items = stored_state.get("action_items", [])

            # Extract item_id from action_id
            try:
                selected_item_id = int(action_id.replace("select_action_", ""))
            except ValueError:
                selected_item_id = None

            # Find selected item and index
            selected_idx = None
            selected_title = None
            for idx, item in enumerate(action_items):
                item_id = item.get("id") or item.get("item_id")
                if selected_item_id and item_id == selected_item_id:
                    selected_idx = idx
                    selected_title = item.get("title")
                    break

            if selected_idx is None:
                # Fallback: try metadata payload index
                selected_idx = meta.get("action_index") if isinstance(meta, dict) else None
                if isinstance(selected_idx, int) and 0 <= selected_idx < len(action_items):
                    selected_title = action_items[selected_idx].get("title")
                    selected_item_id = action_items[selected_idx].get("id") or action_items[selected_idx].get("item_id")

            # Add user's selection as visible message
            display_title = (meta.get("display_title") if isinstance(meta, dict) else None) or selected_title or "That action"
            intent = meta.get("intent") if isinstance(meta, dict) else None
            is_alternates = intent in {"request_alternates", "negotiate"}
            _add_user_tap_message(thread, f"Alternates for: {display_title}" if is_alternates else f"Change: {display_title}")

            # Persist targeted action in thread context
            insights = dict(thread.actionable_insights or {})
            if selected_idx is not None:
                insights["targeted_action_index"] = selected_idx
            if selected_item_id is not None:
                insights["targeted_action_id"] = selected_item_id
            thread.actionable_insights = insights

            # Reconstruct state with updated insights and run change flow
            stored_state = _reconstruct_state(thread, uid, service)
            user_message = f"Show me alternatives for {display_title}" if is_alternates else f"I want to change {display_title}"
            result = await process_care_plan_message(
                state=stored_state,
                user_message=user_message,
                thread_id=thread.id
            )

            # Update thread with bot response
            bot_response = result.get("bot_response", "")
            if bot_response:
                raw_messages = list(thread.raw_messages or [])
                raw_messages.append({
                    "id": str(uuid4()),
                    "role": "assistant",
                    "content": bot_response,
                    "created_at": datetime.datetime.utcnow().isoformat(),
                })
                thread.raw_messages = raw_messages

            _persist_state(thread, result)
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
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
                    type=block.get("type") or "quick_actions",
                    title=block.get("title"),
                    subtitle=block.get("subtitle"),
                    payload=block.get("payload", {}),
                    actions=actions,
                    dismissible=True, priority="high",
                    analytics={"surface": "care_plan_checkin", "source": "action_select"}
                ))

            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": [],
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": resp_ui_blocks,
            }
        
        # Handle alternate selection (select_alt_0, select_alt_1, etc.)
        if action_id.startswith("select_alt_"):
            logger.info(f"[SELECT_ALT] Step 1: Parsing index from {action_id}")
            try:
                selected_idx = int(action_id.replace("select_alt_", ""))
            except ValueError:
                selected_idx = 0
            
            # Get the alternate title from stored candidates for display
            stored_state = _reconstruct_state(thread, uid, service)
            candidates = stored_state.get("alternate_candidates", [])
            if candidates and selected_idx < len(candidates):
                alt_title = candidates[selected_idx].get("title", f"Option {selected_idx + 1}") if isinstance(candidates[selected_idx], dict) else f"Option {selected_idx + 1}"
            else:
                alt_title = f"Option {selected_idx + 1}"
            
            # Add user's selection as visible message
            _add_user_tap_message(thread, f"I'll go with: {alt_title}")
            
            logger.info(f"[SELECT_ALT] Step 2: Index={selected_idx}, reconstructing state...")
            
            # Load current state from thread
            stored_state = _reconstruct_state(thread, uid, service)
            logger.info(f"[SELECT_ALT] Step 3: State reconstructed, invoking graph...")
            
            # Process the selection through LangGraph
            result = await process_alternate_selection(
                state=stored_state,
                selected_index=selected_idx,
                thread_id=thread.id
            )

            # Handle LangGraph interrupt (confirmation)
            interrupt_payloads = result.get("__interrupt__") or []
            if interrupt_payloads:
                payload = interrupt_payloads[0].value if hasattr(interrupt_payloads[0], "value") else interrupt_payloads[0]
                ui_block = UIBlock(
                    id=str(uuid4()),
                    type="quick_actions",
                    title="Confirm replacement",
                    subtitle=payload.get("message") if isinstance(payload, dict) else "Confirm this replacement?",
                    actions=[
                        UIBlockAction(id="confirm_replace", title="Yes, confirm", action_type="submit_event", style="primary"),
                        UIBlockAction(id="cancel_replace", title="No, keep plan", action_type="submit_event", style="secondary"),
                    ],
                    dismissible=True,
                    priority="high",
                    analytics={"surface": "care_plan_checkin", "source": "langgraph_interrupt"}
                )

                # Persist state for resume
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
                    "ui_blocks": [ui_block],
                }
            logger.info(f"[SELECT_ALT] Step 4: Graph completed, result keys={list(result.keys())}")
            
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
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, "Yes, skip it"))
            
            stored_state = _reconstruct_state(thread, uid, service)
            
            # Get the targeted action to mark as skipped
            targeted_action_id = stored_state.get("targeted_action_id")
            targeted_action_idx = stored_state.get("targeted_action_index")
            action_items = stored_state.get("action_items", [])
            
            # Find the item to skip (by ID or index)
            skipped_item = None
            skipped_item_title = "the action"
            if targeted_action_id:
                skipped_item = db.query(ActionPlanItem).filter(
                    ActionPlanItem.id == targeted_action_id
                ).first()
            elif targeted_action_idx is not None and 0 <= targeted_action_idx < len(action_items):
                item_data = action_items[targeted_action_idx]
                item_id = item_data.get("id") or item_data.get("item_id")
                if item_id:
                    skipped_item = db.query(ActionPlanItem).filter(
                        ActionPlanItem.id == item_id
                    ).first()
            
            if skipped_item:
                skipped_item_title = skipped_item.title or "the action"
                
                # Record skip feedback for GPT memory and carry-forward tracking
                feedback = ActionPlanFeedback(
                    uid=uid,
                    plan_id=skipped_item.plan_id,
                    item_id=skipped_item.id,
                    feedback_type="skipped",
                    action_title=skipped_item.title,
                    action_category=skipped_item.category,
                    target_hormone=skipped_item.target_hormone,
                    feedback_source="care_plan_checkin",
                    feedback_text="Skipped via chat - will be carried forward",
                    created_at=datetime.datetime.utcnow()
                )
                db.add(feedback)
                
                # Store skipped item ID in thread for carry-forward during next plan generation
                insights = dict(thread.actionable_insights or {})
                skipped_items = insights.get("skipped_item_ids", [])
                if skipped_item.id not in skipped_items:
                    skipped_items.append(skipped_item.id)
                insights["skipped_item_ids"] = skipped_items
                thread.actionable_insights = insights
                
                logger.info(f"[SKIP] Marked item {skipped_item.id} ({skipped_item_title}) as skipped for carry-forward")
            
            # Process through LangGraph for response
            result = await process_care_plan_message(
                state=stored_state,
                user_message="Yes, skip it",
                thread_id=thread.id
            )
            
            # Customize response to mention carry-forward
            bot_response = result.get("bot_response", f"I've skipped {skipped_item_title} for you.")
            if skipped_item and "carry" not in bot_response.lower():
                bot_response = f"Got it, I've skipped {skipped_item_title}. It'll be carried forward to tomorrow's plan so you can try it then. 💪"
            
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
        
        # Handle interrupt confirmations
        if action_id in ["confirm_replace", "cancel_replace", "keep_as_is"]:
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, action_id.replace("_", " ").title()))
            
            stored_state = _reconstruct_state(thread, uid, service)
            resume_value = True if action_id == "confirm_replace" else False
            result = await process_alternate_selection(
                state=stored_state,
                thread_id=thread.id,
                resume=resume_value
            )

            bot_response = result.get("bot_response", "All set.")
            raw_messages = list(thread.raw_messages or [])
            raw_messages.append({
                "id": str(uuid4()),
                "role": "assistant",
                "content": bot_response,
                "created_at": datetime.datetime.utcnow().isoformat(),
            })
            thread.raw_messages = raw_messages

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
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, "Show me alternatives"))
            
            stored_state = _reconstruct_state(thread, uid, service)
            result = await process_care_plan_message(
                state=stored_state,
                user_message="Show me alternatives",
                thread_id=thread.id
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
                    type=block.get("type") or "quick_actions",
                    title=block.get("title"),
                    subtitle=block.get("subtitle"),
                    payload=block.get("payload", {}),
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
        
        # Handle mark_done, swap_action, ask_why from choice buttons
        if action_id in ["mark_done", "swap_action", "ask_why"]:
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, action_id.replace("_", " ").title()))
            
            stored_state = _reconstruct_state(thread, uid, service)
            
            # Convert to natural language message
            message_map = {
                "mark_done": "I completed it",
                "swap_action": "I want to swap this action",
                "ask_why": "Why is this action in my plan?"
            }
            user_message = message_map.get(action_id, action_id)
            
            result = await process_care_plan_message(
                state=stored_state,
                user_message=user_message,
                thread_id=thread.id
            )
            
            bot_response = result.get("bot_response", "")
            if bot_response:
                raw_messages = list(thread.raw_messages or [])
                raw_messages.append({
                    "id": str(uuid4()),
                    "role": "assistant",
                    "content": bot_response,
                    "created_at": datetime.datetime.utcnow().isoformat(),
                })
                thread.raw_messages = raw_messages
            
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
                    type=block.get("type") or "quick_actions",
                    title=block.get("title"),
                    subtitle=block.get("subtitle"),
                    payload=block.get("payload", {}),
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
        
        # Handle main tap options from start screen (want-to-change, alternate-suggestions, manage_plan)
        tap_to_message = {
            "want-to-change": "I want to change my plan",
            "alternate-suggestions": "Show me alternate suggestions",
            "manage_plan": "I want to manage my plan",
        }
        logger.info(f"[EVENT] Checking tap_to_message for action_id='{action_id}', available={list(tap_to_message.keys())}")
        
        if action_id in tap_to_message:
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, tap_to_message[action_id]))
            
            stored_state = _reconstruct_state(thread, uid, service)
            user_message = tap_to_message[action_id]
            
            result = await process_care_plan_message(
                state=stored_state,
                user_message=user_message,
                thread_id=thread.id
            )
            
            bot_response = result.get("bot_response", "")
            if bot_response:
                raw_messages = list(thread.raw_messages or [])
                raw_messages.append({
                    "id": str(uuid4()),
                    "role": "assistant",
                    "content": bot_response,
                    "created_at": datetime.datetime.utcnow().isoformat(),
                })
                thread.raw_messages = raw_messages
            
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
                    type=block.get("type") or "quick_actions",
                    title=block.get("title"),
                    subtitle=block.get("subtitle"),
                    payload=block.get("payload", {}),
                    actions=actions,
                    dismissible=True, priority="high",
                    analytics={"surface": "care_plan_checkin", "source": "tap_option"}
                ))
            
            # Rebuild tap options - always include manage_plan for access to PlanManagerModal
            if not resp_ui_blocks:
                tap_options = [
                    {"id": "want-to-change", "text": "👎 I want to change it"},
                    {"id": "alternate-suggestions", "text": "🔁 I want alternate suggestions"},
                    {"id": "manage_plan", "text": "🧩 Manage plan"},
                ]
            else:
                # When UI blocks are present, still show manage_plan so user can access modal
                tap_options = [{"id": "manage_plan", "text": "🧩 Manage plan"}]
            
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": resp_ui_blocks,
            }
        
        # Unknown action - return with manage_plan option
        logger.warning(f"Unknown UI event action: {action_id}")
        history = service.format_history_for_mobile(thread)
        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": [{"id": "manage_plan", "text": "🧩 Manage plan"}],
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": [],
        }
        
    except Exception as e:
        logger.error(f"UI event error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
