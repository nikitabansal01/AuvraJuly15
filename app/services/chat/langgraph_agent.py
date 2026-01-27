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


# ═══════════════════════════════════════════════════════════════════════════════
# PERSONLISE UI BLOCK HELPERS (UNLOCK-GATED)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_unlocked_preference_types(user_id: str, db_session: Any) -> List[str]:
    """Return preference keys (Preferences API) that the user has unlocked via rewards."""
    try:
        from app.api.v1.endpoints.preferences import PREFERENCE_REWARD_MAP
        from app.services.reward_service import RewardService

        unlocked_ids = RewardService(db_session).get_unlocked_reward_ids(user_id)
        unlocked_prefs: List[str] = []
        for pref_type, reward_id in PREFERENCE_REWARD_MAP.items():
            if reward_id in unlocked_ids:
                unlocked_prefs.append(pref_type)
        return unlocked_prefs
    except Exception:
        return []


def _get_personalise_unlock_info(user_id: str, db_session: Any, user_timezone: Optional[str] = None) -> Dict[str, Any]:
    """Compute unlocked + locked personalisation factors, including days remaining to unlock.

    Source of truth:
    - Preferences gating: PREFERENCE_REWARD_MAP
    - Streak progress / days remaining: RewardService.get_all_rewards_status
    """
    try:
        from app.api.v1.endpoints.preferences import PREFERENCE_REWARD_MAP
        from app.services.reward_service import RewardService

        reward_status = RewardService(db_session).get_all_rewards_status(user_id, user_timezone)
        current_streak = int(reward_status.get("current_streak") or 0)
        rewards = reward_status.get("rewards") or []
        reward_by_id = {r.get("id"): r for r in rewards if r.get("id")}
        unlocked_reward_ids = set(RewardService(db_session).get_unlocked_reward_ids(user_id))

        unlocked_prefs: List[str] = []
        locked_prefs: List[Dict[str, Any]] = []

        for pref_type, reward_id in PREFERENCE_REWARD_MAP.items():
            if reward_id in unlocked_reward_ids:
                unlocked_prefs.append(pref_type)
                continue

            r = reward_by_id.get(reward_id) or {}
            locked_prefs.append(
                {
                    "preference_type": pref_type,
                    "reward_id": reward_id,
                    "required_streak": int(r.get("required_streak") or 0),
                    "days_remaining": int(r.get("days_remaining") or 0),
                }
            )

        # Stable ordering: most-immediate unlocks first
        locked_prefs.sort(key=lambda x: (x.get("days_remaining", 9999), x.get("required_streak", 9999)))

        return {
            "current_streak": current_streak,
            "unlocked_preference_types": unlocked_prefs,
            "locked_preferences": locked_prefs,
        }
    except Exception:
        return {
            "current_streak": 0,
            "unlocked_preference_types": [],
            "locked_preferences": [],
        }


def _personalise_label(pref_type: str) -> str:
    labels = {
        "diet_preference": "🥗 Diet preference",
        "food_allergies": "🚫 Food allergies",
        "cuisine_preference": "🍜 Cuisine preference",
        "dine_out_frequency": "🍽️ Dine out frequency",
        "cultural_background": "🌍 Cultural background",
        "body_metrics": "📏 Body metrics",
        "cravings": "🍫 Cravings",
    }
    return labels.get(pref_type, pref_type.replace("_", " ").title())


def _personalise_prompt(pref_type: str) -> str:
    prompts = {
        "diet_preference": "I want to update my diet preference",
        "food_allergies": "I want to update my food allergies",
        "cuisine_preference": "I want to update my cuisine preference",
        "dine_out_frequency": "I want to update my dine out frequency",
        "cultural_background": "I want to update my cultural background",
        "body_metrics": "I want to update my body metrics",
        "cravings": "I want to update my cravings",
    }
    return prompts.get(pref_type, f"I want to update my {pref_type.replace('_', ' ')}")


def _personalise_overview_blocks(unlock_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Overview cards:
    - Unlocked factors: actionable
    - Locked factors: show days remaining to unlock
    """
    unlocked_prefs: List[str] = list(unlock_info.get("unlocked_preference_types") or [])
    locked: List[Dict[str, Any]] = list(unlock_info.get("locked_preferences") or [])
    current_streak = int(unlock_info.get("current_streak") or 0)

    blocks: List[Dict[str, Any]] = []

    if unlocked_prefs:
        actions: List[Dict[str, Any]] = []
        for idx, pref in enumerate(unlocked_prefs[:8]):
            actions.append(
                {
                    "id": f"personalise_pick_{pref}",
                    "title": _personalise_label(pref),
                    "action_type": "send_text",
                    "payload": {"text": _personalise_prompt(pref)},
                    "style": "primary" if idx == 0 else "secondary",
                }
            )

        # Pro-only blood report upload (not implemented in backend yet).
        actions.append(
            {
                "id": "personalise_blood_report_paywall",
                "title": "🧪 Upload blood report (Pro)",
                "action_type": "open_modal",
                "payload": {"modal": "PaywallScreen"},
                "style": "ghost",
            }
        )

        blocks.append(
            {
                "id": "personalise_overview_unlocked",
                "type": "quick_actions",
                "title": "Personalize (unlocked)",
                "subtitle": "Edit unlocked factors (matches your Personalize page unlocks).",
                "actions": actions,
            }
        )

    if locked:
        lines: List[str] = []
        for lp in locked[:5]:
            pt = lp.get("preference_type")
            dr = lp.get("days_remaining")
            if pt:
                lines.append(f"{_personalise_label(pt)} — unlock in {dr} day(s)")
        subtitle = "Locked factors (keep your streak going):\n" + "\n".join(lines)

        blocks.append(
            {
                "id": "personalise_overview_locked",
                "type": "quick_actions",
                "title": "Personalize (locked)",
                "subtitle": subtitle,
                "actions": [
                    {
                        "id": "personalise_how_to_unlock",
                        "title": f"How to unlock (streak: {current_streak} days)",
                        "action_type": "send_text",
                        "payload": {"text": "How many days left to unlock each personalisation factor?"},
                        "style": "secondary",
                    }
                ],
            }
        )

    if not blocks:
        blocks.append(
            {
                "id": "personalise_locked_overview",
                "type": "quick_actions",
                "title": "Personalize",
                "subtitle": "You haven't unlocked any personalisation factors yet. Keep your streak going to unlock them.",
                "actions": [
                    {
                        "id": "personalise_tell_me_how_to_unlock",
                        "title": "How do I unlock?",
                        "action_type": "send_text",
                        "payload": {"text": "How do I unlock personalisation features?"},
                        "style": "primary",
                    }
                ],
            }
        )

    return blocks


def _detect_preference_focus(message_lower: str) -> Optional[str]:
    """Detect which preference the user is trying to edit."""
    # Keep this intentionally simple and deterministic.
    if any(k in message_lower for k in ["diet", "vegan", "vegetarian", "keto", "paleo", "gluten"]):
        return "diet_preference"
    if any(k in message_lower for k in ["allergy", "allergies", "lactose", "nuts", "shellfish"]):
        return "food_allergies"
    if "cuisine" in message_lower or "indian" in message_lower or "mediterranean" in message_lower:
        return "cuisine_preference"
    if any(k in message_lower for k in ["dine out", "eat out", "restaurant", "takeout"]):
        return "dine_out_frequency"
    if any(k in message_lower for k in ["cultural", "culture", "ethnicity", "background"]):
        return "cultural_background"
    if any(k in message_lower for k in ["body metrics", "height", "weight", "waist", "bmi"]):
        return "body_metrics"
    if "craving" in message_lower or "cravings" in message_lower:
        return "cravings"
    return None


def _parse_set_preference_command(message: str) -> Optional[Dict[str, str]]:
    """Parse a deterministic command sent by UI buttons.

    Format:
      set_preference <preference_type> <value>
    Example:
      set_preference diet_preference vegan
    """
    t = message.strip()
    if not t:
        return None
    parts = t.split()
    if len(parts) < 3:
        return None
    if parts[0].lower() != "set_preference":
        return None
    pref_type = parts[1].strip()
    value_raw = " ".join(parts[2:]).strip()
    if not pref_type or not value_raw:
        return None

    # Allow multi-select/body-metrics via JSON literals.
    # Examples:
    #   set_preference food_allergies ["nuts","dairy"]
    #   set_preference body_metrics {"height_cm":170,"weight_kg":65}
    value: Any = value_raw
    if value_raw[:1] in {"[", "{"}:
        try:
            import json

            value = json.loads(value_raw)
        except Exception:
            value = value_raw

    return {"preference_type": pref_type, "value": value}


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR HANDLING & RETRY LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

async def retry_with_backoff(func, max_retries: int = 3, initial_delay: float = 1.0):
    """
    Retry async function with exponential backoff.
    
    Ensures resilience against temporary API failures.
    """
    import asyncio
    
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise  # Final attempt failed
            
            delay = initial_delay * (2 ** attempt)
            logger.warning(f"⚠️ Attempt {attempt + 1} failed: {str(e)}. Retrying in {delay}s...")
            await asyncio.sleep(delay)


# ═══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE SYSTEM ACTIVATION
# ═══════════════════════════════════════════════════════════════════════════════

# CRITICAL: Set to True to activate ALL intelligence modules
# This enables: Memory, Emotional Intelligence, Context Awareness, etc.
INTELLIGENCE_AVAILABLE = True

# Import intelligence modules
if INTELLIGENCE_AVAILABLE:
    try:
        from app.services.chat.intelligence.emotional_intelligence import EmotionalIntelligence
        from app.services.chat.intelligence.memory_engine import MemoryEngine
        from app.services.chat.intelligence.context_engine import ContextEngine
        from app.services.chat.intelligence.prompt_architect import PromptArchitect
        from app.services.chat.intelligence.response_composer import ResponseComposer
        from app.services.chat.intelligence.proactive_engine import ProactiveEngine
        from app.services.chat.intelligence.wellness_score import WellnessScoreCalculator
        from app.services.chat.intelligence.symptom_predictor import SymptomPredictor
        from app.services.chat.intelligence.intelligent_cache import get_cache
        from app.services.chat.intelligence.session_summarizer import SessionSummarizer
        
        logger.info("🧠 Intelligence modules loaded successfully (including wellness, prediction, caching)")
    except ImportError as e:
        logger.warning(f"⚠️ Intelligence modules not available: {e}")
        INTELLIGENCE_AVAILABLE = False
else:
    logger.info("Intelligence modules disabled")


def get_llm(model: str = "gpt-5-mini", temperature: float = 0.7, streaming: bool = False):
    """Get configured LLM instance with optional streaming."""
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        openai_api_key=settings.OPENAI_API_KEY,
        streaming=streaming  # Enable token-by-token streaming
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
# NOTE: Intelligence modules already imported above (lines 75-91)
# Import additional formatting functions here if not already imported
# ═══════════════════════════════════════════════════════════════════════════════

if INTELLIGENCE_AVAILABLE:
    try:
        from app.services.chat.intelligence.memory_engine import format_memory_for_prompt
        from app.services.chat.intelligence.emotional_intelligence import format_emotional_guidance_for_prompt
        from app.services.chat.intelligence.context_engine import format_context_for_prompt
    except ImportError:
        pass  # Main imports already handled above


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
    
    # Extract first name for personal greetings
    first_name = "there"
    full_name = patient_profile.get("name", "") if patient_profile else ""
    if full_name:
        first_name = full_name.split()[0]
    
    # Patient info - PROMINENTLY DISPLAYED AT TOP
    if patient_profile:
        # Get diagnosed conditions for personalization
        conditions = patient_profile.get("diagnosed_conditions", [])
        conditions_str = ", ".join(conditions[:3]) if conditions else "None specified"
        top_concern = patient_profile.get("top_concern", "general wellness")
        
        # Get cycle info
        phase = patient_profile.get("phase", "unknown")
        cycle_day = patient_profile.get("cycle_day", "?")
        
        # Create prominent user profile box
        header = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  🧑‍⚕️ YOU ARE TALKING TO: {first_name.upper():^52} ║
║  CONDITIONS: {conditions_str[:50]:^55} ║
║  TOP CONCERN: {top_concern[:50]:^54} ║
║  CYCLE: {phase.upper():^60} (Day {cycle_day})        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
        sections.append(header)
        
        # USER PREFERENCES from chatbot_memory - CRITICAL FOR PERSONALIZATION
        chatbot_memory = patient_profile.get("chatbot_memory", {})
        if chatbot_memory:
            prefs_parts = []
            if chatbot_memory.get("diet_preference"):
                prefs_parts.append(f"🥗 Diet: {chatbot_memory['diet_preference']}")
            if chatbot_memory.get("food_allergies"):
                allergies = chatbot_memory['food_allergies']
                if isinstance(allergies, list):
                    prefs_parts.append(f"🚫 Allergies: {', '.join(allergies)}")
                else:
                    prefs_parts.append(f"🚫 Allergies: {allergies}")
            if chatbot_memory.get("cuisine_preference"):
                cuisines = chatbot_memory['cuisine_preference']
                if isinstance(cuisines, list):
                    prefs_parts.append(f"🍜 Cuisines: {', '.join(cuisines[:3])}")
                else:
                    prefs_parts.append(f"🍜 Cuisines: {cuisines}")
            if chatbot_memory.get("cultural_background"):
                prefs_parts.append(f"🌍 Cultural: {chatbot_memory['cultural_background']}")
            if chatbot_memory.get("body_metrics"):
                metrics = chatbot_memory['body_metrics']
                if isinstance(metrics, dict) and metrics.get("bmi"):
                    prefs_parts.append(f"📏 BMI: {metrics['bmi']} ({metrics.get('bmi_category', 'unknown')})")
            if chatbot_memory.get("cravings"):
                cravings = chatbot_memory['cravings']
                if isinstance(cravings, list):
                    prefs_parts.append(f"🍫 Cravings: {', '.join(cravings[:3])}")
            
            if prefs_parts:
                sections.append(f"═══ {first_name.upper()}'S PREFERENCES ═══\n" + "\n".join(prefs_parts))
        
        # Additional patient details
        patient_parts = []
        
        if patient_profile.get("age"):
            patient_parts.append(f"Age: {patient_profile['age']}")
        
        if patient_profile.get("primary_hormone"):
            patient_parts.append(f"Hormone focus: {patient_profile['primary_hormone']}")
        
        if patient_parts:
            sections.append("Additional Info:\n" + "\n".join(patient_parts))
    
    # Today's plan
    if todays_plan:
        total = todays_plan.get("total_assignments", 0)
        completed = todays_plan.get("completed_assignments", 0)
        rate = todays_plan.get("completion_rate", 0)
        
        plan_text = f"═══ {first_name.upper()}'S TODAY'S PLAN ═══\nProgress: {completed}/{total} ({rate*100:.0f}%)"
        
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
            sections.append(f"═══ {first_name.upper()}'S RECENT ACTIVITY ═══\n" + "\n".join(recent_parts))
    
    return "\n\n".join(sections) if sections else "New user - first interaction"


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

MASTER_SYSTEM_PROMPT = """You are AUVRA — a deeply knowledgeable, warmly empathetic women's health companion. 

You are not a chatbot. You are the kind of doctor everyone wishes they had: one who truly listens, remembers everything, and makes complex health feel simple and personal.

═══════════════════════════════════════════════════════════════════════════════
CRITICAL: USE THE CONTEXT DATA BELOW
═══════════════════════════════════════════════════════════════════════════════

You will receive PATIENT PROFILE, TODAY'S PLAN, and RECENT ACTIVITY below.
You MUST actively use this data in your responses:

• Their NAME → Use it naturally (not every message, but occasionally)
• Their CONDITIONS → Reference when giving advice ("With your PCOS...")
• Their CYCLE PHASE → Connect to how they might be feeling
• Their TODAY'S PLAN → Know what they're working on, ask about specific items
• Their SYMPTOMS → Acknowledge what they're dealing with

❌ WRONG: Give generic wellness advice that could apply to anyone
✅ RIGHT: "Since you're in your luteal phase and dealing with [their symptom], here's what might help..."

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
═══ CONTEXT: Care Plan Companion 💜 ═══
You're helping them with their daily wellness plan. Be their supportive wellness buddy!

YOUR APPROACH:
• Completed something → Celebrate genuinely! "That's amazing! 🎉 How did it feel?"
• Want to skip → Show understanding, "I totally get it. What feels more doable today?"
• Overwhelmed → Be gentle, "Let's focus on just ONE thing. What feels most manageable?"
• Just checking in → Ask about their energy, mood, what's on their mind

PERSONALIZATION:
• Reference their cycle phase: "{phase} can make {symptom} feel more intense"
• Acknowledge streaks: "You've been so consistent! {streak_days} days 💪"
• Remember their preferences: "Last time you mentioned {preference}..."

KEEP IT WARM: Short sentences. One thought at a time. Emojis sparingly but warmly (💜🌸✨).
""",
    "symptom_checkin": """
═══ CONTEXT: Symptom Check-in 🌸 ═══
Help them track and understand their symptoms with empathy and insight.

YOUR APPROACH:
• When logging severity → Validate first! "A 7? That sounds really uncomfortable 💜"
• High severity (7-9) → Express genuine concern, suggest comfort measures
• Connect to cycle → "During {phase}, {symptom} is so common because of {hormone reason}"
• Spotting patterns → "I've noticed your {symptom} tends to peak around day {day}..."

PERSONALIZATION:
• Use their name occasionally: "{name}, how are you feeling right now?"
• Reference history: "Last week you mentioned {previous_symptom}. How's that?"
• Celebrate improvements: "Your {symptom} went from 7 to 4! That's real progress! 🎉"

AVOID: Medical diagnosis, medication recommendations. Do suggest when to see a doctor.
""",
    "personalise": """
═══ CONTEXT: Personalization ✨ ═══
Help them customize their experience to make Auvra truly theirs.

YOUR APPROACH:
• Be curious and warm: "Tell me more about your lifestyle..."
• Explain the value: "Knowing your {factor} helps me give you better {benefit}"
• Make it feel special: "The more I know you, the better I can support you 💜"
• Celebrate sharing: "Thanks for sharing that! This really helps me understand you better"

TOPICS TO EXPLORE:
• Diet preferences, restrictions, favorite foods
• Exercise habits, energy patterns
• Sleep patterns, stress triggers
• Work schedule, lifestyle factors

KEEP IT CONVERSATIONAL: Like a friend getting to know them, not a form to fill.
""",
    "know_body": """
═══ CONTEXT: Health Education 🌸 ═══
Educational mode - help them understand their body with clarity and empowerment.

YOUR APPROACH:
• Make complex simple: Use analogies, everyday language
• Connect to THEIR body: "During your {phase}, your {hormone} is {doing this}..."
• Empower with knowledge: "Knowing this can help you {practical benefit}"
• Encourage curiosity: "Great question! Let me explain..."

TOPICS YOU EXCEL AT:
• Menstrual cycle phases and what happens in each
• Hormone fluctuations and their effects
• Common symptoms and why they happen
• Body-mind connections

ALWAYS ADD: "For anything specific to you, your doctor is your best resource 💜"
"""
}


def generate_choices(content: str, context: str, user_message: str = "", tool_results: List[Dict] = None) -> Optional[List[str]]:
    """
    Generate SMART choice buttons based on response content, context, and tool results.
    
    These should be contextually relevant and guide the conversation forward.
    """
    content_lower = content.lower()
    message_lower = user_message.lower() if user_message else ""
    
    # If a tool was just used, generate relevant follow-ups
    if tool_results:
        for result in tool_results:
            tool_name = result.get("tool", "")
            tool_result = result.get("result", {})
            
            if tool_name == "update_user_preference":
                if tool_result.get("success"):
                    return ["Tell me more about myself", "What else can I personalize?", "That's all for now 💜"]
            
            if tool_name == "complete_assignment":
                if tool_result.get("success"):
                    return ["What's next on my plan?", "Show my progress 📊", "I'm done for now 💜"]
            
            if tool_name == "log_symptom":
                if tool_result.get("success"):
                    return ["Track another symptom", "Show my symptom trends 📊", "What might be causing this?"]
            
            if tool_name == "get_user_symptoms":
                symptoms = tool_result.get("tracked_symptoms", {})
                if symptoms:
                    most_common = list(symptoms.keys())[0] if symptoms else None
                    if most_common:
                        return [f"Update my {most_common}", "Track something new", "Show my trends 📊"]
    
    # Question patterns - make choices feel conversational and relevant
    if "what would you like to" in content_lower or "what specific" in content_lower or "let me know what" in content_lower:
        if context == "care_plan_modal":
            return ["Change the timing", "Try different activities", "Reduce the number of tasks"]
        elif context == "personalise":
            return ["🥗 Personalize my diet", "💪 Personalize my exercise", "😴 Personalize my sleep", "🎯 Personalize my goals"]
        elif context == "symptom_checkin":
            return ["📊 Track a symptom", "🔍 See my patterns", "❓ Ask about a symptom"]
    
    if "would you like" in content_lower or "want me to" in content_lower or "aspects" in content_lower:
        if context == "personalise":
             return ["🥗 Personalize my diet", "💪 Personalize my exercise", "😴 Personalize my sleep", "🎯 Personalize my goals"]
        return ["Yes please! 💜", "Not right now", "Tell me more first"]
    
    if "how are you feeling" in content_lower or "how do you feel" in content_lower:
        return ["Really good! 🌟", "Okay, I guess", "Not my best day", "I need support 💜"]
    
    if "skip" in content_lower or "can't do" in content_lower or "struggling" in content_lower:
        return ["Give me an easier option", "Skip just for today", "Help me adjust my plan"]
    
    if "accomplished" in content_lower or "completed" in content_lower or "done" in content_lower:
        return ["Yes, I did it! 🎉", "Most of it", "I tried my best", "It was hard today"]
    
    # Personalisation-specific patterns
    if context == "personalise":
        if "diet" in content_lower or "food" in content_lower or "eat" in content_lower:
            return ["I'm vegetarian 🥬", "No restrictions", "I have allergies"]
        if "exercise" in content_lower or "workout" in content_lower:
            return ["Light exercise 🚶", "Moderate 🏃", "Intense workouts 💪", "I don't exercise much"]
        if "sleep" in content_lower:
            return ["Less than 6 hours", "6-8 hours", "More than 8 hours"]
        if "stress" in content_lower:
            return ["Very stressed 😰", "Somewhat stressed", "Managing okay", "Not stressed 😊"]
    
    # Symptom checkin patterns
    if context == "symptom_checkin":
        if "bloating" in message_lower or "bloat" in content_lower:
            return ["Yes, it's bothering me", "A little bit", "Not really today"]
        if "cramps" in content_lower or "pain" in content_lower:
            return ["Severe 😣", "Moderate", "Mild", "None today 😊"]
    
    # Care plan patterns
    if context == "care_plan_modal":
        if "change" in message_lower or "adjust" in message_lower:
            return ["⏰ Change timing", "🔄 Different actions", "📉 Fewer tasks today"]
    
    # Context-specific defaults - engaging and action-oriented
    defaults = {
        "symptom_checkin": ["📊 Track a symptom", "🔍 Show my patterns", "✨ I'm feeling good today!"],
        "care_plan_modal": ["✅ Mark something done", "⏰ Adjust my schedule", "📋 Show today's plan"],
        "know_body": ["📚 Tell me more!", "🌸 Explain another topic", "❓ I have a question"],
        "personalise": ["🥗 Personalize my diet", "💪 Personalize my exercise", "😴 Personalize my sleep", "🎯 Personalize my goals"]
    }
    
    return defaults.get(context, ["Yes please! 💜", "No thanks", "Tell me more 🤔"])


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
                "ui_blocks": None,
                "tool_calls": [],
                "safety_check": safety_check
            }

        # Deterministic preference updates for button-driven personalisation.
        if conversation_context == "personalise" and db_session:
            parsed = _parse_set_preference_command(message)
            if parsed:
                try:
                    from app.services.chat.tools import update_user_preference
                    tool_result = await update_user_preference.ainvoke(
                        {
                            "user_id": user_id,
                            "preference_type": parsed["preference_type"],
                            "preference_value": parsed["value"],
                            "db_session": db_session,
                        }
                    )
                    tz = patient_profile.get("timezone", "UTC") if isinstance(patient_profile, dict) else "UTC"
                    unlock_info = _get_personalise_unlock_info(user_id, db_session, tz)
                    content = tool_result.get("message") or (
                        "Saved." if tool_result.get("success") else "I couldn't save that yet."
                    )
                    if not tool_result.get("success") and tool_result.get("error_code") == "PREFERENCE_LOCKED":
                        pref_type = parsed.get("preference_type")
                        locked = next(
                            (
                                lp
                                for lp in (unlock_info.get("locked_preferences") or [])
                                if lp.get("preference_type") == pref_type
                            ),
                            None,
                        )
                        if locked is not None:
                            dr = int(locked.get("days_remaining") or 0)
                            req = int(locked.get("required_streak") or 0)
                            cur = int(unlock_info.get("current_streak") or 0)
                            content = (
                                f"{content}\n\nThat factor is locked for now — unlock in {dr} day(s). "
                                f"(Need a {req}-day streak; you’re at {cur}.)"
                            )

                    return {
                        "content": content,
                        "response_type": "text",
                        "choices": None,
                        "slider_config": None,
                        "actions": None,
                        "ui_blocks": _personalise_overview_blocks(unlock_info),
                        "tool_calls": [
                            {
                                "tool": "update_user_preference",
                                "args": {
                                    "preference_type": parsed["preference_type"],
                                    "preference_value": parsed["value"],
                                },
                                "result": tool_result,
                            }
                        ],
                        "safety_check": safety_check,
                        "metadata": {"deterministic": True},
                    }
                except Exception as e:
                    logger.warning(f"Failed deterministic set_preference: {e}")
        
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
                
                # Load rich context (pass profile gaps for Deep Profiling in personalise mode)
                timezone = patient_profile.get("timezone", "UTC")
                try:
                    profile_gaps = deep_memory.get("profile_gaps") if deep_memory else None
                    if conversation_context == "personalise" and profile_gaps:
                        rich_context = await context_engine.build_full_context(
                            user_id, timezone, profile_gaps=profile_gaps
                        )
                    else:
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
        
        # Extract user-specific data for prompt personalization
        user_name = "there"
        user_phase = "unknown"
        user_conditions = []
        user_symptoms = []
        user_streak = 0
        user_preferences = {}
        
        if patient_profile:
            full_name = patient_profile.get("name", "")
            user_name = full_name.split()[0] if full_name else "there"
            user_phase = patient_profile.get("phase", "unknown")
            user_conditions = patient_profile.get("diagnosed_conditions", [])
            # Get preferences from chatbot_memory
            chatbot_memory = patient_profile.get("chatbot_memory", {})
            if chatbot_memory:
                user_preferences = {
                    "diet": chatbot_memory.get("diet_preference"),
                    "allergies": chatbot_memory.get("food_allergies"),
                    "cuisine": chatbot_memory.get("cuisine_preference"),
                }
        
        if recent_summary:
            symptoms_data = recent_summary.get("symptoms_reported", [])
            user_symptoms = [s.get("type", "") for s in symptoms_data[:3]]
            user_streak = recent_summary.get("streak", {}).get("current", 0)
        
        # Build personalized conversation guidance with ACTUAL user data
        conversation_guidance_template = CONVERSATION_PROMPTS.get(
            conversation_context, 
            CONVERSATION_PROMPTS["care_plan_modal"]
        )
        
        # Replace placeholders with actual user data
        conversation_guidance = conversation_guidance_template.replace(
            "{phase}", user_phase
        ).replace(
            "{name}", user_name
        ).replace(
            "{symptom}", user_symptoms[0] if user_symptoms else "symptoms"
        ).replace(
            "{streak_days}", str(user_streak)
        ).replace(
            "{preference}", str(user_preferences.get("diet", "your preferences"))
        ).replace(
            "{previous_symptom}", user_symptoms[0] if user_symptoms else "your symptoms"
        ).replace(
            "{day}", str(patient_profile.get("cycle_day", "?")) if patient_profile else "?"
        ).replace(
            "{hormone}", "estrogen" if user_phase in ["follicular", "ovulation"] else "progesterone"
        ).replace(
            "{hormone reason}", "hormone fluctuations during this phase"
        ).replace(
            "{factor}", "preferences"
        ).replace(
            "{benefit}", "personalized recommendations"
        ).replace(
            "{doing this}", "at its peak" if user_phase == "ovulation" else "fluctuating"
        ).replace(
            "{practical benefit}", "plan your activities better"
        )
        
        # Add user's actual conditions and symptoms to guidance
        if user_conditions:
            conversation_guidance += f"\n\n🎯 USER'S CONDITIONS: {', '.join(user_conditions[:3])}"
        if user_symptoms:
            conversation_guidance += f"\n🩺 RECENT SYMPTOMS: {', '.join(user_symptoms)}"
        if user_preferences.get("diet"):
            conversation_guidance += f"\n🥗 DIET: {user_preferences['diet']}"
        
        # Relationship stage adjustments
        relationship_notes = {
            "new_acquaintance": "🆕 NEW USER: Be extra welcoming, introduce yourself briefly.",
            "building_trust": "",
            "established": "💜 ESTABLISHED: Can be more familiar, reference your history.",
            "deep_relationship": "💜 DEEP RELATIONSHIP: Very familiar, anticipate needs."
        }
        relationship_note = relationship_notes.get(relationship_stage, "")
        
        # Add tool usage instructions
        tool_instructions = """
IMPORTANT - TOOL USAGE:
You have access to tools to take REAL ACTIONS. USE THEM when appropriate:

• When user shares preferences (diet, exercise, sleep habits, allergies):
  → ALWAYS call update_user_preference to SAVE this information
  → Example: User says "I'm vegetarian" → call update_user_preference(preference_type="diet", preference_value="vegetarian")

• When user asks about symptoms or wants to track:
  → Call get_user_symptoms to see what they've tracked before
  → Ask about THEIR actual symptoms, not generic ones

• When user wants to complete/skip a task:
  → Call complete_assignment or skip_assignment

• When user asks about their body/hormones:
  → Call explain_hormone or get_hormone_analysis

NEVER just acknowledge preferences without saving them. The user expects their information to be remembered!
"""
        
        system_prompt = MASTER_SYSTEM_PROMPT.format(
            context_section=full_context,
            conversation_guidance=conversation_guidance + "\n" + relationship_note + "\n" + tool_instructions,
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
        # STEP 6: GENERATE RESPONSE WITH TOOLS
        # ═══════════════════════════════════════════════════════════════════
        
        # Get tools for this context
        from app.services.chat.tools import get_tools_by_context
        tools = get_tools_by_context(conversation_context)
        
        # Bind tools to LLM
        llm = get_llm()
        llm_with_tools = llm.bind_tools(tools)
        
        # First call - may return tool calls
        response = await llm_with_tools.ainvoke(messages)
        
        tool_calls_made = []
        
        # Check if response contains tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.info(f"🔧 Tool calls requested: {[tc['name'] for tc in response.tool_calls]}")
            
            # Execute each tool call
            for tool_call in response.tool_calls:
                tool_name = tool_call['name']
                # Create a copy of args to avoid polluting history with non-serializable objects (like db_session)
                execution_args = tool_call['args'].copy() if tool_call.get('args') else {}
                
                # Inject user_id and db_session into execution args
                execution_args['user_id'] = user_id
                execution_args['db_session'] = db_session
                
                # Find and execute the tool
                for tool in tools:
                    if tool.name == tool_name:
                        try:
                            # Use execution_args for calling the tool
                            tool_result = await tool.ainvoke(execution_args)
                            tool_calls_made.append({
                                "tool": tool_name,
                                "args": {k: v for k, v in execution_args.items() if k != 'db_session'},
                                "result": tool_result
                            })
                            logger.info(f"✅ Tool {tool_name} executed: {tool_result.get('success', 'completed')}")
                        except Exception as e:
                            logger.error(f"❌ Tool {tool_name} failed: {str(e)}")
                            tool_calls_made.append({
                                "tool": tool_name,
                                "error": str(e)
                            })
                        break
            
            # Add tool results to messages and get final response
            from langchain_core.messages import ToolMessage
            
            messages.append(response)
            for i, tool_call in enumerate(response.tool_calls):
                result = tool_calls_made[i].get('result', tool_calls_made[i].get('error', 'Error'))
                messages.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call['id']
                ))
            
            # Get final response after tool execution
            final_response = await llm_with_tools.ainvoke(messages)
            raw_content = final_response.content if hasattr(final_response, 'content') else str(final_response)
        else:
            raw_content = response.content if hasattr(response, 'content') else str(response)
        
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 7: COMPOSE & ENHANCE RESPONSE
        # ═══════════════════════════════════════════════════════════════════
        
        final_content = raw_content
        response_type = "text"
        choices = None
        slider_config = None
        ui_blocks: Optional[List[Dict[str, Any]]] = None
        
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
                choices = generate_choices(raw_content, conversation_context, message, tool_calls_made)
        else:
            # Basic choice generation - include tool results for smarter choices
            choices = generate_choices(raw_content, conversation_context, message, tool_calls_made)
            
            # Basic slider detection
            if any(phrase in raw_content.lower() for phrase in ["how severe", "scale of", "rate your", "1 to"]):
                response_type = "slider"
                slider_config = {
                    "min": 1,
                    "max": 9,
                    "step": 1,
                    "labels": ["None 😊", "Mild", "Moderate", "Strong", "Intense 💪"]
                }
        
        # Add urgent disclaimer if needed
        if safety_check.get("is_urgent"):
            final_content = f"{safety_check['message']}\n\n{final_content}"

        # Personalise UI blocks (unlocked-only).
        if conversation_context == "personalise" and db_session:
            try:
                tz = patient_profile.get("timezone", "UTC") if isinstance(patient_profile, dict) else "UTC"
                unlock_info = _get_personalise_unlock_info(user_id, db_session, tz)
                unlocked_prefs = list(unlock_info.get("unlocked_preference_types") or [])
                focus = _detect_preference_focus(message.lower())

                # If the user is focusing a specific preference, show a focused picker when possible.
                if focus and focus in unlocked_prefs:
                    from app.api.v1.endpoints.preferences import PREFERENCE_OPTIONS

                    options = PREFERENCE_OPTIONS.get(focus)
                    # Single-select preferences: direct picker UI.
                    single_select = {"diet_preference", "dine_out_frequency", "cultural_background"}
                    multi_select = {"food_allergies", "cuisine_preference", "cravings"}

                    if options and focus in single_select:
                        ui_blocks = [
                            {
                                "id": f"personalise_pick_{focus}",
                                "type": "single_select",
                                "title": _personalise_label(focus),
                                "subtitle": "Pick one:",
                                "actions": [
                                    {
                                        "id": f"set_{focus}_{opt['id']}",
                                        "title": f"{opt.get('icon', '')} {opt.get('label', opt['id'])}".strip(),
                                        "action_type": "send_text",
                                        "payload": {"text": f"set_preference {focus} {opt['id']}"},
                                        "style": "primary" if i == 0 else "secondary",
                                    }
                                    for i, opt in enumerate(options[:10])
                                ],
                            }
                        ]
                    elif options and focus in multi_select:
                        # For multi-select, offer a few quick single-item setters plus a hint for lists.
                        hint = (
                            f"To set multiple, reply like: set_preference {focus} "
                            f"[\"{options[0]['id']}\", \"{options[1]['id']}\"]"
                            if len(options) >= 2
                            else f"To set multiple, reply like: set_preference {focus} [\"{options[0]['id']}\"]"
                        )
                        ui_blocks = [
                            {
                                "id": f"personalise_multi_{focus}",
                                "type": "multi_select_quick",
                                "title": _personalise_label(focus),
                                "subtitle": hint,
                                "actions": [
                                    {
                                        "id": f"set_{focus}_only_{opt['id']}",
                                        "title": f"{opt.get('icon', '')} {opt.get('label', opt['id'])}".strip(),
                                        "action_type": "send_text",
                                        "payload": {"text": f"set_preference {focus} [\"{opt['id']}\"]"},
                                        "style": "secondary",
                                    }
                                    for opt in options[:8]
                                ],
                            }
                        ]
                    elif focus == "body_metrics":
                        ui_blocks = [
                            {
                                "id": "personalise_body_metrics_hint",
                                "type": "form_hint",
                                "title": _personalise_label(focus),
                                "subtitle": "Reply with JSON, e.g. set_preference body_metrics {\"height_cm\":170,\"weight_kg\":65}",
                                "actions": [],
                            }
                        ]
                    else:
                        ui_blocks = _personalise_overview_blocks(unlock_info)
                elif focus:
                    # The user asked about a preference that isn't unlocked.
                    try:
                        from app.api.v1.endpoints.preferences import PREFERENCE_REWARD_MAP

                        if focus in PREFERENCE_REWARD_MAP:
                            locked = next(
                                (
                                    lp
                                    for lp in (unlock_info.get("locked_preferences") or [])
                                    if lp.get("preference_type") == focus
                                ),
                                None,
                            )
                            if locked is not None:
                                dr = int(locked.get("days_remaining") or 0)
                                req = int(locked.get("required_streak") or 0)
                                cur = int(unlock_info.get("current_streak") or 0)
                                ui_blocks = [
                                    {
                                        "id": f"personalise_locked_{focus}",
                                        "type": "quick_actions",
                                        "title": f"{_personalise_label(focus)} (locked)",
                                        "subtitle": (
                                            f"Unlock in {dr} day(s). Need a {req}-day streak; you’re at {cur}.\n"
                                            "Once it’s unlocked, I’ll let you edit it here (and on your Personalize page)."
                                        ),
                                        "actions": [
                                            {
                                                "id": "personalise_show_unlocked",
                                                "title": "Show unlocked options",
                                                "action_type": "send_text",
                                                "payload": {"text": "What personalisation options are unlocked for me?"},
                                                "style": "secondary",
                                            },
                                            {
                                                "id": "personalise_how_unlock_specific",
                                                "title": "How do I unlock faster?",
                                                "action_type": "send_text",
                                                "payload": {
                                                    "text": "How can I keep my streak and unlock more personalisation features?"
                                                },
                                                "style": "secondary",
                                            },
                                        ],
                                    }
                                ]
                            else:
                                ui_blocks = _personalise_overview_blocks(unlock_info)
                        else:
                            ui_blocks = _personalise_overview_blocks(unlock_info)
                    except Exception:
                        ui_blocks = _personalise_overview_blocks(unlock_info)
                else:
                    # Default overview block.
                    ui_blocks = _personalise_overview_blocks(unlock_info)
            except Exception as e:
                logger.warning(f"Failed to build personalise ui_blocks: {e}")
        
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
            "ui_blocks": ui_blocks,
            "tool_calls": tool_calls_made,  # Include actual tool calls
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
            "ui_blocks": None,
            "tool_calls": [],
            "error": str(e)
        }


# For backward compatibility
chat_graph = None

# NOTE: Token/SSE streaming helpers were intentionally removed.
# The backend now only supports non-streaming chat responses.
