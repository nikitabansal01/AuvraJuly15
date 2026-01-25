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
- Skip action with streak warning (ANY skip risks streak)
- Multi-stage workflows (alternate suggestions)
- UI Blocks integration
- Cross-chatbot context awareness
"""

from __future__ import annotations

from typing import TypedDict, List, Dict, Any, Literal, Optional
from datetime import date, datetime
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command
from pydantic import BaseModel
import uuid
import json
import logging

from app.langgraph.helpers.llm_client import call_llm, call_llm_structured
from app.langgraph.helpers.database_helpers import (
    get_cycle_info, get_todays_action_plan, get_streak_info, get_reward_status
)
from app.langgraph.helpers.ui_blocks_helper import (
    generate_intelligent_ctas, create_confirmation_block, 
    create_alternates_selection_block, clear_ui_blocks, create_action_selection_block
)
from app.core.database import get_db, ActionPlanItem, ActionPlanRefreshLog, SessionLocal
# NEW: Unified memory for cross-chatbot context
from app.langgraph.memory import get_unified_context, format_context_for_prompt

logger = logging.getLogger(__name__)


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
    
    # User context
    cycle_day: Optional[int]
    cycle_phase: Optional[str]
    primary_hormone: Optional[str]
    current_streak: int
    
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
    
    # UI Elements
    ui_blocks: List[Dict[str, Any]]
    
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
        
        cycle_day=None,
        cycle_phase=None,
        primary_hormone=None,
        current_streak=0,
        
        refresh_tokens_available=0,
        refresh_tokens_unlocked=False,
        
        messages=[],
        current_intent=None,
        user_message=None,
        
        targeted_action_id=None,
        targeted_action_index=None,
        change_reason=None,
        barrier_type=None,
        
        workflow_stage=None,
        alternate_candidates=[],
        selected_alternate_index=None,
        selected_alternate=None,
        
        ui_blocks=[],
        
        bot_response="",
        actions_to_execute=[],
        
        phase="init",
        error=None
    )


# ═══════════════════════════════════════════════════════════════════
# NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

async def load_daily_plan_and_tokens(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Load today's plan AND refresh token status AND unified cross-chatbot context."""
    try:
        db = next(get_db())
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
            "cycle_day": cycle_info.get("cycle_day"),
            "cycle_phase": cycle_info.get("phase"),
            "primary_hormone": cycle_info.get("primary_hormone"),
            "current_streak": current_streak,
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


async def classify_user_intent(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Use LLM to classify intent (replaces hardcoded string matching)."""
    
    user_message = state.get("user_message", "")
    if not user_message:
        return {**state, "error": "no_user_message", "phase": "complete"}
    
    # Build action list for context
    messages = state.get("messages", [])
    recent_msgs = messages[-6:]
    chat_context = "\n".join([f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in recent_msgs])

    # Build action list for context
    actions_list = "\n".join([
        f"{i+1}. {item.get('title', 'Unknown')} ({item.get('category', 'general')})"
        for i, item in enumerate(state.get("action_items", []))
    ])

    prompt = f"""Classify the user's intent for this care plan check-in message.

User Message: "{user_message}"

Recent Chat History:
{chat_context}

Today's Action Items:
{actions_list}

Intent Categories:
1. **complete_action**: User completed an action ("Done!", "✓", "I ate the walnuts")
2. **skip_action**: User skipping ("Skip yoga", "Can't do this", "Not today")
3. **change_action**: User wants to replace a SPECIFIC action ("Change the salmon", "I want something else for action 2")
4. **request_alternates**: Asking for options for a specific action ("Show me alternatives for the walnuts")
5. **negotiate**: Conditional/barriers for specific action ("If I can't find X, what else?", "This is too hard")
6. **ask_why**: Asking rationale for ONE specific action ("Why walnuts?", "What does salmon help?")
7. **cancel_action**: User wants to stop changing/cancel request ("Never mind", "Cancel", "Go back", "Keep as is")
8. **plan_feedback**: User is giving FEEDBACK about the OVERALL PLAN quality, NOT asking about a specific action!
   Examples: "This looks generic", "Not personalized", "Seems random", "Doesn't match my symptoms", "Same as yesterday"
9. **challenge_science**: User is questioning the RESEARCH or SCIENCE behind recommendations
   Examples: "Is this backed by research?", "Have you done thorough research?", "Just random suggestions?"
10. **explain_plan**: User wants to understand HOW the whole plan was created
   Examples: "How did you make this?", "Why these specific items?", "What's the logic?"
11. **general**: General chat or unclear

⚠️ CRITICAL DISTINCTION:
- If user says "Why walnuts?" → ask_why (about ONE action)
- If user says "Why these items?" or "My plan looks generic" → plan_feedback (about WHOLE plan)
- If user says "All four of them" or "The whole plan" → This is about the ENTIRE plan, NOT a specific action!

Rules for targeted_action_index:
- ONLY set this if user clearly refers to ONE specific action by name/number
- If user says "all of them", "the whole plan", "my actions", etc. → set to null
- If user is giving general feedback about the plan → set to null

Rules for feedback_topic (only for plan_feedback/challenge_science intents):
- "generic": Plan feels like generic wellness, not personalized
- "repetitive": Same items appearing repeatedly
- "not_personalized": Doesn't match user's specific conditions/symptoms
- "science_question": Questioning the research behind recommendations
- "other": Other feedback

Output JSON:
{{
  "intent": "complete_action|skip_action|change_action|request_alternates|negotiate|ask_why|cancel_action|plan_feedback|challenge_science|explain_plan|general",
  "targeted_action_index": 1-4 or null,
  "proposed_replacement": "string" or null,
  "confidence": 0.0-1.0,
  "feedback_topic": "generic|repetitive|not_personalized|science_question|other" or null
}}
"""
    
    try:
        classification = await call_llm_structured(prompt, response_model=IntentClassification)
        
        # Map action index to actual ID (validate bounds)
        targeted_id = state.get("targeted_action_id")      # Default to existing
        targeted_idx = state.get("targeted_action_index")  # Default to existing

        if classification.targeted_action_index:
            idx = classification.targeted_action_index - 1  # Convert to 0-based
            if 0 <= idx < len(state.get("action_items", [])):
                targeted_idx = idx
                targeted_id = state["action_items"][idx].get("id") or state["action_items"][idx].get("item_id")

        return {
            **state,
            "current_intent": classification.intent,
            "targeted_action_id": targeted_id,
            "targeted_action_index": targeted_idx,
            "change_reason": classification.proposed_replacement or state.get("change_reason"), # Store as reason if present
            "feedback_topic": classification.feedback_topic,  # NEW: Pass feedback topic for feedback handlers
            "messages": state.get("messages", []) + [{"role": "user", "content": user_message}],
            "phase": "processing"
        }
    except Exception as e:
        logger.error(f"Error classifying intent: {e}")
        return {
            **state,
            "current_intent": "general",
            "error": str(e),
            "phase": "processing"
        }


async def handle_complete_action(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Mark action complete, update streak."""
    try:
        db = next(get_db())
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
        db.commit()
        
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

Guidelines:
1. Use the hormone buddy voice (e.g., "I'm {hormone}!")
2. Reference the specific action they completed
3. If streak milestone (7, 14, 21, 30 days), celebrate extra
4. If completed all 4, be EXTRA enthusiastic
5. Keep it 2-3 sentences, warm and energetic
6. Use an emoji that matches the hormone ({hormone})
7. Vary the celebration style - don't always use same format

Example variations (DON'T copy, just show variety):
- Streak focus: "Your {current}-day streak is glowing!"
- Action focus: "Those {action_title} are already working their magic!"
- Momentum focus: "Just {remaining} more and you've nailed the day!"
"""
        
        try:
            response = await call_llm(celebration_prompt, max_tokens=150)
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
            "actions_to_execute": [{"type": "refresh_plan"}],
            "ui_blocks": [], # Clear any previous buttons
            "phase": "complete"
        }
    except Exception as e:
        logger.error(f"Error completing action: {e}")
        return {**state, "bot_response": f"Error: {e}", "error": str(e), "phase": "complete"}


async def handle_skip_action(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Process skip with CRITICAL streak warning - ANY skip risks streak. Uses LLM for personalized response."""
    
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
    
    skip_prompt = f"""Generate an empathetic but clear warning about skipping an action.

User Context:
{formatted_context[:1000] if formatted_context else "User with active streak"}

Details:
- Action to skip: {action_title} ({action_category})
- User's reason: {skip_reason}
- Skip category: {skip_category}
- Current streak: {current_streak} days

Guidelines:
1. Acknowledge their reason empathetically FIRST
2. Explain streak risk clearly (skipping ANY action affects streak)
3. Offer alternatives based on their reason:
   - no_time: Suggest quicker version
   - dont_like: Suggest swap
   - not_feeling_well: Suggest gentler option
   - no_ingredients: Suggest substitution
4. End with a question offering alternatives
5. Keep it 2-3 sentences, warm but honest
6. DON'T be preachy or guilt-tripping

Example tone (don't copy exactly):
"I totally get it - [action] isn't feeling right today. Just a heads up: skipping will affect your [X]-day streak. Want me to find something easier/quicker that works better for you?"
"""
    
    try:
        response = await call_llm(skip_prompt, max_tokens=150)
        if not response or len(response.strip()) < 20:
            response = f"Heads up: skipping {action_title} will put your {current_streak}-day streak at risk. Want a quicker or easier alternative instead?"
    except Exception as llm_error:
        logger.warning(f"Skip warning LLM failed: {llm_error}")
        response = f"Heads up: skipping {action_title} will put your {current_streak}-day streak at risk. Want a quicker or easier alternative instead?"
    
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
        return {**state, "bot_response": response, "phase": "complete"}


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
            response = "Looks like you've already completed all your actions for today. 🎉"
            return {
                **state,
                "bot_response": response,
                "ui_blocks": [],
                "phase": "complete"
            }

        current_intent = state.get("current_intent") or "change_action"
        is_alternates = current_intent in {"request_alternates", "negotiate"}
        response = "Which action would you like alternates for?" if is_alternates else "Which action would you like to change?"
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

    # EARLY CHECK: Do they have tokens?
    if state.get("refresh_tokens_available", 0) <= 0:
        current_streak = state.get("current_streak", 0)
        return {
            **state,
            "bot_response": f"I'd love to help you adjust your plan, but you need more refresh credits! Reach a 16-day streak to unlock 2 daily refreshes. (Current streak: {current_streak})",
            "phase": "complete"
        }
    
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

        return {
            **state,
            "barrier_type": barrier_data.barrier_type,
            "change_reason": barrier_data.requested_item or barrier_data.specific_barrier,
            "targeted_action_id": targeted_action_id,
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
    """Generate 3 alternatives - token check happens at SELECTION."""
    
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
                n=3,
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

        alternates = result.get("actions", [])

        ui_block = create_alternates_selection_block(
            alternates=[{"title": alt.get("title"), "specific_action": alt.get("specific_action"), "why_better": alt.get("purpose") or ""} for alt in alternates],
            title=f"3 Alternatives for {action.get('title', 'action')}"
        )

        return {
            **state,
            "alternate_candidates": alternates,
            "ui_blocks": [ui_block],
            "workflow_stage": "awaiting_alternate_selection",
            "bot_response": "Here are 3 options that might work better:",
            "phase": "awaiting_selection"
        }
    except Exception as e:
        logger.error(f"Error generating alternates: {e}")
        return {
            **state,
            "bot_response": "I couldn't generate alternatives. Please try again.",
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
            "bot_response": "Invalid selection. Please choose again.",
            "error": "invalid_selection",
            "phase": "awaiting_selection"
        }
    
    # Check if user has tokens
    if state.get("refresh_tokens_available", 0) <= 0:
        logger.info(f"[GRAPH_NODE] No refresh tokens available")
        current_streak = state.get("current_streak", 0)
        response = (
            f"Oops! You need to complete a {16 - current_streak if current_streak < 16 else 0}-day streak "
            f"to unlock 'Refresh Action' credits. You currently have {current_streak} days."
        )
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
                    f"Perfect! I've replaced {original.title} with {replacement_title}. "
                    "Your refresh count will sync shortly."
                )
            else:
                tokens_remaining = refresh_result.get("remaining", 0)
                response = f"Perfect! I've replaced {original.title} with {replacement_title}. You have {tokens_remaining} refresh(es) left today."

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
        from app.services.llm_service import call_llm
        response = await call_llm(prompt, max_tokens=300)
        
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
        from app.services.llm_service import call_llm
        response = await call_llm(prompt, max_tokens=400)
        
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
        from app.services.llm_service import call_llm
        response = await call_llm(prompt, max_tokens=350)
        
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


async def _get_user_context_for_explanation(user_id: int) -> dict:
    """Fetch user context to explain plan personalization."""
    try:
        from app.database import SessionLocal
        from app.models.user import User
        from app.models.response import Response
        
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            
            # Get user's period concerns from responses
            concerns_response = db.query(Response).filter(
                Response.user_id == user_id,
                Response.question_id == 5  # Period concerns question
            ).first()
            
            # Get diagnosed conditions
            conditions_response = db.query(Response).filter(
                Response.user_id == user_id,
                Response.question_id == 18  # Diagnosed conditions question
            ).first()
            
            return {
                "cycle_phase": getattr(user, 'current_cycle_phase', 'follicular') if user else 'unknown',
                "concerns": concerns_response.answer_text if concerns_response else 'hormone balance',
                "conditions": conditions_response.answer_text if conditions_response else 'none specified'
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


async def handle_general_response(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Handle general/unclear intents."""
    
    user_message = state.get("user_message", "")
    action_items = state.get("action_items", [])

    # Check if user is asking an informational question - NO UI blocks for these
    msg_lower = user_message.lower()
    is_question = any(word in msg_lower for word in ["what", "which", "how many", "remaining", "left", "show", "tell me"])
    
    if is_question:
        incomplete = [item for item in action_items if not item.get("is_completed")]
        if "remaining" in msg_lower or "left" in msg_lower or "not completed" in msg_lower:
            if not incomplete:
                response = "Great work! You've completed all your actions for today. 🎉"
            else:
                titles = ", ".join([item.get("title", "action") for item in incomplete])
                response = f"You have {len(incomplete)} action(s) remaining: {titles}."
        else:
            actions_list = ", ".join([item.get("title", "action") for item in action_items[:4]])
            completed_count = len([item for item in action_items if item.get("is_completed")])
            response = f"Today's plan: {actions_list}. You've completed {completed_count}/{len(action_items)} so far."
        
        return {
            **state,
            "bot_response": response,
            "ui_blocks": [],
            "phase": "complete"
        }
    
    # For unclear intents, offer help
    actions_list = ", ".join([item.get("title", "action") for item in action_items[:4]])
    response = f"Got it. Want to adjust anything in today's plan? Your actions are: {actions_list}. You can mark one done, ask for a swap, or ask why it's here."
    ui_blocks = await _maybe_add_ctas(state, response, user_message)
    
    return {
        **state,
        "bot_response": response,
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
        "cancel_action": "handle_cancel_action",
        "plan_feedback": "handle_plan_feedback",  # NEW: Overall plan quality feedback
        "challenge_science": "handle_challenge_science",  # NEW: Questioning research
        "explain_plan": "handle_explain_plan",  # NEW: How was plan created
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
    """Handle user cancellation."""
    return {
        **state,
        "bot_response": "No problem! We'll keep things exactly as they are. You can always make changes later if you need to.",
        "ui_blocks": [], # Clear any previous buttons
        "phase": "complete"
    }


async def handle_ask_why(state: CarePlanCheckInState) -> CarePlanCheckInState:
    """Handle user asking 'why' about an action."""
    
    targeted_idx = state.get("targeted_action_index")
    action_items = state.get("action_items", [])
    
    if targeted_idx is None or not (0 <= targeted_idx < len(action_items)):
        return {
            **state,
            "bot_response": "Which action are you asking about?",
            "ui_blocks": [],
            "phase": "complete"
        }

    action = action_items[targeted_idx]
    
    # Use existing purpose/hormone metadata for explanation
    hormone = action.get('target_hormone', 'your health')
    purpose = action.get('purpose', 'support your goals')
    title = action.get('title', 'this action')
    
    explanation = f"{title} was chosen to support your {hormone}. {purpose}"
    response = f"{explanation} Want a simpler alternative or keep it as is?"
    ui_blocks = await _maybe_add_ctas(state, response, state.get("user_message"))

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
    workflow.add_node("classify_user_intent", classify_user_intent)
    workflow.add_node("handle_complete_action", handle_complete_action)
    workflow.add_node("handle_skip_action", handle_skip_action)
    workflow.add_node("handle_change_action", handle_change_action)
    workflow.add_node("generate_alternate_suggestions", generate_alternate_suggestions)
    workflow.add_node("generate_direct_replacement_suggestion", generate_direct_replacement_suggestion)
    workflow.add_node("handle_general_response", handle_general_response)
    workflow.add_node("handle_cancel_action", handle_cancel_action)
    workflow.add_node("handle_ask_why", handle_ask_why)
    # NEW: Feedback handling nodes
    workflow.add_node("handle_plan_feedback", handle_plan_feedback)
    workflow.add_node("handle_challenge_science", handle_challenge_science)
    workflow.add_node("handle_explain_plan", handle_explain_plan)
    
    # Set entry point
    workflow.set_entry_point("classify_user_intent")
    
    # Intent routing
    workflow.add_conditional_edges(
        "classify_user_intent",
        route_by_intent,
        {
            "handle_complete_action": "handle_complete_action",
            "handle_skip_action": "handle_skip_action",
            "handle_change_action": "handle_change_action",
            "generate_alternate_suggestions": "generate_alternate_suggestions",
            "handle_general_response": "handle_general_response",
            "handle_cancel_action": "handle_cancel_action",
            "handle_ask_why": "handle_ask_why",
            # NEW: Feedback routes
            "handle_plan_feedback": "handle_plan_feedback",
            "handle_challenge_science": "handle_challenge_science",
            "handle_explain_plan": "handle_explain_plan"
        }
    )
    
    # All handlers go to END
    workflow.add_edge("handle_complete_action", END)
    workflow.add_edge("handle_skip_action", END)
    workflow.add_edge("handle_general_response", END)
    workflow.add_edge("handle_cancel_action", END)
    workflow.add_edge("handle_ask_why", END)
    workflow.add_edge("generate_alternate_suggestions", END)
    workflow.add_edge("generate_direct_replacement_suggestion", END)
    # NEW: Feedback handlers go to END
    workflow.add_edge("handle_plan_feedback", END)
    workflow.add_edge("handle_challenge_science", END)
    workflow.add_edge("handle_explain_plan", END)
    
    # Change action → alternates
    workflow.add_conditional_edges(
        "handle_change_action",
        route_after_change,
        {
            "generate_alternate_suggestions": "generate_alternate_suggestions",
            "generate_direct_replacement_suggestion": "generate_direct_replacement_suggestion",
            "END": END
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
    
    workflow.set_entry_point("check_refresh_tokens_and_replace")
    workflow.add_edge("check_refresh_tokens_and_replace", END)

    # Return the uncompiled graph; we compile once below with a checkpointer.
    return workflow


# Compile graphs with checkpointer for interrupts and resumable state
_checkpointer = InMemorySaver()
care_plan_load_graph = create_load_plan_graph().compile(checkpointer=_checkpointer)
care_plan_process_graph = create_process_message_graph().compile(checkpointer=_checkpointer)
care_plan_selection_graph = create_process_selection_graph().compile(checkpointer=_checkpointer)


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════

async def load_care_plan(user_id: str, thread_id: Optional[str] = None) -> CarePlanCheckInState:
    """Load today's care plan."""
    state = create_initial_state(user_id)
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    result = await care_plan_load_graph.ainvoke(state, config=config) if config else await care_plan_load_graph.ainvoke(state)
    return result


async def process_care_plan_message(
    state: CarePlanCheckInState,
    user_message: str,
    thread_id: Optional[str] = None
) -> CarePlanCheckInState:
    """Process user message about care plan."""
    updated_state = {**state, "user_message": user_message}
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    result = await care_plan_process_graph.ainvoke(updated_state, config=config) if config else await care_plan_process_graph.ainvoke(updated_state)
    return result


async def process_alternate_selection(
    state: CarePlanCheckInState,
    selected_index: Optional[int] = None,
    thread_id: Optional[str] = None,
    resume: Optional[bool] = None
) -> CarePlanCheckInState:
    """Process alternate selection and check tokens."""
    updated_state = {**state, "selected_alternate_index": selected_index}
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    if resume is not None:
        result = await care_plan_selection_graph.ainvoke(Command(resume=resume), config=config) if config else await care_plan_selection_graph.ainvoke(Command(resume=resume))
    else:
        result = await care_plan_selection_graph.ainvoke(updated_state, config=config) if config else await care_plan_selection_graph.ainvoke(updated_state)
    return result
