"""
Intelligent UI Block Generation Helper

This module provides LLM-powered CTA (Call-To-Action) generation for chatbot flows.
Instead of hardcoding buttons in each LangGraph node, this helper analyzes
conversation context and intelligently decides what actions to offer.

Design Philosophy:
- Backend decides WHAT to show (not hardcoded)
- Frontend "dumb renders" what backend sends
- LLM decides contextually appropriate actions
- Consistent structure across all 5 chatbot flows
"""

from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel
import logging

from app.langgraph.helpers.llm_client import call_llm_structured

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS FOR STRUCTURED CTA GENERATION
# ═══════════════════════════════════════════════════════════════════

class SuggestedAction(BaseModel):
    """A single suggested CTA action."""
    id: str  # Unique ID for action (e.g., "confirm_replace", "show_alternates")
    title: str  # Display text (e.g., "Yes, replace it", "Show alternatives")
    style: Literal["primary", "secondary", "destructive", "ghost"] = "primary"
    action_type: Literal["submit_event", "open_modal", "send_text"] = "submit_event"
    payload: Dict[str, Any] = {}


class UIBlockSuggestion(BaseModel):
    """Complete UI block suggestion from LLM."""
    should_show_cta: bool  # False if no CTAs needed (e.g., just informational response)
    block_type: str  # "quick_actions", "choice_buttons", "swipeable_cards", "slider_1_9", etc.
    title: Optional[str] = None
    subtitle: Optional[str] = None
    actions: List[SuggestedAction] = []
    reasoning: str  # Why these CTAs were chosen (for debugging)


# ═══════════════════════════════════════════════════════════════════
# CTA CONTEXT TEMPLATES FOR DIFFERENT FLOWS
# ═══════════════════════════════════════════════════════════════════

FLOW_CTA_GUIDELINES = {
    "care_plan_checkin": """
Care Plan Check-in CTA Guidelines:
- After showing alternates: "Yes, switch to [title]" (primary), "Show more options" (secondary)
- After skip warning: "Show me alternatives" (primary), "I understand, skip it" (destructive)  
- After completion: NO CTAs (let user type naturally)
- After asking which action: List action titles as buttons
- After asking for confirmation: "Yes, confirm" (primary), "No, cancel" (secondary)
- After replacement success: NO CTAs (flow complete)
- After cancellation: NO CTAs (flow complete)
""",
    
    "symptom_checkin": """
Symptom Check-in CTA Guidelines:
- After asking severity: Slider 1-9 (NOT buttons)
- After logging symptom: "That's all" (primary), "There's more" (secondary)
- After asking about symptoms: Quick tap options based on common symptoms
- After summary: NO CTAs (flow complete)
- If user seems done: "All done" (primary), "One more thing" (secondary)
""",
    
    "weekly_checkin": """
Weekly Check-in CTA Guidelines:
- Questions should have tap_options from LLM, NOT hardcoded
- Include "Something else..." as last option (triggers text input)
- After final question: NO CTAs (auto-generate summary)
- For category questions: 3-4 relevant options based on cycle phase
""",
    
    "personalization": """
Personalization CTA Guidelines:
- For profile questions: Tap options based on field type
- For diet: ["Vegetarian", "Vegan", "Keto", "No restrictions", "Other"]
- For allergies: Common allergens + "None" + "Other"
- Skip always available: "Skip for now" (ghost style)
- After saving: "Continue" (primary) if more gaps, else NO CTAs
""",
    
    "know_my_body": """
Know My Body CTA Guidelines:
- Educational Q&A: Usually NO CTAs (user asks questions)
- After explanation: "Tell me more" (secondary), "Ask something else" (secondary)
- For topic suggestions: 3-4 related topics as subtle buttons
- Never interrupt learning flow with big CTAs
"""
}


# ═══════════════════════════════════════════════════════════════════
# MAIN FUNCTION: INTELLIGENT CTA GENERATION
# ═══════════════════════════════════════════════════════════════════

async def generate_intelligent_ctas(
    flow_type: str,
    bot_response: str,
    conversation_state: Dict[str, Any],
    last_user_message: Optional[str] = None,
    max_actions: int = 4
) -> Optional[Dict[str, Any]]:
    """
    Generate contextually appropriate CTAs based on conversation state.
    
    Args:
        flow_type: One of "care_plan_checkin", "symptom_checkin", "weekly_checkin", 
                   "personalization", "know_my_body"
        bot_response: The message the bot is about to send
        conversation_state: Current state dict from LangGraph
        last_user_message: The user's most recent message (for context)
        max_actions: Maximum number of action buttons to generate
        
    Returns:
        A UIBlock dict ready to be used in state["ui_blocks"], or None if no CTAs needed.
    """
    
    guidelines = FLOW_CTA_GUIDELINES.get(flow_type, "")
    
    # Extract relevant state info for context
    state_context = _extract_state_context(flow_type, conversation_state)
    
    prompt = f"""Analyze this chatbot interaction and decide what CTA buttons to show.

FLOW TYPE: {flow_type}
BOT RESPONSE: "{bot_response}"
LAST USER MESSAGE: "{last_user_message or 'N/A'}"

STATE CONTEXT:
{state_context}

{guidelines}

RULES:
1. If bot response is informational or flow is complete, set should_show_cta=false
2. If asking a question, provide relevant tap options
3. Actions should be SHORT (2-4 words max)
4. Maximum {max_actions} actions
5. Primary style for main action, secondary for alternatives
6. Use destructive style ONLY for irreversible actions (skip, delete)

Output JSON:
{{
  "should_show_cta": true/false,
  "block_type": "quick_actions|choice_buttons|slider_1_9|swipeable_cards",
  "title": "optional title above buttons",
  "subtitle": "optional subtitle",
  "actions": [
    {{
      "id": "unique_id",
      "title": "Button Text",
      "style": "primary|secondary|destructive|ghost",
      "action_type": "submit_event|send_text",
      "payload": {{}}
    }}
  ],
  "reasoning": "Brief explanation of why these CTAs"
}}
"""

    try:
        suggestion = await call_llm_structured(prompt, response_model=UIBlockSuggestion)
        
        if not suggestion.should_show_cta or not suggestion.actions:
            return None
        
        # Convert to UIBlock format expected by frontend
        return {
            "id": f"{flow_type}_cta_{hash(bot_response) % 10000}",
            "type": suggestion.block_type,
            "title": suggestion.title,
            "subtitle": suggestion.subtitle,
            "actions": [
                {
                    "id": action.id,
                    "title": action.title,
                    "style": action.style,
                    "action_type": action.action_type,
                    "payload": action.payload
                }
                for action in suggestion.actions[:max_actions]
            ],
            "dismissible": True,
            "priority": "normal"
        }
        
    except Exception as e:
        logger.warning(f"Failed to generate intelligent CTAs: {e}")
        return None


def _extract_state_context(flow_type: str, state: Dict[str, Any]) -> str:
    """Extract relevant state info for CTA generation context."""
    
    context_parts = []
    
    # Common fields
    if state.get("phase"):
        context_parts.append(f"Phase: {state['phase']}")
    if state.get("error"):
        context_parts.append(f"Error: {state['error']}")
    
    # Flow-specific fields
    if flow_type == "care_plan_checkin":
        if state.get("workflow_stage"):
            context_parts.append(f"Workflow Stage: {state['workflow_stage']}")
        if state.get("alternate_candidates"):
            context_parts.append(f"Alternates Available: {len(state['alternate_candidates'])}")
        if state.get("current_intent"):
            context_parts.append(f"User Intent: {state['current_intent']}")
        if state.get("targeted_action_index") is not None:
            items = state.get("action_items", [])
            if 0 <= state["targeted_action_index"] < len(items):
                context_parts.append(f"Targeted Action: {items[state['targeted_action_index']].get('title', 'Unknown')}")
    
    elif flow_type == "symptom_checkin":
        if state.get("symptoms_pending_severity"):
            context_parts.append(f"Awaiting Severity For: {state['symptoms_pending_severity']}")
        if state.get("symptoms_mentioned"):
            context_parts.append(f"Symptoms Logged: {len(state['symptoms_mentioned'])}")
        if state.get("sufficient_exploration"):
            context_parts.append("User signaled completion")
    
    elif flow_type == "weekly_checkin":
        context_parts.append(f"Questions Asked: {state.get('question_count', 0)}/4")
        context_parts.append(f"Topics Covered: {state.get('topics_covered', [])}")
        if state.get("should_complete"):
            context_parts.append("Ready to complete")
    
    elif flow_type == "personalization":
        if state.get("profile_gaps"):
            context_parts.append(f"Profile Gaps: {state['profile_gaps'][:3]}")
        if state.get("current_topic"):
            context_parts.append(f"Current Topic: {state['current_topic']}")
        context_parts.append(f"Profile Density: {state.get('profile_density', 0):.0f}%")
    
    elif flow_type == "know_my_body":
        if state.get("question_category"):
            context_parts.append(f"Question Category: {state['question_category']}")
        if state.get("specific_topic"):
            context_parts.append(f"Topic: {state['specific_topic']}")
    
    return "\n".join(context_parts) if context_parts else "No additional context"


# ═══════════════════════════════════════════════════════════════════
# QUICK HELPER FUNCTIONS FOR COMMON CTA PATTERNS
# ═══════════════════════════════════════════════════════════════════

def create_confirmation_block(
    confirm_text: str = "Yes, confirm",
    cancel_text: str = "No, cancel",
    title: Optional[str] = None,
    confirm_payload: Dict[str, Any] = {}
) -> Dict[str, Any]:
    """Create a simple confirmation CTA block."""
    return {
        "id": f"confirm_block_{hash(confirm_text) % 10000}",
        "type": "quick_actions",
        "title": title,
        "actions": [
            {"id": "confirm", "title": confirm_text, "style": "primary", "action_type": "submit_event", "payload": confirm_payload},
            {"id": "cancel", "title": cancel_text, "style": "secondary", "action_type": "submit_event", "payload": {}}
        ],
        "dismissible": True
    }


def create_alternates_selection_block(
    alternates: List[Dict[str, Any]],
    title: str = "Choose an alternative"
) -> Dict[str, Any]:
    """Create a CTA block for selecting from alternatives."""
    return {
        "id": "alternate_suggestions",
        "type": "swipeable_cards",
        "title": title,
        "payload": {
            "cards": [
                {
                    "id": f"alt_{i}",
                    "title": alt.get("title", f"Option {i+1}"),
                    "description": alt.get("specific_action", ""),
                    "benefit": alt.get("why_better", "")
                }
                for i, alt in enumerate(alternates)
            ]
        },
        "actions": [
            {"id": f"select_alt_{i}", "title": alt.get("title", f"Option {i+1}"), "style": "primary", "action_type": "submit_event"}
            for i, alt in enumerate(alternates)
        ],
        "dismissible": True
    }


def create_severity_slider_block(
    symptom_type: str,
    title: Optional[str] = None
) -> Dict[str, Any]:
    """Create a 1-9 severity slider block for symptom check-in."""
    return {
        "id": f"severity_{symptom_type.replace(' ', '_')}",
        "type": "slider_1_9",
        "title": title or f"Rate your {symptom_type}",
        "subtitle": "1 = Barely noticeable, 9 = Unbearable",
        "payload": {"symptom_type": symptom_type},
        "actions": [],  # Slider doesn't use action buttons
        "dismissible": False
    }


def clear_ui_blocks() -> List:
    """Return empty list to clear all UI blocks."""
    return []
