"""
Know My Body LangGraph Implementation - FIXED VERSION
Complete production implementation with ALL nodes.

FIXES APPLIED:
1. ✅ All 5 question type nodes implemented
2. ✅ Multi-invocation pattern
3. ✅ Complete routing
4. ✅ Hormone buddy voices
5. ✅ User interest tracking
6. ✅ UNIFIED MEMORY: Cross-chatbot context awareness

Features:
- Educational Q&A about cycles, hormones, symptoms
- Hormone buddy voices (first-person)
- Cycle-aware explanations
- Action plan rationale with research
- Conversational memory tracking
- Cross-chatbot memory for personalized education
"""

from typing import TypedDict, List, Dict, Any, Optional, Literal
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
import json
import logging

from app.langgraph.helpers.llm_client import call_llm, call_llm_structured
from app.langgraph.helpers.database_helpers import get_cycle_info, get_todays_action_plan, get_user_profile
from app.core.database import get_db
from app.langgraph.helpers.ui_blocks_helper import generate_intelligent_ctas, create_confirmation_block

# NEW: Unified memory for cross-chatbot context
from app.langgraph.memory import get_unified_context, format_context_for_prompt

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# HORMONE BUDDY VOICES
# ═══════════════════════════════════════════════════════════════════

HORMONE_BUDDIES = {
    "estrogen": {
        "emoji": "🌸",
        "personality": "energetic, social, optimistic",
        "voice": "Hi! I'm Estrogen 🌸, your energy and confidence hormone! I peak before ovulation and make you feel amazing - clear skin, high energy, social butterfly mode!"
    },
    "progesterone": {
        "emoji": "🌙",
        "personality": "calm, nurturing, sleepy",
        "voice": "Hey there, I'm Progesterone 🌙, the calming one. I rise after ovulation to prepare your body for rest. If you're feeling a bit tired or moody, that's just me doing my job."
    },
    "testosterone": {
        "emoji": "💪",
        "personality": "confident, driven, assertive",
        "voice": "I'm Testosterone 💪! I peak around ovulation and give you that confidence boost. Great time for workouts and tackling challenges!"
    },
    "cortisol": {
        "emoji": "⚡",
        "personality": "alert, stressed, responsive",
        "voice": "I'm Cortisol ⚡, your stress response hormone. I'm highest in the morning to wake you up. Keep me balanced with good sleep and stress management!"
    }
}


# ═══════════════════════════════════════════════════════════════════
# STATE SCHEMA
# ═══════════════════════════════════════════════════════════════════

class KnowMyBodyState(TypedDict):
    """State for Know My Body educational chat."""
    
    # Session
    user_id: str
    session_id: str
    
    # User context
    cycle_day: Optional[int]
    cycle_phase: Optional[str]
    primary_hormone: Optional[str]
    secondary_hormones: List[str]
    
    # Current user input
    user_question: Optional[str]
    
    # Question classification
    question_category: Optional[str]
    specific_topic: Optional[str]
    
    # Conversation
    messages: List[Dict[str, str]]
    user_interests: List[str]  # Track what user asks about
    
    # Action plan context (for rationale questions)
    todays_action_plan: List[Dict[str, Any]]
    
    # UNIFIED MEMORY: Cross-chatbot context
    unified_context: Optional[Dict[str, Any]]
    formatted_context: Optional[str]
    
    # Educational content
    explanation: Optional[str]
    hormone_voice: Optional[str]
    research_citations: List[str]
    
    # Phase tracking
    phase: Literal["init", "loaded", "processing", "complete"]
    
    # Output
    bot_response: str
    educational_content: Dict[str, Any]
    
    # Error
    error: Optional[str]


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class QuestionClassification(BaseModel):
    """Classification of educational question."""
    category: str
    specific_topic: str
    is_in_scope: bool
    hormone_mentioned: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# HELPER: Create initial state
# ═══════════════════════════════════════════════════════════════════

def create_initial_state(user_id: str) -> KnowMyBodyState:
    """Create properly initialized state."""
    return KnowMyBodyState(
        user_id=user_id,
        session_id="",
        
        cycle_day=None,
        cycle_phase=None,
        primary_hormone=None,
        secondary_hormones=[],
        
        user_question=None,
        
        question_category=None,
        specific_topic=None,
        
        messages=[],
        user_interests=[],
        
        todays_action_plan=[],
        
        explanation=None,
        hormone_voice=None,
        research_citations=[],
        
        phase="init",
        
        bot_response="",
        educational_content={},
        
        error=None,
        
        # Memory context
        unified_context=None,
        formatted_context=None
    )


# ═══════════════════════════════════════════════════════════════════
# NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

async def load_context(state: KnowMyBodyState) -> KnowMyBodyState:
    """Load user context including cross-chatbot memory for educational responses."""
    try:
        db = next(get_db())
        user_id = state["user_id"]
        
        # ══════════════════════════════════════════════════════════════
        # NEW: Load unified cross-chatbot memory context
        # ══════════════════════════════════════════════════════════════
        unified_ctx = await get_unified_context(user_id, "know_my_body")
        formatted_ctx = format_context_for_prompt(unified_ctx)
        
        # Get cycle info
        cycle_info = get_cycle_info(user_id, db)
        
        # Get today's action plan (for rationale questions)
        plan_data = get_todays_action_plan(user_id, db)
        
        # Get user profile for interest tracking
        profile = get_user_profile(user_id, db)
        memory = profile.chatbot_memory if hasattr(profile, 'chatbot_memory') and profile.chatbot_memory else {}
        interests = memory.get("educational_interests", [])
        
        greeting = f"Hi! I'm here to help you understand your body better. You're in your {cycle_info.get('phase', 'current')} phase right now. What would you like to know?"
        
        return {
            **state,
            "cycle_day": cycle_info.get("cycle_day"),
            "cycle_phase": cycle_info.get("phase"),
            "primary_hormone": cycle_info.get("primary_hormone"),
            "todays_action_plan": plan_data.get("items", []) if plan_data else [],
            "user_interests": interests,
            "unified_context": unified_ctx,
            "formatted_context": formatted_ctx,
            "bot_response": greeting,
            "messages": [{"role": "assistant", "content": greeting}],
            "phase": "loaded"
        }
    except Exception as e:
        logger.error(f"Error loading context: {e}")
        return {
            **state,
            "bot_response": "Hi! Ask me anything about your cycle, hormones, or health!",
            "messages": [{"role": "assistant", "content": "Hi! Ask me anything about your cycle, hormones, or health!"}],
            "phase": "loaded",
            "error": str(e),
            "unified_context": {},
            "formatted_context": ""
        }


async def classify_question(state: KnowMyBodyState) -> KnowMyBodyState:
    """Classify the educational question type with full user context."""
    
    user_question = state.get("user_question", "")
    formatted_ctx = state.get("formatted_context", "")
    
    if not user_question:
        return {**state, "error": "no_question"}
    
    classification_prompt = f"""Classify this educational question and personalize the response.

User Question: "{user_question}"

======================================================================
COMPLETE USER CONTEXT (Use to personalize your answer!)
======================================================================
{formatted_ctx}

======================================================================
CURRENT CONTEXT
======================================================================
- Current Phase: {state.get('cycle_phase')} (Day {state.get('cycle_day')})
- Primary Hormone: {state.get('primary_hormone')}

Categories:
1. **cycle_education**: General cycle/phase questions ("What happens in luteal?", "How long is a cycle?")
2. **hormone_explanation**: Hormone function/behavior ("What does progesterone do?", "Why is estrogen important?")
3. **symptom_link**: Why symptoms happen ("Why do I have cramps?", "What causes bloating?")
4. **action_rationale**: Why specific actions in plan ("Why walnuts?", "Why yoga during this phase?")
5. **general_wellness**: Broader health topics related to hormones

Is this question in scope for hormonal/cycle education?

Output JSON: {{
  "category": "cycle_education|hormone_explanation|symptom_link|action_rationale|general_wellness",
  "specific_topic": "the main topic",
  "is_in_scope": true/false,
  "hormone_mentioned": "estrogen|progesterone|testosterone|cortisol|null"
}}
"""
    
    try:
        classification = await call_llm_structured(classification_prompt, response_model=QuestionClassification)
        
        if not classification.is_in_scope:
            # Generate personalized out-of-scope response
            oos_prompt = f"""User asked a question outside your expertise: "{user_question}"

User Context:
{formatted_ctx}

Generate a warm, personalized response that:
1. Gently explains this is outside your focus (hormonal health and cycles)
2. Suggests a relevant topic they might be interested in based on their profile
3. Keep it 2 sentences, friendly
"""
            try:
                response = await call_llm(oos_prompt, model="gpt-4o-mini")
            except:
                response = "That's a great question, but it's outside my expertise. I focus on hormonal health and menstrual cycles. Is there anything about your cycle or hormones I can help explain?"
            
            return {
                **state,
                "bot_response": response,
                "messages": state.get("messages", []) + [
                    {"role": "user", "content": user_question},
                    {"role": "assistant", "content": response}
                ],
                "user_question": None,
                "error": "out_of_scope",
                "phase": "complete"
            }
        
        # Track interest
        interests = state.get("user_interests", [])[:]
        if classification.specific_topic not in interests:
            interests.append(classification.specific_topic)
        
        return {
            **state,
            "question_category": classification.category,
            "specific_topic": classification.specific_topic,
            "hormone_voice": classification.hormone_mentioned,
            "user_interests": interests,
            "messages": state.get("messages", []) + [{"role": "user", "content": user_question}],
            "phase": "processing"
        }
    except Exception as e:
        logger.error(f"Error classifying question: {e}")
        return {**state, "question_category": "general_wellness", "phase": "processing"}


async def explain_cycle_concept(state: KnowMyBodyState) -> KnowMyBodyState:
    """Explain cycle/phase concepts with personalization."""
    
    topic = state.get("specific_topic", "cycle")
    user_question = state.get("user_question", "")
    formatted_ctx = state.get("formatted_context", "")
    
    prompt = f"""Explain {topic} to a user asking: "{user_question}"

======================================================================
COMPLETE USER CONTEXT (Personalize your explanation!)
======================================================================
{formatted_ctx}

======================================================================
CURRENT CONTEXT
======================================================================
- Current Phase: {state.get('cycle_phase')} (Day {state.get('cycle_day')})

Guidelines:
1. Simple language, no medical jargon
2. Connect to user's CURRENT phase and their specific conditions
3. Use emojis for hormones (🌸 Estrogen, 🌙 Progesterone, 💪 Testosterone)
4. If they have specific conditions (PCOS, endometriosis, etc.), mention how this topic relates
5. 3-4 sentences, warm and educational
6. Make it feel personalized to THEIR situation, not generic

Do NOT give a generic explanation - connect it to THIS user's health profile!
"""
    
    try:
        explanation = await call_llm(prompt, model="gpt-4o")
    except:
        explanation = f"The {topic} is an important part of understanding your cycle. Would you like more details?"
    
    return {
        **state,
        "explanation": explanation,
        "bot_response": explanation,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": explanation}],
        "user_question": None,
        "phase": "complete"
    }


async def explain_hormone_mechanism(state: KnowMyBodyState) -> KnowMyBodyState:
    """Hormone buddy explains themselves in first person with personalization."""
    
    hormone = state.get("hormone_voice") or state.get("specific_topic", "").lower()
    user_question = state.get("user_question", "")
    formatted_ctx = state.get("formatted_context", "")
    
    # Get hormone buddy info
    buddy = HORMONE_BUDDIES.get(hormone, {})
    emoji = buddy.get("emoji", "💫")
    personality = buddy.get("personality", "helpful")
    
    prompt = f"""Explain {hormone} hormone using FIRST PERSON (hormone buddy voice).

User Question: "{user_question}"

======================================================================
COMPLETE USER CONTEXT (Personalize your explanation!)
======================================================================
{formatted_ctx}

======================================================================
CURRENT CONTEXT
======================================================================
- Current Phase: {state.get('cycle_phase')} (Day {state.get('cycle_day')})

Hormone Personality: {personality}
Emoji: {emoji}

Guidelines:
1. Hormone speaks in FIRST PERSON ("I'm {hormone.capitalize()} {emoji}...")
2. Explain what you do in the context of THEIR specific conditions
3. If they have PCOS, endometriosis, thyroid issues, etc. - explain how you affect THEIR condition
4. Connect to user's current phase
5. Playful but scientifically accurate
6. 3-4 sentences
7. Make it feel like you're speaking to THIS user, not giving generic info

Do NOT be generic - reference their specific health situation!
"""
    
    try:
        explanation = await call_llm(prompt, model="gpt-4o")
    except:
        explanation = buddy.get("voice", f"I'm {hormone.capitalize()}! I'm an important hormone in your body.")
    
    return {
        **state,
        "explanation": explanation,
        "hormone_voice": hormone,
        "bot_response": explanation,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": explanation}],
        "user_question": None,
        "phase": "complete"
    }


async def link_symptom_to_hormone(state: KnowMyBodyState) -> KnowMyBodyState:
    """Explain why symptoms happen in hormonal context with personalization."""
    
    symptom = state.get("specific_topic", "symptom")
    user_question = state.get("user_question", "")
    formatted_ctx = state.get("formatted_context", "")
    
    prompt = f"""Explain why the symptom "{symptom}" happens in hormonal context.

User Question: "{user_question}"

======================================================================
COMPLETE USER CONTEXT (Personalize your explanation!)
======================================================================
{formatted_ctx}

======================================================================
CURRENT CONTEXT
======================================================================
- Current Phase: {state.get('cycle_phase')} (Day {state.get('cycle_day')})
- Primary Hormone: {state.get('primary_hormone')}

Guidelines:
1. Connect symptom to specific hormone(s)
2. Explain the mechanism simply
3. Reference user's current phase
4. If they have conditions (PCOS, endometriosis, etc.), explain how this symptom relates
5. Use hormone emojis (🌸 🌙 💪 ⚡)
6. Suggest if it's normal or something to watch
7. 3-4 sentences, supportive tone
8. Reference their specific situation - don't be generic!

Do NOT give a generic explanation - make it specific to THIS user's health profile!
"""
    
    try:
        explanation = await call_llm(prompt, model="gpt-4o")
    except:
        explanation = f"{symptom.capitalize()} can be related to hormonal changes during your cycle. Would you like more details?"
    
    return {
        **state,
        "explanation": explanation,
        "bot_response": explanation,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": explanation}],
        "user_question": None,
        "phase": "complete"
    }


async def explain_action_rationale(state: KnowMyBodyState) -> KnowMyBodyState:
    """Explain why a specific action is in the plan with personalization."""
    
    topic = state.get("specific_topic", "action")
    user_question = state.get("user_question", "")
    action_plan = state.get("todays_action_plan", [])
    formatted_ctx = state.get("formatted_context", "")
    
    # Try to find matching action
    matching_action = None
    for action in action_plan:
        if topic.lower() in action.get("title", "").lower():
            matching_action = action
            break
    
    if matching_action:
        action_context = f"""
Action: {matching_action.get('title')}
Category: {matching_action.get('category')}
Target Hormone: {matching_action.get('target_hormone')}
Purpose: {matching_action.get('purpose', 'Support hormonal health')}
"""
    else:
        action_context = f"Topic: {topic}"
    
    prompt = f"""Explain why this action is recommended for THIS specific user.

User Question: "{user_question}"

{action_context}

======================================================================
COMPLETE USER CONTEXT (Explain why this is perfect for THEM!)
======================================================================
{formatted_ctx}

======================================================================
CURRENT CONTEXT
======================================================================
- Current Phase: {state.get('cycle_phase')} (Day {state.get('cycle_day')})

Guidelines:
1. Explain the science behind why this helps THIS user specifically
2. Connect to their diagnosed conditions if relevant
3. Reference their current phase
4. Use hormone emojis
5. 3-4 sentences, encouraging
6. Make it personal - don't just explain the general benefit

Do NOT give a generic explanation - connect it to THIS user's specific health situation!
"""
    
    try:
        explanation = await call_llm(prompt, model="gpt-4o")
    except:
        explanation = f"This action supports your {state.get('primary_hormone', 'hormonal')} levels during your current phase!"
    
    return {
        **state,
        "explanation": explanation,
        "bot_response": explanation,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": explanation}],
        "user_question": None,
        "phase": "complete"
    }


async def handle_general_wellness(state: KnowMyBodyState) -> KnowMyBodyState:
    """Handle general wellness questions with personalization."""
    
    topic = state.get("specific_topic", "wellness")
    user_question = state.get("user_question", "")
    formatted_ctx = state.get("formatted_context", "")
    
    prompt = f"""Answer this wellness question with cycle/hormone awareness.

User Question: "{user_question}"

======================================================================
COMPLETE USER CONTEXT (Personalize your answer!)
======================================================================
{formatted_ctx}

======================================================================
CURRENT CONTEXT
======================================================================
- Current Phase: {state.get('cycle_phase')} (Day {state.get('cycle_day')})

Guidelines:
1. Connect to hormonal health where relevant
2. Reference their specific conditions if applicable
3. Be helpful and accurate
4. If truly out of scope, gently redirect
5. Use hormone emojis where relevant
6. 3-4 sentences
"""
    
    try:
        explanation = await call_llm(prompt, model="gpt-4o-mini")
    except:
        explanation = "That's a great question! It relates to overall wellness. For specific medical concerns, please consult your healthcare provider. Is there anything about your cycle I can help with?"
    
    return {
        **state,
        "explanation": explanation,
        "bot_response": explanation,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": explanation}],
        "user_question": None,
        "phase": "complete"
    }


async def track_user_interests(state: KnowMyBodyState) -> KnowMyBodyState:
    """Save user interests to profile (Layer 3 memory)."""
    try:
        db = next(get_db())
        profile = get_user_profile(state["user_id"], db)
        memory = profile.chatbot_memory if hasattr(profile, 'chatbot_memory') and profile.chatbot_memory else {}
        
        memory["educational_interests"] = state.get("user_interests", [])[-20:]  # Keep last 20
        profile.chatbot_memory = memory
        db.commit()
    except Exception as e:
        logger.error(f"Error saving interests: {e}")
    
    return state


# ═══════════════════════════════════════════════════════════════════
# CONDITIONAL EDGE ROUTING
# ═══════════════════════════════════════════════════════════════════

def route_by_category(state: KnowMyBodyState) -> str:
    """Route based on question category."""
    
    if state.get("error") == "out_of_scope":
        return "track_user_interests"
    
    category = state.get("question_category", "general_wellness")
    
    routing = {
        "cycle_education": "explain_cycle_concept",
        "hormone_explanation": "explain_hormone_mechanism",
        "symptom_link": "link_symptom_to_hormone",
        "action_rationale": "explain_action_rationale",
        "general_wellness": "handle_general_wellness"
    }
    
    return routing.get(category, "handle_general_wellness")


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - START (Invocation 1)
# ═══════════════════════════════════════════════════════════════════

def create_start_graph():
    """Graph for starting Know My Body session."""
    
    workflow = StateGraph(KnowMyBodyState)
    
    workflow.add_node("load_context", load_context)
    
    workflow.set_entry_point("load_context")
    workflow.add_edge("load_context", END)  # Wait for question
    
    return workflow.compile()


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - ANSWER (Invocation 2+)
# ═══════════════════════════════════════════════════════════════════

def create_answer_graph():
    """Graph for answering educational questions."""
    
    workflow = StateGraph(KnowMyBodyState)
    
    # Add ALL nodes
    workflow.add_node("classify_question", classify_question)
    workflow.add_node("explain_cycle_concept", explain_cycle_concept)
    workflow.add_node("explain_hormone_mechanism", explain_hormone_mechanism)
    workflow.add_node("link_symptom_to_hormone", link_symptom_to_hormone)
    workflow.add_node("explain_action_rationale", explain_action_rationale)
    workflow.add_node("handle_general_wellness", handle_general_wellness)
    workflow.add_node("track_user_interests", track_user_interests)
    
    # Set entry point
    workflow.set_entry_point("classify_question")
    
    # Classification → route to appropriate handler
    workflow.add_conditional_edges(
        "classify_question",
        route_by_category,
        {
            "explain_cycle_concept": "explain_cycle_concept",
            "explain_hormone_mechanism": "explain_hormone_mechanism",
            "link_symptom_to_hormone": "link_symptom_to_hormone",
            "explain_action_rationale": "explain_action_rationale",
            "handle_general_wellness": "handle_general_wellness",
            "track_user_interests": "track_user_interests"
        }
    )
    
    # All handlers → track interests → end
    workflow.add_edge("explain_cycle_concept", "track_user_interests")
    workflow.add_edge("explain_hormone_mechanism", "track_user_interests")
    workflow.add_edge("link_symptom_to_hormone", "track_user_interests")
    workflow.add_edge("explain_action_rationale", "track_user_interests")
    workflow.add_edge("handle_general_wellness", "track_user_interests")
    workflow.add_edge("track_user_interests", END)
    
    return workflow.compile()


# Compile graphs
know_my_body_start_graph = create_start_graph()
know_my_body_answer_graph = create_answer_graph()


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════

async def start_know_my_body(user_id: str) -> KnowMyBodyState:
    """Start Know My Body session."""
    state = create_initial_state(user_id)
    result = await know_my_body_start_graph.ainvoke(state)
    return result


async def answer_question(
    state: KnowMyBodyState,
    user_question: str
) -> KnowMyBodyState:
    """Answer educational question."""
    updated_state = {**state, "user_question": user_question}
    result = await know_my_body_answer_graph.ainvoke(updated_state)
    return result
