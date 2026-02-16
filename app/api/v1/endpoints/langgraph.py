"""
LangGraph API Router - Unified interface for all 5 conversation flows.

This router exposes the LangGraph implementations via clean REST endpoints.
State is stored in Redis (or DB) between invocations.

Supported Flows:
1. Weekly Check-in: /langgraph/weekly-checkin/...
2. Care Plan Check-in: /langgraph/care-plan/...
3. Symptom Check-in: /langgraph/symptom/...
4. Personalization: /langgraph/personalization/...
5. Know My Body: /langgraph/know-my-body/...
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
import json
import logging
from uuid import uuid4

from app.core.database import get_db
from app.api.v1.endpoints.auth import get_current_user
from app.core.rate_limiter import get_rate_limiter, CONVERSATION_LIMIT
from app.langgraph.helpers.llm_cache import get_redis_client

# Import all LangGraph public APIs
from app.langgraph.graphs import (
    # Weekly Check-in
    start_weekly_checkin,
    continue_weekly_checkin,
    WeeklyCheckInState,
    
    # Care Plan Check-in
    load_care_plan,
    process_care_plan_message,
    process_alternate_selection,
    CarePlanCheckInState,
    
    # Symptom Check-in
    start_symptom_checkin,
    continue_symptom_checkin,
    SymptomCheckInState,
    
    # Personalization
    start_personalization,
    continue_personalization,
    PersonalizationState,
    
    # Know My Body
    start_know_my_body,
    answer_question,
    KnowMyBodyState,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Get rate limiter instance for endpoint protection
limiter = get_rate_limiter()


# ═══════════════════════════════════════════════════════════════════════════════
# STATE STORAGE (Redis-backed with TTL, in-memory fallback)
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory fallback only used when Redis is unavailable
_state_store_fallback: Dict[str, Dict[str, Any]] = {}
_STATE_TTL_SECONDS = 3600  # 1 hour TTL — sessions expire after inactivity
_STATE_PREFIX = "session:state:"


async def _store_state(session_id: str, state: Dict[str, Any]) -> None:
    """Store state in Redis with TTL. Falls back to in-memory if Redis unavailable."""
    try:
        redis = await get_redis_client()
        if redis:
            key = f"{_STATE_PREFIX}{session_id}"
            await redis.setex(key, _STATE_TTL_SECONDS, json.dumps(state, default=str))
            # Remove from fallback if it was there
            _state_store_fallback.pop(session_id, None)
            return
    except Exception as e:
        logger.warning(f"Redis state store failed, using fallback: {e}")
    
    # Fallback: in-memory with size guard (max 1000 sessions)
    if len(_state_store_fallback) >= 1000:
        # Evict oldest 100 sessions
        keys_to_remove = list(_state_store_fallback.keys())[:100]
        for k in keys_to_remove:
            _state_store_fallback.pop(k, None)
        logger.warning(f"[STATE] Evicted {len(keys_to_remove)} stale sessions from fallback store")
    _state_store_fallback[session_id] = dict(state)


async def _get_state(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve state from Redis. Falls back to in-memory."""
    try:
        redis = await get_redis_client()
        if redis:
            key = f"{_STATE_PREFIX}{session_id}"
            data = await redis.get(key)
            if data:
                return json.loads(data)
            # Not in Redis, check fallback
            return _state_store_fallback.get(session_id)
    except Exception as e:
        logger.warning(f"Redis state get failed, using fallback: {e}")
    
    return _state_store_fallback.get(session_id)


async def _delete_state(session_id: str) -> None:
    """Delete state from Redis and fallback."""
    try:
        redis = await get_redis_client()
        if redis:
            await redis.delete(f"{_STATE_PREFIX}{session_id}")
    except Exception as e:
        logger.warning(f"Redis state delete failed: {e}")
    _state_store_fallback.pop(session_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class TapOption(BaseModel):
    id: str
    text: str


class UIBlock(BaseModel):
    id: str
    type: str
    title: Optional[str] = None
    subtitle: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


class ChatMessage(BaseModel):
    id: str
    text: str
    isBot: bool


class StartResponse(BaseModel):
    """Response when starting a conversation flow."""
    session_id: str
    flow_type: str
    bot_response: str
    messages: List[ChatMessage] = []
    tap_options: List[TapOption] = []
    ui_blocks: List[UIBlock] = []
    is_complete: bool = False
    metadata: Dict[str, Any] = {}


class ContinueRequest(BaseModel):
    """Request to continue a conversation."""
    session_id: str
    user_input: str
    input_mode: str = "type"  # tap, type, yap, slider


class ContinueResponse(BaseModel):
    """Response when continuing a conversation."""
    session_id: str
    flow_type: str
    bot_response: str
    messages: List[ChatMessage] = []
    tap_options: List[TapOption] = []
    ui_blocks: List[UIBlock] = []
    is_complete: bool = False
    summary: Optional[str] = None
    metadata: Dict[str, Any] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Convert State to Response
# ═══════════════════════════════════════════════════════════════════════════════

def _state_to_messages(messages: List[Dict[str, str]]) -> List[ChatMessage]:
    """Convert state messages to ChatMessage list."""
    result = []
    for msg in messages:
        result.append(ChatMessage(
            id=msg.get("id", str(uuid4())),
            text=msg.get("content", ""),
            isBot=msg.get("role") == "assistant"
        ))
    return result


def _state_to_tap_options(options: Optional[List[str]]) -> List[TapOption]:
    """Convert tap options to TapOption list."""
    if not options:
        return []
    return [TapOption(id=f"opt_{i}", text=opt) for i, opt in enumerate(options)]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. WEEKLY CHECK-IN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/weekly-checkin/start", response_model=StartResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_weekly_start(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start a new Weekly Check-in conversation using LangGraph."""
    uid = current_user["uid"]
    
    try:
        state = await start_weekly_checkin(user_id=uid)
        session_id = state.get("session_id", str(uuid4()))
        
        # Store state for continuation
        await _store_state(session_id, state)
        
        # Extract bot response from messages
        messages = state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else "Welcome to your weekly check-in!"
        
        # Get current question
        current_q = state.get("current_question", {})
        
        return StartResponse(
            session_id=session_id,
            flow_type="weekly_checkin",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=_state_to_tap_options(current_q.get("tap_options")),
            is_complete=state.get("phase") == "complete",
            metadata={
                "cycle_phase": state.get("cycle_phase"),
                "question_count": state.get("question_count", 0)
            }
        )
    except Exception as e:
        logger.error(f"Weekly checkin start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/weekly-checkin/continue", response_model=ContinueResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_weekly_continue(
    http_request: Request,
    request: ContinueRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Continue Weekly Check-in conversation."""
    uid = current_user["uid"]
    
    # Get stored state
    state = await _get_state(request.session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Verify ownership
    if state.get("user_id") != uid:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    try:
        updated_state = await continue_weekly_checkin(
            state=state,
            user_input=request.user_input,
            input_mode=request.input_mode
        )
        
        # Update stored state
        await _store_state(request.session_id, updated_state)
        
        messages = updated_state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else ""
        current_q = updated_state.get("current_question", {})
        
        return ContinueResponse(
            session_id=request.session_id,
            flow_type="weekly_checkin",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=_state_to_tap_options(current_q.get("tap_options")),
            is_complete=updated_state.get("phase") == "complete",
            summary=updated_state.get("summary"),
            metadata={
                "question_count": updated_state.get("question_count", 0),
                "topics_covered": updated_state.get("topics_covered", [])
            }
        )
    except Exception as e:
        logger.error(f"Weekly checkin continue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CARE PLAN CHECK-IN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/care-plan/start", response_model=StartResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_care_plan_start(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Load care plan and start check-in using LangGraph."""
    uid = current_user["uid"]
    
    try:
        state = await load_care_plan(user_id=uid)
        session_id = str(uuid4())
        state["session_id"] = session_id
        
        await _store_state(session_id, state)
        
        if state.get("error"):
            return StartResponse(
                session_id=session_id,
                flow_type="care_plan",
                bot_response=state.get("bot_response", "No plan today"),
                is_complete=True,
                metadata={"error": state.get("error")}
            )
        
        return StartResponse(
            session_id=session_id,
            flow_type="care_plan",
            bot_response=state.get("bot_response", "Here's your care plan for today!"),
            tap_options=[TapOption(id="done", text="✅ Done"), TapOption(id="change", text="🔄 Change")],
            is_complete=False,
            metadata={
                "action_items": state.get("action_items", []),
                "refresh_tokens": state.get("refresh_tokens_available", 0)
            }
        )
    except Exception as e:
        logger.error(f"Care plan start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/care-plan/continue", response_model=ContinueResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_care_plan_continue(
    http_request: Request,
    request: ContinueRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Process user message about care plan."""
    uid = current_user["uid"]
    
    state = await _get_state(request.session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if state.get("user_id") != uid:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    try:
        updated_state = await process_care_plan_message(
            state=state,
            user_message=request.user_input
        )
        
        await _store_state(request.session_id, updated_state)
        
        # Build UI blocks from state
        ui_blocks = []
        for block in updated_state.get("ui_blocks", []):
            ui_blocks.append(UIBlock(
                id=block.get("id", str(uuid4())),
                type=block.get("type", "card"),
                title=block.get("title"),
                subtitle=block.get("subtitle"),
                payload=block.get("payload")
            ))
        
        return ContinueResponse(
            session_id=request.session_id,
            flow_type="care_plan",
            bot_response=updated_state.get("bot_response", ""),
            ui_blocks=ui_blocks,
            is_complete=updated_state.get("phase") == "complete",
            metadata={
                "current_intent": updated_state.get("current_intent"),
                "workflow_stage": updated_state.get("workflow_stage")
            }
        )
    except Exception as e:
        logger.error(f"Care plan continue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SelectAlternateRequest(BaseModel):
    session_id: str
    selected_index: int


@router.post("/care-plan/select-alternate", response_model=ContinueResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_care_plan_select_alternate(
    http_request: Request,
    request: SelectAlternateRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Select an alternate action."""
    uid = current_user["uid"]
    
    state = await _get_state(request.session_id)
    if not state or state.get("user_id") != uid:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    try:
        updated_state = await process_alternate_selection(
            state=state,
            selected_index=request.selected_index
        )
        
        await _store_state(request.session_id, updated_state)
        
        return ContinueResponse(
            session_id=request.session_id,
            flow_type="care_plan",
            bot_response=updated_state.get("bot_response", ""),
            is_complete=updated_state.get("phase") == "complete",
            metadata={"tokens_remaining": updated_state.get("refresh_tokens_available")}
        )
    except Exception as e:
        logger.error(f"Care plan select error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SYMPTOM CHECK-IN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/symptom/start", response_model=StartResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_symptom_start(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start symptom check-in with Dr. Auvra."""
    uid = current_user["uid"]
    
    try:
        state = await start_symptom_checkin(user_id=uid)
        session_id = state.get("session_id", str(uuid4()))
        
        await _store_state(session_id, state)
        
        messages = state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else "Hi! I'm Dr. Auvra 💜"
        
        return StartResponse(
            session_id=session_id,
            flow_type="symptom_checkin",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=_state_to_tap_options(state.get("tap_options")),
            metadata={
                "cycle_phase": state.get("cycle_phase"),
                "cycle_day": state.get("cycle_day")
            }
        )
    except Exception as e:
        logger.error(f"Symptom start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/symptom/continue", response_model=ContinueResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_symptom_continue(
    http_request: Request,
    request: ContinueRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Continue symptom conversation."""
    uid = current_user["uid"]
    
    state = await _get_state(request.session_id)
    if not state or state.get("user_id") != uid:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    try:
        updated_state = await continue_symptom_checkin(
            state=state,
            user_input=request.user_input
        )
        
        await _store_state(request.session_id, updated_state)
        
        messages = updated_state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else ""
        
        return ContinueResponse(
            session_id=request.session_id,
            flow_type="symptom_checkin",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=_state_to_tap_options(updated_state.get("tap_options")),
            is_complete=updated_state.get("phase") == "complete",
            summary=updated_state.get("summary"),
            metadata={
                "symptoms_logged": updated_state.get("symptoms_mentioned", [])
            }
        )
    except Exception as e:
        logger.error(f"Symptom continue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PERSONALIZATION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/personalization/start", response_model=StartResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_personalization_start(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start personalization conversation."""
    uid = current_user["uid"]
    
    try:
        state = await start_personalization(user_id=uid)
        session_id = state.get("session_id", str(uuid4()))
        
        await _store_state(session_id, state)
        
        messages = state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else "Let's personalize your experience!"
        
        return StartResponse(
            session_id=session_id,
            flow_type="personalization",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=_state_to_tap_options(state.get("tap_options")),
            is_complete=state.get("phase") == "complete",
            metadata={
                "profile_density": state.get("profile_density"),
                "trait_chips": state.get("trait_chips", []),
                "unlocked_features": state.get("unlocked_features", [])
            }
        )
    except Exception as e:
        logger.error(f"Personalization start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/personalization/continue", response_model=ContinueResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_personalization_continue(
    http_request: Request,
    request: ContinueRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Continue personalization conversation."""
    uid = current_user["uid"]
    
    state = await _get_state(request.session_id)
    if not state or state.get("user_id") != uid:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    try:
        updated_state = await continue_personalization(
            state=state,
            user_input=request.user_input
        )
        
        await _store_state(request.session_id, updated_state)
        
        messages = updated_state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else ""
        
        return ContinueResponse(
            session_id=request.session_id,
            flow_type="personalization",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=_state_to_tap_options(updated_state.get("tap_options")),
            is_complete=updated_state.get("phase") == "complete",
            metadata={
                "profile_density": updated_state.get("profile_density"),
                "trait_chips": updated_state.get("trait_chips", [])
            }
        )
    except Exception as e:
        logger.error(f"Personalization continue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. KNOW MY BODY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

class AskQuestionRequest(BaseModel):
    question: str


@router.post("/know-my-body/start", response_model=StartResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_know_my_body_start(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start Know My Body conversation."""
    uid = current_user["uid"]
    
    try:
        state = await start_know_my_body(user_id=uid)
        session_id = state.get("session_id", str(uuid4()))
        
        await _store_state(session_id, state)
        
        messages = state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else "Hi! Ask me anything about your body and cycle 💜"
        
        return StartResponse(
            session_id=session_id,
            flow_type="know_my_body",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=[
                TapOption(id="why_hormones", text="Why do hormones matter?"),
                TapOption(id="what_phase", text="What phase am I in?"),
                TapOption(id="why_symptoms", text="Why do I feel this way?")
            ],
            metadata={
                "cycle_phase": state.get("cycle_phase")
            }
        )
    except Exception as e:
        logger.error(f"Know my body start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/know-my-body/ask", response_model=ContinueResponse)
@limiter.limit(CONVERSATION_LIMIT)
async def lg_know_my_body_ask(
    http_request: Request,
    request: ContinueRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ask a question about the body/cycle."""
    uid = current_user["uid"]
    
    state = await _get_state(request.session_id)
    if not state:
        # For know my body, allow starting fresh with a question
        state = await start_know_my_body(user_id=uid)
        await _store_state(request.session_id, state)
    
    if state.get("user_id") != uid:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    try:
        updated_state = await answer_question(
            state=state,
            question=request.user_input
        )
        
        await _store_state(request.session_id, updated_state)
        
        messages = updated_state.get("messages", [])
        bot_response = messages[-1]["content"] if messages else ""
        
        # Build related questions as tap options
        related = updated_state.get("related_questions", [])
        tap_options = [TapOption(id=f"related_{i}", text=q) for i, q in enumerate(related[:3])]
        
        return ContinueResponse(
            session_id=request.session_id,
            flow_type="know_my_body",
            bot_response=bot_response,
            messages=_state_to_messages(messages),
            tap_options=tap_options,
            is_complete=False,  # Always allow more questions
            metadata={
                "question_category": updated_state.get("question_category"),
                "interests_tracked": updated_state.get("user_interests", [])
            }
        )
    except Exception as e:
        logger.error(f"Know my body ask error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
