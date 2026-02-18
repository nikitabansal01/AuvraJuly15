"""
Care Plan Check-in LangGraph Implementation - ENHANCED VERSION
Complete production implementation with cross-chatbot memory.

ENHANCEMENTS:
1. ✅ Multi-invocation pattern (request/response model)
2. ✅ Complete graph routing (no fallback to non-existent nodes)
3. ✅ Bounds checking for alternate selection
4. ✅ Complete ActionPlanItem fields
5. ✅ Proper error handling
6. ✅ UNIFIED MEMORY: Access to all chatbot conversations
7. ✅ LLM-GENERATED RESPONSES: No more hardcoded templates

Features:
- Refresh token gating (16-day streak, 2x per day)
- LLM intent classification with feedback understanding
- Skip action with streak guidance (complete at least 1 action/day)
- Multi-stage workflows (alternate suggestions)
- UI Blocks integration
- Cross-chatbot context awareness
"""

from __future__ import annotations

import asyncio
import random  # Retry jitter to prevent thundering herd  
import json
import os
import logging
import uuid
from typing import TypedDict, List, Dict, Any, Literal, Optional
from datetime import date, datetime
from langgraph.graph import StateGraph, END
try:
    from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
except Exception:  # pragma: no cover - optional dependency in some envs
    PostgresSaver = None
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore
except Exception:  # pragma: no cover - optional dependency in some envs
    AsyncPostgresSaver = None
try:
    from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
except Exception:  # pragma: no cover - optional dependency in some envs
    SqliteSaver = None
try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # type: ignore
except Exception:  # pragma: no cover - optional dependency in some envs
    AsyncSqliteSaver = None
from langgraph.types import interrupt, Command
from pydantic import BaseModel
from langchain_openai import ChatOpenAI

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError
from app.langgraph.helpers.llm_client import call_llm, call_llm_structured
from app.langgraph.helpers.llm_config import get_llm_config, get_max_tokens_for_intent, get_temperature_for_intent
from app.langgraph.helpers.llm_cache import call_llm_with_cache
from app.langgraph.helpers.conversation_memory import truncate_conversation_smart, get_conversation_stats
from app.langgraph.helpers.circuit_breaker import (
    openai_breaker,
    get_degraded_response,
    CircuitBreakerError
)
from app.langgraph.helpers.database_helpers import (
    get_cycle_info, get_todays_action_plan, get_streak_info, get_reward_status
)
from app.langgraph.helpers.ui_blocks_helper import (
    generate_intelligent_ctas, create_confirmation_block, 
    create_alternates_selection_block, clear_ui_blocks, create_action_selection_block
)
from app.core.database import get_db, ActionPlanItem, ActionPlanRefreshLog, SessionLocal, get_db_session
# NEW: Unified memory for cross-chatbot context
from app.langgraph.memory import get_unified_context, format_context_for_prompt

logger = logging.getLogger(__name__)

STREAM_MODE_UI = "custom"
STREAM_MODE_TEXT = "messages"


async def _maybe_add_ctas(
    state: CarePlanCheckInState,
    bot_response: str,
    last_user_message: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Generate contextual CTAs via LLM when no explicit UI blocks are set."""
    try:
        suggestion = await generate_intelligent_ctas(
            flow_type="care_plan_checkin",
            bot_response=bot_response,
            conversation_state=state,
            last_user_message=last_user_message,
        )
        return [suggestion] if suggestion else []
    except Exception as e:
        logger.warning(f"CTA generation failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════
# STATE SCHEMA
# ═══════════════════════════════════════════════════════════════════

class CarePlanCheckInState(TypedDict):
    """State for daily Care Plan check-in conversation."""
    
    # Session
    user_id: str
    thread_id: str
    message_id: str
    
    # Action plan context
    plan_id: Optional[int]
    plan_date: Optional[date]
    action_items: List[Dict[str, Any]]
    action_plan: List[Dict[str, Any]]
    
    # User context
    cycle_day: Optional[int]
    cycle_phase: Optional[str]
    primary_hormone: Optional[str]
    current_streak: int
    streak: int
    
    # REFRESH TOKENS (CRITICAL)
    refresh_tokens_available: int
    refresh_tokens_unlocked: bool
    
    # Conversation
    messages: List[Dict[str, str]]
    current_intent: Optional[str]
    user_message: Optional[str]  # Current user input
    
    # Intent-specific state
    targeted_action_id: Optional[int]
    targeted_action_index: Optional[int]  # 0-3 index
    change_reason: Optional[str]
    barrier_type: Optional[str]
    feedback_topic: Optional[str]  # NEW: For plan feedback intents (generic, repetitive, etc.)
    
    # Multi-stage workflow
    workflow_stage: Optional[str]
    alternate_candidates: List[Dict[str, Any]]
    selected_alternate_index: Optional[int]
    selected_alternate: Optional[Dict[str, Any]]
    last_options_shown: Optional[List[str]]
    user_preferences: Dict[str, Any]
    
    # UI Elements
    ui_blocks: List[Dict[str, Any]]
    ui_elements: List[Dict[str, Any]]
    
    # Outputs
    bot_response: str
    actions_to_execute: List[Dict[str, Any]]
    
    # Phase tracking
    phase: Literal["init", "loaded", "processing", "awaiting_selection", "awaiting_feedback_response", "complete"]
    
    # Error
    error: Optional[str]
    
    # UNIFIED MEMORY: Cross-chatbot context (NEW)
    unified_context: Optional[Dict[str, Any]]
    formatted_context: Optional[str]


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class IntentClassification(BaseModel):
    """LLM-powered intent classification."""
    intent: str
    targeted_action_index: Optional[int] = None  # 1-4 (user-facing)
    proposed_replacement: Optional[str] = None   # "cashews", "dance", etc.
    confidence: float
    feedback_topic: Optional[str] = None  # For feedback intents: "generic", "science", "personalization", etc.


class BarrierAnalysis(BaseModel):
    """Analysis of user's barrier to action - fully LLM-powered, no heuristics."""
    barrier_type: str  # allergy, dietary, ingredients, dislike, time, energy, etc.
    specific_barrier: str  # Exact complaint or issue
    urgency: str  # immediate or flexible
    is_specific_request: bool = False  # True if user wants a SPECIFIC item (not just alternatives)
    requested_item: Optional[str] = None  # The specific item they want (e.g., "cashews", "dance", "yoga")


class SkipReason(BaseModel):
    """Extracted skip reason."""
    category: str
    detailed_reason: str


class AlternateAction(BaseModel):
    """Alternate action suggestion."""
    title: str
    specific_action: str
    why_better: str
    target_hormone: str
    purpose: str
    
    # Rich fields for full ActionPlanItem fidelity
    food_amounts: List[str] = []
    food_items: List[str] = []
    exercise_durations: List[str] = []
    exercise_types: List[str] = []
    exercise_intensities: List[str] = []
    mindfulness_durations: List[str] = []
    mindfulness_techniques: List[str] = []
    conditions: List[str] = []
    symptoms: List[str] = []


class AlternatesList(BaseModel):
    """List of alternate actions."""
    alternatives: List[AlternateAction]


# ═══════════════════════════════════════════════════════════════════
# PRODUCTION-GRADE: OpenAI Function Calling for Intent Classification
# Same pattern as CarePlanSemanticMatcher — proven in production.
# Why function calling > JSON mode:
#   1. Schema enforced by OpenAI (strict: true) — no parse errors
#   2. tool_choice forces the LLM to call the function — guaranteed output
#   3. Enum validation — intent MUST be one of the 13 valid values
#   4. Lower temperature (0.1) — consistent classification
# ═══════════════════════════════════════════════════════════════════

_care_plan_openai_client = None

def _get_care_plan_openai_client() -> AsyncOpenAI:
    """Singleton OpenAI client with production settings."""
    global _care_plan_openai_client
    if _care_plan_openai_client is None:
        _care_plan_openai_client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=10.0,
            max_retries=2
        )
    return _care_plan_openai_client


# Tool definition: ONE function with enum — the LLM MUST call this function
# and MUST pick from the enum values. No ambiguity, no parse errors.
CARE_PLAN_INTENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "classify_care_plan_intent",
            "description": "Classify the user's message into one intent category for the daily care plan check-in",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": [
                            "complete_action", "skip_action", "change_action",
                            "request_alternates", "negotiate", "ask_why",
                            "request_clarification", "cancel_action",
                            "plan_feedback", "challenge_science", "explain_plan",
                            "health_question", "general"
                        ],
                        "description": "The user's primary intent"
                    },
                    "targeted_action_index": {
                        "type": ["integer", "null"],
                        "description": "1-based index of the action the user refers to, or null if not about a specific action"
                    },
                    "proposed_replacement": {
                        "type": ["string", "null"],
                        "description": "Specific replacement item if user requests one (e.g. 'cashews', 'yoga'), otherwise null"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Classification confidence from 0.0 to 1.0"
                    },
                    "feedback_topic": {
                        "type": ["string", "null"],
                        "description": "For plan_feedback/challenge_science: generic, repetitive, not_personalized, science_question, or other. Null for other intents."
                    }
                },
                "required": ["intent", "targeted_action_index", "proposed_replacement", "confidence", "feedback_topic"],
                "additionalProperties": False
            }
        }
    }
]


# ═══════════════════════════════════════════════════════════════════
# HELPER: Create initial state
# ═══════════════════════════════════════════════════════════════════

def create_initial_state(user_id: str, thread_id: str = None) -> CarePlanCheckInState:
    """Create properly initialized state."""
    return CarePlanCheckInState(
        user_id=user_id,
        thread_id=thread_id or str(uuid.uuid4()),
        message_id=str(uuid.uuid4()),
        
        plan_id=None,
        plan_date=None,
        action_items=[],
        action_plan=[],
        
        cycle_day=None,
        cycle_phase=None,
        primary_hormone=None,
        current_streak=0,
        streak=0,
        
        refresh_tokens_available=0,
        refresh_tokens_unlocked=False,
        
        messages=[],
        current_intent=None,
        user_message=None,
        
        targeted_action_id=None,
        targeted_action_index=None,
        change_reason=None,
        barrier_type=None,
        feedback_topic=None,
        
        workflow_stage=None,
        alternate_candidates=[],
        selected_alternate_index=None,
        selected_alternate=None,
        last_options_shown=None,
        user_preferences={},
        
        ui_blocks=[],
        ui_elements=[],
        
        bot_response="",
        actions_to_execute=[],
        
        phase="init",
        error=None
        ,
        unified_context=None,
        formatted_context=None
    )


# ═══════════════════════════════════════════════════════════════════
# NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

async def load_daily_plan_and_tokens(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Load today's plan AND refresh token status AND unified cross-chatbot context."""
    try:
        # FIX: Use context manager to prevent connection leaks
        with get_db_session() as db:
            user_id = state["user_id"]
            
            # ══════════════════════════════════════════════════════════════
            # NEW: Load unified cross-chatbot memory context
            # This gives us EVERYTHING about the user - past conversations,
            # preferences, feedback from other chatbots, etc.
            # ══════════════════════════════════════════════════════════════
            unified_ctx = await get_unified_context(user_id, "care_plan_checkin")
            formatted_ctx = format_context_for_prompt(unified_ctx)
            
            # Load cycle and streak info
            cycle_info = get_cycle_info(user_id, db)
            streak_info = get_streak_info(user_id, db)
            
            # Load today's action plan
            plan_data = get_todays_action_plan(user_id, db)
            
            if not plan_data:
                return {
                    **state,
                    "error": "no_plan_today",
                    "bot_response": "You don't have an action plan for today yet.",
                    "phase": "complete"
                }
            
            # Check refresh tokens (16-day streak unlock, 2x per day)
            current_streak = streak_info.get("current_streak", 0)
            
            from app.services.reward_service import RewardService
            reward_service = RewardService(db)
            status = reward_service.get_refresh_status(user_id)
            
            refresh_tokens = status["remaining"]
            refresh_unlocked = status["limit"] > 0 # Basically if they have any limit at all
            
            return {
                **state,
                "plan_id": plan_data["plan_id"],
                "plan_date": plan_data["plan_date"],
                "action_items": plan_data["items"],
                "action_plan": plan_data["items"],
                "cycle_day": cycle_info.get("cycle_day"),
                "cycle_phase": cycle_info.get("phase"),
                "primary_hormone": cycle_info.get("primary_hormone"),
                "current_streak": current_streak,
                "streak": current_streak,
                "refresh_tokens_available": refresh_tokens,
                "refresh_tokens_unlocked": refresh_unlocked,
                # NEW: Unified cross-chatbot context for truly personalized responses
                "unified_context": unified_ctx,
                "formatted_context": formatted_ctx,
                "phase": "loaded"
            }
    except Exception as e:
        logger.error(f"Error loading plan: {e}")
        return {**state, "error": str(e), "phase": "complete"}


INTENT_TO_NODE: Dict[str, str] = {
    "complete_action": "handle_complete_action",
    "skip_action": "handle_skip_action",
    "change_action": "handle_change_action",
    "request_alternates": "handle_change_action",
    "negotiate": "handle_change_action",
    "ask_why": "handle_ask_why",
    "request_clarification": "handle_request_clarification",
    "cancel_action": "handle_cancel_action",
    "plan_feedback": "handle_plan_feedback",
    "challenge_science": "handle_challenge_science",
    "explain_plan": "handle_explain_plan",
    "health_question": "handle_health_question",
    "show_streak": "handle_show_streak",
    "general": "handle_general_response",
}


class IntentRoutingOutput(BaseModel):
    """Structured intent schema used with with_structured_output()."""
    intent: Literal[
        "complete_action",
        "skip_action",
        "change_action",
        "request_alternates",
        "negotiate",
        "ask_why",
        "request_clarification",
        "cancel_action",
        "plan_feedback",
        "challenge_science",
        "explain_plan",
        "health_question",
        "show_streak",
        "general",
    ]
    targeted_action_index: Optional[int] = None  # 1-based (user-facing)
    proposed_replacement: Optional[str] = None
    feedback_topic: Optional[str] = None


class HealthGuardrailOutput(BaseModel):
    """Structured moderation output for health-safety post checks."""
    safety_level: Literal["safe", "caution", "urgent"]
    should_append_urgent_guidance: bool = False


_intent_router_llm = None
_guardrail_llm = None


def _get_intent_router_llm():
    """Singleton structured LLM router."""
    global _intent_router_llm
    if _intent_router_llm is None:
        model = os.getenv("CARE_PLAN_INTENT_MODEL", "gpt-5-mini")
        _intent_router_llm = ChatOpenAI(model=model, temperature=0).with_structured_output(IntentRoutingOutput)
    return _intent_router_llm


def _get_guardrail_llm():
    """Singleton structured LLM moderation model for post-response safety checks."""
    global _guardrail_llm
    if _guardrail_llm is None:
        model = os.getenv("CARE_PLAN_GUARDRAIL_MODEL", os.getenv("CARE_PLAN_INTENT_MODEL", "gpt-5-mini"))
        _guardrail_llm = ChatOpenAI(model=model, temperature=0).with_structured_output(HealthGuardrailOutput)
    return _guardrail_llm


async def classify_user_intent(state: CarePlanCheckInState) -> Command:
    """Classify intent with structured output and route via Command(goto=...)."""
    user_message = (state.get("user_message") or "").strip()
    if not user_message:
        return Command(
            update={**state, "error": "no_user_message", "current_intent": "general", "phase": "processing"},
            goto="handle_general_response",
        )

    messages = list(state.get("messages") or [])
    action_items = list(state.get("action_items") or [])
    last_assistant = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg.get("content", "")
            break

    actions_list = "\n".join(
        [f"{idx + 1}. {item.get('title', 'Unknown')} ({item.get('category', 'general')})" for idx, item in enumerate(action_items)]
    ) or "No actions loaded."

    workflow_stage = state.get("workflow_stage") or "none"
    last_options_shown = state.get("last_options_shown") or []
    recent_messages = messages[-10:]
    chat_context = "\n".join([f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in recent_messages])

    system_prompt = """You classify care-plan chat intent for a women's health app.
Always return one of the schema intents.
Important rules:
- Requests like "I need more options", "show me more", "actually show me more options" MUST be request_alternates.
- If workflow stage is awaiting_alternate_selection and the user asks for different/new/more options, use request_alternates.
- If user dislikes an item and asks for alternatives, use request_alternates or change_action (prefer request_alternates when explicitly asking for options).
- If user asks why they take something, use ask_why.
- If user asks how/when/how much, use request_clarification.
- If user confirms completion, use complete_action.
- If user asks streak/progress/milestones, use show_streak.
Use last assistant message and workflow stage context."""
    context_prompt = f"""Workflow stage: {workflow_stage}
Last assistant message: {last_assistant or "none"}
Last option titles shown: {", ".join([str(o) for o in last_options_shown]) if last_options_shown else "none"}
Today's actions:
{actions_list}
Recent chat:
{chat_context}
User message: {user_message}

Return targeted_action_index as 1-based when user references a specific action."""

    try:
        structured_llm = _get_intent_router_llm()
        classification = await structured_llm.ainvoke(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": context_prompt}]
        )
    except Exception as e:
        logger.warning(f"[INTENT] Structured router failed, fallback to general: {e}")
        fallback_intent = "request_alternates" if workflow_stage == "awaiting_alternate_selection" else "general"
        classification = IntentRoutingOutput(intent=fallback_intent)

    if workflow_stage == "awaiting_alternate_selection" and classification.intent == "general" and last_options_shown:
        classification = IntentRoutingOutput(
            intent="request_alternates",
            targeted_action_index=classification.targeted_action_index,
            proposed_replacement=classification.proposed_replacement,
            feedback_topic=classification.feedback_topic,
        )

    targeted_idx = state.get("targeted_action_index")
    targeted_id = state.get("targeted_action_id")

    if classification.targeted_action_index is not None:
        idx = int(classification.targeted_action_index) - 1
        if 0 <= idx < len(action_items):
            targeted_idx = idx
            targeted_id = action_items[idx].get("id") or action_items[idx].get("item_id")
    elif classification.intent in {"request_alternates", "change_action", "ask_why", "request_clarification"}:
        # Preserve previously-selected item through multi-step workflows.
        targeted_idx = state.get("targeted_action_index")
        targeted_id = state.get("targeted_action_id")

    # Track preferences from conversational cues.
    user_preferences = dict(state.get("user_preferences") or {})
    dislikes = list(user_preferences.get("dislikes", []))
    if classification.intent in {"change_action", "request_alternates", "negotiate"} and targeted_idx is not None:
        action_title = action_items[targeted_idx].get("title")
        if action_title and action_title not in dislikes:
            dislikes.append(action_title)
    if dislikes:
        user_preferences["dislikes"] = dislikes[-20:]
    if classification.proposed_replacement:
        preferred = list(user_preferences.get("preferred_replacements", []))
        preferred.append(classification.proposed_replacement)
        # keep order, drop duplicates
        seen = set()
        deduped = []
        for item in preferred:
            key = str(item).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        user_preferences["preferred_replacements"] = deduped[-20:]

    updated_state = {
        **state,
        "current_intent": classification.intent,
        "targeted_action_index": targeted_idx,
        "targeted_action_id": targeted_id,
        "change_reason": classification.proposed_replacement or state.get("change_reason"),
        "feedback_topic": classification.feedback_topic,
        "user_preferences": user_preferences,
        "messages": messages + [{"role": "user", "content": user_message}],
        "phase": "processing",
    }

    goto_node = INTENT_TO_NODE.get(classification.intent, "handle_general_response")
    return Command(update=updated_state, goto=goto_node)


async def pre_model_hook_trim_messages(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Trim message history to 20 before model routing, and hydrate ui_blocks from persisted ui_elements."""
    messages = list(state.get("messages") or [])
    if len(messages) > 20:
        trimmed = truncate_conversation_smart(messages, max_messages=20, max_tokens=get_llm_config().max_conversation_tokens)
    else:
        trimmed = messages

    ui_elements = list(state.get("ui_elements") or [])
    ui_blocks = list(state.get("ui_blocks") or [])
    if not ui_blocks and ui_elements:
        ui_blocks = ui_elements

    return {
        **state,
        "messages": trimmed,
        "ui_blocks": ui_blocks,
    }


async def _apply_health_guardrail(text: str) -> str:
    """Post-model health guardrail using structured LLM moderation."""
    if not text:
        return text

    moderation_prompt = """Review this assistant response for health-safety risk.
Mark as urgent only if the message could reasonably be interpreted as encouraging unsafe behavior
or ignoring emergency warning signs.
Set should_append_urgent_guidance=true only when urgent guidance should be appended."""
    try:
        moderation = await _get_guardrail_llm().ainvoke(
            [
                {"role": "system", "content": moderation_prompt},
                {"role": "user", "content": f"Assistant response:\n{text}"},
            ]
        )
    except Exception as e:
        logger.warning(f"[GUARDRAIL] Primary moderation model failed: {e}")
        try:
            fallback_model = os.getenv("CARE_PLAN_GUARDRAIL_FALLBACK_MODEL", "gpt-4o-mini")
            fallback_llm = ChatOpenAI(model=fallback_model, temperature=0).with_structured_output(HealthGuardrailOutput)
            moderation = await fallback_llm.ainvoke(
                [
                    {"role": "system", "content": moderation_prompt},
                    {"role": "user", "content": f"Assistant response:\n{text}"},
                ]
            )
        except Exception as fallback_error:
            logger.warning(f"[GUARDRAIL] Fallback moderation failed; returning original text: {fallback_error}")
            return text

    if moderation.should_append_urgent_guidance:
        return (
            f"{text}\n\nIf you have severe symptoms or feel unsafe, seek urgent medical care or call emergency services now."
        )
    return text


async def post_model_hook_guardrail(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Ensure guardrails and persistent UI element state run on every response."""
    ui_blocks = list(state.get("ui_blocks") or [])
    ui_elements = list(state.get("ui_elements") or [])

    if ui_blocks:
        ui_elements = ui_blocks
    elif not ui_blocks and ui_elements and state.get("workflow_stage"):
        # Preserve latest CTA set while user is in an active workflow.
        ui_blocks = ui_elements

    return {
        **state,
        "bot_response": await _apply_health_guardrail((state.get("bot_response") or "").strip()),
        "ui_blocks": ui_blocks,
        "ui_elements": ui_elements,
        "action_plan": list(state.get("action_plan") or state.get("action_items") or []),
        "streak": int(state.get("streak") or state.get("current_streak") or 0),
    }


async def handle_complete_action(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Mark action complete, update streak."""
    try:
        # FIX: Use context manager to prevent connection leaks
        with get_db_session() as db:
            action_id = state.get("targeted_action_id")
            
            if not action_id:
                return {
                    **state,
                    "bot_response": "I'm not sure which action you completed. Can you specify?",
                    "phase": "loaded"
                }
            
            item = db.query(ActionPlanItem).get(action_id)
            if not item:
                return {
                    **state,
                    "bot_response": "I couldn't find that action. Please try again.",
                    "phase": "loaded"
                }
            
            item.is_completed = True
            item.completed_at = datetime.utcnow()
            # db.commit() now handled by context manager
            
            # Update streak
            from app.services.streak_service import StreakService
            streak_service = StreakService(db)
            current, longest = streak_service.update_streak_on_completion(state["user_id"])
            
            # ═══════════════════════════════════════════════════════════════════════
            # LLM-GENERATED CELEBRATION - Uses unified context for personalization
            # ═══════════════════════════════════════════════════════════════════════
            hormone = (item.target_hormone or "hormone").capitalize()
            action_title = item.title or "action"
            formatted_context = state.get("formatted_context", "")
            
            action_items = state.get("action_items", [])
            completed_count = sum(1 for a in action_items if a.get("is_completed") or a.get("id") == action_id)
            remaining = 4 - completed_count
        
        # Check if user mentioned OTHER actions in the same message (multi-action support)
        user_message = state.get("user_message", "")
        other_incomplete = [
            a.get("title", "action") for a in action_items
            if not a.get("is_completed") and a.get("id") != action_id
        ]
        multi_action_hint = ""
        if other_incomplete and len(user_message) > 15:
            # If user wrote a longer message, they might have mentioned multiple actions
            other_names = ", ".join(other_incomplete[:3])
            multi_action_hint = f"""
8. IMPORTANT: The user said: "{user_message}"
   If they mentioned completing OTHER actions besides {action_title} (like "{other_names}"), 
   acknowledge the first completion AND ask: "Did you also complete [other action]? I can mark that done too!"
   Only ask about actions they actually mentioned, not all remaining ones."""
        
        celebration_prompt = f"""Generate a celebration message for completing an action.

User Context (use to personalize):
{formatted_context[:1500] if formatted_context else "New user"}

Details:
- Action completed: {action_title}
- Target hormone: {hormone}
- Current streak: {current} days
- Longest streak: {longest} days
- Actions completed today: {completed_count}/4
- Remaining: {remaining}
- User's original message: "{user_message}"

Guidelines:
1. Use the hormone buddy voice (e.g., "I'm {hormone}!")
2. Reference the specific action they completed
3. If streak milestone (7, 14, 21, 30 days), celebrate extra
4. If completed all 4, be EXTRA enthusiastic
5. Keep it 2-3 sentences, warm and energetic
6. Use an emoji that matches the hormone ({hormone})
7. Vary the celebration style - don't always use same format{multi_action_hint}

Example variations (DON'T copy, just show variety):
- Streak focus: "Your {current}-day streak is glowing!"
- Action focus: "Those {action_title} are already working their magic!"
- Momentum focus: "Just {remaining} more and you've nailed the day!"
"""
        
        try:
            # PRODUCTION FIX: Use cached LLM call with config
            config = get_llm_config()
            response = await call_llm_with_cache(
                call_llm,
                celebration_prompt,
                temperature=config.celebration_temperature,
                max_tokens=config.max_tokens_celebration,
                intent="complete_action"
            )
            if not response or len(response.strip()) < 10:
                # Fallback
                response = f"🎉 {hormone} is celebrating! That's {current} days strong. "
                if completed_count >= 4:
                    response += "You crushed your ENTIRE plan today! 🔥"
                else:
                    response += f"Just {remaining} more to go!"
        except Exception as llm_error:
            logger.warning(f"Celebration LLM failed: {llm_error}")
            response = f"🎉 {hormone} is celebrating! That's {current} days strong. "
            if completed_count >= 4:
                response += "You crushed your ENTIRE plan today! 🔥"
            else:
                response += f"Just {remaining} more to go!"
        
        return {
            **state,
            "bot_response": response,
            "current_streak": current,
            "streak": current,
            "action_plan": action_items,
            "actions_to_execute": [{"type": "refresh_plan"}],
            "workflow_stage": None,  # Clear any active workflow
            "ui_blocks": [], # Clear any previous buttons
            "phase": "complete"
        }
    except Exception as e:
        logger.error(f"Error completing action: {e}")
        return {**state, "bot_response": f"Error: {e}", "error": str(e), "phase": "complete"}


async def handle_skip_action(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Process skip with streak guidance. Uses LLM for personalized response."""
    
    user_message = state.get("user_message", "")
    targeted_idx = state.get("targeted_action_index")
    action_items = state.get("action_items", [])
    formatted_context = state.get("formatted_context", "")
    
    # Get action title
    if targeted_idx is not None and 0 <= targeted_idx < len(action_items):
        action_title = action_items[targeted_idx].get("title", "this action")
        action_category = action_items[targeted_idx].get("category", "action")
    else:
        action_title = "this action"
        action_category = "action"
    
    # Extract skip reason
    try:
        reason_prompt = f"""User is skipping an action.
Message: "{user_message}"

Extract skip reason category:
- no_time, not_feeling_well, dont_like, no_ingredients, cultural_religious, other

Also extract: detailed_reason

Output JSON: {{
  "category": "...",
  "detailed_reason": "..."
}}
"""
        reason_data = await call_llm_structured(reason_prompt, response_model=SkipReason)
        skip_category = reason_data.category
        skip_reason = reason_data.detailed_reason
    except:
        skip_category = "other"
        skip_reason = user_message
    
    # ═══════════════════════════════════════════════════════════════════════
    # LLM-GENERATED SKIP WARNING - Personalized and empathetic
    # ═══════════════════════════════════════════════════════════════════════
    current_streak = state.get("current_streak", 0)
    completed_today_count = sum(
        1 for item in action_items
        if item.get("is_completed")
    )
    
    skip_prompt = f"""Generate an empathetic but clear warning about skipping an action.

User Context:
{formatted_context[:1000] if formatted_context else "User with active streak"}

Details:
- Action to skip: {action_title} ({action_category})
- User's reason: {skip_reason}
- Skip category: {skip_category}
- Current streak: {current_streak} days
- Completed actions today: {completed_today_count}

Guidelines:
1. Acknowledge their reason empathetically FIRST
2. Explain streak rule clearly:
   - Streak is protected if they complete at least 1 action today
   - If they already completed 1+, reassure them their streak is safe for today
   - If they completed 0, remind them to complete one of the remaining actions
3. Offer alternatives based on their reason:
   - no_time: Suggest quicker version
   - dont_like: Suggest swap
   - not_feeling_well: Suggest gentler option
   - no_ingredients: Suggest substitution
4. End with a question offering alternatives
5. Keep it 2-3 sentences, warm but honest
6. DON'T be preachy or guilt-tripping

Example tone (don't copy exactly):
"I totally get it - [action] isn't feeling right today. To protect your streak, complete at least one action today. Want me to find something easier/quicker that works better for you?"
"""
    
    try:
        # PRODUCTION FIX: Use cached LLM call with config
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            skip_prompt,
            temperature=config.explanation_temperature,
            max_tokens=config.max_tokens_skip,
            intent="skip_action"
        )
        if not response or len(response.strip()) < 20:
            if completed_today_count > 0:
                response = (
                    f"No worries if you skip {action_title}. "
                    f"Your streak is already protected today because you've completed {completed_today_count} action(s). "
                    "Want a quicker or easier alternative instead?"
                )
            else:
                response = (
                    f"Heads up: skipping {action_title} is okay, but complete at least one action today "
                    f"to protect your {current_streak}-day streak. "
                    "Want a quicker or easier alternative instead?"
                )
    except Exception as llm_error:
        logger.warning(f"Skip warning LLM failed: {llm_error}")
        if completed_today_count > 0:
            response = (
                f"No worries if you skip {action_title}. "
                f"Your streak is already protected today because you've completed {completed_today_count} action(s). "
                "Want a quicker or easier alternative instead?"
            )
        else:
            response = (
                f"Heads up: skipping {action_title} is okay, but complete at least one action today "
                f"to protect your {current_streak}-day streak. "
                "Want a quicker or easier alternative instead?"
            )
    
    # Offer alternative based on reason
    if skip_category in ["no_time", "dont_like", "not_feeling_well", "no_ingredients"]:
        ui_blocks = [create_confirmation_block(
            confirm_text="Show me alternatives",
            cancel_text="I understand, skip it",
            title=None,
            confirm_payload={"action": "show_alternates"}
        )]
        # Override styles: primary for alternates, destructive for skip
        ui_blocks[0]["actions"][0]["style"] = "primary"
        ui_blocks[0]["actions"][0]["id"] = "show_alternates"
        ui_blocks[0]["actions"][1]["style"] = "destructive"
        ui_blocks[0]["actions"][1]["id"] = "confirm_skip"
        
        return {
            **state,
            "bot_response": response,
            "ui_blocks": ui_blocks,
            "workflow_stage": "skip_decision",
            "change_reason": skip_category,
            "phase": "awaiting_selection"
        }
    else:
        return {**state, "bot_response": response, "workflow_stage": None, "ui_blocks": [], "phase": "complete"}


async def handle_change_action(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Process change request - extract barrier, route to alternates."""
    
    user_message = state.get("user_message", "")
    targeted_idx = state.get("targeted_action_index")
    action_items = state.get("action_items", [])
    
    if targeted_idx is None or not (0 <= targeted_idx < len(action_items)):
        # Show explicit action list (only incomplete items) instead of generic CTAs
        available_actions = [
            item for item in action_items
            if not item.get("is_completed")
        ]

        if not available_actions:
            # All completed - celebrate with personality
            streak = state.get("current_streak", 0)
            response = f"Amazing! You've completed all your actions for today! 🎉 That's {streak} days strong. You're doing incredible work for your health."
            return {
                **state,
                "bot_response": response,
                "ui_blocks": [],
                "phase": "complete"
            }

        current_intent = state.get("current_intent") or "change_action"
        is_alternates = current_intent in {"request_alternates", "negotiate"}
        # More natural language
        response = "Sure! Which action would you like to see alternatives for?" if is_alternates else "Of course! Which action isn't working for you today?"
        title = "Choose action for alternates" if is_alternates else "Choose action to change"
        subtitle = "Select from your remaining actions"
        ui_blocks = [
            create_action_selection_block(
                available_actions,
                title=title,
                subtitle=subtitle,
                intent=current_intent,
            )
        ]
        return {
            **state,
            "bot_response": response,
            "ui_blocks": ui_blocks,
            "workflow_stage": "awaiting_action_selection",
            "phase": "awaiting_selection"
        }

    # NOTE: Token check removed from here. Let users BROWSE alternatives freely.
    # Token check happens at COMMIT time in check_refresh_tokens_and_replace().
    # This way users with 0 tokens can still explore options and understand
    # what's available — they only get blocked when trying to actually swap.
    
    action = action_items[targeted_idx]
    
    # If the classifier already extracted a specific replacement (e.g. "cashews"), use it
    extracted_replacement = state.get("change_reason") 
    # Note: classifier puts proposed_replacement into change_reason now
    
    # Check if this "reason" looks like a replacement item (heuristics + LLM check)
    is_specific_replacement = False
    
    # Analyze barrier/intent more deeply
    try:
        barrier_prompt = f"""User wants to change: {action.get("title", "action")}
Message: "{user_message}"
Previous extracted intent/reason: "{extracted_replacement}"

1. Identify barrier: allergy, dietary, ingredients, dislike, etc.
2. Determine if user is requesting a SPECIFIC replacement item (e.g., "use cashews", "do yoga").

Output JSON: {{
  "barrier_type": "...",
  "specific_barrier": "exact complaint or request",
  "urgency": "immediate|flexible",
  "is_specific_request": true/false,
  "requested_item": "extracted item name if true, else null"
}}
"""
        # We need a slightly richer model or dynamic parsing. 
        # For simplicity, let's stick to BarrierAnalysis but add fields if needed, 
        # or just parse the dictionary if using basic LLM call
        # ... Let's assume we update BarrierAnalysis model first.
        
        # ACTUALLY: Let's reuse BarrierAnalysis but check specific_barrier text
        barrier_data = await call_llm_structured(barrier_prompt, response_model=BarrierAnalysis)
        
        # FULLY LLM-POWERED: Use the model's is_specific_request field, NO heuristics!
        target_stage = "generating_alternates"
        
        if barrier_data.is_specific_request and barrier_data.requested_item:
            # User specified exactly what they want (e.g., "change to cashews", "I want yoga instead")
            target_stage = "generating_direct_replacement"
            # Store the requested item for direct replacement generation
            extracted_replacement = barrier_data.requested_item
        elif extracted_replacement and barrier_data.is_specific_request:
            # Fallback: classifier found replacement but barrier analysis confirms specific request
            target_stage = "generating_direct_replacement"
              
        targeted_action_id = action.get("id") or action.get("item_id") or state.get("targeted_action_id")

        user_preferences = dict(state.get("user_preferences") or {})
        dislikes = list(user_preferences.get("dislikes", []))
        current_title = action.get("title")
        if current_title and current_title not in dislikes:
            dislikes.append(current_title)
        user_preferences["dislikes"] = dislikes[-20:]

        return {
            **state,
            "barrier_type": barrier_data.barrier_type,
            "change_reason": barrier_data.requested_item or barrier_data.specific_barrier,
            "targeted_action_id": targeted_action_id,
            "user_preferences": user_preferences,
            "last_options_shown": None,
            "workflow_stage": target_stage,
            "phase": "processing"
        }
    except Exception as e:
        logger.error(f"Error analyzing barrier: {e}")
        return {
            **state,
            "barrier_type": "other",
            "change_reason": user_message,
            "workflow_stage": "generating_alternates",
            "phase": "processing"
        }


async def generate_alternate_suggestions(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Generate alternatives with dedupe against last_options_shown, then keep 4 CTA choices."""
    
    targeted_idx = state.get("targeted_action_index")
    action_items = state.get("action_items", [])
    
    if targeted_idx is None or not (0 <= targeted_idx < len(action_items)):
        return {**state, "error": "no_targeted_action", "phase": "complete"}
    
    action = action_items[targeted_idx]
    reason = state.get('change_reason', '')

    try:
        from app.services.action_plan_generator import get_action_plan_generator
        generator = get_action_plan_generator()
        async_db = generator.async_session_maker()

        try:
            result = await generator.generate_replacement_candidates(
                user_id=state["user_id"],
                item_id=action.get("id") or action.get("item_id"),
                reason=reason,
                n=6,
                db=async_db,
                enforce_same_category=True
            )
        finally:
            await async_db.close()

        if not result.get("success"):
            return {
                **state,
                "bot_response": result.get("error") or "I couldn't generate alternatives. Please try again.",
                "error": result.get("error") or "candidate_generation_failed",
                "phase": "complete"
            }

        raw_alternates = result.get("actions", []) or []
        previous_titles = {str(t).strip().lower() for t in (state.get("last_options_shown") or []) if str(t).strip()}
        deduped: List[Dict[str, Any]] = []
        seen_titles = set()

        for alt in raw_alternates:
            title = str(alt.get("title") or "").strip()
            key = title.lower()
            if not title or key in seen_titles or key in previous_titles:
                continue
            seen_titles.add(key)
            deduped.append(alt)

        # If the generator returned too few net-new options, allow previously seen ones as fallback.
        if len(deduped) < 4:
            for alt in raw_alternates:
                title = str(alt.get("title") or "").strip()
                key = title.lower()
                if not title or key in seen_titles:
                    continue
                seen_titles.add(key)
                deduped.append(alt)
                if len(deduped) >= 4:
                    break

        alternates = deduped[:4]
        if not alternates:
            return {
                **state,
                "bot_response": "I couldn't find new options yet. Want me to try a different style of alternatives?",
                "workflow_stage": "awaiting_alternate_selection",
                "phase": "awaiting_selection",
            }
        
        # Generate personalized introduction for alternatives
        barrier_type = state.get("barrier_type", "preference")
        original_title = action.get('title', 'that action')
        
        intro_prompt = f"""Generate a brief, warm introduction for showing alternative actions.

Context:
- Original action: {original_title}
- User's reason for change: {barrier_type}
- Number of alternatives: {len(alternates)}

Guidelines:
1. Acknowledge their reason briefly
2. Express you found great alternatives
3. Keep it 1 short sentence
4. Be encouraging

Example: "I found 4 options that should work better with your schedule!"
"""
        
        # PRODUCTION FIX: intro generation is actually fast enough sequentially
        # (Alternative: could parallelize if we generate alternatives summary separately)
        try:
            config = get_llm_config()
            intro = await call_llm_with_cache(
                call_llm,
                intro_prompt,
                temperature=config.explanation_temperature,
                max_tokens=config.max_tokens_alternatives_intro,
                intent="request_alternates"
            )
            if not intro or len(intro.strip()) < 10:
                intro = f"Here are {len(alternates)} alternatives that might fit you better:"
        except:
            intro = f"Here are {len(alternates)} alternatives that might fit you better:"

        ui_block = create_alternates_selection_block(
            alternates=[{"title": alt.get("title"), "specific_action": alt.get("specific_action"), "why_better": alt.get("purpose") or ""} for alt in alternates],
            title=f"Alternatives for {action.get('title', 'action')}"
        )

        return {
            **state,
            "alternate_candidates": alternates,
            "last_options_shown": [str(alt.get("title") or "").strip() for alt in alternates if str(alt.get("title") or "").strip()],
            "ui_blocks": [ui_block],
            "workflow_stage": "awaiting_alternate_selection",
            "bot_response": intro,
            "phase": "awaiting_selection"
        }
    except Exception as e:
        logger.error(f"Error generating alternates: {e}")
        return {
            **state,
            "bot_response": "Hmm, I'm having trouble finding alternatives right now. Would you like to try a different action, or should we keep this one for today?",
            "error": str(e),
            "phase": "complete"
        }


async def check_refresh_tokens_and_replace(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Check tokens at FINAL replacement step and process replacement."""
    logger.info(f"[GRAPH_NODE] check_refresh_tokens_and_replace START")
    
    selected_idx = state.get("selected_alternate_index")
    alternates = state.get("alternate_candidates", [])
    
    # BOUNDS CHECK (Fix for Issue 8)
    if selected_idx is None or not (0 <= selected_idx < len(alternates)):
        logger.warning(f"[GRAPH_NODE] Invalid selection: idx={selected_idx}, alternates={len(alternates)}")
        return {
            **state,
            "bot_response": "I didn't catch which option you wanted. Could you tap on one of the alternatives above?",
            "error": "invalid_selection",
            "phase": "awaiting_selection"
        }
    
    # Check if user has tokens
    if state.get("refresh_tokens_available", 0) <= 0:
        logger.info(f"[GRAPH_NODE] No refresh tokens available")
        current_streak = state.get("current_streak", 0)
        days_needed = max(0, 16 - current_streak)
        if days_needed > 0:
            response = f"You're so close! Just {days_needed} more day{'s' if days_needed > 1 else ''} to unlock refresh credits. Keep that {current_streak}-day streak going! 💪"
        else:
            response = "You've used all your refresh credits for today. They'll reset tomorrow - you've got this! 💜"
        return {
            **state,
            "bot_response": response,
            "workflow_stage": None,
            "ui_blocks": [],
            "error": "insufficient_refresh_tokens",
            "phase": "complete"
        }
    
    # Has tokens → proceed to replacement
    logger.info(f"[GRAPH_NODE] Processing replacement with tokens available")

    selected_alt = alternates[selected_idx]
    original_action_id = state.get("targeted_action_id")
    if not original_action_id:
        return {**state, "error": "no_original_action", "phase": "complete"}

    # Interrupt for human confirmation (LangGraph interrupt)
    approval = interrupt({
        "type": "confirm_replace",
        "original_action_id": original_action_id,
        "replacement_title": selected_alt.get("title"),
        "message": "Confirm this replacement?"
    })

    if approval is False:
        return {
            **state,
            "bot_response": "No worries — we’ll keep your plan as it is.",
            "ui_blocks": [],
            "phase": "complete"
        }

    try:
        # Use proper context manager for database session
        from app.core.database import SessionLocal
        db = SessionLocal()
        logger.info(f"[GRAPH_NODE] DB session created")
        
        try:
            logger.info(f"[GRAPH_NODE] Querying original action id={original_action_id}")
            # Mark original as replaced
            original = db.query(ActionPlanItem).get(original_action_id)
            if not original:
                return {**state, "error": "original_not_found", "phase": "complete"}
            
            logger.info(f"[GRAPH_NODE] Replacing via ActionPlanGenerator")
            from app.services.action_plan_generator import get_action_plan_generator
            generator = get_action_plan_generator()
            async_db = generator.async_session_maker()

            try:
                replace_result = await generator.replace_action_from_action_dict(
                    user_id=state["user_id"],
                    item_id=original_action_id,
                    replacement_action=selected_alt,
                    reason=state.get("change_reason"),
                    db=async_db
                )
            finally:
                await async_db.close()

            if not replace_result.get("success"):
                return {
                    **state,
                    "bot_response": replace_result.get("error") or "Sorry, I couldn't process the replacement. Please try again.",
                    "error": replace_result.get("error") or "replacement_failed",
                    "phase": "complete"
                }

            replacement_action = replace_result.get("replacement_action") or {}
            replacement_title = replacement_action.get("title") or selected_alt.get("title") or "the new action"
            
            # CONSUME REFRESH TOKEN - Log to ActionPlanRefreshLog
            logger.info(f"[GRAPH_NODE] Creating refresh log")
            from datetime import date as date_type
            refresh_log = ActionPlanRefreshLog(
                uid=state["user_id"],
                plan_id=original.plan_id,  # Use original's plan_id
                refresh_date=date_type.today(),
                refresh_count=1,
                original_action={
                    "id": original_action_id,
                    "title": original.title if original else None,
                    "time_slot": original.time_slot if original else None
                },
                replacement_action={
                    "title": replacement_title,
                    "specific_action": replacement_action.get("specific_action") or selected_alt.get("specific_action")
                },
                replacement_reason=state.get("change_reason", "user_request"),
                thread_id=state.get("thread_id"),
                created_at=datetime.utcnow()
            )
            db.add(refresh_log)
            
            # ACTUALLY CONSUME THE REFRESH TOKEN (update UserStreakData)
            logger.info(f"[GRAPH_NODE] Consuming refresh token via RewardService")
            from app.services.reward_service import RewardService
            reward_service = RewardService(db)
            refresh_result = reward_service.use_refresh(state["user_id"])
            logger.info(f"[GRAPH_NODE] Refresh token consumed: {refresh_result}")
            
            if not refresh_result.get("success"):
                logger.warning(f"[GRAPH_NODE] Refresh failed: {refresh_result.get('error')}")
                tokens_remaining = max(0, (state.get("refresh_tokens_available", 0) - 1))
                response = (
                    f"Done! Swapped {original.title} → {replacement_title}. "
                    f"This one should fit you better. Your refresh count will sync shortly."
                )
            else:
                tokens_remaining = refresh_result.get("remaining", 0)
                # More personalized message
                if tokens_remaining > 0:
                    response = f"Done! Swapped {original.title} → {replacement_title}. This one should work better for you. You have {tokens_remaining} refresh(es) left if you need more changes."
                else:
                    response = f"Done! Swapped {original.title} → {replacement_title}. This one should fit you better. That's your last refresh for today - make it count! 💪"

            logger.info(f"[GRAPH_NODE] Committing to database")
            db.commit()
            logger.info(f"[GRAPH_NODE] Database commit successful")
            return {
                **state,
                "bot_response": response,
                "actions_to_execute": [{"type": "refresh_plan"}],
                "workflow_stage": None,
                "ui_blocks": [],
                "phase": "complete"
            }
        except Exception as e:
            db.rollback()
            raise e
        finally:
            logger.info(f"[GRAPH_NODE] Closing DB session")
            db.close()
    
    except Exception as e:
        logger.error(f"[GRAPH_NODE] Error during replacement: {e}")
        import traceback
        traceback.print_exc()
        return {
            **state,
            "bot_response": "Sorry, I couldn't process the replacement. Please try again.",
            "error": str(e),
            "phase": "complete"
        }


async def handle_plan_feedback(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """
    Handle when user gives feedback about the OVERALL plan quality.
    
    ARCHITECTURAL FIX: Uses LLM with full unified context instead of hardcoded templates.
    This enables truly personalized, contextual responses that reference:
    - User's past conversations across ALL chatbots
    - Their specific symptoms and conditions
    - Previous feedback they've given
    - Their preferences and what worked/didn't work before
    """
    
    user_message = state.get("user_message", "")
    action_items = state.get("action_items", [])
    user_id = state.get("user_id")
    feedback_topic = state.get("feedback_topic", "generic")
    
    # Log the feedback for learning (this should also be stored in memory for future reference)
    logger.info(f"[PLAN_FEEDBACK] User {user_id} feedback - topic: {feedback_topic}, message: {user_message}")
    
    # Get unified cross-chatbot context (loaded in load_daily_plan_and_tokens)
    formatted_context = state.get("formatted_context", "")
    unified_context = state.get("unified_context", {})
    
    # Build detailed action plan context with evidence
    actions_with_evidence = []
    for i, item in enumerate(action_items[:4], 1):
        action_info = {
            "number": i,
            "title": item.get("title", "Unknown"),
            "category": item.get("category", "general"),
            "why_it_works": item.get("why_it_works", item.get("evidence_summary", "")),
            "target_symptom": item.get("target_symptom", ""),
            "citations": item.get("citations", [])[:2]  # First 2 citations
        }
        actions_with_evidence.append(action_info)
    
    # Get user's profile info from unified context
    user_profile = unified_context.get("user_profile", {})
    learned_prefs = unified_context.get("learned_preferences", {})
    recent_convos = unified_context.get("recent_conversations", [])
    
    # Build the LLM prompt using proper prompt engineering structure
    prompt = f"""<role>
You are Auvra, a warm and knowledgeable hormone health companion. You're responding to a user who has given feedback about their action plan.
</role>

<context>
## User Profile & History
{formatted_context}

## Today's Action Plan (the user is giving feedback about this):
{json.dumps(actions_with_evidence, indent=2)}

## User's Feedback
Topic: {feedback_topic}
Message: "{user_message}"
</context>

<task>
The user has expressed concern about their action plan. Your job is to:

1. ACKNOWLEDGE their specific feedback genuinely (don't dismiss it)
2. EXPLAIN the personalization that went into their plan by referencing SPECIFIC details from their profile
3. OFFER concrete next steps

IMPORTANT GUIDELINES:
- Reference SPECIFIC things from their history (symptoms, conditions, past conversations)
- If they say the plan is "generic" or "not personalized", prove it IS personalized by citing specific details about THEM
- Don't just list generic benefits - explain why THIS item for THIS person
- If they've mentioned similar feedback before (check recent_conversations), acknowledge you're working on it
- Keep it conversational and empathetic, not defensive
- Keep response under 150 words

Example of a BAD response (too generic):
"I hear you! Each item was chosen for your hormone health. Would you like me to explain more?"

Example of a GOOD response (personalized):
"I hear your concern about the plan feeling generic. Looking at your profile, I chose salmon specifically because you mentioned fatigue as your main concern last week - omega-3s directly support energy production. The yoga recommendation is timed for your luteal phase when cortisol tends to spike. What I haven't accounted for yet is your preference for morning workouts - would you like me to adjust the timing?"
</task>

<output_format>
Respond naturally as Auvra. Be specific, reference their data, and end with a clear offer to help.
</output_format>"""

    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            prompt,
            temperature=config.explanation_temperature,
            max_tokens=config.max_tokens_negotiate,
            intent="negotiate"
        )
        
        if not response or len(response.strip()) < 20:
            # Fallback if LLM fails
            response = f"I appreciate you sharing that feedback. Your plan was created based on your {user_profile.get('cycle_phase', 'current cycle phase')} and concerns you've shared. Would you like me to regenerate it with different items, or explain the reasoning behind each action?"
    except Exception as e:
        logger.error(f"Error generating plan feedback response: {e}")
        response = f"Thank you for that feedback. I want to make sure your plan truly fits your needs. Would you like me to explain why I chose each item for you, or generate a fresh plan?"
    
    ui_blocks = [
        {
            "type": "quick_replies",
            "replies": [
                {"label": "Explain each action", "value": "explain why you chose each of these items for me specifically"},
                {"label": "Regenerate plan", "value": "give me a completely different plan with new items"},
                {"label": "Update my profile", "value": "I want to update my health information"}
            ]
        }
    ]
    
    return {
        **state,
        "bot_response": response,
        "ui_blocks": ui_blocks,
        "phase": "awaiting_feedback_response"
    }


async def handle_challenge_science(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """
    Handle when user questions the research/science behind the plan.
    
    ARCHITECTURAL FIX: Uses LLM with full context to provide genuinely personalized
    scientific explanations that reference the user's specific conditions.
    """
    
    user_message = state.get("user_message", "")
    action_items = state.get("action_items", [])
    user_id = state.get("user_id")
    
    logger.info(f"[CHALLENGE_SCIENCE] User {user_id} questioning science: {user_message}")
    
    # Get unified cross-chatbot context
    formatted_context = state.get("formatted_context", "")
    unified_context = state.get("unified_context", {})
    
    # Build detailed action plan with all evidence
    actions_with_evidence = []
    for i, item in enumerate(action_items[:4], 1):
        actions_with_evidence.append({
            "number": i,
            "title": item.get("title", "Unknown"),
            "category": item.get("category", "general"),
            "evidence_summary": item.get("evidence_summary", item.get("why_it_works", "No evidence available")),
            "target_symptom": item.get("target_symptom", "general wellness"),
            "citations": item.get("citations", []),
            "pubmed_ids": item.get("pubmed_ids", [])
        })
    
    prompt = f"""<role>
You are Auvra, a hormone health expert who can explain scientific research in an accessible, trustworthy way.
</role>

<context>
## User Profile & History
{formatted_context}

## Today's Action Plan with Evidence:
{json.dumps(actions_with_evidence, indent=2)}

## User's Question about the Science:
"{user_message}"
</context>

<task>
The user is questioning whether your recommendations are backed by real science. Your job is to:

1. Validate their skepticism (it's healthy to question!)
2. Explain the SPECIFIC research behind each action item
3. Connect each piece of evidence to THEIR specific condition/symptom
4. Cite actual studies when available (use the citations/pubmed_ids provided)

IMPORTANT GUIDELINES:
- Be specific: "A 2022 study in Journal of Nutrition found..." not "Studies show..."
- Connect to THEIR symptoms: "Since you mentioned fatigue, this helps because..."
- If evidence is missing for an item, be honest about it
- Don't be defensive - embrace their curiosity
- Keep under 200 words

Example of BAD response:
"All my recommendations are based on research! Salmon is good for hormones."

Example of GOOD response:
"I love that you're asking! Let me break it down:

For the salmon - a 2021 meta-analysis in Nutrients found that omega-3s reduce inflammatory markers by 15% in women with PCOS (which you mentioned during onboarding). Since inflammation drives many of your symptoms, this directly targets your concern.

The evening yoga specifically comes from research on cortisol timing during the luteal phase - your current phase according to cycle day 22."
</task>

<output_format>
Respond naturally as Auvra. Be specific with citations, connect to their profile, and be genuinely helpful.
</output_format>"""

    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            prompt,
            temperature=config.explanation_temperature,
            max_tokens=config.max_tokens_challenge_science,
            intent="challenge_science"
        )
        
        if not response or len(response.strip()) < 20:
            # Fallback with whatever evidence we have
            science_explanations = []
            for item in actions_with_evidence:
                evidence = item.get("evidence_summary", "")
                citations = item.get("citations", [])
                citation_text = f" ({', '.join(citations[:2])})" if citations else ""
                science_explanations.append(f"**{item['title']}**: {evidence or 'Selected for your hormone health'}{citation_text}")
            
            response = f"Great question! Here's the research behind today's plan:\n\n" + "\n".join(science_explanations)
    except Exception as e:
        logger.error(f"Error generating science explanation: {e}")
        response = "I appreciate your curiosity about the research! Each item was selected based on studies relevant to hormone health. Would you like me to go deeper on any specific action?"
    
    ui_blocks = [
        {
            "type": "quick_replies",
            "replies": [
                {"label": "Tell me more", "value": "explain more about the research behind these items"},
                {"label": "Different options", "value": "show me alternative items with different research backing"},
                {"label": "Looks good!", "value": "okay I trust the science, let's continue"}
            ]
        }
    ]
    
    return {
        **state,
        "bot_response": response,
        "ui_blocks": ui_blocks,
        "phase": "complete"
    }


async def handle_explain_plan(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """
    Handle when user wants to understand HOW the plan was created.
    
    ARCHITECTURAL FIX: Uses LLM with full unified context to explain the personalization
    process in a way that references SPECIFIC user data.
    """
    
    user_message = state.get("user_message", "")
    action_items = state.get("action_items", [])
    user_id = state.get("user_id")
    
    logger.info(f"[EXPLAIN_PLAN] User {user_id} wants explanation: {user_message}")
    
    # Get unified cross-chatbot context
    formatted_context = state.get("formatted_context", "")
    unified_context = state.get("unified_context", {})
    
    # Build detailed action info
    actions_with_reasons = []
    for i, item in enumerate(action_items[:4], 1):
        actions_with_reasons.append({
            "number": i,
            "title": item.get("title", "Unknown"),
            "category": item.get("category", "general"),
            "why_it_works": item.get("why_it_works", ""),
            "target_symptom": item.get("target_symptom", ""),
            "target_hormone": item.get("target_hormone", "")
        })
    
    prompt = f"""<role>
You are Auvra, a hormone health expert explaining how you created a personalized action plan.
</role>

<context>
## User's Complete Profile & History
{formatted_context}

## Today's Action Plan:
{json.dumps(actions_with_reasons, indent=2)}

## User's Question:
"{user_message}"
</context>

<task>
The user wants to understand HOW their plan was created. Walk them through your process by referencing SPECIFIC data from their profile.

Structure your explanation like this:
1. What you know about them (cite specific symptoms, conditions, preferences from their profile)
2. How their cycle phase influenced the choices
3. Why each specific action was chosen for THEM (not generic benefits)
4. Any past feedback or preferences that shaped the plan

IMPORTANT:
- Be SPECIFIC: "You mentioned fatigue on January 15th" not "your concerns"
- Connect each action to THEIR data: "The salmon addresses your PCOS diagnosis because..."
- If you don't have specific data, acknowledge it and offer to learn more
- Keep it warm and educational, not robotic
- Under 180 words

Example of BAD response:
"I created your plan based on your cycle phase and concerns. Each item supports hormone health."

Example of GOOD response:
"Here's exactly how I built today's plan for you:

First, I looked at what you've shared: PCOS diagnosis, fatigue as your main concern, and preference for vegetarian options when possible.

Since you're on cycle day 18 (luteal phase), your progesterone is rising and cortisol can spike. So I chose:
- Evening yoga: Directly targets cortisol, especially important since you mentioned work stress in last week's check-in
- Pumpkin seeds: High in zinc which supports the progesterone surge happening now

I noticed you've had salmon twice this week, so I swapped in mackerel for omega-3 variety."
</task>

<output_format>
Respond as Auvra, walking through your personalization process with specific examples.
</output_format>"""

    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            prompt,
            temperature=config.explanation_temperature,
            max_tokens=config.max_tokens_explain_plan,
            intent="explain_plan"
        )
        
        if not response or len(response.strip()) < 20:
            user_context = await _get_user_context_for_explanation(user_id)
            response = f"""Let me show you how I created today's plan:

**Your Profile**: Cycle phase ({user_context.get('cycle_phase', 'unknown')}), concerns ({user_context.get('concerns', 'hormone wellness')})

**Today's Actions**:
{chr(10).join([f"• {item.get('title')}: {item.get('why_it_works', 'supports your goals')}" for item in action_items[:4]])}

Would you like me to explain any specific item in more detail?"""
    except Exception as e:
        logger.error(f"Error generating plan explanation: {e}")
        user_context = await _get_user_context_for_explanation(user_id)
        response = f"I created your plan based on your {user_context.get('cycle_phase', 'current')} phase and concerns like {user_context.get('concerns', 'hormone balance')}. Would you like me to explain any specific action?"
    
    ui_blocks = [
        {
            "type": "quick_replies",
            "replies": [
                {"label": "Makes sense!", "value": "great, let's continue with the plan"},
                {"label": "Still concerns", "value": "I still have concerns about the personalization"},
                {"label": "Change something", "value": "I'd like to swap one of the items"}
            ]
        }
    ]
    
    return {
        **state,
        "bot_response": response,
        "ui_blocks": ui_blocks,
        "phase": "complete"
    }


async def _get_user_context_for_explanation(user_id) -> dict:
    """Fetch user context to explain plan personalization."""
    try:
        from app.core.database import SessionLocal, UserProfile, UserResponse
        
        db = SessionLocal()
        try:
            # user_id is Firebase UID (string), not integer
            uid = str(user_id)
            profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
            
            # Get user's concerns and conditions from UserResponse
            user_response = db.query(UserResponse).filter(
                UserResponse.uid == uid
            ).order_by(UserResponse.id.desc()).first()
            
            concerns = "hormone balance"
            conditions = "none specified"
            cycle_phase = "unknown"
            
            if user_response:
                # Collect concerns from all concern fields
                all_concerns = []
                if user_response.period_concerns:
                    all_concerns.extend(user_response.period_concerns if isinstance(user_response.period_concerns, list) else [str(user_response.period_concerns)])
                if user_response.top_concern:
                    all_concerns.append(user_response.top_concern)
                if all_concerns:
                    concerns = ", ".join(all_concerns[:3])
                
                if user_response.diagnosed_conditions:
                    conditions = ", ".join(user_response.diagnosed_conditions)
                
                if user_response.primary_hormone:
                    cycle_phase = f"{user_response.primary_hormone} focus"
            
            return {
                "cycle_phase": cycle_phase,
                "concerns": concerns,
                "conditions": conditions
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error fetching user context: {e}")
        return {
            "cycle_phase": "unknown",
            "concerns": "hormone health",
            "conditions": "none specified"
        }


async def handle_health_question(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """
    Handle general health questions that aren't specifically about today's plan.
    
    ARCHITECTURAL FIX: When user asks "How can I regulate my periods?" or similar 
    health questions, this handler provides knowledgeable, personalized responses
    using the unified cross-chatbot context.
    
    This bridges the gap between care_plan_checkin and know_my_body chatbots,
    letting users ask health questions from any context.
    """
    
    user_message = state.get("user_message", "")
    user_id = state.get("user_id")
    action_items = state.get("action_items", [])
    
    # Get unified cross-chatbot context
    formatted_context = state.get("formatted_context", "")
    unified_context = state.get("unified_context", {})
    
    # Get user profile for personalization
    user_profile = unified_context.get("user_profile", {})
    cycle_phase = state.get("cycle_phase", "unknown")
    cycle_day = state.get("cycle_day")
    
    # Build today's plan context
    actions_text = "\n".join([
        f"- {item.get('title', 'Unknown')} ({item.get('category', 'general')})"
        for item in action_items[:4]
    ]) if action_items else "No actions loaded"
    
    prompt = f"""<role>
You are Auvra, a knowledgeable and warm hormone health companion. You're answering a health question from a user who is viewing their daily care plan.
</role>

<user_context>
{formatted_context if formatted_context else "User context not available."}

Current Cycle Info:
- Cycle Day: {cycle_day}
- Phase: {cycle_phase}
</user_context>

<todays_plan>
{actions_text}
</todays_plan>

<question>
User asked: "{user_message}"
</question>

<task>
Answer their health question as a knowledgeable hormone health expert. Your response should:

1. **Be educational and informative** - Give real, helpful information
2. **Personalize to their situation** - Reference their specific conditions, symptoms, or cycle phase
3. **Connect to their plan if relevant** - If any of today's actions relate to their question, mention it
4. **Be warm but authoritative** - You know this topic well

Guidelines:
- For period regulation questions: Discuss hormonal balance, lifestyle factors, nutrition
- For symptom questions: Explain the hormonal connection and what helps
- For general wellness: Connect to their cycle phase and hormones
- Keep response 100-150 words
- End with a gentle check-in or offer to explain more

Do NOT say things like:
❌ "I'm not a medical professional" (you ARE a hormone health companion)
❌ "Got it" without actually answering
❌ Generic wellness advice without personalization
</task>

<output>
Respond as Auvra with a helpful, personalized answer.
</output>"""

    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            prompt,
            temperature=config.explanation_temperature,
            max_tokens=config.max_tokens_health_question,
            intent="health_question"
        )
        
        if not response or len(response.strip()) < 20:
            logger.warning(f"LLM returned empty response for health question")
            response = f"That's a great question about your health! Based on your {cycle_phase} phase, there are several approaches that can help. Would you like me to explain some strategies that work well with your cycle?"
    except Exception as e:
        logger.error(f"Error in handle_health_question LLM call: {e}")
        response = f"I'd love to help you with that. Your hormones and cycle phase definitely play a role. Would you like me to explain how your current {cycle_phase} phase affects this?"
    
    # Add helpful follow-up options
    ui_blocks = [
        {
            "type": "quick_replies",
            "replies": [
                {"label": "Tell me more", "value": "Please explain more about this"},
                {"label": "How does my plan help?", "value": "How does today's plan help with this?"},
                {"label": "Back to my plan", "value": "Let's go back to my daily plan"}
            ]
        }
    ]
    
    return {
        **state,
        "bot_response": response,
        "ui_blocks": ui_blocks,
        "phase": "complete"
    }


async def handle_show_streak(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Return streak/progress details with milestone-aware messaging."""
    try:
        with get_db_session() as db:
            from app.services.streak_service import StreakService

            streak_status = StreakService(db).get_full_streak_status(state["user_id"])

        current = int(streak_status.get("current_streak") or state.get("current_streak") or 0)
        longest = int(streak_status.get("longest_streak") or current)
        freeze_count = int(streak_status.get("freeze_count") or 0)
        refresh_tokens = int(state.get("refresh_tokens_available") or 0)

        milestone_days = [7, 14, 21, 30]
        next_milestone = next((d for d in milestone_days if d > current), None)
        if next_milestone is None:
            milestone_text = "You've cleared all core milestones. Keep stacking healthy days."
        else:
            remaining = max(0, next_milestone - current)
            milestone_text = f"{remaining} more day{'s' if remaining != 1 else ''} to your {next_milestone}-day milestone."

        response = (
            f"You're on a {current}-day streak (longest: {longest}). "
            f"You currently have {freeze_count} freeze token{'s' if freeze_count != 1 else ''} "
            f"and {refresh_tokens} refresh credit{'s' if refresh_tokens != 1 else ''} left today. "
            f"{milestone_text}"
        )

        return {
            **state,
            "current_streak": current,
            "streak": current,
            "bot_response": response,
            "phase": "complete",
        }
    except Exception as e:
        logger.error(f"Error in handle_show_streak: {e}")
        current = int(state.get("current_streak") or state.get("streak") or 0)
        return {
            **state,
            "bot_response": f"You're currently on a {current}-day streak. Keep going - you're building real momentum.",
            "phase": "complete",
        }


async def handle_general_response(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """
    Handle general/unclear intents with FULL LLM INTELLIGENCE.
    
    ARCHITECTURAL FIX: This was previously hardcoded with "Got it. Want to adjust anything?"
    Now uses LLM with full user context to give personalized, intelligent responses.
    
    This is the fallback handler - if we can't classify the intent clearly, we let
    the LLM respond naturally using all available context about the user.
    
    ADDITIONAL FIX: Added repeat detection — if the general handler keeps firing
    with the same generic response, we force a more helpful approach.
    """
    
    user_message = state.get("user_message", "")
    action_items = state.get("action_items", [])
    user_id = state.get("user_id")
    
    # Get unified cross-chatbot context (should already be loaded)
    formatted_context = state.get("formatted_context", "")
    unified_context = state.get("unified_context", {})
    
    # REPEAT DETECTION: Check if the last bot messages were generic fallbacks
    # If user keeps getting generic responses, something is wrong — be more proactive
    messages = state.get("messages", [])
    recent_bot_msgs = [m.get("content", "") for m in messages[-6:] if m.get("role") == "assistant"]
    generic_patterns = ["i'm here to help", "want to adjust", "what would you like", "is there something specific"]
    repeat_count = sum(1 for msg in recent_bot_msgs if any(p in msg.lower() for p in generic_patterns))
    is_repeating = repeat_count >= 2
    
    # Get the last bot message for context
    last_bot_message = ""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            last_bot_message = m.get("content", "")
            break
    
    # Build action plan context
    actions_with_status = []
    for i, item in enumerate(action_items[:4], 1):
        status = "✓ Done" if item.get("is_completed") else "○ Pending"
        actions_with_status.append(f"{i}. {item.get('title', 'Unknown')} [{status}]")
    actions_text = "\n".join(actions_with_status) if actions_with_status else "No actions loaded"
    
    # Build repeat avoidance instruction
    repeat_instruction = ""
    if is_repeating:
        repeat_instruction = """
⚠️ CRITICAL: The bot has been giving GENERIC responses repeatedly. The user is likely frustrated.
DO NOT give another generic response. Instead:
- If user seems to want to change something → proactively offer to change/replace an action
- If user seems confused → walk them through what they can do with their plan
- If user asked a question → ANSWER IT directly using your knowledge
- Reference their SPECIFIC actions and health context in your response
"""
    
    # For ALL messages — use LLM with full context
    prompt = f"""<role>
You are Auvra, a warm and knowledgeable hormone health companion. You're having a conversation with a user about their daily wellness plan.
</role>

<user_context>
{formatted_context if formatted_context else "User context not available."}
</user_context>

<todays_plan>
{actions_text}
</todays_plan>

<last_bot_message>
{last_bot_message if last_bot_message else "Bot just presented the daily action plan."}
</last_bot_message>

<conversation>
User just said: "{user_message}"
</conversation>
{repeat_instruction}
<task>
Respond naturally and helpfully as Auvra. Your response should:

1. **Actually answer their question or address their concern** - don't give a generic reply
2. **Use their context** - reference their specific conditions, symptoms, cycle phase if relevant
3. **Be conversational** - like talking to a supportive friend who happens to know about hormone health
4. **Stay helpful** - if they're asking about something health-related, provide useful information
5. **If they ask about progress or status** - look at today's plan above (✓ Done vs ○ Pending) and tell them exactly which ones are done and which remain
6. **If they seem to want changes** - proactively ask which action they'd like to change and offer to find alternatives
7. **If truly unclear what they want** - ask a clarifying question, but make it natural and reference THEIR plan

IMPORTANT:
- NEVER respond with generic templates like "Got it. Want to adjust anything?"
- NEVER say "I'm here to help! Is there something specific..."
- NEVER repeat the same response structure as the last bot message
- If they ask "how many left?" or "what's remaining?" → tell them specific pending items from the plan
- If they ask about periods, hormones, symptoms → answer with medical knowledge
- If they're giving feedback → acknowledge it genuinely
- If they express emotions (frustrated, happy, tired) → be empathetic and supportive
- Keep response under 100 words unless detailed explanation needed
- Always reference AT LEAST ONE specific detail from their plan or health context
</task>

<output>
Respond directly as Auvra. Be warm, helpful, and specific to this user. NO generic responses.
</output>"""

    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            prompt,
            temperature=config.general_temperature,
            max_tokens=config.max_tokens_general,
            intent="general"
        )
        
        if not response or len(response.strip()) < 10:
            # Fallback only if LLM completely fails
            logger.warning(f"LLM returned empty/short response for general handler")
            incomplete = [item for item in action_items if not item.get("is_completed")]
            if incomplete:
                names = [item.get("title", "action") for item in incomplete[:3]]
                response = f"You still have {', '.join(names)} on your list today. Would you like tips on any of them, or want to swap one out?"
            else:
                response = "Looks like you've completed all your actions today — amazing work! 💜 Anything else on your mind?"
    except Exception as e:
        logger.error(f"Error in handle_general_response LLM call: {e}")
        incomplete = [item for item in action_items if not item.get("is_completed")]
        if incomplete:
            names = [item.get("title", "action") for item in incomplete[:3]]
            response = f"You have {', '.join(names)} remaining today. Want to know more about any of them?"
        else:
            response = "Great job completing all your actions today! 🎉 Let me know if you have any health questions."
    
    # Smart UI blocks based on whether response is a question
    ui_blocks = []
    if "?" in response:
        # Don't add CTA buttons when Auvra asked a question - wait for user response
        pass
    else:
        ui_blocks = await _maybe_add_ctas(state, response, user_message)
    
    return {
        **state,
        "bot_response": response,
        "workflow_stage": None,  # Clear any active workflow
        "ui_blocks": ui_blocks,
        "phase": "complete"
    }


# ═══════════════════════════════════════════════════════════════════
# CONDITIONAL EDGE ROUTING (PURE FUNCTIONS)
# ═══════════════════════════════════════════════════════════════════

def route_by_intent(state: CarePlanCheckInState) -> str:
    """Route based on LLM-classified intent. FIXED: No fallback to non-existent nodes."""
    intent = state.get("current_intent", "general")
    
    intent_routing = {
        "complete_action": "handle_complete_action",
        "skip_action": "handle_skip_action",
        "change_action": "handle_change_action",
        "request_alternates": "handle_change_action", # Treat alternates as change request
        "negotiate": "handle_change_action",  # Treat negotiate as change
        "ask_why": "handle_ask_why",
        "request_clarification": "handle_request_clarification",  # NEW: Handle timing/how-to questions
        "cancel_action": "handle_cancel_action",
        "plan_feedback": "handle_plan_feedback",  # Overall plan quality feedback
        "challenge_science": "handle_challenge_science",  # Questioning research
        "explain_plan": "handle_explain_plan",  # How was plan created
        "health_question": "handle_health_question",  # NEW: General health questions
        "general": "handle_general_response"
    }
    
    return intent_routing.get(intent, "handle_general_response")


def route_after_change(state: CarePlanCheckInState) -> str:
    """Route after handle_change_action."""
    if state.get("workflow_stage") == "generating_alternates":
        return "generate_alternate_suggestions"
    if state.get("workflow_stage") == "generating_direct_replacement":
        return "generate_direct_replacement_suggestion"
    return "END"


async def generate_direct_replacement_suggestion(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Generate a SINGLE specific replacement based on user request."""
    
    targeted_idx = state.get("targeted_action_index")
    action_items = state.get("action_items", [])
    
    if targeted_idx is None:
        return {**state, "phase": "complete"}
    
    original_action = action_items[targeted_idx]
    requested_item = state.get("change_reason")  # e.g., "cashews"
    
    try:
        from app.services.action_plan_generator import get_action_plan_generator
        generator = get_action_plan_generator()
        async_db = generator.async_session_maker()

        try:
            result = await generator.generate_replacement_candidates(
                user_id=state["user_id"],
                item_id=original_action.get("id") or original_action.get("item_id"),
                reason=f"User requested: replace with {requested_item}",
                n=1,
                db=async_db,
                enforce_same_category=True
            )
        finally:
            await async_db.close()

        if not result.get("success") or not result.get("actions"):
            return {
                **state,
                "bot_response": "I couldn't find a specific match. Let me find some alternatives instead.",
                "workflow_stage": "generating_alternates",
                "phase": "processing"
            }

        alternatives = result.get("actions")[:1]
        alt = alternatives[0]

        ui_blocks = [{
            "id": "alternate_suggestions",
            "type": "quick_actions",
            "title": f"Switch to {alt.get('title')}?",
            "subtitle": "You requested this specific change.",
            "actions": [
                {
                    "id": "select_alt_0",
                    "title": f"Yes, confirm {alt.get('title')}",
                    "action_type": "submit_event",
                    "style": "primary"
                }
            ],
            "dismissible": True
        }]

        return {
            **state,
            "alternate_candidates": alternatives,
            "ui_blocks": ui_blocks,
            "bot_response": f"I found a match for '{requested_item}'. Shall we switch to {alt.get('title')}?",
            "workflow_stage": "awaiting_direct_replacement_selection",
            "phase": "awaiting_selection"
        }
    except Exception as e:
        logger.error(f"Error generating direct replacement: {e}")
        return {
            **state,
            "bot_response": "I couldn't find a specific match. Let me find some alternatives instead.",
            "workflow_stage": "generating_alternates", # Fallback
            "phase": "processing" # Will be re-routed if we didn't end
        }


async def handle_cancel_action(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Handle user cancellation with personalized acknowledgment."""
    
    user_message = state.get("user_message", "")
    formatted_context = state.get("formatted_context", "")
    action_items = state.get("action_items", [])
    
    # Get what they might have been changing
    targeted_idx = state.get("targeted_action_index")
    action_title = "your plan"
    if targeted_idx is not None and 0 <= targeted_idx < len(action_items):
        action_title = action_items[targeted_idx].get("title", "that action")
    
    cancel_prompt = f"""Generate a warm acknowledgment for user canceling a change request.

User Context:
{formatted_context[:500] if formatted_context else "User in care plan chat"}

Details:
- They were considering changing: {action_title}
- They decided to cancel/keep as is

Guidelines:
1. Acknowledge their decision warmly
2. Reassure them they can change later
3. Briefly mention why the current item might be good (if context available)
4. Keep it 1-2 sentences
5. End positively
"""
    
    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            cancel_prompt,
            temperature=config.celebration_temperature,
            max_tokens=config.max_tokens_cancel,
            intent="cancel_action"
        )
        if not response or len(response.strip()) < 10:
            response = f"No problem! We'll keep {action_title} as is. You can always adjust things later if you need to."
    except:
        response = f"No problem! We'll keep {action_title} as is. You can always adjust things later if you need to."
    
    return {
        **state,
        "bot_response": response,
        "workflow_stage": None,  # Clear active workflow
        "ui_blocks": [],
        "phase": "complete"
    }


async def handle_ask_why(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Handle user asking 'why' about an action with detailed, personalized explanation."""
    
    targeted_idx = state.get("targeted_action_index")
    action_items = state.get("action_items", [])
    formatted_context = state.get("formatted_context", "")
    user_message = state.get("user_message", "")
    
    if targeted_idx is None or not (0 <= targeted_idx < len(action_items)):
        # Ask which action they want to know about
        actions_list = "\n".join([f"{i+1}. {item.get('title', 'Action')}" for i, item in enumerate(action_items[:4])])
        return {
            **state,
            "bot_response": f"I'd love to explain! Which action are you curious about?\n{actions_list}",
            "ui_blocks": [],
            "phase": "complete"
        }

    action = action_items[targeted_idx]
    title = action.get('title', 'this action')
    category = action.get('category', 'action')
    hormone = action.get('target_hormone', 'hormonal health')
    purpose = action.get('purpose', '')
    why_it_works = action.get('why_it_works', action.get('evidence_summary', ''))
    citations = action.get('citations', [])
    
    why_prompt = f"""Explain WHY this specific action was chosen for THIS specific user.

User Context:
{formatted_context if formatted_context else "User asking about their action plan"}

Action Details:
- Title: {title}
- Category: {category}
- Target Hormone: {hormone}
- Purpose: {purpose}
- Scientific Rationale: {why_it_works}
- Research Citations: {citations[:2] if citations else "evidence-based recommendation"}

User asked: "{user_message}"

Guidelines:
1. Explain why THIS action for THIS user specifically
2. Reference their conditions/symptoms if relevant
3. Connect to their current cycle phase
4. Include 1-2 specific benefits backed by the science
5. Keep it conversational and educational (3-4 sentences)
6. End by asking if they want alternatives or have more questions

Do NOT be generic - make them understand why this is FOR THEM.
"""
    
    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            why_prompt,
            temperature=config.explanation_temperature,
            max_tokens=config.max_tokens_why,
            intent="ask_why"
        )
        if not response or len(response.strip()) < 20:
            response = f"{title} was chosen specifically for your {hormone} support during this phase. {purpose} Would you like more details or prefer an alternative?"
    except Exception as e:
        logger.warning(f"LLM failed for ask_why: {e}")
        response = f"{title} was chosen specifically for your {hormone} support during this phase. {purpose} Would you like more details or prefer an alternative?"
    
    ui_blocks = [
        {
            "type": "quick_replies",
            "replies": [
                {"label": "Tell me more", "value": f"Tell me more about why {title} helps"},
                {"label": "Show alternatives", "value": f"Show me alternatives to {title}"},
                {"label": "Keep it", "value": "That makes sense, keep it"}
            ]
        }
    ]

    return {
        **state,
        "bot_response": response,
        "ui_blocks": ui_blocks,
        "phase": "complete"
    }


async def handle_request_clarification(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Handle user asking HOW/WHEN to do an action (timing, instructions, details)."""
    
    targeted_idx = state.get("targeted_action_index")
    action_items = state.get("action_items", [])
    formatted_context = state.get("formatted_context", "")
    user_message = state.get("user_message", "")
    
    if targeted_idx is None or not (0 <= targeted_idx < len(action_items)):
        # Ask which action they want clarification about
        actions_list = "\n".join([f"{i+1}. {item.get('title', 'Action')}" for i, item in enumerate(action_items[:4])])
        return {
            **state,
            "bot_response": f"I'd love to give you more details! Which action do you want to know more about?\n{actions_list}",
            "ui_blocks": [],
            "phase": "complete"
        }

    action = action_items[targeted_idx]
    title = action.get('title', 'this action')
    specific_action = action.get('specific_action', '')
    category = action.get('category', 'action')
    
    # Extract relevant details based on category
    details = {}
    if category == 'nutrition':
        details['food_items'] = action.get('food_items', [])
        details['food_amounts'] = action.get('food_amounts', [])
    elif category == 'movement':
        details['exercise_types'] = action.get('exercise_types', [])
        details['exercise_durations'] = action.get('exercise_durations', [])
        details['exercise_intensities'] = action.get('exercise_intensities', [])
    elif category == 'mindfulness':
        details['mindfulness_techniques'] = action.get('mindfulness_techniques', [])
        details['mindfulness_durations'] = action.get('mindfulness_durations', [])
    
    clarification_prompt = f"""Provide clear, practical instructions for HOW/WHEN to do this action.

User Context:
{formatted_context[:500] if formatted_context else "User in care plan check-in"}

Action Details:
- Title: {title}
- Specific Action: {specific_action}
- Category: {category}
- Details: {json.dumps(details, indent=2)}

User's Question: "{user_message}"

Guidelines:
1. Answer their SPECIFIC question (timing, duration, how-to, etc.)
2. Be practical and actionable - give clear instructions
3. If it's a timing question (when to take), suggest optimal timing based on the action
4. If it's a how-to question, give step-by-step guidance
5. Keep it 2-4 sentences, clear and helpful
6. Reference their specific context if relevant
7. End by asking if they need more help or are ready to do it

Examples:
- Q: "When to take cinnamon?" → A: "Take cinnamon with your breakfast! It helps regulate blood sugar when consumed with your morning meal. You can sprinkle it on oatmeal, add to coffee, or mix into yogurt. Ready to try it?"
- Q: "How long should I do yoga?" → A: "Aim for 15-20 minutes of gentle yoga. Even 10 minutes will help with stress relief. Would you like a specific routine suggestion?"
- Q: "As breakfast or after meal?" → A: "With breakfast is best! Taking it with food helps with absorption and prevents any stomach sensitivity. Sound good?"
"""
    
    try:
        config = get_llm_config()
        response = await call_llm_with_cache(
            call_llm,
            clarification_prompt,
            temperature=config.explanation_temperature,
            max_tokens=config.max_tokens_clarification,
            intent="request_clarification"
        )
        if not response or len(response.strip()) < 20:
            response = f"For {title}, I recommend {specific_action}. This should take about 15-20 minutes. Would you like more specific guidance?"
    except Exception as e:
        logger.warning(f"LLM failed for request_clarification: {e}")
        response = f"For {title}, I recommend {specific_action}. This should take about 15-20 minutes. Would you like more specific guidance?"
    
    # Add helpful follow-up options (was missing — every other handler has these)
    ui_blocks = [
        {
            "type": "quick_replies",
            "replies": [
                {"label": "Got it, I'll do it!", "value": f"I completed {title}"},
                {"label": "Tell me more", "value": f"Tell me more about how to do {title}"},
                {"label": "Show alternatives", "value": f"Show me alternatives to {title}"}
            ]
        }
    ]
    
    return {
        **state,
        "bot_response": response,
        "ui_blocks": ui_blocks,
        "phase": "complete"
    }


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - LOAD PLAN (Invocation 1)
# ═══════════════════════════════════════════════════════════════════

def create_load_plan_graph():
    """Graph for LOADING daily plan (first invocation)."""
    
    workflow = StateGraph(CarePlanCheckInState)
    
    workflow.add_node("load_daily_plan_and_tokens", load_daily_plan_and_tokens)
    
    workflow.set_entry_point("load_daily_plan_and_tokens")
    workflow.add_edge("load_daily_plan_and_tokens", END)

    # Return the uncompiled graph; we compile once below with a checkpointer
    # so interrupts/resume work consistently across invocations.
    return workflow


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - PROCESS MESSAGE (Invocation 2+)
# ═══════════════════════════════════════════════════════════════════

def create_process_message_graph():
    """Graph for processing user messages about care plan."""
    
    workflow = StateGraph(CarePlanCheckInState)
    
    # Add all nodes
    workflow.add_node("pre_model_hook_trim_messages", pre_model_hook_trim_messages)
    workflow.add_node("classify_user_intent", classify_user_intent)
    workflow.add_node("handle_complete_action", handle_complete_action)
    workflow.add_node("handle_skip_action", handle_skip_action)
    workflow.add_node("handle_change_action", handle_change_action)
    workflow.add_node("generate_alternate_suggestions", generate_alternate_suggestions)
    workflow.add_node("generate_direct_replacement_suggestion", generate_direct_replacement_suggestion)
    workflow.add_node("handle_general_response", handle_general_response)
    workflow.add_node("handle_cancel_action", handle_cancel_action)
    workflow.add_node("handle_ask_why", handle_ask_why)
    workflow.add_node("handle_request_clarification", handle_request_clarification)  # NEW: Handle timing/how-to questions
    # Feedback handling nodes
    workflow.add_node("handle_plan_feedback", handle_plan_feedback)
    workflow.add_node("handle_challenge_science", handle_challenge_science)
    workflow.add_node("handle_explain_plan", handle_explain_plan)
    # NEW: Health question handler for cross-chatbot functionality
    workflow.add_node("handle_health_question", handle_health_question)
    workflow.add_node("handle_show_streak", handle_show_streak)
    workflow.add_node("post_model_hook_guardrail", post_model_hook_guardrail)
    
    # Set entry point
    workflow.set_entry_point("pre_model_hook_trim_messages")
    workflow.add_edge("pre_model_hook_trim_messages", "classify_user_intent")
    
    # All handlers pass through post-model guardrails before END.
    workflow.add_edge("handle_complete_action", "post_model_hook_guardrail")
    workflow.add_edge("handle_skip_action", "post_model_hook_guardrail")
    workflow.add_edge("handle_general_response", "post_model_hook_guardrail")
    workflow.add_edge("handle_cancel_action", "post_model_hook_guardrail")
    workflow.add_edge("handle_ask_why", "post_model_hook_guardrail")
    workflow.add_edge("handle_request_clarification", "post_model_hook_guardrail")
    workflow.add_edge("generate_alternate_suggestions", "post_model_hook_guardrail")
    workflow.add_edge("generate_direct_replacement_suggestion", "post_model_hook_guardrail")
    workflow.add_edge("handle_plan_feedback", "post_model_hook_guardrail")
    workflow.add_edge("handle_challenge_science", "post_model_hook_guardrail")
    workflow.add_edge("handle_explain_plan", "post_model_hook_guardrail")
    workflow.add_edge("handle_health_question", "post_model_hook_guardrail")
    workflow.add_edge("handle_show_streak", "post_model_hook_guardrail")
    workflow.add_edge("post_model_hook_guardrail", END)
    
    # Change action → alternates
    workflow.add_conditional_edges(
        "handle_change_action",
        route_after_change,
        {
            "generate_alternate_suggestions": "generate_alternate_suggestions",
            "generate_direct_replacement_suggestion": "generate_direct_replacement_suggestion",
            "END": "post_model_hook_guardrail",
        }
    )

    # Return the uncompiled graph; we compile once below with a checkpointer.
    return workflow


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - PROCESS SELECTION (Invocation 3)
# ═══════════════════════════════════════════════════════════════════

def create_process_selection_graph():
    """Graph for processing alternate selection with token check."""
    
    workflow = StateGraph(CarePlanCheckInState)
    
    workflow.add_node("check_refresh_tokens_and_replace", check_refresh_tokens_and_replace)
    workflow.add_node("post_model_hook_guardrail", post_model_hook_guardrail)
    
    workflow.set_entry_point("check_refresh_tokens_and_replace")
    workflow.add_edge("check_refresh_tokens_and_replace", "post_model_hook_guardrail")
    workflow.add_edge("post_model_hook_guardrail", END)

    # Return the uncompiled graph; we compile once below with a checkpointer.
    return workflow


# ═══════════════════════════════════════════════════════════════════════════
# Async graph runtime and checkpointer bootstrap
# ═══════════════════════════════════════════════════════════════════════════

_graph_runtime_lock = asyncio.Lock()
_graph_runtime: Optional[Dict[str, Any]] = None


async def _run_checkpointer_setup(checkpointer: Any) -> None:
    setup = getattr(checkpointer, "setup", None)
    if callable(setup):
        maybe_awaitable = setup()
        if asyncio.iscoroutine(maybe_awaitable):
            await maybe_awaitable


async def _create_async_checkpointer():
    """Create an async-compatible checkpointer for ainvoke/astream flows."""
    postgres_dsn = os.getenv("LANGGRAPH_CHECKPOINT_POSTGRES_DSN")
    if postgres_dsn:
        if AsyncPostgresSaver is not None:
            saver_cm = AsyncPostgresSaver.from_conn_string(postgres_dsn)
            saver = await saver_cm.__aenter__()
            await _run_checkpointer_setup(saver)
            logger.info("Care plan checkpointer configured: AsyncPostgresSaver")
            return saver, saver_cm

        if PostgresSaver is not None:
            logger.error(
                "PostgresSaver is sync-only for this runtime path. "
                "Install/use AsyncPostgresSaver for async ainvoke/astream."
            )
        else:
            logger.error(
                "LANGGRAPH_CHECKPOINT_POSTGRES_DSN is set but Postgres checkpoint package is unavailable. "
                "Install langgraph-checkpoint-postgres."
            )

    sqlite_path = os.getenv(
        "LANGGRAPH_CHECKPOINT_SQLITE_PATH",
        os.path.join(os.getcwd(), ".langgraph", "checkpoints", "care_plan_checkin.sqlite"),
    )
    sqlite_dir = os.path.dirname(sqlite_path)
    if sqlite_dir:
        os.makedirs(sqlite_dir, exist_ok=True)

    if AsyncSqliteSaver is not None:
        saver_cm = AsyncSqliteSaver.from_conn_string(sqlite_path)
        saver = await saver_cm.__aenter__()
        await _run_checkpointer_setup(saver)
        logger.info(f"Care plan checkpointer configured: AsyncSqliteSaver ({sqlite_path})")
        return saver, saver_cm

    if SqliteSaver is not None:
        logger.error(
            "SqliteSaver is sync-only and incompatible with async ainvoke/astream. "
            "Install/use AsyncSqliteSaver (requires aiosqlite)."
        )
    else:
        logger.warning(
            "No SQLite checkpointer implementation available. "
            "Compiling care-plan graphs without a checkpointer."
        )

    return None, None


async def _get_graph_runtime() -> Dict[str, Any]:
    global _graph_runtime
    if _graph_runtime is not None:
        return _graph_runtime

    async with _graph_runtime_lock:
        if _graph_runtime is not None:
            return _graph_runtime

        checkpointer, checkpointer_cm = await _create_async_checkpointer()
        if checkpointer is None:
            load_graph = create_load_plan_graph().compile()
            process_graph = create_process_message_graph().compile()
            selection_graph = create_process_selection_graph().compile()
        else:
            load_graph = create_load_plan_graph().compile(checkpointer=checkpointer)
            process_graph = create_process_message_graph().compile(checkpointer=checkpointer)
            selection_graph = create_process_selection_graph().compile(checkpointer=checkpointer)

        _graph_runtime = {
            "checkpointer": checkpointer,
            "checkpointer_cm": checkpointer_cm,
            "load_graph": load_graph,
            "process_graph": process_graph,
            "selection_graph": selection_graph,
        }
        return _graph_runtime


async def close_care_plan_graph_runtime() -> None:
    """Close async checkpointer context on app shutdown if wired by caller."""
    global _graph_runtime
    if _graph_runtime is None:
        return

    runtime = _graph_runtime
    _graph_runtime = None
    checkpointer_cm = runtime.get("checkpointer_cm")
    if checkpointer_cm is not None and hasattr(checkpointer_cm, "__aexit__"):
        await checkpointer_cm.__aexit__(None, None, None)


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════

async def load_care_plan(user_id: str, thread_id: Optional[str] = None) -> CarePlanCheckInState:
    """Load today's care plan."""
    runtime = await _get_graph_runtime()
    state = create_initial_state(user_id)
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    load_graph = runtime["load_graph"]
    result = await load_graph.ainvoke(state, config=config) if config else await load_graph.ainvoke(state)
    return result


async def process_care_plan_message(
    state: CarePlanCheckInState,
    user_message: str,
    thread_id: Optional[str] = None
) -> CarePlanCheckInState:
    """Process user message about care plan."""
    runtime = await _get_graph_runtime()
    updated_state = {**state, "user_message": user_message}
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    process_graph = runtime["process_graph"]
    result = await process_graph.ainvoke(updated_state, config=config) if config else await process_graph.ainvoke(updated_state)
    return result


async def stream_care_plan_message(
    state: CarePlanCheckInState,
    user_message: str,
    thread_id: Optional[str] = None,
    stream_mode: str = STREAM_MODE_TEXT,
):
    """Streaming helper: use stream_mode='messages' for text or stream_mode='custom' for UI elements."""
    runtime = await _get_graph_runtime()
    updated_state = {**state, "user_message": user_message}
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    process_graph = runtime["process_graph"]
    if config:
        async for chunk in process_graph.astream(updated_state, config=config, stream_mode=stream_mode):
            yield chunk
    else:
        async for chunk in process_graph.astream(updated_state, stream_mode=stream_mode):
            yield chunk


async def process_alternate_selection(
    state: CarePlanCheckInState,
    selected_index: Optional[int] = None,
    thread_id: Optional[str] = None,
    resume: Optional[bool] = None
) -> CarePlanCheckInState:
    """Process alternate selection and check tokens."""
    runtime = await _get_graph_runtime()
    updated_state = {**state, "selected_alternate_index": selected_index}
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    selection_graph = runtime["selection_graph"]
    if resume is not None:
        result = await selection_graph.ainvoke(Command(resume=resume), config=config) if config else await selection_graph.ainvoke(Command(resume=resume))
    else:
        result = await selection_graph.ainvoke(updated_state, config=config) if config else await selection_graph.ainvoke(updated_state)
    return result


async def stream_alternate_selection(
    state: CarePlanCheckInState,
    selected_index: Optional[int] = None,
    thread_id: Optional[str] = None,
    stream_mode: str = STREAM_MODE_UI,
):
    """Streaming helper for alternate-selection UI events (default stream_mode='custom')."""
    runtime = await _get_graph_runtime()
    updated_state = {**state, "selected_alternate_index": selected_index}
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    selection_graph = runtime["selection_graph"]
    if config:
        async for chunk in selection_graph.astream(updated_state, config=config, stream_mode=stream_mode):
            yield chunk
    else:
        async for chunk in selection_graph.astream(updated_state, stream_mode=stream_mode):
            yield chunk
