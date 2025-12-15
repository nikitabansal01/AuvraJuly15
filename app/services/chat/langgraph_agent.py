"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - LangGraph Agent (Simplified & Robust)
═══════════════════════════════════════════════════════════════════════════════
A simplified, working chatbot that doesn't rely on complex tool calling.
All context is pre-loaded and passed to the LLM directly.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_llm(model: str = "gpt-4o-mini", temperature: float = 0.7):
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
- You can help them understand and manage their daily action plan
- You track their symptoms and can identify patterns

BOUNDARIES:
- You are NOT a doctor - always recommend professional consultation for medical concerns
- Don't diagnose conditions
- Don't recommend medications
- For emergencies, immediately direct to emergency services

{context_section}
"""

CARE_PLAN_PROMPT = """
CURRENT CONVERSATION: Care Plan Discussion

You are helping the user manage their daily wellness action plan.

GOALS:
- Help them understand what's in their plan for today
- Acknowledge when they want to skip or change tasks
- Provide encouragement and motivation
- Celebrate completions

When user wants to modify a task:
1. Acknowledge their situation with empathy
2. Reassure them it's okay to adjust
3. Offer encouragement

Remember: The plan is personalized to their hormone needs and cycle phase.
"""

SYMPTOM_PROMPT = """
CURRENT CONVERSATION: Symptom Check-in

You are helping the user track and understand their symptoms.

GOALS:
- Help log symptoms with appropriate severity
- Provide phase-appropriate context
- Offer comfort and understanding
- Suggest relevant lifestyle adjustments

Connect symptoms to cycle phase when relevant.
Be supportive and understanding about how symptoms affect daily life.
"""

PERSONALISE_PROMPT = """
CURRENT CONVERSATION: Personalization

You are helping the user update their health preferences and profile.

GOALS:
- Understand their changing health goals
- Explain how their profile affects their personalized plan
- Make them feel heard and in control

Ask clarifying questions to understand the full picture.
"""

KNOW_BODY_PROMPT = """
CURRENT CONVERSATION: Health Education

You are an educational health guide helping the user understand their body.

GOALS:
- Answer health questions accurately
- Explain complex concepts simply
- Connect information to their specific situation
- Always add appropriate disclaimers

Always remind them to consult a professional for personal medical advice.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def check_emergency_keywords(message: str) -> Dict[str, Any]:
    """Check if message contains emergency keywords."""
    emergency_keywords = [
        "suicide", "kill myself", "end my life", "want to die",
        "severe bleeding", "hemorrhage", "can't breathe",
        "chest pain", "heart attack", "stroke symptoms",
        "overdose", "poisoning"
    ]
    
    urgent_keywords = [
        "emergency", "hospital", "911", "ambulance",
        "severe pain", "passing out", "fainted",
        "very dizzy", "heavy bleeding"
    ]
    
    message_lower = message.lower()
    
    for keyword in emergency_keywords:
        if keyword in message_lower:
            return {
                "is_emergency": True,
                "is_urgent": True,
                "message": "If you're having a medical emergency, please call 911 or your local emergency services immediately. You can also text HOME to 741741 (Crisis Text Line) if you need support."
            }
    
    for keyword in urgent_keywords:
        if keyword in message_lower:
            return {
                "is_emergency": False,
                "is_urgent": True,
                "message": "If this is a medical emergency, please seek immediate medical attention."
            }
    
    return {"is_emergency": False, "is_urgent": False, "message": None}


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

def format_context_section(
    patient_profile: Dict[str, Any],
    todays_plan: Dict[str, Any],
    recent_summary: Dict[str, Any],
    conversation_context: str
) -> str:
    """Format all context into a readable section for the LLM."""
    sections = []
    
    # Patient info
    if patient_profile:
        patient_parts = []
        if patient_profile.get("name"):
            patient_parts.append(f"Name: {patient_profile['name']}")
        if patient_profile.get("age"):
            patient_parts.append(f"Age: {patient_profile['age']}")
        if patient_profile.get("phase"):
            patient_parts.append(f"Current Phase: {patient_profile['phase']} (Day {patient_profile.get('cycle_day', '?')})")
        if patient_profile.get("top_concern"):
            patient_parts.append(f"Top Concern: {patient_profile['top_concern']}")
        if patient_profile.get("primary_hormone"):
            patient_parts.append(f"Primary Hormone Focus: {patient_profile['primary_hormone']}")
        
        if patient_parts:
            sections.append("PATIENT PROFILE:\n" + "\n".join(patient_parts))
    
    # Today's plan
    if todays_plan:
        total = todays_plan.get("total_assignments", 0)
        completed = todays_plan.get("completed_assignments", 0)
        rate = todays_plan.get("completion_rate", 0)
        
        plan_text = f"TODAY'S PLAN: {completed}/{total} completed ({rate*100:.0f}%)"
        
        # Add assignment names if available
        assignments = []
        for slot in ["morning", "afternoon", "evening", "anytime"]:
            slot_items = todays_plan.get(slot, [])
            if slot_items:
                for item in slot_items[:3]:  # Limit to 3 per slot
                    title = item.get("title", "Task")
                    status = "✓" if item.get("is_completed") else "○"
                    assignments.append(f"  {status} {title}")
        
        if assignments:
            plan_text += "\n" + "\n".join(assignments[:6])  # Max 6 items
        
        sections.append(plan_text)
    
    # Recent activity
    if recent_summary:
        recent_parts = []
        symptoms = recent_summary.get("symptoms_reported", [])
        if symptoms:
            symptom_names = [s.get("type", "unknown") for s in symptoms[:3]]
            recent_parts.append(f"Recent symptoms: {', '.join(symptom_names)}")
        
        completions = recent_summary.get("completions", {})
        if completions and completions.get("rate"):
            recent_parts.append(f"7-day completion rate: {completions['rate']*100:.0f}%")
        
        if recent_parts:
            sections.append("RECENT ACTIVITY:\n" + "\n".join(recent_parts))
    
    return "\n\n".join(sections) if sections else "First conversation with user"


def get_conversation_prompt(context: str) -> str:
    """Get the appropriate conversation prompt based on context."""
    prompts = {
        "care_plan_modal": CARE_PLAN_PROMPT,
        "symptom_checkin": SYMPTOM_PROMPT,
        "personalise": PERSONALISE_PROMPT,
        "know_body": KNOW_BODY_PROMPT
    }
    return prompts.get(context, CARE_PLAN_PROMPT)


def generate_choices(content: str, context: str) -> Optional[List[str]]:
    """Generate smart choice buttons based on response content and context."""
    content_lower = content.lower()
    
    # Common patterns
    if "would you like" in content_lower:
        return ["Yes, please", "No, thanks", "Tell me more"]
    
    if "how are you feeling" in content_lower:
        return ["Great 🌟", "Okay", "Not great", "Need support"]
    
    if "skip" in content_lower or "can't do" in content_lower:
        return ["Suggest alternative", "Skip for today", "Reschedule"]
    
    # Context-specific defaults
    if context == "symptom_checkin":
        return ["Log a symptom", "Check my trends", "I'm feeling good"]
    
    if context == "care_plan_modal":
        return ["Show my plan", "Mark something done", "I need to skip something"]
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN CHAT FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

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
    db_session: Any = None  # Kept for API compatibility, not used in graph
) -> Dict[str, Any]:
    """
    Run the chat agent with given inputs.
    
    This is a simplified, robust implementation that:
    1. Checks for emergencies
    2. Builds context into the prompt
    3. Calls the LLM directly
    4. Returns formatted response
    """
    try:
        # 1. Safety check first
        safety_check = check_emergency_keywords(message)
        if safety_check["is_emergency"]:
            return {
                "content": safety_check["message"],
                "response_type": "text",
                "choices": ["I'm safe now", "Connect me to support"],
                "slider_config": None,
                "actions": None,
                "tool_calls": [],
                "safety_check": safety_check
            }
        
        # 2. Build the full prompt
        context_section = format_context_section(
            patient_profile, todays_plan, recent_summary, conversation_context
        )
        conversation_prompt = get_conversation_prompt(conversation_context)
        
        full_system_prompt = BASE_SYSTEM_PROMPT.format(context_section=context_section)
        full_system_prompt += "\n" + conversation_prompt
        
        # Add urgent disclaimer if needed
        if safety_check["is_urgent"]:
            full_system_prompt += f"\n\nIMPORTANT: {safety_check['message']}"
        
        # 3. Build messages
        messages = [
            SystemMessage(content=full_system_prompt),
            HumanMessage(content=message)
        ]
        
        # Add memory context if available
        if memory_context and memory_context.get("recent_messages"):
            # Insert recent conversation history before the current message
            history_messages = []
            for msg in memory_context.get("recent_messages", [])[-6:]:  # Last 6 messages
                if msg.get("role") == "user":
                    history_messages.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    history_messages.append(AIMessage(content=msg.get("content", "")))
            
            if history_messages:
                messages = [messages[0]] + history_messages + [messages[1]]
        
        # 4. Call the LLM
        llm = get_llm()
        response = await llm.ainvoke(messages)
        
        content = response.content if hasattr(response, 'content') else str(response)
        
        # 5. Generate response metadata
        response_type = "text"
        slider_config = None
        
        # Check if asking about severity
        if any(phrase in content.lower() for phrase in ["how severe", "scale of", "rate your", "1 to"]):
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
        
        # Generate choices
        choices = generate_choices(content, conversation_context)
        
        return {
            "content": content,
            "response_type": response_type,
            "choices": choices,
            "slider_config": slider_config,
            "actions": None,
            "tool_calls": [],
            "safety_check": safety_check
        }
        
    except Exception as e:
        logger.error(f"Chat agent error: {str(e)}", exc_info=True)
        return {
            "content": "I'm having trouble processing that right now. Could you try again?",
            "response_type": "text",
            "choices": ["Try again", "Contact support"],
            "slider_config": None,
            "actions": None,
            "tool_calls": [],
            "error": str(e)
        }


# For backward compatibility - these are no longer used but may be imported elsewhere
chat_graph = None
