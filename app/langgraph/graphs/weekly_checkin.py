"""
Weekly Check-in LangGraph Implementation - FIXED VERSION
Complete production implementation with correct LangGraph patterns.

FIXES APPLIED:
1. ✅ Multi-invocation pattern (request/response model)
2. ✅ Proper state initialization
3. ✅ No state mutation in routing functions
4. ✅ Database persistence for insights
5. ✅ Pydantic model type fixes
6. ✅ UNIFIED MEMORY: Cross-chatbot context awareness

Features:
- NO streak gating (removed per user feedback)
- 3-4 questions maximum (enforced)
- Multi-format input (tap/type/voice/slider)
- LLM-powered question generation
- Insights storage for insights page
- Cross-chatbot memory for personalized questions
"""

from typing import TypedDict, List, Dict, Any, Literal, Optional
from datetime import date, datetime, timedelta
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
import uuid
import json
import logging

from app.langgraph.helpers.llm_client import call_llm, call_llm_structured
from app.langgraph.helpers.database_helpers import (
    get_cycle_info, get_recent_symptoms, get_action_completion_stats, get_user_profile
)
from app.core.database import get_db, WeeklyCheckInSession
from app.langgraph.helpers.ui_blocks_helper import generate_intelligent_ctas, create_confirmation_block

# NEW: Unified memory for cross-chatbot context
from app.langgraph.memory import get_unified_context, format_context_for_prompt

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# STATE SCHEMA
# ═══════════════════════════════════════════════════════════════════

class WeeklyCheckInState(TypedDict):
    """State for Weekly Check-in conversation graph."""
    
    # Session identifiers
    user_id: str
    session_id: str
    check_in_id: str
    scheduled_date: Optional[date]
    started_at: Optional[datetime]
    
    # User context (loaded at start)
    cycle_day: Optional[int]
    cycle_phase: Optional[str]
    primary_hormone: Optional[str]
    
    # Recent activity context
    recent_symptoms: List[Dict[str, Any]]
    recent_completions: Dict[str, Any]
    last_checkin_summary: Optional[str]
    
    # Conversation state
    messages: List[Dict[str, str]]
    questions_asked: List[str]
    answers_collected: Dict[str, Any]
    current_question: Optional[Dict[str, Any]]
    
    # CURRENT USER INPUT (passed by external caller)
    user_input: Optional[str]
    input_mode: Literal["tap", "type", "yap", "slider"]
    
    # Tap options for frontend
    tap_options: Optional[List[str]]
    
    # Completion tracking (ENFORCE 3-4 LIMIT)
    question_count: int
    topics_covered: List[str]
    should_complete: bool
    
    # UNIFIED MEMORY: Cross-chatbot context
    unified_context: Optional[Dict[str, Any]]
    formatted_context: Optional[str]
    
    # Current phase of conversation
    phase: Literal["init", "questioning", "processing_input", "complete"]
    
    # Generated outputs
    insights: List[Dict[str, Any]]
    summary: Optional[str]
    severity_trend: Optional[str]
    identified_triggers: List[str]
    identified_relief_factors: List[str]
    
    # Error handling
    error: Optional[str]


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS FOR STRUCTURED LLM OUTPUTS
# ═══════════════════════════════════════════════════════════════════

class NextQuestion(BaseModel):
    """Model for next question generation."""
    question_text: str
    question_category: str
    tap_options: List[str]
    is_final_question: bool


class ParsedAnswer(BaseModel):
    """Model for parsed free-text answer."""
    key_points: List[str]
    insights: List[Dict[str, Any]]  # FIXED: Changed from str to Any
    extracted_symptoms: List[str]


class WeeklySummary(BaseModel):
    """Model for weekly summary output."""
    summary: str
    key_insights: List[str]
    severity_trend: str
    triggers: List[str]
    relief_factors: List[str]


# ═══════════════════════════════════════════════════════════════════
# HELPER: Create initial state
# ═══════════════════════════════════════════════════════════════════

def create_initial_state(user_id: str, session_id: str = None) -> WeeklyCheckInState:
    """Create properly initialized state for new check-in."""
    return WeeklyCheckInState(
        user_id=user_id,
        session_id=session_id or str(uuid.uuid4()),
        check_in_id=str(uuid.uuid4()),
        scheduled_date=date.today(),
        started_at=datetime.utcnow(),
        
        # Context (will be loaded)
        cycle_day=None,
        cycle_phase=None,
        primary_hormone=None,
        recent_symptoms=[],
        recent_completions={"total": 0, "completed": 0, "completion_rate": 0},
        last_checkin_summary=None,
        
        # Conversation (initialized empty)
        messages=[],
        questions_asked=[],
        answers_collected={},
        current_question=None,
        
        # Input (none initially)
        user_input=None,
        input_mode="tap",
        tap_options=None,
        
        # Completion tracking
        question_count=0,
        topics_covered=[],
        should_complete=False,
        
        # Phase tracking
        phase="init",
        
        # Outputs (initialized empty)
        insights=[],
        summary=None,
        severity_trend=None,
        identified_triggers=[],
        identified_relief_factors=[],
        
        # Error
        error=None,
        
        # Memory context
        unified_context=None,
        formatted_context=None
    )


# ═══════════════════════════════════════════════════════════════════
# NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

async def load_user_context(state: WeeklyCheckInState) -> WeeklyCheckInState:
    """Load all contextual data including cross-chatbot memory for personalized conversation."""
    try:
        db = next(get_db())
        user_id = state["user_id"]
        
        # ══════════════════════════════════════════════════════════════
        # NEW: Load unified cross-chatbot memory context
        # This gives us EVERYTHING about the user - past conversations,
        # preferences, feedback from other chatbots, etc.
        # ══════════════════════════════════════════════════════════════
        unified_ctx = await get_unified_context(user_id, "weekly_checkin")
        formatted_ctx = format_context_for_prompt(unified_ctx)
        
        # Load cycle info
        cycle_info = get_cycle_info(user_id, db)
        
        # Load recent symptoms (past 7 days)
        recent_symptoms = get_recent_symptoms(user_id, days=7, db=db)
        
        # Load action completion stats
        completions = get_action_completion_stats(user_id, days=7, db=db)
        
        # Load last check-in summary from database
        last_checkin_summary = None
        try:
            last_session = db.query(WeeklyCheckInSession).filter(
                WeeklyCheckInSession.uid == user_id,
                WeeklyCheckInSession.is_complete == True
            ).order_by(WeeklyCheckInSession.session_date.desc()).first()
            if last_session:
                last_checkin_summary = last_session.weekly_summary
        except Exception as e:
            logger.warning(f"Could not load last check-in: {e}")
        
        return {
            **state,
            "cycle_day": cycle_info.get("cycle_day"),
            "cycle_phase": cycle_info.get("phase"),
            "primary_hormone": cycle_info.get("primary_hormone"),
            "recent_symptoms": recent_symptoms,
            "recent_completions": completions,
            "last_checkin_summary": last_checkin_summary,
            "unified_context": unified_ctx,
            "formatted_context": formatted_ctx,
            "phase": "init"
        }
    except Exception as e:
        logger.error(f"Error loading user context: {e}")
        return {**state, "error": str(e), "unified_context": {}, "formatted_context": ""}


async def generate_greeting(state: WeeklyCheckInState) -> WeeklyCheckInState:
    """Create warm, cycle-aware opening message with full user context."""
    
    # Safe access with defaults
    completions = state.get("recent_completions", {})
    completed = completions.get("completed", 0)
    total = completions.get("total", 0)
    formatted_ctx = state.get("formatted_context", "")
    
    prompt = f"""You are Dr. Auvra conducting a weekly check-in.

======================================================================
COMPLETE USER CONTEXT (Use this to personalize your greeting!)
======================================================================
{formatted_ctx}

======================================================================
THIS WEEK'S SUMMARY
======================================================================
- Cycle Phase: {state.get('cycle_phase', 'Unknown')} (Day {state.get('cycle_day', '?')})
- Primary Hormone to Support: {state.get('primary_hormone', 'Unknown')}
- Recent Symptoms (past week): {len(state.get('recent_symptoms', []))} logged
- Action Plan Completions: {completed}/{total}
- Last Check-in Summary: {state.get('last_checkin_summary', 'No previous check-in')}

Create a personalized greeting that:
1. References their name if you know it
2. Mentions their current cycle phase naturally
3. Acknowledges something specific from their recent activity (completed actions, symptoms logged)
4. Transitions warmly into asking how they've been
5. Keep it 2-3 sentences, conversational and warm

Do NOT give a generic greeting - make it feel like you KNOW this user!
"""
    
    greeting = await call_llm(prompt, model="gpt-4o-mini")
    
    return {
        **state,
        "messages": [{"role": "assistant", "content": greeting}],
        "phase": "questioning"
    }


async def generate_next_question(state: WeeklyCheckInState) -> WeeklyCheckInState:
    """Dynamically generate personalized next question with full user context."""
    
    question_count = state.get("question_count", 0)
   
    # Hard stop at 4 questions - set should_complete and exit
    if question_count >= 4:
        return {**state, "should_complete": True, "phase": "complete"}
    
    # Get unified context for personalization
    formatted_ctx = state.get("formatted_context", "")
    
    # Generate question with LLM
    recent_messages = state["messages"][-6:] if len(state["messages"]) > 6 else state["messages"]
    
    prompt = f"""Generate the BEST personalized next question for weekly check-in.

======================================================================
COMPLETE USER CONTEXT (Use this to ask RELEVANT questions!)
======================================================================
{formatted_ctx}

======================================================================
CONVERSATION CONTEXT
======================================================================
Conversation So Far:
{recent_messages}

Questions Asked So Far: {question_count}/4 (MAX: 4, target: 3-4)
Topics Covered: {state.get('topics_covered', [])}

Recent Symptoms: {state.get('recent_symptoms', [])}
Last Week Summary: {state.get('last_checkin_summary', 'None')}

======================================================================
QUESTION GUIDELINES
======================================================================
1. **PERSONALIZATION**: Ask about things specific to THIS user:
   - Their diagnosed conditions (if any)
   - Symptoms they've logged before
   - Actions they completed or skipped this week
   - Things they mentioned in past conversations
   
2. **VARIETY**: Ask about areas NOT covered: symptoms, triggers, mood, energy, action_feedback

3. **CYCLE-AWARE**: Reference their current cycle phase if relevant

4. If at question 3, prepare for natural wrap-up
5. If at question 4, this MUST be final question

Generate tap_options that are SPECIFIC to this user, not generic.
Example for someone with PCOS: ["Bloating was rough", "Cravings were intense", "Energy was low", "Something else..."]
Example for someone with stress: ["Work stress", "Sleep issues", "Anxiety", "Something else..."]

Output JSON:
{{
  "question_text": "...",
  "question_category": "symptoms|triggers|relief|action_feedback",
  "tap_options": ["option1 specific to user", "option2 specific to user", "option3 specific to user", "Something else..."],
  "is_final_question": {str(question_count >= 3).lower()}
}}
"""
    
    try:
        question_data = await call_llm_structured(prompt, response_model=NextQuestion)
        
        return {
            **state,
            "current_question": question_data.model_dump(),
            "tap_options": question_data.tap_options,
            "messages": state["messages"] + [{
                "role": "assistant",
                "content": question_data.question_text
            }],
            "phase": "questioning"
        }
    except Exception as e:
        logger.error(f"Error generating question: {e}")
        # Fallback question
        return {
            **state,
            "current_question": {
                "question_text": "How have you been feeling this week?",
                "question_category": "symptoms",
                "tap_options": ["Great", "Okay", "Not so good", "Something else..."],
                "is_final_question": False
            },
            "tap_options": ["Great", "Okay", "Not so good", "Something else..."],
            "messages": state["messages"] + [{
                "role": "assistant",
                "content": "How have you been feeling this week?"
            }],
            "phase": "questioning"
        }


async def process_user_input(state: WeeklyCheckInState) -> WeeklyCheckInState:
    """Process user input - handles both tap and text."""
    
    user_input = state.get("user_input", "")
    input_mode = state.get("input_mode", "tap")
    
    if not user_input:
        return {**state, "error": "No user input provided"}
    
    # Detect "I want to type instead" intent via structured LLM (no keyword/regex routing).
    wants_text_input = False
    if input_mode == "tap":
        class TapToTypeIntent(BaseModel):
            wants_text_input: bool

        try:
            intent_result = await call_llm_structured(
                f"""Classify whether this tap response means the user wants to type a custom answer.
Response: "{user_input}"
Return JSON: {{"wants_text_input": true|false}}""",
                response_model=TapToTypeIntent,
            )
            wants_text_input = bool(intent_result.wants_text_input)
        except Exception:
            wants_text_input = False

    if input_mode == "tap" and wants_text_input:
        return {
            **state,
            "input_mode": "type",
            "messages": state["messages"] + [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": "Please type your answer:"}
            ],
            "phase": "questioning"
        }
    
    # Get current question category
    current_q = state.get("current_question", {})
    question_cat = current_q.get("question_category", "general")
    
    # For text input, parse with LLM
    parsed_insights = []
    if input_mode in ["type", "yap"]:
        try:
            parse_prompt = f"""Parse user's answer to extract structured information.

Question: {current_q.get('question_text', '')}
Answer: {user_input}
Context: Cycle Phase = {state.get('cycle_phase')}

Extract:
1. Key symptoms mentioned (with severity if stated)
2. Triggers/factors (stress, sleep, diet, exercise)
3. Action plan feedback
4. Mood/energy changes

Output JSON:
{{
  "key_points": ["point1", "point2"],
  "insights": [{{"type": "symptom|trigger|relief", "description": "..."}}],
  "extracted_symptoms": ["symptom1", "symptom2"]
}}
"""
            parsed = await call_llm_structured(parse_prompt, response_model=ParsedAnswer)
            parsed_insights = parsed.insights
        except Exception as e:
            logger.warning(f"Failed to parse answer: {e}")
    
    # Store answer
    answers = state.get("answers_collected", {}).copy()
    answers[question_cat] = {
        "raw": user_input,
        "parsed_insights": parsed_insights
    }
    
    # Update topics
    topics = state.get("topics_covered", [])[:]
    if question_cat not in topics:
        topics.append(question_cat)
    
    # Update insights
    all_insights = state.get("insights", []) + parsed_insights
    
    # Increment question count
    question_count = state.get("question_count", 0) + 1
    
    return {
        **state,
        "answers_collected": answers,
        "topics_covered": topics,
        "insights": all_insights,
        "question_count": question_count,
        "messages": state["messages"] + [{"role": "user", "content": user_input}],
        "user_input": None,  # Clear for next iteration
        "phase": "processing_input"
    }


def check_should_complete(state: WeeklyCheckInState) -> WeeklyCheckInState:
    """Determine if conversation should end. PURE FUNCTION - no side effects."""
    
    question_count = state.get("question_count", 0)
    
    # Hard limit: 4 questions
    if question_count >= 4:
        return {**state, "should_complete": True}
    
    # Soft completion: 3 questions + sufficient coverage
    if question_count >= 3:
        required_topics = ["symptoms", "triggers", "relief", "action_feedback"]
        covered = state.get("topics_covered", [])
        
        # If covered at least 2 required topics, can complete
        if len(set(required_topics) & set(covered)) >= 2:
            return {**state, "should_complete": True}
    
    return {**state, "should_complete": False}


async def generate_summary(state: WeeklyCheckInState) -> WeeklyCheckInState:
    """Create insights for insights page."""
    
    prompt = f"""Create a weekly check-in summary for insights page.

Week's Answers:
{json.dumps(state.get('answers_collected', {}), indent=2)}

User Context:
- Cycle Phase: {state.get('cycle_phase')}
- Primary Hormone: {state.get('primary_hormone')}

Generate:
1. **Summary** (3-4 sentences): This week overview with cycle context
2. **Key Insights** (2-3 bullets):
   - Patterns noticed
   - Cycle correlations
   - Action plan impact
3. **Severity Trend**: "improving"|"stable"|"worsening"
4. **Identified Triggers**: List of triggers mentioned
5. **Relief Factors**: What helped

Output JSON:
{{
  "summary": "...",
  "key_insights": ["...", "...", "..."],
  "severity_trend": "...",
  "triggers": ["stress", "poor sleep"],
  "relief_factors": ["yoga", "walnuts"]
}}
"""
    
    try:
        summary_data = await call_llm_structured(prompt, response_model=WeeklySummary)
        
        return {
            **state,
            "summary": summary_data.summary,
            "insights": state.get("insights", []) + [
                {"type": "weekly_insight", "description": insight}
                for insight in summary_data.key_insights
            ],
            "severity_trend": summary_data.severity_trend,
            "identified_triggers": summary_data.triggers,
            "identified_relief_factors": summary_data.relief_factors,
            "phase": "complete"
        }
    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        return {
            **state,
            "summary": "Weekly check-in completed. Thank you for sharing!",
            "phase": "complete",
            "error": str(e)
        }


async def save_to_insights_page(state: WeeklyCheckInState) -> WeeklyCheckInState:
    """Save weekly check-in to database for insights page."""
    try:
        db = next(get_db())
        
        # Create WeeklyCheckIn record
        # NOTE: This requires WeeklyCheckInSession model to exist in database
        # For now, we log what would be saved
        
        check_in_data = {
            "id": state["check_in_id"],
            "user_id": state["user_id"],
            "check_in_date": state.get("scheduled_date", date.today()).isoformat(),
            "summary": state.get("summary", ""),
            "insights": json.dumps(state.get("insights", [])),
            "severity_trend": state.get("severity_trend"),
            "triggers": json.dumps(state.get("identified_triggers", [])),
            "relief_factors": json.dumps(state.get("identified_relief_factors", [])),
            "answers": json.dumps(state.get("answers_collected", {})),
            "created_at": datetime.utcnow().isoformat()
        }
        
        logger.info(f"Saving check-in to database: {check_in_data.get('check_in_id')}")
        
        # Save to database using WeeklyCheckInSession model
        from datetime import date as date_type
        weekly_session = WeeklyCheckInSession(
            uid=state["user_id"],
            session_date=date_type.today(),
            questions_asked=state.get("answers_collected", []),
            question_count=state.get("question_count", 0),
            topics_covered=state.get("topics_covered", []),
            weekly_summary=state.get("summary", ""),
            insights={
                "severity_trend": state.get("severity_trend"),
                "triggers": state.get("identified_triggers", []),
                "relief_factors": state.get("identified_relief_factors", [])
            },
            cycle_day=state.get("cycle_day"),
            cycle_phase=state.get("cycle_phase"),
            is_complete=True,
            completed_at=datetime.utcnow()
        )
        db.add(weekly_session)
        db.commit()
        logger.info(f"✅ Saved weekly check-in to DB: {weekly_session.id}")
        
        return {**state, "phase": "complete"}
        
    except Exception as e:
        logger.error(f"Error saving check-in: {e}")
        return {**state, "error": str(e), "phase": "complete"}


# ═══════════════════════════════════════════════════════════════════
# CONDITIONAL EDGE ROUTING (PURE FUNCTIONS - NO STATE MUTATION)
# ═══════════════════════════════════════════════════════════════════

def route_after_input_processing(state: WeeklyCheckInState) -> str:
    """Route based on should_complete flag. PURE FUNCTION."""
    if state.get("should_complete", False):
        return "generate_summary"
    return "generate_next_question"


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - START FLOW (Invocation 1)
# ═══════════════════════════════════════════════════════════════════

def create_start_graph():
    """Graph for STARTING a weekly check-in (load context + generate greeting + first question)."""
    
    workflow = StateGraph(WeeklyCheckInState)
    
    # Add nodes
    workflow.add_node("load_user_context", load_user_context)
    workflow.add_node("generate_greeting", generate_greeting)
    workflow.add_node("generate_next_question", generate_next_question)
    
    # Set entry point
    workflow.set_entry_point("load_user_context")
    
    # Add edges
    workflow.add_edge("load_user_context", "generate_greeting")
    workflow.add_edge("generate_greeting", "generate_next_question")
    workflow.add_edge("generate_next_question", END)  # PAUSE - Wait for user input
    
    return workflow.compile()


# ═══════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION - CONTINUE FLOW (Invocation 2+)
# ═══════════════════════════════════════════════════════════════════

def create_continue_graph():
    """Graph for CONTINUING a weekly check-in (process input + decide next step)."""
    
    workflow = StateGraph(WeeklyCheckInState)
    
    # Add nodes
    workflow.add_node("process_user_input", process_user_input)
    workflow.add_node("check_should_complete", check_should_complete)
    workflow.add_node("generate_next_question", generate_next_question)
    workflow.add_node("generate_summary", generate_summary)
    workflow.add_node("save_to_insights_page", save_to_insights_page)
    
    # Set entry point
    workflow.set_entry_point("process_user_input")
    
    # Process input → check completion
    workflow.add_edge("process_user_input", "check_should_complete")
    
    # Completion check routing
    workflow.add_conditional_edges(
        "check_should_complete",
        route_after_input_processing,
        {
            "generate_summary": "generate_summary",
            "generate_next_question": "generate_next_question"
        }
    )
    
    # Next question → END (wait for more input)
    workflow.add_edge("generate_next_question", END)
    
    # Summary → Save → END
    workflow.add_edge("generate_summary", "save_to_insights_page")
    workflow.add_edge("save_to_insights_page", END)
    
    return workflow.compile()


# Compile graphs
weekly_checkin_start_graph = create_start_graph()
weekly_checkin_continue_graph = create_continue_graph()


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API - For use by API endpoints
# ═══════════════════════════════════════════════════════════════════

async def start_weekly_checkin(user_id: str) -> WeeklyCheckInState:
    """Start a new weekly check-in session."""
    state = create_initial_state(user_id)
    result = await weekly_checkin_start_graph.ainvoke(state)
    return result


async def continue_weekly_checkin(
    state: WeeklyCheckInState,
    user_input: str,
    input_mode: str = "tap"
) -> WeeklyCheckInState:
    """Continue an existing weekly check-in with user's answer."""
    updated_state = {
        **state,
        "user_input": user_input,
        "input_mode": input_mode
    }
    result = await weekly_checkin_continue_graph.ainvoke(updated_state)
    return result
