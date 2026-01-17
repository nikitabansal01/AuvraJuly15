"""
Personalization LangGraph Implementation - FIXED VERSION
Complete production implementation with correct LangGraph patterns.

FIXES APPLIED:
1. ✅ Multi-invocation pattern (request/response model)
2. ✅ Profile value validation before storage
3. ✅ Proper graph flow (not ending at elicitation)
4. ✅ Complete unlock tier implementation

Features:
- Reward-gated progressive profiling (7 unlock tiers)
- Conversational elicitation (NOT static questions)
- Cycle-aware discovery prompts
- Profile density scoring
- chatbot_memory JSON storage
"""

from typing import TypedDict, List, Dict, Any, Optional, Literal
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
import json
import logging

from app.langgraph.helpers.llm_client import call_llm, call_llm_structured
from app.langgraph.helpers.database_helpers import get_cycle_info, get_streak_info, get_user_profile
from app.core.database import get_db

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# REWARD UNLOCK TIERS (OFFICIAL)
# ═══════════════════════════════════════════════════════════════════

PREFERENCE_REWARD_MAP = {
    "diet_preference": 7,
    "food_allergies": 8,
    "cuisine_preference": 12,
    "cultural_background": 14,
    "body_metrics": 18,
    "cravings": 21,
    "sleep_profile": 30
}

# Map features to profile fields
FEATURE_TO_FIELDS = {
    "diet_preference": ["dietary_restrictions", "diet_type", "vegetarian", "vegan", "keto"],
    "food_allergies": ["food_allergies", "food_sensitivities", "intolerances"],
    "cuisine_preference": ["cuisine_preferences", "favorite_cuisines", "cooking_style"],
    "cultural_background": ["ethnicity", "cultural_food_traditions", "religious_diet"],
    "body_metrics": ["height", "weight", "target_weight"],
    "cravings": ["craving_patterns", "comfort_foods", "emotional_eating"],
    "sleep_profile": ["sleep_time", "wake_time", "sleep_quality"]
}


# ═══════════════════════════════════════════════════════════════════
# STATE SCHEMA
# ═══════════════════════════════════════════════════════════════════

class PersonalizationState(TypedDict):
    """State for Personalization conversation graph."""
    
    # Session
    user_id: str
    session_id: str
    
    # Reward context
    current_streak: int
    unlocked_features: List[str]
    unlocked_fields: List[str]
    recently_unlocked: Optional[str]
    
    # Profile state
    current_profile_data: Dict[str, Any]
    profile_gaps: List[str]
    profile_density: float
    
    # Conversation
    messages: List[Dict[str, str]]
    current_topic: Optional[str]
    user_input: Optional[str]
    
    # Cycle awareness
    cycle_day: Optional[int]
    cycle_phase: Optional[str]
    
    # Phase tracking
    phase: Literal["init", "loaded", "asking", "processing", "complete"]
    
    # Updates
    profile_updates: Dict[str, Any]
    
    # Output
    bot_response: str
    trait_chips: List[Dict[str, str]]
    discovery_prompts: List[Dict[str, Any]]
    
    # Error
    error: Optional[str]


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class ProfileExtraction(BaseModel):
    """Extracted profile data from user response."""
    field_value: Any
    confidence: str = "high"
    needs_clarification: bool = False


class ValidationResult(BaseModel):
    """Validation result for profile field."""
    is_valid: bool
    cleaned_value: Any
    error_message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# HELPER: Create initial state
# ═══════════════════════════════════════════════════════════════════

def create_initial_state(user_id: str) -> PersonalizationState:
    """Create properly initialized state."""
    return PersonalizationState(
        user_id=user_id,
        session_id="",
        
        current_streak=0,
        unlocked_features=[],
        unlocked_fields=[],
        recently_unlocked=None,
        
        current_profile_data={},
        profile_gaps=[],
        profile_density=0.0,
        
        messages=[],
        current_topic=None,
        user_input=None,
        
        cycle_day=None,
        cycle_phase=None,
        
        phase="init",
        
        profile_updates={},
        
        bot_response="",
        trait_chips=[],
        discovery_prompts=[],
        
        error=None
    )


# ═══════════════════════════════════════════════════════════════════
# FIELD VALIDATORS (Fix for Issue 13)
# ═══════════════════════════════════════════════════════════════════

def validate_profile_field(field_name: str, value: Any) -> ValidationResult:
    """Validate profile field value based on field type."""
    
    # List fields - ensure value is a list
    list_fields = ["food_allergies", "food_sensitivities", "cuisine_preferences", 
                   "craving_patterns", "comfort_foods", "dietary_restrictions"]
    
    if field_name in list_fields:
        if isinstance(value, str):
            # Split by comma
            value = [v.strip() for v in value.split(",") if v.strip()]
        elif not isinstance(value, list):
            value = [str(value)]
        return ValidationResult(is_valid=True, cleaned_value=value)
    
    # Numeric fields
    numeric_fields = ["height", "weight", "target_weight"]
    if field_name in numeric_fields:
        try:
            if isinstance(value, str):
                # Extract numbers
                import re
                numbers = re.findall(r'[\d.]+', value)
                if numbers:
                    value = float(numbers[0])
                else:
                    return ValidationResult(is_valid=False, cleaned_value=None, 
                                          error_message="Please provide a number")
            return ValidationResult(is_valid=True, cleaned_value=float(value))
        except:
            return ValidationResult(is_valid=False, cleaned_value=None,
                                  error_message="Invalid number format")
    
    # Time fields
    time_fields = ["sleep_time", "wake_time"]
    if field_name in time_fields:
        # Accept various time formats, store as string
        if isinstance(value, str):
            return ValidationResult(is_valid=True, cleaned_value=value.strip())
        return ValidationResult(is_valid=True, cleaned_value=str(value))
    
    # Boolean fields
    bool_fields = ["vegetarian", "vegan", "keto"]
    if field_name in bool_fields:
        if isinstance(value, bool):
            return ValidationResult(is_valid=True, cleaned_value=value)
        if isinstance(value, str):
            value_lower = value.lower()
            if value_lower in ["yes", "true", "1"]:
                return ValidationResult(is_valid=True, cleaned_value=True)
            elif value_lower in ["no", "false", "0"]:
                return ValidationResult(is_valid=True, cleaned_value=False)
        return ValidationResult(is_valid=True, cleaned_value=bool(value))
    
    # Default - accept as string
    return ValidationResult(is_valid=True, cleaned_value=str(value) if value else "")


# ═══════════════════════════════════════════════════════════════════
# NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

async def load_profile_and_check_unlocks(state: PersonalizationState) -> PersonalizationState:
    """Load profile and determine unlocked features."""
    try:
        db = next(get_db())
        user_id = state["user_id"]
        
        # Get streak
        streak_info = get_streak_info(user_id, db)
        current_streak = streak_info.get("current_streak", 0)
        
        # Get cycle info
        cycle_info = get_cycle_info(user_id, db)
        
        # Get current profile
        profile = get_user_profile(user_id, db)
        profile_data = profile.chatbot_memory if hasattr(profile, 'chatbot_memory') and profile.chatbot_memory else {}
        
        # Determine unlocked features
        unlocked_features = []
        unlocked_fields = []
        recently_unlocked = None
        
        for feature, required_days in PREFERENCE_REWARD_MAP.items():
            if current_streak >= required_days:
                unlocked_features.append(feature)
                unlocked_fields.extend(FEATURE_TO_FIELDS.get(feature, []))
                
                # Check if just unlocked
                if current_streak == required_days:
                    recently_unlocked = feature
        
        # Find gaps (unlocked but empty fields)
        profile_gaps = [
            field for field in unlocked_fields
            if field not in profile_data or not profile_data.get(field)
        ]
        
        # Calculate density
        filled = len([f for f in unlocked_fields if profile_data.get(f)])
        total = max(len(unlocked_fields), 1)
        density = (filled / total) * 100
        
        # Generate greeting
        if recently_unlocked:
            greeting = f"🎉 Congrats on {current_streak} days! You just unlocked {recently_unlocked.replace('_', ' ')}! Let me learn more about you."
        elif profile_gaps:
            greeting = f"Hi! Your profile is {density:.0f}% complete. Want to fill in some details?"
        else:
            greeting = f"Your profile looks great! ({density:.0f}% complete). Want to update anything?"
        
        return {
            **state,
            "current_streak": current_streak,
            "unlocked_features": unlocked_features,
            "unlocked_fields": unlocked_fields,
            "recently_unlocked": recently_unlocked,
            "current_profile_data": profile_data,
            "profile_gaps": profile_gaps,
            "profile_density": density,
            "cycle_day": cycle_info.get("cycle_day"),
            "cycle_phase": cycle_info.get("phase"),
            "bot_response": greeting,
            "messages": [{"role": "assistant", "content": greeting}],
            "phase": "loaded"
        }
    except Exception as e:
        logger.error(f"Error loading profile: {e}")
        return {
            **state,
            "error": str(e),
            "bot_response": "Let's personalize your experience! What would you like to share?",
            "phase": "loaded"
        }


async def generate_discovery_question(state: PersonalizationState) -> PersonalizationState:
    """Generate conversational question for next gap."""
    
    gaps = state.get("profile_gaps", [])
    
    if not gaps:
        response = "Your profile is complete! 🎉 Is there anything you'd like to update?"
        return {
            **state,
            "bot_response": response,
            "messages": state.get("messages", []) + [{"role": "assistant", "content": response}],
            "phase": "complete"
        }
    
    # Pick first gap
    gap_field = gaps[0]
    
    elicitation_prompt = f"""Ask a warm, conversational question to discover: {gap_field}

User Context:
- Cycle Phase: {state.get('cycle_phase')} (Day {state.get('cycle_day')})
- Streak: {state.get('current_streak')} days

Guidelines:
1. Make it conversational, NOT clinical
2. Briefly explain WHY you're asking
3. Make it optional
4. Cycle-aware where relevant
5. Warm tone (Dr. Auvra style)

Keep it 2-3 sentences, warm and personal.
"""
    
    try:
        question = await call_llm(elicitation_prompt, model="gpt-4o-mini")
    except:
        question = f"Could you tell me about your {gap_field.replace('_', ' ')}?"
    
    return {
        **state,
        "current_topic": gap_field,
        "bot_response": question,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": question}],
        "phase": "asking"
    }


async def process_profile_response(state: PersonalizationState) -> PersonalizationState:
    """Extract and validate profile data from user response."""
    
    user_input = state.get("user_input", "")
    current_topic = state.get("current_topic")
    
    if not user_input or not current_topic:
        return {**state, "error": "missing_input_or_topic", "phase": "asking"}
    
    # Check for skip signals
    skip_signals = ["skip", "pass", "don't want to", "later", "not now"]
    if any(signal in user_input.lower() for signal in skip_signals):
        # Remove from gaps and move on
        gaps = [g for g in state.get("profile_gaps", []) if g != current_topic]
        
        response = "No problem! We can come back to that later."
        
        return {
            **state,
            "profile_gaps": gaps,
            "current_topic": None,
            "bot_response": response,
            "messages": state.get("messages", []) + [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response}
            ],
            "user_input": None,
            "phase": "loaded"
        }
    
    # Extract value using LLM
    extraction_prompt = f"""Extract profile value for field: {current_topic}

User Response: "{user_input}"

Output JSON:
{{
  "field_value": <extracted value - list for multiple items, single value otherwise>,
  "confidence": "high|medium|low",
  "needs_clarification": true/false
}}
"""
    
    try:
        extracted = await call_llm_structured(extraction_prompt, response_model=ProfileExtraction)
        
        # VALIDATE before storing (Fix for Issue 13)
        validation = validate_profile_field(current_topic, extracted.field_value)
        
        if not validation.is_valid:
            response = f"I didn't quite catch that. {validation.error_message}"
            return {
                **state,
                "bot_response": response,
                "messages": state.get("messages", []) + [
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": response}
                ],
                "user_input": None,
                "phase": "asking"  # Ask again
            }
        
        # Store in database
        db = next(get_db())
        profile = get_user_profile(state["user_id"], db)
        memory = profile.chatbot_memory if hasattr(profile, 'chatbot_memory') and profile.chatbot_memory else {}
        memory[current_topic] = validation.cleaned_value
        
        profile.chatbot_memory = memory
        db.commit()
        
        # Update state
        profile_updates = state.get("profile_updates", {}).copy()
        profile_updates[current_topic] = validation.cleaned_value
        
        gaps = [g for g in state.get("profile_gaps", []) if g != current_topic]
        
        acknowledgment = f"Got it! I've saved your {current_topic.replace('_', ' ')}. 💜"
        
        return {
            **state,
            "profile_updates": profile_updates,
            "profile_gaps": gaps,
            "current_topic": None,
            "bot_response": acknowledgment,
            "messages": state.get("messages", []) + [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": acknowledgment}
            ],
            "user_input": None,
            "phase": "loaded"  # Ready for next question
        }
        
    except Exception as e:
        logger.error(f"Error processing response: {e}")
        return {
            **state,
            "error": str(e),
            "bot_response": "I had trouble saving that. Let's try again.",
            "user_input": None,
            "phase": "asking"
        }


async def generate_trait_chips(state: PersonalizationState) -> PersonalizationState:
    """Generate visual trait chips for profile display."""
    
    profile_data = state.get("current_profile_data", {})
    updates = state.get("profile_updates", {})
    
    # Merge current and updates
    all_data = {**profile_data, **updates}
    
    chips = []
    for key, value in all_data.items():
        if value:
            if isinstance(value, list):
                for v in value:
                    chips.append({"label": str(v), "category": key})
            else:
                chips.append({"label": str(value), "category": key})
    
    return {
        **state,
        "trait_chips": chips,
        "phase": "complete"
    }


# ═══════════════════════════════════════════════════════════════════
# CONDITIONAL EDGE ROUTING
# ═══════════════════════════════════════════════════════════════════

def route_after_load(state: PersonalizationState) -> str:
    """Route after loading profile."""
    if state.get("profile_gaps"):
        return "generate_discovery_question"
    return "generate_trait_chips"


def route_after_response(state: PersonalizationState) -> str:
    """Route after processing response."""
    phase = state.get("phase", "loaded")
    if phase == "asking":
        return "END"  # Wait for user input
    if state.get("profile_gaps"):
        return "generate_discovery_question"
    return "generate_trait_chips"


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - START (Invocation 1)
# ═══════════════════════════════════════════════════════════════════

def create_start_graph():
    """Graph for starting personalization - load and ask first question."""
    
    workflow = StateGraph(PersonalizationState)
    
    workflow.add_node("load_profile_and_check_unlocks", load_profile_and_check_unlocks)
    workflow.add_node("generate_discovery_question", generate_discovery_question)
    workflow.add_node("generate_trait_chips", generate_trait_chips)
    
    workflow.set_entry_point("load_profile_and_check_unlocks")
    
    workflow.add_conditional_edges(
        "load_profile_and_check_unlocks",
        route_after_load,
        {
            "generate_discovery_question": "generate_discovery_question",
            "generate_trait_chips": "generate_trait_chips"
        }
    )
    
    workflow.add_edge("generate_discovery_question", END)  # Wait for input
    workflow.add_edge("generate_trait_chips", END)
    
    return workflow.compile()


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - CONTINUE (Invocation 2+)
# ═══════════════════════════════════════════════════════════════════

def create_continue_graph():
    """Graph for processing user profile responses."""
    
    workflow = StateGraph(PersonalizationState)
    
    workflow.add_node("process_profile_response", process_profile_response)
    workflow.add_node("generate_discovery_question", generate_discovery_question)
    workflow.add_node("generate_trait_chips", generate_trait_chips)
    
    workflow.set_entry_point("process_profile_response")
    
    workflow.add_conditional_edges(
        "process_profile_response",
        route_after_response,
        {
            "END": END,
            "generate_discovery_question": "generate_discovery_question",
            "generate_trait_chips": "generate_trait_chips"
        }
    )
    
    workflow.add_edge("generate_discovery_question", END)
    workflow.add_edge("generate_trait_chips", END)
    
    return workflow.compile()


# Compile graphs
personalization_start_graph = create_start_graph()
personalization_continue_graph = create_continue_graph()


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════

async def start_personalization(user_id: str) -> PersonalizationState:
    """Start personalization session."""
    state = create_initial_state(user_id)
    result = await personalization_start_graph.ainvoke(state)
    return result


async def continue_personalization(
    state: PersonalizationState,
    user_input: str
) -> PersonalizationState:
    """Continue personalization with user response."""
    updated_state = {**state, "user_input": user_input}
    result = await personalization_continue_graph.ainvoke(updated_state)
    return result
