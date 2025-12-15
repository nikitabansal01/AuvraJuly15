"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - LangGraph Agent
═══════════════════════════════════════════════════════════════════════════════
The brain of the chatbot - a LangGraph StateGraph that orchestrates:
- Context-aware routing to specialized agents
- Tool calling for actions
- Memory management
- Safety checks
- Response generation

Architecture:
┌─────────────────┐
│  Entry Router   │ ──> Determines conversation context
└────────┬────────┘
         │
    ┌────┴────┐
    │ Router  │ ──> Routes to appropriate agent
    └────┬────┘
         │
   ┌─────┴─────┐────────┬────────────┬────────────┐
   ▼           ▼        ▼            ▼            ▼
┌──────┐   ┌──────┐  ┌──────┐   ┌──────┐    ┌──────┐
│ Care │   │Symp- │  │Person│   │ Know │    │Safety│
│ Plan │   │tom   │  │alize │   │ Body │    │Check │
│Agent │   │Agent │  │Agent │   │Agent │    │      │
└──┬───┘   └──┬───┘  └──┬───┘   └──┬───┘    └──┬───┘
   │          │         │          │           │
   └──────────┴─────────┴──────────┴───────────┘
                        │
                   ┌────┴────┐
                   │Response │ ──> Format response for frontend
                   │Former   │
                   └─────────┘
"""

import logging
from typing import TypedDict, Annotated, Sequence, List, Dict, Any, Optional, Literal
from datetime import datetime
import operator

from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.models.chat_models import (
    ConversationContext, ResponseType, InputMode,
    ChatMessageResponse, SliderConfig, ChatAction
)
from app.services.chat.tools import get_tools_by_context, check_emergency_keywords

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# STATE DEFINITION
# ═══════════════════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    """State that flows through the graph."""
    # Core conversation
    messages: Annotated[Sequence[BaseMessage], operator.add]
    user_id: str
    session_id: str  # UUID string
    
    # Context
    conversation_context: str
    patient_profile: Dict[str, Any]
    todays_plan: Dict[str, Any]
    recent_summary: Dict[str, Any]
    memory_context: Dict[str, Any]
    
    # Input
    input_mode: str  # tap, yap, type
    current_input: str
    
    # Safety
    safety_check: Dict[str, Any]
    
    # Response building
    response_type: str
    choices: Optional[List[str]]
    slider_config: Optional[Dict[str, Any]]
    actions: Optional[List[Dict[str, Any]]]
    
    # Flow control
    next_step: str
    current_agent: str
    tool_calls: List[str]


# ═══════════════════════════════════════════════════════════════════════════════
# LLM SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def get_llm(model: str = "gpt-4o", temperature: float = 0.7):
    """Get configured LLM instance."""
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        openai_api_key=settings.OPENAI_API_KEY
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════

BASE_SYSTEM_PROMPT = """You are AUVRA, a warm and knowledgeable women's health companion. 
You speak like a supportive friend who also happens to be a hormone expert.

PERSONALITY:
- Warm and empathetic, but professional
- Use occasional emojis (1-2 per response max) for warmth 💜
- Direct and clear, avoid being overly chatty
- Celebrate small wins enthusiastically
- Address concerns with understanding before solutions

COMMUNICATION STYLE:
- Use "you" and "your" - make it personal
- Avoid clinical jargon unless user uses it first
- Ask one question at a time
- Keep responses concise (2-4 sentences typical)
- Use user's name occasionally if known

KNOWLEDGE:
- You have complete access to the user's health profile, cycle data, and history
- You understand hormone fluctuations throughout the menstrual cycle
- You can modify their daily action plan based on conversation
- You track their symptoms and can identify patterns

BOUNDARIES:
- You are NOT a doctor - always recommend professional consultation for medical concerns
- Don't diagnose conditions
- Don't recommend medications
- For emergencies, immediately direct to emergency services

CURRENT USER CONTEXT:
{patient_context}

TODAY'S PLAN SUMMARY:
{plan_context}

RECENT ACTIVITY:
{recent_context}
"""

CARE_PLAN_AGENT_PROMPT = """You are helping the user manage their daily wellness action plan.

GOALS:
- Help them understand what's in their plan for today
- Allow them to complete, skip, or reschedule tasks
- Provide alternatives when they can't do something
- Celebrate completions and maintain motivation

When user wants to modify a task:
1. First acknowledge their situation with empathy
2. Use tools to make the change
3. Offer an alternative if appropriate
4. Encourage them

Remember: The plan is personalized to their hormone needs and cycle phase.
"""

SYMPTOM_AGENT_PROMPT = """You are helping the user track and understand their symptoms.

GOALS:
- Help log symptoms with appropriate severity
- Identify patterns and triggers
- Provide phase-appropriate context
- Suggest relevant lifestyle adjustments

SLIDER GUIDANCE:
When asking about symptom severity:
- 1-3: Mild (noticeable but not bothersome)
- 4-6: Moderate (affecting daily activities)
- 7-9: Severe (significantly impacting life)

Always ask about contributing factors after logging severity.
Connect symptoms to cycle phase when relevant.
"""

PERSONALISE_AGENT_PROMPT = """You are helping the user update their health preferences and profile.

GOALS:
- Understand their changing health goals
- Update preferences naturally through conversation
- Explain how changes affect their personalized plan
- Make them feel heard and in control

Ask clarifying questions to understand the full picture before making changes.
"""

KNOW_BODY_AGENT_PROMPT = """You are an educational health guide helping the user understand their body.

GOALS:
- Answer health questions accurately using knowledge base
- Explain complex concepts simply
- Connect information to their specific situation
- Always add appropriate disclaimers

Use the search_health_knowledge tool to find accurate information.
Cite sources when available.
Always remind them to consult a professional for personal medical advice.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def safety_check_node(state: AgentState) -> AgentState:
    """Check for emergency or urgent keywords."""
    current_input = state.get("current_input", "")
    
    safety_result = check_emergency_keywords(current_input)
    
    return {
        **state,
        "safety_check": safety_result,
        "next_step": "emergency_response" if safety_result["is_emergency"] else "route"
    }


async def emergency_response_node(state: AgentState) -> AgentState:
    """Handle emergency situations."""
    safety_check = state.get("safety_check", {})
    
    emergency_message = AIMessage(content=safety_check.get(
        "message",
        "If you're experiencing a medical emergency, please call 911 or your local emergency services immediately."
    ))
    
    return {
        **state,
        "messages": state["messages"] + [emergency_message],
        "response_type": "text",
        "next_step": "end"
    }


async def router_node(state: AgentState) -> AgentState:
    """Route to appropriate agent based on context and input."""
    context = state.get("conversation_context", "care_plan_modal")
    current_input = state.get("current_input", "").lower()
    
    # Keyword-based routing override
    symptom_keywords = ["feel", "pain", "cramp", "bloat", "tired", "mood", "headache", "symptom"]
    education_keywords = ["why", "what is", "how does", "explain", "tell me about", "learn"]
    plan_keywords = ["plan", "task", "skip", "done", "complete", "reschedule", "assignment"]
    
    # Determine agent
    if any(kw in current_input for kw in symptom_keywords) and context != "care_plan_modal":
        agent = "symptom_agent"
    elif any(kw in current_input for kw in education_keywords):
        agent = "know_body_agent"
    elif any(kw in current_input for kw in plan_keywords):
        agent = "care_plan_agent"
    else:
        # Default based on conversation context
        agent_map = {
            "care_plan_modal": "care_plan_agent",
            "symptom_checkin": "symptom_agent",
            "personalise": "personalise_agent",
            "know_body": "know_body_agent"
        }
        agent = agent_map.get(context, "care_plan_agent")
    
    return {
        **state,
        "current_agent": agent,
        "next_step": agent
    }


async def care_plan_agent_node(state: AgentState) -> AgentState:
    """Handle care plan conversations."""
    llm = get_llm()
    tools = get_tools_by_context("care_plan_modal")
    llm_with_tools = llm.bind_tools(tools)
    
    # Build context
    patient = state.get("patient_profile", {})
    plan = state.get("todays_plan", {})
    
    system_prompt = BASE_SYSTEM_PROMPT.format(
        patient_context=_format_patient_context(patient),
        plan_context=_format_plan_context(plan),
        recent_context=_format_recent_context(state.get("recent_summary", {}))
    ) + "\n\n" + CARE_PLAN_AGENT_PROMPT
    
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
    
    response = await llm_with_tools.ainvoke(messages)
    
    # Check for tool calls
    tool_calls = []
    if hasattr(response, 'tool_calls') and response.tool_calls:
        tool_calls = [tc['name'] for tc in response.tool_calls]
    
    return {
        **state,
        "messages": state["messages"] + [response],
        "tool_calls": tool_calls,
        "next_step": "process_tools" if tool_calls else "format_response"
    }


async def symptom_agent_node(state: AgentState) -> AgentState:
    """Handle symptom tracking conversations."""
    llm = get_llm()
    tools = get_tools_by_context("symptom_checkin")
    llm_with_tools = llm.bind_tools(tools)
    
    patient = state.get("patient_profile", {})
    
    system_prompt = BASE_SYSTEM_PROMPT.format(
        patient_context=_format_patient_context(patient),
        plan_context=_format_plan_context(state.get("todays_plan", {})),
        recent_context=_format_recent_context(state.get("recent_summary", {}))
    ) + "\n\n" + SYMPTOM_AGENT_PROMPT
    
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
    
    response = await llm_with_tools.ainvoke(messages)
    
    # Detect if we need slider
    response_type = "text"
    slider_config = None
    
    if "how severe" in response.content.lower() or "scale of" in response.content.lower():
        response_type = "slider"
        slider_config = {
            "min": 1,
            "max": 9,
            "step": 1,
            "labels": {
                "1": "Barely there",
                "5": "Moderate",
                "9": "Severe"
            }
        }
    
    tool_calls = []
    if hasattr(response, 'tool_calls') and response.tool_calls:
        tool_calls = [tc['name'] for tc in response.tool_calls]
    
    return {
        **state,
        "messages": state["messages"] + [response],
        "response_type": response_type,
        "slider_config": slider_config,
        "tool_calls": tool_calls,
        "next_step": "process_tools" if tool_calls else "format_response"
    }


async def personalise_agent_node(state: AgentState) -> AgentState:
    """Handle personalization conversations."""
    llm = get_llm()
    tools = get_tools_by_context("personalise")
    llm_with_tools = llm.bind_tools(tools)
    
    patient = state.get("patient_profile", {})
    
    system_prompt = BASE_SYSTEM_PROMPT.format(
        patient_context=_format_patient_context(patient),
        plan_context=_format_plan_context(state.get("todays_plan", {})),
        recent_context=_format_recent_context(state.get("recent_summary", {}))
    ) + "\n\n" + PERSONALISE_AGENT_PROMPT
    
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
    
    response = await llm_with_tools.ainvoke(messages)
    
    tool_calls = []
    if hasattr(response, 'tool_calls') and response.tool_calls:
        tool_calls = [tc['name'] for tc in response.tool_calls]
    
    return {
        **state,
        "messages": state["messages"] + [response],
        "tool_calls": tool_calls,
        "next_step": "process_tools" if tool_calls else "format_response"
    }


async def know_body_agent_node(state: AgentState) -> AgentState:
    """Handle educational conversations."""
    llm = get_llm()
    tools = get_tools_by_context("know_body")
    llm_with_tools = llm.bind_tools(tools)
    
    patient = state.get("patient_profile", {})
    
    system_prompt = BASE_SYSTEM_PROMPT.format(
        patient_context=_format_patient_context(patient),
        plan_context=_format_plan_context(state.get("todays_plan", {})),
        recent_context=_format_recent_context(state.get("recent_summary", {}))
    ) + "\n\n" + KNOW_BODY_AGENT_PROMPT
    
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
    
    response = await llm_with_tools.ainvoke(messages)
    
    tool_calls = []
    if hasattr(response, 'tool_calls') and response.tool_calls:
        tool_calls = [tc['name'] for tc in response.tool_calls]
    
    return {
        **state,
        "messages": state["messages"] + [response],
        "tool_calls": tool_calls,
        "next_step": "process_tools" if tool_calls else "format_response"
    }


async def process_tools_node(state: AgentState) -> AgentState:
    """Process tool calls and get results."""
    # Tool processing is handled by LangGraph's ToolNode
    # This node just manages the flow after tools are called
    return {
        **state,
        "next_step": state["current_agent"]  # Return to agent with tool results
    }


async def format_response_node(state: AgentState) -> AgentState:
    """Format the final response for the frontend."""
    messages = state["messages"]
    last_message = messages[-1] if messages else None
    
    if not last_message:
        return state
    
    content = last_message.content if hasattr(last_message, 'content') else str(last_message)
    
    # Detect choice buttons needed
    choices = None
    if "?" in content:
        # Smart choice generation based on context
        choices = _generate_choices(content, state)
    
    # Detect actions
    actions = []
    tool_calls = state.get("tool_calls", [])
    for tool in tool_calls:
        if tool in ["complete_assignment", "skip_assignment", "reschedule_assignment"]:
            actions.append({
                "type": "refresh_plan",
                "message": "Your plan has been updated"
            })
        elif tool == "log_symptom":
            actions.append({
                "type": "symptom_logged",
                "message": "Symptom recorded"
            })
    
    return {
        **state,
        "choices": choices,
        "actions": actions if actions else None,
        "next_step": "end"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _format_patient_context(patient: Dict[str, Any]) -> str:
    """Format patient profile for prompt."""
    if not patient:
        return "No patient profile available"
    
    parts = []
    if patient.get("name"):
        parts.append(f"Name: {patient['name']}")
    if patient.get("age"):
        parts.append(f"Age: {patient['age']}")
    if patient.get("phase"):
        parts.append(f"Current Phase: {patient['phase']} (Day {patient.get('cycle_day', '?')})")
    if patient.get("top_concern"):
        parts.append(f"Top Concern: {patient['top_concern']}")
    if patient.get("primary_hormone"):
        parts.append(f"Primary Hormone Focus: {patient['primary_hormone']}")
    
    return "\n".join(parts) if parts else "Limited profile data"


def _format_plan_context(plan: Dict[str, Any]) -> str:
    """Format today's plan for prompt."""
    if not plan:
        return "No plan loaded"
    
    total = plan.get("total_assignments", 0)
    completed = plan.get("completed_assignments", 0)
    rate = plan.get("completion_rate", 0)
    
    return f"Today: {completed}/{total} completed ({rate*100:.0f}%)"


def _format_recent_context(recent: Dict[str, Any]) -> str:
    """Format recent activity for prompt."""
    if not recent:
        return "No recent activity"
    
    parts = []
    
    symptoms = recent.get("symptoms_reported", [])
    if symptoms:
        symptom_names = [s["type"] for s in symptoms[:3]]
        parts.append(f"Recent symptoms: {', '.join(symptom_names)}")
    
    completions = recent.get("completions", {})
    if completions:
        rate = completions.get("rate", 0)
        parts.append(f"7-day completion rate: {rate*100:.0f}%")
    
    themes = recent.get("conversation_themes", [])
    if themes:
        parts.append(f"Recent topics: {', '.join(themes[:3])}")
    
    return "\n".join(parts) if parts else "First conversation"


def _generate_choices(content: str, state: AgentState) -> Optional[List[str]]:
    """Generate smart choice buttons based on context."""
    context = state.get("conversation_context", "")
    
    # Common patterns
    if "would you like" in content.lower():
        return ["Yes, please", "No, thanks", "Tell me more"]
    
    if "how are you feeling" in content.lower():
        return ["Great 🌟", "Okay", "Not great", "Need support"]
    
    if "skip" in content.lower() or "can't do" in content.lower():
        return ["Suggest alternative", "Skip for today", "Reschedule"]
    
    # Context-specific
    if context == "symptom_checkin":
        return ["Log a symptom", "Check my trends", "I'm feeling good"]
    
    if context == "care_plan_modal":
        return ["Show my plan", "Mark something done", "I need to skip something"]
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTING LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def should_continue(state: AgentState) -> Literal["safety", "route", "end"]:
    """Determine next step after safety check."""
    next_step = state.get("next_step", "route")
    if state.get("safety_check", {}).get("is_emergency"):
        return "emergency"
    return next_step


def route_to_agent(state: AgentState) -> str:
    """Route to appropriate agent."""
    return state.get("current_agent", "care_plan_agent")


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

def create_chat_graph():
    """Create the LangGraph StateGraph for the chatbot."""
    
    # Initialize graph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("emergency_response", emergency_response_node)
    workflow.add_node("router", router_node)
    workflow.add_node("care_plan_agent", care_plan_agent_node)
    workflow.add_node("symptom_agent", symptom_agent_node)
    workflow.add_node("personalise_agent", personalise_agent_node)
    workflow.add_node("know_body_agent", know_body_agent_node)
    workflow.add_node("format_response", format_response_node)
    
    # Set entry point
    workflow.set_entry_point("safety_check")
    
    # Add edges
    workflow.add_conditional_edges(
        "safety_check",
        lambda state: "emergency_response" if state.get("safety_check", {}).get("is_emergency") else "router"
    )
    
    workflow.add_edge("emergency_response", END)
    
    workflow.add_conditional_edges(
        "router",
        route_to_agent,
        {
            "care_plan_agent": "care_plan_agent",
            "symptom_agent": "symptom_agent",
            "personalise_agent": "personalise_agent",
            "know_body_agent": "know_body_agent"
        }
    )
    
    # Agent to format response
    workflow.add_edge("care_plan_agent", "format_response")
    workflow.add_edge("symptom_agent", "format_response")
    workflow.add_edge("personalise_agent", "format_response")
    workflow.add_edge("know_body_agent", "format_response")
    
    workflow.add_edge("format_response", END)
    
    # NOTE: Do not attach a checkpointer that msgpack-serializes state, because
    # request-scoped objects (e.g., SQLAlchemy Session) are not serializable.
    # If you later add durable memory, ensure the state remains fully serializable.
    app = workflow.compile()
    
    return app


# Create singleton instance
chat_graph = create_chat_graph()


async def run_chat_agent(
    user_id: str,
    session_id: str,
    message: str,
    conversation_context: str,
    input_mode: str,
    patient_profile: Dict[str, Any],
    todays_plan: Dict[str, Any],
    recent_summary: Dict[str, Any],
    memory_context: Dict[str, Any],
    db_session: Any = None
) -> Dict[str, Any]:
    """
    Run the chat agent with given inputs.
    
    Returns:
        Dict with response content, type, choices, actions, etc.
    """
    try:
        # Create initial state
        initial_state = AgentState(
            messages=[HumanMessage(content=message)],
            user_id=user_id,
            session_id=session_id,
            conversation_context=conversation_context,
            patient_profile=patient_profile,
            todays_plan=todays_plan,
            recent_summary=recent_summary,
            memory_context=memory_context,
            input_mode=input_mode,
            current_input=message,
            safety_check={},
            response_type="text",
            choices=None,
            slider_config=None,
            actions=None,
            next_step="safety",
            current_agent="",
            tool_calls=[]
        )
        
        # Run the graph
        config = {"configurable": {"thread_id": f"{user_id}_{session_id}"}}
        result = await chat_graph.ainvoke(initial_state, config)
        
        # Extract response
        messages = result.get("messages", [])
        last_ai_message = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai_message = msg
                break
        
        content = last_ai_message.content if last_ai_message else "I'm not sure how to help with that. Could you rephrase?"
        
        return {
            "content": content,
            "response_type": result.get("response_type", "text"),
            "choices": result.get("choices"),
            "slider_config": result.get("slider_config"),
            "actions": result.get("actions"),
            "tool_calls": result.get("tool_calls", []),
            "safety_check": result.get("safety_check", {})
        }
        
    except Exception as e:
        logger.error(f"Chat agent error: {str(e)}")
        return {
            "content": "I'm having trouble processing that right now. Could you try again?",
            "response_type": "text",
            "choices": ["Try again", "Contact support"],
            "error": str(e)
        }
