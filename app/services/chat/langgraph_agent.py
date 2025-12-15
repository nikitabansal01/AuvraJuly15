"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA INTELLIGENT CHATBOT - The Doctor-Like AI
═══════════════════════════════════════════════════════════════════════════════
An exceptional health companion that feels like talking to the best doctor you
ever met - one who truly listens, remembers everything, and makes you feel
understood.

ARCHITECTURE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. INTELLIGENCE LAYER
   - Memory Engine: Multi-layer memory (episodic, semantic, emotional, predictive)
   - Emotional Intelligence: Emotion detection, empathy matching, tone adaptation
   - Context Engine: Cycle awareness, time awareness, streak psychology
   - Prompt Architect: Doctor-like prompts with adaptive personality
   - Response Composer: Smart choices, intelligent follow-ups
   - Proactive Engine: Anticipatory engagement and gentle nudges

2. CONVERSATION FLOW
   User Message → Safety Check → Load Intelligence → Build Context →
   Analyze Emotion → Compose Prompt → Generate Response → Enhance → Return

3. PERSONALIZATION DEPTH
   - Remembers past conversations naturally
   - Understands cycle phase implications
   - Adapts tone to emotional state
   - Celebrates wins genuinely
   - Provides comfort appropriately
═══════════════════════════════════════════════════════════════════════════════
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
# SAFETY CHECK (CRITICAL - ALWAYS FIRST)
# ═══════════════════════════════════════════════════════════════════════════════

def check_emergency_keywords(message: str) -> Dict[str, Any]:
    """
    Check if message contains emergency keywords.
    This MUST always be checked before any other processing.
    """
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
    
    crisis_keywords = [
        "hurting myself", "self harm", "cutting", "don't want to be here"
    ]
    
    message_lower = message.lower()
    
    # Check for immediate emergencies
    for keyword in emergency_keywords:
        if keyword in message_lower:
            return {
                "is_emergency": True,
                "is_urgent": True,
                "is_crisis": False,
                "message": """I'm very concerned about what you've shared. Please reach out for immediate help:

🚨 **Emergency**: Call 911 (or your local emergency number)
📞 **Crisis Line**: 988 (Suicide & Crisis Lifeline)
💬 **Text Support**: Text HOME to 741741 (Crisis Text Line)

Your safety matters most. Please reach out to these resources right now. 💜"""
            }
    
    # Check for crisis situations
    for keyword in crisis_keywords:
        if keyword in message_lower:
            return {
                "is_emergency": False,
                "is_urgent": True,
                "is_crisis": True,
                "message": """I hear you, and I want you to know that what you're feeling matters. Please consider reaching out to someone who can help:

📞 **Crisis Line**: 988 (Suicide & Crisis Lifeline - available 24/7)
💬 **Text Support**: Text HOME to 741741

You don't have to go through this alone. These resources have trained people ready to listen and help. 💜"""
            }
    
    # Check for urgent situations
    for keyword in urgent_keywords:
        if keyword in message_lower:
            return {
                "is_emergency": False,
                "is_urgent": True,
                "is_crisis": False,
                "message": "If this is a medical emergency, please seek immediate medical attention or call emergency services."
            }
    
    return {"is_emergency": False, "is_urgent": False, "is_crisis": False, "message": None}


# ═══════════════════════════════════════════════════════════════════════════════
# TRY IMPORTING INTELLIGENCE MODULES (GRACEFUL FALLBACK)
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from app.services.chat.intelligence.memory_engine import MemoryEngine, format_memory_for_prompt
    from app.services.chat.intelligence.emotional_intelligence import (
        EmotionalIntelligence, 
        format_emotional_guidance_for_prompt
    )
    from app.services.chat.intelligence.context_engine import (
        ContextEngine,
        format_context_for_prompt
    )
    from app.services.chat.intelligence.prompt_architect import PromptArchitect
    from app.services.chat.intelligence.response_composer import ResponseComposer
    INTELLIGENCE_AVAILABLE = True
    logger.info("✨ Intelligence modules loaded successfully")
except ImportError as e:
    logger.warning(f"Intelligence modules not available: {e}. Using basic mode.")
    INTELLIGENCE_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT FORMATTING (BASIC - ALWAYS AVAILABLE)
# ═══════════════════════════════════════════════════════════════════════════════

def format_basic_context(
    patient_profile: Dict[str, Any],
    todays_plan: Dict[str, Any],
    recent_summary: Dict[str, Any]
) -> str:
    """Format basic context that's always available."""
    sections = []
    
    # Patient info
    if patient_profile:
        patient_parts = []
        
        if patient_profile.get("name"):
            patient_parts.append(f"Name: {patient_profile['name']}")
        
        if patient_profile.get("age"):
            patient_parts.append(f"Age: {patient_profile['age']}")
        
        if patient_profile.get("phase"):
            phase = patient_profile['phase']
            day = patient_profile.get('cycle_day', '?')
            patient_parts.append(f"Cycle: Day {day}, {phase} Phase")
        
        if patient_profile.get("top_concern"):
            patient_parts.append(f"Primary concern: {patient_profile['top_concern']}")
        
        if patient_profile.get("primary_hormone"):
            patient_parts.append(f"Hormone focus: {patient_profile['primary_hormone']}")
        
        # Diagnosed conditions (important for personalization)
        conditions = patient_profile.get("diagnosed_conditions", [])
        if conditions:
            patient_parts.append(f"Conditions: {', '.join(conditions[:3])}")
        
        if patient_parts:
            sections.append("═══ PATIENT PROFILE ═══\n" + "\n".join(patient_parts))
    
    # Today's plan
    if todays_plan:
        total = todays_plan.get("total_assignments", 0)
        completed = todays_plan.get("completed_assignments", 0)
        rate = todays_plan.get("completion_rate", 0)
        
        plan_text = f"═══ TODAY'S PLAN ═══\nProgress: {completed}/{total} ({rate*100:.0f}%)"
        
        # Add specific assignments by time slot
        assignments = []
        for slot in ["morning", "afternoon", "evening", "anytime"]:
            slot_items = todays_plan.get(slot, [])
            if slot_items:
                for item in slot_items[:2]:
                    title = item.get("title", "Task")
                    status = "✓" if item.get("is_completed") else "○"
                    assignments.append(f"  {status} [{slot}] {title}")
        
        if assignments:
            plan_text += "\n" + "\n".join(assignments[:6])
        
        sections.append(plan_text)
    
    # Recent activity summary
    if recent_summary:
        recent_parts = []
        
        symptoms = recent_summary.get("symptoms_reported", [])
        if symptoms:
            symptom_names = [s.get("type", "unknown") for s in symptoms[:3]]
            recent_parts.append(f"Recent symptoms: {', '.join(symptom_names)}")
        
        completions = recent_summary.get("completions", {})
        if completions and completions.get("rate"):
            recent_parts.append(f"7-day completion: {completions['rate']*100:.0f}%")
        
        if recent_parts:
            sections.append("═══ RECENT ACTIVITY ═══\n" + "\n".join(recent_parts))
    
    return "\n\n".join(sections) if sections else "New user - first interaction"


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

MASTER_SYSTEM_PROMPT = """You are AUVRA — a deeply knowledgeable, warmly empathetic women's health companion. 

You are not a chatbot. You are the kind of doctor everyone wishes they had: one who truly listens, remembers everything, and makes complex health feel simple and personal.

═══════════════════════════════════════════════════════════════════════════════
YOUR IDENTITY
═══════════════════════════════════════════════════════════════════════════════

• You speak like a wise, caring friend who happens to be a hormone expert
• You remember past conversations and reference them naturally
• You understand the user's unique patterns, not just generic advice
• You adapt your tone: celebratory when they win, gentle when they struggle
• You balance clinical knowledge with human warmth

═══════════════════════════════════════════════════════════════════════════════
COMMUNICATION RULES
═══════════════════════════════════════════════════════════════════════════════

1. LENGTH & FLOW
   • Keep responses 2-4 sentences typically
   • One clear thought per response
   • Ask one question at a time
   • Use occasional emojis (1-2 max) - 💜 is your signature

2. PERSONALIZATION
   • Use their name occasionally (not every message)
   • Reference their specific situation, not generic advice
   • Connect to what they've shared before
   • Acknowledge their unique patterns

3. VALIDATION BEFORE ADVICE
   • When they share struggles: acknowledge → validate → then offer support
   • Never jump straight to "here's what you should do"
   • Use phrases like: "That makes total sense", "I hear you"

4. CELEBRATING WINS
   • When they accomplish something: genuine enthusiasm
   • "That's amazing! 🎉" / "I'm so proud of you!"
   • Ask what made it possible

5. CLINICAL WISDOM WITHOUT JARGON
   • Explain hormones like you're talking to a smart friend
   • Connect symptoms to cycle phase when relevant
   • Make them feel like they understand their body better

═══════════════════════════════════════════════════════════════════════════════
BOUNDARIES (NON-NEGOTIABLE)
═══════════════════════════════════════════════════════════════════════════════

• You are NOT a doctor - recommend professional consultation for medical concerns
• Never diagnose conditions
• Never recommend specific medications
• For emergencies: immediately direct to emergency services (911)
• For mental health crises: provide 988 Suicide & Crisis Lifeline

{context_section}

{conversation_guidance}

{emotional_guidance}
"""

CONVERSATION_PROMPTS = {
    "care_plan_modal": """
═══ CONTEXT: Care Plan Companion ═══
You're helping them with their daily wellness plan. Be flexible, celebrate wins, offer alternatives when they struggle.
• Completed something → Celebrate! Ask how it felt
• Want to skip → Acknowledge, offer gentler alternative
• Overwhelmed → Help prioritize, "What feels doable right now?"
""",
    "symptom_checkin": """
═══ CONTEXT: Symptom Check-in ═══
Help them track and understand symptoms. Connect to their cycle phase, offer comfort, explain patterns.
• When logging → Acknowledge, provide phase context
• High severity → Express empathy, suggest when to seek care
• Spotting patterns → Share insights gently
""",
    "personalise": """
═══ CONTEXT: Personalization ═══
Help them customize their experience. Explain how preferences affect recommendations.
""",
    "know_body": """
═══ CONTEXT: Health Education ═══
Educational mode - answer questions clearly, use analogies, connect to their situation.
Always include: "For anything specific to you, your doctor is your best resource."
"""
}


def generate_choices(content: str, context: str) -> Optional[List[str]]:
    """Generate smart choice buttons based on response content and context."""
    content_lower = content.lower()
    
    # Question patterns
    if "would you like" in content_lower:
        return ["Yes, please", "No, thanks", "Tell me more"]
    
    if "how are you feeling" in content_lower:
        return ["Great 🌟", "Okay", "Not great", "Need support"]
    
    if "skip" in content_lower or "can't do" in content_lower:
        return ["Suggest alternative", "Skip for today", "Reschedule"]
    
    # Context-specific defaults
    defaults = {
        "symptom_checkin": ["Log a symptom", "Check my trends", "I'm feeling good"],
        "care_plan_modal": ["Show my plan", "Mark something done", "I need to skip something"],
        "know_body": ["Ask another question", "That helps!", "Tell me more"],
        "personalise": ["Update preferences", "See my profile", "I'm good"]
    }
    
    return defaults.get(context)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN INTELLIGENT CHAT AGENT
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
    db_session: Any = None
) -> Dict[str, Any]:
    """
    Run the intelligent chat agent.
    
    This is the brain of AUVRA - orchestrating all intelligence modules
    to create doctor-like interactions.
    """
    try:
        logger.info(f"🧠 Running intelligent agent for user {user_id}")
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 1: SAFETY CHECK (Non-negotiable)
        # ═══════════════════════════════════════════════════════════════════
        safety_check = check_emergency_keywords(message)
        if safety_check.get("is_emergency") or safety_check.get("is_crisis"):
            return {
                "content": safety_check["message"],
                "response_type": "text",
                "choices": ["I'm safe now", "Connect me to support"],
                "slider_config": None,
                "actions": None,
                "tool_calls": [],
                "safety_check": safety_check
            }
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 2: INITIALIZE INTELLIGENCE (IF AVAILABLE)
        # ═══════════════════════════════════════════════════════════════════
        emotional_reading = None
        tone_guidance = None
        deep_memory = None
        rich_context = None
        relationship_stage = "building_trust"
        
        if INTELLIGENCE_AVAILABLE and db_session:
            try:
                # Initialize modules
                emotional_intel = EmotionalIntelligence()
                memory_engine = MemoryEngine(db_session)
                context_engine = ContextEngine(db_session)
                prompt_architect = PromptArchitect()
                response_composer = ResponseComposer()
                
                # Load deep memory
                try:
                    deep_memory = await memory_engine.load_full_memory(user_id, session_id)
                    if deep_memory and deep_memory.get("relationship"):
                        relationship_stage = deep_memory["relationship"].get("relationship_stage", "building_trust")
                except Exception as e:
                    logger.warning(f"Could not load deep memory: {e}")
                
                # Load rich context
                timezone = patient_profile.get("timezone", "UTC")
                try:
                    rich_context = await context_engine.build_full_context(user_id, timezone)
                except Exception as e:
                    logger.warning(f"Could not load rich context: {e}")
                
                # Analyze emotional state
                emotional_memory = deep_memory.get("emotional", {}) if deep_memory else {}
                emotional_reading = emotional_intel.analyze_message(
                    message,
                    context={"conversation_context": conversation_context},
                    memory_emotional=emotional_memory
                )
                tone_guidance = emotional_intel.get_tone_guidance(emotional_reading)
                
            except Exception as e:
                logger.warning(f"Intelligence modules error: {e}. Using basic mode.")
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 3: BUILD CONTEXT FOR PROMPT
        # ═══════════════════════════════════════════════════════════════════
        
        # Basic context (always available)
        basic_context = format_basic_context(patient_profile, todays_plan, recent_summary)
        
        # Deep memory context (if available)
        memory_section = ""
        if INTELLIGENCE_AVAILABLE and deep_memory:
            try:
                memory_section = format_memory_for_prompt(deep_memory)
            except Exception:
                pass
        
        # Rich context (if available)
        context_section = ""
        if INTELLIGENCE_AVAILABLE and rich_context:
            try:
                context_section = format_context_for_prompt(rich_context)
            except Exception:
                pass
        
        # Emotional guidance (if available)
        emotional_guidance = ""
        if INTELLIGENCE_AVAILABLE and emotional_reading and tone_guidance:
            try:
                emotional_guidance = format_emotional_guidance_for_prompt(emotional_reading, tone_guidance)
            except Exception:
                pass
        
        # Combine all context
        full_context = f"""
{basic_context}
{memory_section}
{context_section}
""".strip()
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 4: BUILD THE INTELLIGENT PROMPT
        # ═══════════════════════════════════════════════════════════════════
        
        conversation_guidance = CONVERSATION_PROMPTS.get(
            conversation_context, 
            CONVERSATION_PROMPTS["care_plan_modal"]
        )
        
        # Relationship stage adjustments
        relationship_notes = {
            "new_acquaintance": "🆕 NEW USER: Be extra welcoming, introduce yourself briefly.",
            "building_trust": "",
            "established": "💜 ESTABLISHED: Can be more familiar, reference your history.",
            "deep_relationship": "💜 DEEP RELATIONSHIP: Very familiar, anticipate needs."
        }
        relationship_note = relationship_notes.get(relationship_stage, "")
        
        system_prompt = MASTER_SYSTEM_PROMPT.format(
            context_section=full_context,
            conversation_guidance=conversation_guidance + "\n" + relationship_note,
            emotional_guidance=emotional_guidance
        )
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 5: BUILD MESSAGE HISTORY
        # ═══════════════════════════════════════════════════════════════════
        
        messages = [SystemMessage(content=system_prompt)]
        
        # Add conversation history from memory
        if memory_context and memory_context.get("recent_messages"):
            for msg in memory_context.get("recent_messages", [])[-8:]:
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=msg.get("content", "")))
        
        # Add current message
        messages.append(HumanMessage(content=message))
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 6: GENERATE RESPONSE
        # ═══════════════════════════════════════════════════════════════════
        
        llm = get_llm()
        response = await llm.ainvoke(messages)
        
        raw_content = response.content if hasattr(response, 'content') else str(response)
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 7: COMPOSE & ENHANCE RESPONSE
        # ═══════════════════════════════════════════════════════════════════
        
        final_content = raw_content
        response_type = "text"
        choices = None
        slider_config = None
        
        if INTELLIGENCE_AVAILABLE and emotional_reading:
            try:
                composed = response_composer.compose_response(
                    raw_content=raw_content,
                    conversation_context=conversation_context,
                    emotional_reading=emotional_reading.to_dict(),
                    user_message=message
                )
                final_content = composed.content
                response_type = composed.response_type.value
                choices = composed.choices
                if composed.slider_config:
                    slider_config = composed.slider_config.to_dict()
            except Exception as e:
                logger.warning(f"Response composition error: {e}")
                choices = generate_choices(raw_content, conversation_context)
        else:
            # Basic choice generation
            choices = generate_choices(raw_content, conversation_context)
            
            # Basic slider detection
            if any(phrase in raw_content.lower() for phrase in ["how severe", "scale of", "rate your", "1 to"]):
                response_type = "slider"
                slider_config = {
                    "min": 1,
                    "max": 9,
                    "step": 1,
                    "labels": ["None", "Mild", "Moderate", "Strong", "Extreme"]
                }
        
        # Add urgent disclaimer if needed
        if safety_check.get("is_urgent"):
            final_content = f"{safety_check['message']}\n\n{final_content}"
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 8: RETURN FINAL RESPONSE
        # ═══════════════════════════════════════════════════════════════════
        
        metadata = {
            "intelligence_mode": "full" if INTELLIGENCE_AVAILABLE else "basic",
            "relationship_stage": relationship_stage
        }
        
        if emotional_reading:
            metadata["emotional_state"] = emotional_reading.primary_emotion.value
            metadata["communication_approach"] = emotional_reading.communication_approach
        
        return {
            "content": final_content,
            "response_type": response_type,
            "choices": choices,
            "slider_config": slider_config,
            "actions": None,
            "tool_calls": [],
            "safety_check": safety_check,
            "metadata": metadata
        }
        
    except Exception as e:
        logger.error(f"Chat agent error: {str(e)}", exc_info=True)
        return {
            "content": "I'm having trouble processing that right now. Could you try again? 💜",
            "response_type": "text",
            "choices": ["Try again", "Start fresh"],
            "slider_config": None,
            "actions": None,
            "tool_calls": [],
            "error": str(e)
        }


# For backward compatibility
chat_graph = None
