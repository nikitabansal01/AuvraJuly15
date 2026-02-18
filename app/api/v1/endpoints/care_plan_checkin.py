"""Care Plan Check-in API endpoints (Refactored to use LangGraph)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
from uuid import uuid4
import logging
import datetime
from time import perf_counter

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db, CarePlanCheckInThread, ActionPlan, ActionPlanItem, ActionPlanFeedback, UserProfile
from app.models.ui_blocks import UIBlock, UIBlockAction, UIEventRequest
from app.services.care_plan_checkin_service import CarePlanCheckInService
from app.services.reward_service import RewardService
from app.services.streak_service import StreakService
from app.utils.timezone_utils import get_user_current_date
from app.api.v1.endpoints._chatbot_runtime import (
    build_chatbot_response_payload,
    ensure_actionable_insights,
    ensure_event_not_duplicate,
)
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
    ui_blocks: List[Dict[str, Any]] = []

class StartCarePlanCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []
    actionable_insights: Dict[str, Any] = {}
    ui_blocks: List[UIBlock] = []
    trace: Optional[Dict[str, Any]] = None

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
    trace: Optional[Dict[str, Any]] = None

class TranscribeResponse(BaseModel):
    text: str

# --- Helpers ---
async def _reconstruct_state(thread: CarePlanCheckInThread, uid: str, service: CarePlanCheckInService) -> CarePlanCheckInState:
    """Helper to reconstruct full LangGraph state from thread and service context.
    
    CRITICAL FIX: Now async and loads unified context + cycle info.
    Previously, unified_context and formatted_context were NEVER loaded here,
    causing ALL handlers to receive empty context → generic "I'm here to help!" responses.
    """
    saved_context = ensure_actionable_insights(thread.actionable_insights or {}, flow="care_plan_checkin")
    
    # Build message history - CRITICAL FIX: Use 20 messages (was 8) for proper context
    # 8 messages = only 4 turns of conversation → context lost after 4 exchanges
    # 20 messages = 10 turns → much better conversational continuity
    graph_messages = []
    for rm in (thread.raw_messages or [])[-20:]:
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
    user_timezone = thread.timezone
    if not user_timezone:
        profile = (
            service.db.query(UserProfile)
            .filter(UserProfile.uid == uid)
            .first()
        )
        user_timezone = getattr(profile, "current_timezone", None) or getattr(profile, "timezone", None)

    reward_service = RewardService(service.db)
    refresh_status = reward_service.get_refresh_status(uid)
    streak_status = StreakService(service.db).get_full_streak_status(uid, user_timezone=user_timezone)
    
    # CRITICAL FIX: Load cycle info for personalized responses
    # Previously was always None → handlers couldn't reference cycle phase
    from app.langgraph.helpers.database_helpers import get_cycle_info
    cycle_info = get_cycle_info(uid, service.db)
    
    # CRITICAL FIX: Load unified cross-chatbot context
    # Without this, ALL LLM handlers get empty formatted_context → generic responses
    from app.langgraph.memory import get_unified_context, format_context_for_prompt
    try:
        unified_ctx = await get_unified_context(uid, "care_plan_checkin")
        formatted_ctx = format_context_for_prompt(unified_ctx) if unified_ctx else ""
    except Exception as e:
        logger.warning(f"Failed to load unified context for {uid}: {e}")
        unified_ctx = {}
        formatted_ctx = ""
    
    # Reconstruct state
    persisted_ui_blocks = saved_context.get("ui_blocks", [])
    persisted_ui_elements = saved_context.get("ui_elements", persisted_ui_blocks)
    persisted_action_plan = saved_context.get("action_plan")
    hydrated_action_plan = persisted_action_plan if isinstance(persisted_action_plan, list) and persisted_action_plan else [i for i in items]

    return {
        "user_id": uid,
        "thread_id": thread.id,
        "message_id": str(uuid4()),
        "action_items": [i for i in items],
        "action_plan": hydrated_action_plan,
        "messages": graph_messages,
        
        # Restore persistent context
        "workflow_stage": saved_context.get("workflow_stage"),
        "targeted_action_index": saved_context.get("targeted_action_index"),
        "targeted_action_id": saved_context.get("targeted_action_id"),
        "plan_id": plan_id,
        "barrier_type": saved_context.get("barrier_type"),
        "change_reason": saved_context.get("change_reason"),
        "alternate_candidates": saved_context.get("alternate_candidates", []),
        
        # FIXED: Load actual cycle info instead of None
        "current_streak": streak_status.get("current_streak", 0),
        "streak": int(saved_context.get("streak", streak_status.get("current_streak", 0)) or 0),
        "refresh_tokens_available": refresh_status.get("remaining", 0),
        "refresh_tokens_unlocked": refresh_status.get("limit", 0) > 0,
        "plan_date": plan_date,
        "cycle_day": cycle_info.get("cycle_day"),
        "cycle_phase": cycle_info.get("phase"),
        "primary_hormone": cycle_info.get("primary_hormone"),
        "current_intent": None, "user_message": None,
        "selected_alternate_index": None, "selected_alternate": None,
        "last_options_shown": saved_context.get("last_options_shown"),
        "user_preferences": saved_context.get("user_preferences", {}) or {},
        "ui_blocks": persisted_ui_blocks,
        "ui_elements": persisted_ui_elements,
        "bot_response": "", "actions_to_execute": [], 
        
        # CRITICAL FIX: Include unified context so handlers can personalize
        "unified_context": unified_ctx,
        "formatted_context": formatted_ctx,
        
        "phase": "loaded", "error": None
    }

def _persist_state(thread: CarePlanCheckInThread, final_state: CarePlanCheckInState):
    """Update thread actionable_insights with persistent LangGraph state."""
    new_insights = ensure_actionable_insights(thread.actionable_insights or {}, flow="care_plan_checkin")
    
    # Serialize Pydantic objects to dicts
    def serialize(obj):
        if hasattr(obj, "model_dump"): return obj.model_dump()
        if hasattr(obj, "dict"): return obj.dict()
        return obj

    raw_candidates = final_state.get("alternate_candidates", [])
    serialized_candidates = [serialize(c) for c in raw_candidates]

    # Serialize ui_blocks too
    raw_ui_blocks = final_state.get("ui_blocks", [])
    serialized_ui_blocks = [serialize(b) for b in raw_ui_blocks]
    raw_ui_elements = final_state.get("ui_elements", raw_ui_blocks)
    serialized_ui_elements = [serialize(b) for b in raw_ui_elements]
    action_plan = final_state.get("action_plan", final_state.get("action_items", []))

    new_insights.update({
        "workflow_stage": final_state.get("workflow_stage"),
        "targeted_action_index": final_state.get("targeted_action_index"),
        "targeted_action_id": final_state.get("targeted_action_id"),  # CRITICAL: needed for replacement
        "plan_id": final_state.get("plan_id"),  # Needed for DB operations
        "barrier_type": final_state.get("barrier_type"),
        "change_reason": final_state.get("change_reason"),
        "alternate_candidates": serialized_candidates,
        "ui_blocks": serialized_ui_blocks,  # FIX: Persist CTA buttons across requests
        "ui_elements": serialized_ui_elements,
        "last_options_shown": final_state.get("last_options_shown"),
        "user_preferences": final_state.get("user_preferences", {}),
        "action_plan": [serialize(i) for i in (action_plan or [])],
        "streak": int(final_state.get("streak") or final_state.get("current_streak") or 0),
    })
    thread.actionable_insights = new_insights


def _care_plan_trace(
    *,
    flow: str,
    thread_id: str,
    intent: Optional[str] = None,
    workflow_stage: Optional[str] = None,
    action_id: Optional[str] = None,
    latency_ms: Optional[float] = None,
    guardrail_status: Optional[str] = None,
) -> Dict[str, Any]:
    trace: Dict[str, Any] = {
        "flow": flow,
        "thread_id": thread_id,
        "intent": intent,
        "workflow_stage": workflow_stage,
        "action_id": action_id,
        "guardrail_status": guardrail_status or "unknown",
    }
    if latency_ms is not None:
        trace["latency_ms"] = int(latency_ms)
    return trace


def _build_care_plan_response(
    *,
    thread: CarePlanCheckInThread,
    history: List[Dict[str, Any]],
    tap_options: List[Dict[str, Any]],
    ui_blocks: List[Any],
    source: str,
    trace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resp_ui_blocks = _build_response_ui_blocks(ui_blocks, source=source)
    return build_chatbot_response_payload(
        thread_id=thread.id,
        local_date=thread.local_date.isoformat(),
        history=history,
        tap_options=tap_options,
        ui_blocks=resp_ui_blocks,
        actionable_insights=thread.actionable_insights or {},
        flow="care_plan_checkin",
        trace=trace,
    )


def _serialize_value(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


def _normalize_ui_blocks(blocks: Optional[List[Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for block in blocks or []:
        serialized = _serialize_value(block)
        if isinstance(serialized, dict):
            normalized.append(serialized)
    return normalized


def _attach_ui_blocks_to_latest_bot(raw_messages: List[Dict[str, Any]], ui_blocks: Optional[List[Any]]) -> List[Dict[str, Any]]:
    serialized = _normalize_ui_blocks(ui_blocks)
    if not serialized:
        return raw_messages
    # Clear old ui_blocks from all messages first, then attach to latest bot msg only
    for msg in raw_messages:
        if "ui_blocks" in msg:
            del msg["ui_blocks"]
    for msg in reversed(raw_messages):
        if msg.get("role") in {"bot", "assistant"}:
            msg["ui_blocks"] = serialized
            break
    return raw_messages


def _append_bot_message(
    thread_obj: CarePlanCheckInThread,
    content: str,
    *,
    role: str = "bot",
    ui_blocks: Optional[List[Any]] = None,
) -> None:
    if not content:
        return
    normalized_role = "bot" if role == "assistant" else role
    raw = list(thread_obj.raw_messages or [])
    # Clear ui_blocks from all previous messages — only the latest response
    # should have active CTAs. Old alternatives/buttons from prior turns are stale.
    for msg in raw:
        if "ui_blocks" in msg:
            del msg["ui_blocks"]
    entry: Dict[str, Any] = {
        "id": str(uuid4()),
        "role": normalized_role,
        "content": content,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    serialized_ui = _normalize_ui_blocks(ui_blocks)
    if serialized_ui:
        entry["ui_blocks"] = serialized_ui
    raw.append(entry)
    thread_obj.raw_messages = raw


def _build_response_ui_blocks(raw_blocks: Optional[List[Any]], *, source: str) -> List[UIBlock]:
    resp_ui_blocks: List[UIBlock] = []
    for block in _normalize_ui_blocks(raw_blocks):
        actions: List[UIBlockAction] = []
        for action in block.get("actions", []):
            title = (action.get("title") or "").strip()
            if not title:
                continue
            actions.append(
                UIBlockAction(
                    id=action.get("id", str(uuid4())),
                    title=title,
                    action_type=action.get("action_type", "submit_event"),
                    payload=action.get("payload", {}),
                    style=action.get("style", "primary"),
                )
            )

        block_title = (block.get("title") or "").strip()
        block_subtitle = (block.get("description") or block.get("subtitle") or "").strip()
        if not block_title and not block_subtitle and not actions:
            continue

        resp_ui_blocks.append(
            UIBlock(
                id=block.get("id", str(uuid4())),
                type=block.get("type") or "quick_actions",
                title=block_title or None,
                subtitle=block_subtitle or None,
                payload=block.get("payload", {}),
                actions=actions,
                dismissible=True,
                priority="high",
                analytics={"surface": "care_plan_checkin", "source": source},
            )
        )
    return resp_ui_blocks

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
        started_at = perf_counter()
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)
        if force_new:
            thread = service.create_new_thread(uid)
        else:
            thread = service.get_or_create_today_thread(uid)
            
        history = service.format_history_for_mobile(thread)
        tap_options = _default_tap_options()
        tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")
        trace = _care_plan_trace(
            flow="care_plan_checkin",
            thread_id=thread.id,
            action_id="start",
            latency_ms=(perf_counter() - started_at) * 1000,
            guardrail_status="not_applicable",
        )
        payload = _build_care_plan_response(
            thread=thread,
            history=history,
            tap_options=tap_options,
            ui_blocks=_default_ui_blocks_for_start(),
            source="start",
            trace=trace,
        )
        logger.info("CARE_PLAN_START", extra={"trace": trace})
        return payload
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
        started_at = perf_counter()
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
        state = await _reconstruct_state(thread, uid, service)

        # 3. Invoke Graph
        final_state = await process_care_plan_message(state, message_text, thread_id=thread.id)

        # 4. Save Result to DB
        # NOTE: User message was already saved above. We save the bot response here.
        # IMPORTANT: classify_user_intent adds user_message to state["messages"] even though
        # it was already loaded from thread (since user message was saved before graph ran).
        # This makes new_msgs_count=1 always (duplicate user msg). We must NOT rely on
        # new_msgs_count to determine whether to save bot_response — always save it directly.
        bot_response_text = (final_state.get("bot_response") or "").strip()
        if bot_response_text:
            _append_bot_message(thread, bot_response_text, role="bot", ui_blocks=final_state.get("ui_blocks"))
        elif final_state.get("ui_blocks"):
            # No response text but UI blocks present — attach to latest bot message in history
            thread.raw_messages = _attach_ui_blocks_to_latest_bot(
                list(thread.raw_messages or []),
                final_state.get("ui_blocks"),
            )

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
        
        trace = _care_plan_trace(
            flow="care_plan_checkin",
            thread_id=thread.id,
            intent=final_state.get("current_intent"),
            workflow_stage=final_state.get("workflow_stage"),
            action_id="respond",
            latency_ms=(perf_counter() - started_at) * 1000,
            guardrail_status="post_model_hook",
        )
        payload = _build_care_plan_response(
            thread=thread,
            history=history,
            tap_options=tap_options,
            ui_blocks=final_state.get("ui_blocks", []),
            source="langgraph",
            trace=trace,
        )
        logger.info("CARE_PLAN_RESPOND", extra={"trace": trace})
        return payload

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
        started_at = perf_counter()
        uid = current_user["uid"]
        if not payload.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")
        
        service = CarePlanCheckInService(db)
        thread = service.get_thread_by_id(uid, payload.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        action_id = (payload.action_id or "").strip()
        meta = payload.metadata or {}
        insights = ensure_actionable_insights(thread.actionable_insights or {}, flow="care_plan_checkin")
        idempotency_key = payload.idempotency_key
        if not ensure_event_not_duplicate(insights=insights, idempotency_key=idempotency_key):
            thread.actionable_insights = insights
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            persisted_ui_blocks = insights.get("ui_elements") or insights.get("ui_blocks") or []
            tap_options = [{"id": "manage_plan", "text": "🧩 Manage plan"}]
            if not persisted_ui_blocks:
                tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")

            trace = _care_plan_trace(
                flow="care_plan_checkin",
                thread_id=thread.id,
                workflow_stage=insights.get("workflow_stage"),
                action_id=action_id,
                latency_ms=(perf_counter() - started_at) * 1000,
                guardrail_status="idempotent_replay",
            )
            logger.info("CARE_PLAN_EVENT_DUPLICATE", extra={"trace": trace, "idempotency_key": idempotency_key})
            return _build_care_plan_response(
                thread=thread,
                history=history,
                tap_options=tap_options,
                ui_blocks=persisted_ui_blocks,
                source="idempotent_replay",
                trace=trace,
            )
        thread.actionable_insights = insights
        
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
            stored_state = await _reconstruct_state(thread, uid, service)
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
            stored_state = await _reconstruct_state(thread, uid, service)
            user_message = f"Show me alternatives for {display_title}" if is_alternates else f"I want to change {display_title}"
            result = await process_care_plan_message(
                state=stored_state,
                user_message=user_message,
                thread_id=thread.id
            )

            # Update thread with bot response
            bot_response = result.get("bot_response", "")
            if bot_response:
                _append_bot_message(thread, bot_response, role="bot", ui_blocks=result.get("ui_blocks"))

            _persist_state(thread, result)
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            resp_ui_blocks = _build_response_ui_blocks(result.get("ui_blocks", []), source="action_select")

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
            stored_state = await _reconstruct_state(thread, uid, service)
            candidates = stored_state.get("alternate_candidates", [])
            if candidates and selected_idx < len(candidates):
                alt_title = candidates[selected_idx].get("title", f"Option {selected_idx + 1}") if isinstance(candidates[selected_idx], dict) else f"Option {selected_idx + 1}"
            else:
                alt_title = f"Option {selected_idx + 1}"
            
            # Add user's selection as visible message
            _add_user_tap_message(thread, f"I'll go with: {alt_title}")
            
            logger.info(f"[SELECT_ALT] Step 2: Index={selected_idx}, reconstructing state...")
            
            # Load current state from thread
            stored_state = await _reconstruct_state(thread, uid, service)
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
                _append_bot_message(
                    thread,
                    (payload.get("message") if isinstance(payload, dict) else "Confirm this replacement?") or "Confirm this replacement?",
                    role="bot",
                    ui_blocks=[ui_block],
                )
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
            _append_bot_message(thread, bot_response, role="bot", ui_blocks=result.get("ui_blocks"))
            
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
                "ui_blocks": _build_response_ui_blocks(result.get("ui_blocks", []), source="alternate_selection"),
            }
        
        # Handle confirm_skip
        if action_id == "confirm_skip":
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, "Yes, skip it"))
            
            stored_state = await _reconstruct_state(thread, uid, service)
            
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
            
            _append_bot_message(thread, bot_response, role="bot", ui_blocks=result.get("ui_blocks"))
            
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
                "ui_blocks": _build_response_ui_blocks(result.get("ui_blocks", []), source="confirm_skip"),
            }
        
        # Handle interrupt confirmations
        if action_id in ["confirm_replace", "cancel_replace", "keep_as_is"]:
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, action_id.replace("_", " ").title()))
            
            stored_state = await _reconstruct_state(thread, uid, service)
            resume_value = True if action_id == "confirm_replace" else False
            result = await process_alternate_selection(
                state=stored_state,
                thread_id=thread.id,
                resume=resume_value
            )

            bot_response = result.get("bot_response", "All set.")
            _append_bot_message(thread, bot_response, role="assistant", ui_blocks=result.get("ui_blocks"))

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
                "ui_blocks": _build_response_ui_blocks(result.get("ui_blocks", []), source="interrupt_resume"),
            }

        # Handle show_alternates
        if action_id == "show_alternates":
            # Add user's tap as visible message
            _add_user_tap_message(thread, action_display_text.get(action_id, "Show me alternatives"))
            
            stored_state = await _reconstruct_state(thread, uid, service)
            result = await process_care_plan_message(
                state=stored_state,
                user_message="Show me alternatives",
                thread_id=thread.id
            )
            
            bot_response = result.get("bot_response", "Here are some alternatives:")
            _append_bot_message(thread, bot_response, role="assistant", ui_blocks=result.get("ui_blocks"))
            
            # Persist state
            _persist_state(thread, result)
            
            db.add(thread)
            db.commit()
            db.refresh(thread)
            
            history = service.format_history_for_mobile(thread)
            
            resp_ui_blocks = _build_response_ui_blocks(result.get("ui_blocks", []), source="langgraph_event")
            
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
            
            stored_state = await _reconstruct_state(thread, uid, service)
            
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
                _append_bot_message(thread, bot_response, role="assistant", ui_blocks=result.get("ui_blocks"))
            
            _persist_state(thread, result)
            db.add(thread)
            db.commit()
            db.refresh(thread)
            
            history = service.format_history_for_mobile(thread)
            
            resp_ui_blocks = _build_response_ui_blocks(result.get("ui_blocks", []), source="langgraph_event")
            
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
            
            stored_state = await _reconstruct_state(thread, uid, service)
            user_message = tap_to_message[action_id]
            
            result = await process_care_plan_message(
                state=stored_state,
                user_message=user_message,
                thread_id=thread.id
            )
            
            bot_response = result.get("bot_response", "")
            if bot_response:
                _append_bot_message(thread, bot_response, role="assistant", ui_blocks=result.get("ui_blocks"))
            
            _persist_state(thread, result)
            db.add(thread)
            db.commit()
            db.refresh(thread)
            
            history = service.format_history_for_mobile(thread)
            
            resp_ui_blocks = _build_response_ui_blocks(result.get("ui_blocks", []), source="tap_option")
            
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
        persisted_ui_blocks = (thread.actionable_insights or {}).get("ui_elements") or (thread.actionable_insights or {}).get("ui_blocks") or []
        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": [{"id": "manage_plan", "text": "🧩 Manage plan"}],
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": _build_response_ui_blocks(persisted_ui_blocks, source="unknown_action"),
        }
        
    except Exception as e:
        logger.error(f"UI event error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
