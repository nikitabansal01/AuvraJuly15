"""
═══════════════════════════════════════════════════════════════════════════════
WEEKLY CHECK-IN AI ENGINE
═══════════════════════════════════════════════════════════════════════════════
Intelligent conversational check-in system that mimics a doctor's consultation.

The AI dynamically:
1. Generates contextual questions based on user history
2. Decides optimal input type (slider, tap options, free text)
3. Creates personalized response options
4. Adapts conversation flow based on responses
5. Summarizes and extracts insights

This is the "brain" behind the weekly check-in - making it feel like a real
consultation, not a rigid form.

PROVIDER STRATEGY: OpenAI primary, Groq fallback on ANY error
═══════════════════════════════════════════════════════════════════════════════
"""
import logging
import json
import re
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

# Try to import Groq for fallback
try:
    from groq import AsyncGroq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    AsyncGroq = None

from app.core.database import (
    WeeklyCheckIn, UserProfile, SymptomLog, 
    ActionPlan, ActionPlanItem, UserResponse
)
from app.core.config import Settings
from app.utils.timezone_utils import get_user_current_date

logger = logging.getLogger(__name__)

settings = Settings()

# API Keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Fallback model: llama-3.3-70b-versatile has higher rate limits (30K TPM vs 8K)
GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"

# Initialize clients - OpenAI is primary, Groq is fallback
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None
groq_client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_AVAILABLE and GROQ_API_KEY else None


# ============================================================================
# PYDANTIC MODELS FOR STRUCTURED OUTPUTS
# ============================================================================

class CheckInTapOption(BaseModel):
    id: str
    text: str

class CheckInInsights(BaseModel):
    triggers_identified: List[str] = []
    relief_factors_identified: List[str] = []
    severity_trend: Optional[str] = None
    suggested_additions: List[str] = []
    key_insight: Optional[str] = None

class CheckInResponseModel(BaseModel):
    messages: List[str]
    tap_options: List[CheckInTapOption] = []
    is_complete: bool = False
    insights: Optional[CheckInInsights] = None


class QuestionType(str, Enum):
    """Types of input the AI can request"""
    SLIDER = "slider"           # 1-9 scale
    TAP_CHOICE = "tap_choice"   # Single selection
    MULTI_SELECT = "multi_select"  # Multiple selections
    FREE_TEXT = "free_text"     # Open response
    CONFIRMATION = "confirmation"  # Yes/No


@dataclass
class AIQuestion:
    """A dynamically generated question"""
    question_key: str
    question_type: QuestionType
    message: str  # Combined message for backward compatibility
    tap_options: List[Dict[str, str]]
    follow_up_context: Optional[str] = None
    is_required: bool = True
    slider_labels: Optional[Any] = None  # For slider: ["Low", "High"] or {1: "None", 9: "Extreme"}
    messages: Optional[List[str]] = None  # Array of short messages for multi-bubble display


class WeeklyCheckInAI:
    """
    AI-powered check-in conversation manager.
    
    Uses LLM to generate contextual, personalized questions that feel
    like talking to an understanding doctor.
    """
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CONVERSATION TEMPLATES - Base patterns the AI adapts
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Opening questions - personalized based on history
    OPENING_TEMPLATES = [
        "How was your {symptom} this week?",
        "Let's check in on your {symptom}. How has it been?",
        "I noticed {symptom} was a concern last time. How's it going?",
    ]
    
    # Follow-up patterns based on severity
    SEVERITY_FOLLOWUPS = {
        "low": [  # 1-3
            "That's great to hear! What do you think helped?",
            "Wonderful! Let's see what's been working for you.",
        ],
        "medium": [  # 4-6
            "I see. Were there any specific triggers you noticed?",
            "Let's explore what might have affected it.",
        ],
        "high": [  # 7-9
            "I'm sorry to hear that. Let's figure out what might help.",
            "That sounds tough. Can you tell me more about what happened?",
        ]
    }
    
    # Factor options - personalized based on what's worked before
    NEGATIVE_FACTORS = [
        {"id": "ate_out", "text": "Ate out more", "emoji": "🍔"},
        {"id": "less_sleep", "text": "Less sleep", "emoji": "😴"},
        {"id": "more_stress", "text": "More stress", "emoji": "😰"},
        {"id": "missed_workouts", "text": "Missed workouts", "emoji": "🏃"},
        {"id": "sugary_foods", "text": "Sugary foods", "emoji": "🍭"},
        {"id": "skipped_supplements", "text": "Skipped supplements", "emoji": "💊"},
        {"id": "irregular_meals", "text": "Irregular meals", "emoji": "🍽️"},
        {"id": "caffeine", "text": "More caffeine", "emoji": "☕"},
        {"id": "alcohol", "text": "Had alcohol", "emoji": "🍷"},
        {"id": "hormonal_changes", "text": "Hormonal changes", "emoji": "🌙"},
        {"id": "something_else", "text": "Something else", "emoji": "✏️"},
    ]
    
    POSITIVE_FACTORS = [
        {"id": "regular_meals", "text": "Regular meals", "emoji": "🥗"},
        {"id": "good_sleep", "text": "Good sleep", "emoji": "😊"},
        {"id": "exercise", "text": "Exercise", "emoji": "🏋️"},
        {"id": "less_stress", "text": "Less stress", "emoji": "🧘"},
        {"id": "healthy_eating", "text": "Healthy eating", "emoji": "🥗"},
        {"id": "supplements", "text": "Took supplements", "emoji": "💊"},
        {"id": "hydration", "text": "Stayed hydrated", "emoji": "💧"},
        {"id": "mindfulness", "text": "Mindfulness/meditation", "emoji": "🧠"},
        {"id": "nature", "text": "Time in nature", "emoji": "🌳"},
        {"id": "social", "text": "Social connection", "emoji": "👥"},
        {"id": "something_else", "text": "Something else", "emoji": "✏️"},
    ]
    
    def __init__(self, db: Session):
        self.db = db

    def _slugify_option_id(self, text: str) -> str:
        """Create a stable, frontend-safe id from a tap option label."""
        s = (text or "").strip().lower()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s or "option"

    def _infer_tap_option_category(self, question_text: str) -> Optional[str]:
        qt = (question_text or "").lower()
        if any(k in qt for k in ["diet", "food", "eat", "eating", "meal", "meals", "nutrition", "appetite"]):
            return "diet"
        if any(k in qt for k in ["sleep", "insomnia", "rest", "tired", "fatigue"]):
            return "sleep"
        if any(k in qt for k in ["stress", "anxious", "anxiety", "overwhelmed", "workload", "pressure"]):
            return "stress"
        if any(k in qt for k in ["exercise", "workout", "movement", "activity", "training"]):
            return "exercise"
        if any(k in qt for k in ["supplement", "vitamin", "magnesium", "iron", "omega", "probiotic"]):
            return "supplements"
        return None

    def _default_tap_options_for_category(self, category: Optional[str]) -> List[Dict[str, str]]:
        # Defaults are meant to be *direct answers* to the question category.
        # Always include "Something else" so users can type their own answer
        if category == "diet":
            texts = [
                "Ate out more / takeout",
                "More sugar / desserts",
                "More carbs (bread/pasta)",
                "Skipped meals / irregular meals",
                "Something else",
            ]
        elif category == "sleep":
            texts = [
                "Went to bed later",
                "Woke up a lot at night",
                "Less total sleep",
                "More screen time at night",
                "Something else",
            ]
        elif category == "stress":
            texts = [
                "Work has been more demanding",
                "Personal / family stress",
                "Mental load / overwhelm",
                "Stress about the same",
                "Something else",
            ]
        elif category == "exercise":
            texts = [
                "Worked out more",
                "Worked out less",
                "More walking",
                "No change",
                "Something else",
            ]
        elif category == "supplements":
            texts = [
                "Started a new supplement",
                "Stopped a supplement",
                "Took them inconsistently",
                "No supplements",
                "Something else",
            ]
        else:
            texts = [
                "Not sure",
                "Nothing changed",
                "A few small changes",
                "Something else",
            ]

        opts: List[Dict[str, str]] = []
        for t in texts:
            opts.append({"id": self._slugify_option_id(t), "text": t})
        return opts

    def _postprocess_tap_options(
        self,
        *,
        question_text: str,
        tap_options: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """Ensure tap options are usable and match the question as best as possible."""
        category = self._infer_tap_option_category(question_text)

        cleaned: List[Dict[str, str]] = []
        for opt in tap_options or []:
            if not isinstance(opt, dict):
                continue
            text = (opt.get("text") or "").strip()
            if not text:
                continue
            cleaned.append({
                "id": (opt.get("id") or self._slugify_option_id(text)),
                "text": text,
            })

        # Add "Something else" option for the user to type if they want
        if cleaned and not any(o["id"] == "something_else" for o in cleaned):
            cleaned.append({"id": "something_else", "text": "Something else..."})

        # If empty, provide category-specific defaults
        if not cleaned:
            return self._default_tap_options_for_category(category)

        # If we can infer a category, make sure options actually relate to it.
        # This prevents cases like a diet question getting sleep/stress options.
        if category:
            qt = (question_text or "").lower()
            # Keyword sets for quick relevance checks
            keywords = {
                "diet": [
                    "diet", "food", "eat", "eating", "meal", "meals", "carb", "sugar", "dessert",
                    "dairy", "gluten", "fiber", "protein", "takeout", "restaurant", "snack", "portion",
                ],
                "sleep": ["sleep", "bed", "insomnia", "awake", "woke", "tired", "fatigue", "nap"],
                "stress": ["stress", "anx", "overwhelm", "work", "pressure", "conflict", "tension"],
                "exercise": ["exercise", "workout", "walk", "running", "gym", "activity", "training"],
                "supplements": ["supplement", "vitamin", "magnesium", "iron", "omega", "probiotic"],
            }.get(category, [])

            def _is_relevant(text: str) -> bool:
                tl = text.lower()
                return any(k in tl for k in keywords) or any(k in qt for k in keywords)

            relevant = [o for o in cleaned if _is_relevant(o["text"])]
            # If fewer than half are relevant, fall back to strong defaults
            if len(relevant) < max(2, len(cleaned) // 2):
                cleaned = self._default_tap_options_for_category(category)

        # De-duplicate (case-insensitive) and cap count
        seen = set()
        deduped: List[Dict[str, str]] = []
        for o in cleaned:
            key = o["text"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            o["id"] = o.get("id") or self._slugify_option_id(o["text"])
            deduped.append(o)
        return deduped[:6]
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CONTEXT GATHERING
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_user_context(self, uid: str) -> Dict[str, Any]:
        """
        Gather rich context about the user for personalization.
        This is the "memory" that makes check-ins feel personal.
        """
        context = {
            "user_name": None,
            "primary_concern": "symptoms",  # Generic fallback
            "top_symptoms": [],
            "cycle_phase": None,
            "cycle_day": None,
            "recent_severity": None,
            "improving_trend": None,
            "effective_factors": [],
            "trigger_factors": [],
            "last_checkin_summary": None,
            "recent_symptom_checkin": None,
            "recent_symptom_logs": None,
            "recent_care_plan_checkin": None,
            "weeks_using_app": 0,
            "checkin_streak": 0,
        }
        
        # Get user profile
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        if profile:
            context["user_name"] = profile.name
        
        # Get user response (symptoms, conditions)
        user_response = self.db.query(UserResponse).filter(UserResponse.uid == uid).first()
        if user_response:
            # Extract top symptoms from their onboarding
            # Check top_concern first
            if user_response.top_concern:
                context["primary_concern"] = user_response.top_concern
                context["top_symptoms"] = [user_response.top_concern]
            
            # Aggregate other concerns if needed
            all_concerns = []
            if user_response.body_concerns and isinstance(user_response.body_concerns, list):
                all_concerns.extend(user_response.body_concerns)
            if user_response.period_concerns and isinstance(user_response.period_concerns, list):
                all_concerns.extend(user_response.period_concerns)
                
            if all_concerns:
                # Update top symptoms list (up to 3)
                current_top = context["top_symptoms"]
                for c in all_concerns:
                    if c not in current_top and len(current_top) < 3:
                        current_top.append(c)
                context["top_symptoms"] = current_top
                
                # Fallback for primary concern if top_concern was empty
                if context["primary_concern"] == "symptoms" and all_concerns:
                    context["primary_concern"] = all_concerns[0]
        
        # Get cycle info
        try:
            from app.services.cycle_service import CycleService
            cycle_service = CycleService(self.db)
            cycle_info = cycle_service.get_cycle_phase_info(uid)
            context["cycle_phase"] = cycle_info.phase
            context["cycle_day"] = cycle_info.cycle_day
        except Exception as e:
            logger.warning(f"Could not get cycle info: {e}")
        
        # Get recent check-ins for patterns
        recent_checkins = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == True
            )
        ).order_by(desc(WeeklyCheckIn.completed_at)).limit(4).all()
        
        if recent_checkins:
            context["checkin_streak"] = len(recent_checkins)
            
            # Analyze trends
            severities = [c.concern_severity for c in recent_checkins if c.concern_severity]
            if len(severities) >= 2:
                context["recent_severity"] = severities[0]
                context["improving_trend"] = severities[0] < severities[-1]
            
            # Collect effective factors
            for checkin in recent_checkins:
                if checkin.factors_positive:
                    context["effective_factors"].extend(checkin.factors_positive)
                if checkin.factors_negative:
                    context["trigger_factors"].extend(checkin.factors_negative)
            
            # Last check-in summary
            if recent_checkins[0].conversation_summary:
                context["last_checkin_summary"] = recent_checkins[0].conversation_summary

            # Recent symptom check-ins (daily)
            try:
                from app.core.database import SymptomCheckInThread

                recent_symptom_threads = (
                    self.db.query(SymptomCheckInThread)
                    .filter(SymptomCheckInThread.uid == uid)
                    .order_by(desc(SymptomCheckInThread.local_date), desc(SymptomCheckInThread.updated_at))
                    .limit(3)
                    .all()
                )

                if recent_symptom_threads:
                    lines = []
                    for t in recent_symptom_threads:
                        day = t.local_date.isoformat() if t.local_date else ""
                        ai = t.actionable_insights or {}
                        parts = []
                        if ai.get("progress"):
                            parts.append(f"progress={ai['progress']}")
                        if ai.get("severity_rating"):
                            parts.append(f"severity={ai['severity_rating']}/9")
                        if ai.get("triggers_identified"):
                            parts.append(f"triggers={', '.join(ai['triggers_identified'][:3])}")
                        if ai.get("relief_factors_identified"):
                            parts.append(f"helped={', '.join(ai['relief_factors_identified'][:3])}")

                        summary = (t.rolling_summary or "").strip()
                        if summary:
                            parts.append(f"summary={summary}")

                        if parts:
                            lines.append(f"[{day}] " + " | ".join(parts))

                    context["recent_symptom_checkin"] = "\n".join(lines) if lines else None
            except Exception:
                # Non-fatal: weekly check-in should still run if symptom check-in isn't available
                context["recent_symptom_checkin"] = None

            # Recent structured symptom logs (14 days; compact)
            try:
                from app.core.database import SymptomLog

                start_dt = datetime.utcnow() - timedelta(days=14)
                logs = (
                    self.db.query(SymptomLog)
                    .filter(and_(SymptomLog.user_id == uid, SymptomLog.logged_at >= start_dt))
                    .order_by(desc(SymptomLog.logged_at))
                    .limit(30)
                    .all()
                )

                if logs:
                    lines = []
                    for log in logs[:12]:
                        d = log.logged_date.isoformat() if getattr(log, "logged_date", None) else ""
                        st = (log.symptom_type or "").strip()
                        sev = getattr(log, "severity", None)
                        if st and sev:
                            lines.append(f"[{d}] {st}={sev}/9")
                    context["recent_symptom_logs"] = "\n".join(lines) if lines else None
            except Exception:
                context["recent_symptom_logs"] = None

            # Recent care plan check-ins (daily)
            try:
                from app.core.database import CarePlanCheckInThread

                care_threads = (
                    self.db.query(CarePlanCheckInThread)
                    .filter(CarePlanCheckInThread.uid == uid)
                    .order_by(desc(CarePlanCheckInThread.local_date), desc(CarePlanCheckInThread.updated_at))
                    .limit(2)
                    .all()
                )

                if care_threads:
                    lines = []
                    for t in care_threads:
                        day = t.local_date.isoformat() if getattr(t, "local_date", None) else ""
                        ai = t.actionable_insights or {}
                        parts = []
                        if ai.get("wins"):
                            parts.append("wins=" + ", ".join(ai["wins"][:3]))
                        if ai.get("blockers"):
                            parts.append("blockers=" + ", ".join(ai["blockers"][:3]))
                        if ai.get("actions_to_skip"):
                            parts.append("skip=" + ", ".join(ai["actions_to_skip"][:3]))
                        summary = (t.rolling_summary or "").strip()
                        if summary:
                            parts.append(f"summary={summary}")
                        if parts:
                            lines.append(f"[{day}] " + " | ".join(parts))
                    context["recent_care_plan_checkin"] = "\n".join(lines) if lines else None
            except Exception:
                context["recent_care_plan_checkin"] = None
        
        return context
    
    # ═══════════════════════════════════════════════════════════════════════════
    # DYNAMIC QUESTION GENERATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _get_slider_labels_for_symptom(self, symptom: str) -> List[str]:
        """Get appropriate slider labels based on symptom type."""
        symptom_lower = symptom.lower()
        
        if any(x in symptom_lower for x in ["energy", "mood", "motivation", "focus"]):
            return ["Low", "Moderate", "High"]
        elif any(x in symptom_lower for x in ["pain", "cramps", "headache", "bloating", "acne"]):
            return ["None", "Moderate", "Severe"]
        elif any(x in symptom_lower for x in ["sleep", "rest"]):
            return ["Poor", "Okay", "Great"]
        else:
            return ["Not at all", "Somewhat", "Very much"]

    def generate_opening_question(self, uid: str, checkin: WeeklyCheckIn) -> AIQuestion:
        """
        Generate the first question - always about their primary symptom.
        Personalized based on history and cycle phase.
        """
        context = self.get_user_context(uid)
        symptom = checkin.top_concern or context.get("primary_concern", "symptoms")
        
        # Choose message based on context
        if context.get("last_checkin_summary"):
            # Returning user - reference last check-in
            message = f"How was your {symptom.lower()} this week?"
        elif context.get("checkin_streak", 0) == 0:
            # First check-in
            message = f"Let's start with your {symptom.lower()}. How has it been this week?"
        else:
            message = f"How was your {symptom.lower()} this week?"
        
        # Customize slider labels based on symptom
        slider_labels = self._get_slider_labels_for_symptom(symptom)
        
        return AIQuestion(
            question_key="concern_severity",
            question_type=QuestionType.SLIDER,
            message=message,
            tap_options=[],
            slider_labels=slider_labels,
            is_required=True
        )
    
    async def generate_followup_question(
        self, 
        uid: str, 
        checkin: WeeklyCheckIn,
        previous_response: Any,
        previous_question_key: str
    ) -> Optional[AIQuestion]:
        """
        Generate the next question based on previous response.
        Uses LLM to generate dynamic questions and options.
        """
        context = self.get_user_context(uid)
        
        # If we just finished the severity slider, we can use the LLM to start the investigation
        # Or if we are in the middle of the conversation
        
        # Build conversation history for LLM
        history = []
        if checkin.raw_messages:
            for msg in checkin.raw_messages:
                role = "assistant" if msg.get("role") == "assistant" else "user"
                history.append({"role": role, "content": msg.get("content", "")})
        
        # Add the most recent user response if not already in history (it should be added by service)
        # But just in case, we rely on raw_messages being up to date.
        
        return await self.generate_with_llm(uid, checkin, history, context)

    # ═══════════════════════════════════════════════════════════════════════════
    # LLM INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def generate_with_llm(
        self, 
        uid: str, 
        checkin: WeeklyCheckIn,
        conversation_so_far: List[Dict],
        context: Dict[str, Any]
    ) -> Optional[AIQuestion]:
        """
        Use LLM to generate truly dynamic questions.
        """
        # HARD LIMIT: Force completion after 3 doctor turns (6 total messages: 3 questions + 3 responses)
        # Count assistant messages (doctor's turns)
        doctor_turns = sum(1 for msg in conversation_so_far if msg.get("role") == "assistant")
        if doctor_turns >= 3:
            logger.info(f"Forcing completion after {doctor_turns} doctor turns - generating completion message")
            # Generate a warm completion message
            symptom = checkin.top_concern or "your symptoms"
            return await self._generate_forced_completion_message(uid, checkin, conversation_so_far, context)
        
        symptom = checkin.top_concern or context.get("primary_concern", "symptoms")
        
        user_name = context.get('user_name', 'there')
        
        # Build rich context string for the prompt
        medical_context = context.get("medical_context", {})
        
        # Format previous check-in data
        prev_checkin_context = ""
        if context.get("last_severity"):
            trend = "improved" if context.get("improving_trend") else "worsened"
            prev_checkin_context = f"""
            LAST WEEK'S CHECK-IN:
            - Severity: {context.get('last_severity')}/9
            - Trend: Symptoms have {trend} since last week
            - Previous Triggers: {', '.join(context.get('previous_triggers', []))}
            - Previous Relief: {', '.join(context.get('previous_relief_factors', []))}
            - Summary: {context.get('last_checkin_summary', 'None')}
            """
        
        # Format action plan data
        action_plan_context = ""
        if context.get("last_week_actions"):
            completed = [a['title'] for a in context.get("last_week_actions", []) if a['completed']]
            action_plan_context = f"""
            LAST WEEK'S ACTION PLAN:
            - Completed Actions: {', '.join(completed) if completed else 'None'}
            - Liked Actions: {', '.join(context.get('liked_actions', []))}
            - Disliked Actions: {', '.join(context.get('disliked_actions', []))}
            """

        system_prompt = f"""
You are Dr. Auvra, an empathetic women's health specialist conducting a brief weekly check-in.

PATIENT: {user_name}
CONCERN: {symptom}
CYCLE: {context.get('cycle_phase', 'Unknown')} (Day {context.get('cycle_day', 'Unknown')})
{prev_checkin_context}
{action_plan_context}

MEDICAL KNOWLEDGE ({symptom.upper()}):
- Triggers: {', '.join(medical_context.get('common_triggers', [])[:5])}
- Relief: {', '.join(medical_context.get('relief_factors', [])[:5])}

RECENT DAILY SYMPTOM CHECK-INS (if available):
{context.get('recent_symptom_checkin', 'None')}

RECENT SYMPTOM LOGS (structured; if available):
{context.get('recent_symptom_logs', 'None')}

RECENT CARE PLAN CHECK-INS (daily; if available):
{context.get('recent_care_plan_checkin', 'None')}

YOUR GOAL: Quickly identify what caused symptoms to improve/worsen this week.
- If better → What helped? (to reinforce in action plan)
- If worse → What triggered it? (to avoid in action plan)

CRITICAL RESPONSE RULES:
1. KEEP RESPONSES SHORT - Max 2 sentences per message
2. SPLIT INTO MULTIPLE MESSAGES - Return an array of 2 short messages, not 1 long one
3. First message: Acknowledge/empathize (1 sentence)
4. Second message: Ask ONE specific question (1 sentence)
5. Generate 4-6 tap options
6. Tap options MUST be plausible *patient answers* to the question you asked (message 2).
    - They must be direct responses, not advice.
    - They must match the topic of your question (e.g., if you ask about diet changes, all options should be diet changes).
    - Do NOT include "Something else" or "Other" as an option. The system adds this automatically.

EXAMPLE GOOD RESPONSE:
{{
    "messages": [
        "I'm sorry your stress has been strong this week, {user_name}.",
        "Has anything changed at work or home that might have contributed?"
    ],
    "tap_options": [
        {{"id": "work_stress", "text": "Work has been demanding"}},
        {{"id": "sleep_issues", "text": "I haven't been sleeping well"}},
        {{"id": "personal_issues", "text": "Personal issues came up"}},
        {{"id": "same_as_usual", "text": "Everything's been about the same"}}
    ],
    "is_complete": false
}}

EXAMPLE BAD RESPONSE (TOO LONG):
{{
    "messages": ["I'm sorry to hear that your stress has been strong this week, {user_name}. Compared to last week, has anything changed in your routine or environment that might have contributed to this increase?"],
    ...
}}

COMPLETION (after 2-3 questions):
When is_complete: true, provide a WARM, HIGHLY PERSONALIZED summary (max 3 short messages):
- Reference SPECIFIC triggers/relief factors the user mentioned
- Tell them EXACTLY how their action plan will change tomorrow
{{
    "messages": [
        "Thank you for sharing about your {symptom} this week, {user_name}! 💜",
        "I noted that [specific trigger they mentioned] affected you, and [specific relief they mentioned] really helped.",
        "Starting tomorrow onwards, I'll adjust your action plan to include more [relief-related activities] and help you manage [trigger]. You're doing great!"
    ],
    "tap_options": [],
    "is_complete": true,
    "insights": {{
        "triggers_identified": ["work stress", "poor sleep"],
        "relief_factors_identified": ["meditation", "walking"],
        "severity_trend": "worsening",
        "suggested_additions": ["evening relaxation routine"],
        "key_insight": "Work stress is main trigger"
    }}
}}

OUTPUT JSON:
{{
    "messages": ["short msg 1", "short msg 2"],
    "tap_options": [...],
    "is_complete": boolean,
    "insights": {{...}} // Only when is_complete: true
}}
"""
        
        try:
            # ================================================================
            # API CALL: OpenAI PRIMARY, Groq FALLBACK on ANY error
            # ================================================================
            response = None
            openai_error = None
            
            # Try OpenAI first
            if openai_client:
                logger.info("🚀 Trying OpenAI for weekly check-in...")
                try:
                    response = await openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            *conversation_so_far
                        ],
                        temperature=0.7,
                        response_format={"type": "json_object"}
                    )
                    logger.info("✅ OpenAI response received")
                except Exception as e:
                    openai_error = str(e)
                    logger.warning(f"❌ OpenAI failed: {openai_error[:200]}")
            else:
                openai_error = "No OpenAI API key"
            
            # Fallback to Groq if OpenAI failed
            if openai_error and groq_client:
                logger.info(f"🔄 Falling back to Groq with {GROQ_FALLBACK_MODEL}...")
                
                # gpt-oss models are reasoning models - don't support response_format
                is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                enhanced_prompt = system_prompt
                if is_reasoning_model:
                    enhanced_prompt += "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no thinking, no preamble."
                
                try:
                    create_params = {
                        "model": GROQ_FALLBACK_MODEL,
                        "messages": [
                            {"role": "system", "content": enhanced_prompt},
                            *conversation_so_far
                        ],
                        "temperature": 0.7
                    }
                    # Only add response_format for non-reasoning models
                    if not is_reasoning_model:
                        create_params["response_format"] = {"type": "json_object"}
                    
                    response = await groq_client.chat.completions.create(**create_params)
                    logger.info("✅ Groq fallback successful!")
                except Exception as e:
                    logger.error(f"❌ Groq also failed: {e}")
                    return self._generate_post_severity_question(checkin, 5, context)
            elif openai_error:
                logger.error("❌ OpenAI failed and no Groq available for fallback")
                return self._generate_post_severity_question(checkin, 5, context)
            
            content = response.choices[0].message.content
            
            # Clean content for reasoning models that might output markdown
            cleaned_content = content.strip()
            if cleaned_content.startswith("```json"):
                cleaned_content = cleaned_content[7:]
            if cleaned_content.startswith("```"):
                cleaned_content = cleaned_content[3:]
            if cleaned_content.endswith("```"):
                cleaned_content = cleaned_content[:-3]
            cleaned_content = cleaned_content.strip()
            
            try:
                # Parse JSON first
                parsed_json = json.loads(cleaned_content)
                
                # Validate with Pydantic
                validated_response = CheckInResponseModel.model_validate(parsed_json)
                data = validated_response.model_dump()
                logger.info("✅ Pydantic validation SUCCESSFUL for weekly check-in")
                
            except ValidationError as ve:
                logger.error(f"❌ Pydantic Validation Failed: {ve}")
                logger.error(f"Content was: {content[:1000]}...")
                for err in ve.errors():
                    logger.error(f"   -> Field: {err['loc']}, Error: {err['msg']}")
                # Fallback to severity question on validation failure
                return self._generate_post_severity_question(checkin, 5, context)
            except json.JSONDecodeError as je:
                logger.error(f"❌ JSON Decode Failed: {je}")
                return self._generate_post_severity_question(checkin, 5, context)
            
            if data.get("is_complete"):
                # Store insights in checkin for action plan generation
                insights = data.get("insights", {})
                if insights:
                    checkin.actionable_insights = insights
                    checkin.factors_negative = insights.get("triggers_identified", [])
                    checkin.factors_positive = insights.get("relief_factors_identified", [])
                
                # Combine messages into one for storage, but return as array for frontend
                messages = data.get("messages", [])
                if isinstance(messages, list):
                    combined_message = " ".join(messages)
                else:
                    combined_message = messages
                
                # Store completion message
                raw_messages = checkin.raw_messages or []
                for msg in messages if isinstance(messages, list) else [messages]:
                    raw_messages.append({
                        "role": "assistant",
                        "content": msg,
                        "question_key": "completion",
                        "timestamp": datetime.utcnow().isoformat()
                    })
                checkin.raw_messages = raw_messages
                
                # Return None to signal completion
                return None
            
            # Handle multiple messages format
            messages = data.get("messages", [])
            if isinstance(messages, str):
                messages = [messages]
            
            # Combine for the single message field, but also store separately
            combined_message = " ".join(messages) if messages else data.get("message", "")
            
            # Map to AIQuestion with messages array for frontend
            tap_options = self._postprocess_tap_options(
                question_text=(messages[-1] if messages else combined_message),
                tap_options=data.get("tap_options", []),
            )
            
            # Create AIQuestion with additional messages field
            question = AIQuestion(
                question_key=f"dynamic_{len(conversation_so_far)}",
                question_type=QuestionType.TAP_CHOICE if tap_options else QuestionType.FREE_TEXT,
                message=combined_message,
                tap_options=tap_options,
                is_required=True
            )
            # Attach messages array for frontend multi-bubble display
            question.messages = messages
            
            return question
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            # Fallback to rule-based if LLM fails
            return self._generate_post_severity_question(checkin, 5, context)
    
    async def _generate_forced_completion_message(
        self,
        uid: str,
        checkin: WeeklyCheckIn,
        conversation_so_far: List[Dict],
        context: Dict[str, Any]
    ) -> Optional[AIQuestion]:
        """
        Generate a warm completion message when hard limit is reached.
        Uses LLM to summarize the conversation and provide reassurance.
        """
        symptom = checkin.top_concern or "your symptoms"
        user_name = context.get('user_name', 'there')
        
        # Build conversation for summary
        history_text = ""
        for msg in conversation_so_far:
            role = "Doctor" if msg.get("role") == "assistant" else "Patient"
            history_text += f"{msg.get('content', '')}\n"
        
        prompt = f"""
You are Dr. Auvra completing a weekly check-in with {user_name} about their {symptom}.

CONVERSATION:
{history_text}

Generate a JSON response with a WARM, HIGHLY PERSONALIZED completion message that:
1. Acknowledges SPECIFICALLY what the user shared (triggers, relief factors, severity changes)
2. Makes them feel deeply understood and validated - reference their exact words
3. CRITICALLY: Tell them EXACTLY how their action plan will change tomorrow based on this check-in
4. Use their name and symptom naturally throughout

EXAMPLE OUTPUT (notice how specific it is about action plan changes):
{{
    "messages": [
        "Thank you for sharing about your {symptom} this week, {user_name}! 💜",
        "I noted that [specific trigger they mentioned] has been making things harder, and that [specific relief they mentioned] has really helped you.",
        "Starting tomorrow onwards, I'll adjust your action plan to include more [relief-related activities] and help you avoid [trigger-related situations]. You're doing great!"
    ],
    "insights": {{
        "triggers_identified": ["work stress", "poor sleep"],
        "relief_factors_identified": ["morning meditation", "herbal tea"],
        "severity_trend": "worsening",
        "suggested_additions": ["evening relaxation routine", "stress-reducing movement"],
        "suggested_removals": ["high-intensity exercise"],
        "key_insight": "Work stress is the main trigger - needs calming activities"
    }}
}}

CRITICAL RULES:
- Messages MUST be personalized with SPECIFIC details from conversation (not generic "stress affected you")
- ALWAYS explain HOW the action plan will change based on their specific insights
- Messages must be SHORT (1 sentence each, max 3 messages)
- severity_trend: "improving" | "worsening" | "stable"
- suggested_additions: Specific actions to add based on what HELPED them
- suggested_removals: Actions to remove based on what TRIGGERED symptoms
- key_insight: One sentence summary that captures the main takeaway
- Make the user feel TRULY UNDERSTOOD and CARED FOR
"""
        
        try:
            # ================================================================
            # API CALL: OpenAI PRIMARY, Groq FALLBACK on ANY error
            # ================================================================
            response = None
            openai_error = None
            
            # Try OpenAI first
            if openai_client:
                logger.info("🚀 Trying OpenAI for completion...")
                try:
                    response = await openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        response_format={"type": "json_object"}
                    )
                    logger.info("✅ OpenAI response received")
                except Exception as e:
                    openai_error = str(e)
                    logger.warning(f"❌ OpenAI failed: {openai_error[:200]}")
            else:
                openai_error = "No OpenAI API key"
            
            # Fallback to Groq if OpenAI failed
            if openai_error and groq_client:
                logger.info(f"🔄 Falling back to Groq with {GROQ_FALLBACK_MODEL}...")
                
                # gpt-oss models are reasoning models - don't support response_format
                is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                enhanced_prompt = prompt
                if is_reasoning_model:
                    enhanced_prompt += "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no thinking, no preamble."
                
                try:
                    create_params = {
                        "model": GROQ_FALLBACK_MODEL,
                        "messages": [{"role": "user", "content": enhanced_prompt}],
                        "temperature": 0.7
                    }
                    if not is_reasoning_model:
                        create_params["response_format"] = {"type": "json_object"}
                    
                    response = await groq_client.chat.completions.create(**create_params)
                    logger.info("✅ Groq fallback successful!")
                except Exception as e:
                    logger.error(f"❌ Groq also failed: {e}")
                    raise Exception("Both OpenAI and Groq failed")
            elif openai_error:
                raise Exception(f"OpenAI failed and no Groq fallback: {openai_error}")
            
            content = response.choices[0].message.content
            
            # Clean content for reasoning models
            cleaned_content = content.strip()
            if cleaned_content.startswith("```json"):
                cleaned_content = cleaned_content[7:]
            if cleaned_content.startswith("```"):
                cleaned_content = cleaned_content[3:]
            if cleaned_content.endswith("```"):
                cleaned_content = cleaned_content[:-3]
            cleaned_content = cleaned_content.strip()
            
            data = json.loads(cleaned_content)
            messages_list = data.get("messages", [])
            insights = data.get("insights", {})
            
            # Store insights in checkin for action plan generation
            checkin.actionable_insights = insights
            checkin.factors_negative = insights.get("triggers_identified", [])
            checkin.factors_positive = insights.get("relief_factors_identified", [])
            
            # Store completion messages in raw_messages
            raw_messages = checkin.raw_messages or []
            for msg in messages_list:
                raw_messages.append({
                    "role": "assistant",
                    "content": msg,
                    "question_key": "completion",
                    "timestamp": datetime.utcnow().isoformat()
                })
            checkin.raw_messages = raw_messages
            
            # Return None to signal completion
            logger.info(f"Generated forced completion with insights: {insights}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to generate completion message: {e}")
            # Fallback to a generic completion message
            messages = checkin.raw_messages or []
            fallback_msg = f"Thank you for sharing about your {symptom} this week. I'll use this to personalize your action plan. Take care! 💜"
            messages.append({
                "role": "assistant",
                "content": fallback_msg,
                "question_key": "completion",
                "timestamp": datetime.utcnow().isoformat()
            })
            checkin.raw_messages = messages
            return None


    def _generate_closing_question(self, checkin: WeeklyCheckIn, context: Dict[str, Any]) -> Optional[AIQuestion]:
        """
        Generate the final closing message/question when conversation is complete.
        Returns None to signal completion to the service.
        """
        # We return None here because the service handles the actual completion logic
        # when it receives None from generate_followup_question.
        return None

    def _generate_post_severity_question(self, checkin: WeeklyCheckIn, severity: int, context: Dict[str, Any]) -> AIQuestion:
        """Fallback question generation if LLM fails."""
        symptom = checkin.top_concern or "symptoms"
        
        if severity >= 7:
            message = f"I'm sorry to hear that {symptom.lower()} is bothering you. Can you tell me more about what's happening?"
        elif severity >= 4:
            message = f"I see. What do you think might be contributing to your {symptom.lower()} this week?"
        else:
            message = f"That's good! What have you been doing differently that might be helping?"
            
        return AIQuestion(
            question_key="fallback_followup",
            question_type=QuestionType.FREE_TEXT,
            message=message,
            tap_options=[],
            is_required=True
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SUMMARY GENERATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def generate_summary(self, checkin: WeeklyCheckIn, context: Dict[str, Any]) -> str:
        """
        Generate a natural language summary of the check-in.
        This is stored and used for future personalization.
        """
        # Use LLM to generate a high-quality summary if possible
        try:
            # Extract conversation history
            history_text = ""
            if checkin.raw_messages:
                for msg in checkin.raw_messages:
                    role = "Doctor" if msg.get("role") == "assistant" else "Patient"
                    history_text += f"{role}: {msg.get('content', '')}\n"
            
            symptom = checkin.top_concern or "symptoms"
            
            prompt = f"""
            Summarize this weekly check-in conversation between Dr. Auvra and a patient regarding {symptom}.
            
            CONVERSATION:
            {history_text}
            
            OUTPUT FORMAT:
            Create a concise, warm summary (2-3 sentences) addressed TO THE PATIENT that captures:
            1. Their current status/severity
            2. Identified triggers or causes
            3. What helped or didn't help
            
            Write it in the second person (e.g., "You mentioned your bloating increased due to...")
            """
            
            # We can't use async here easily if this is called from a sync context, 
            # but generate_summary is usually called from complete_checkin which is sync in some places
            # For now, we'll stick to the rule-based approach as a fallback or if async is an issue,
            # but ideally this should be an LLM call.
            
            # Since we are in an async service flow usually, let's assume we can't easily await here 
            # without refactoring complete_checkin to be async.
            # So we will improve the rule-based summary to be more "patient-facing" style.
            
        except Exception:
            pass
            
        parts = []
        
        # Symptom summary
        symptom = checkin.top_concern or "symptoms"
        severity = checkin.concern_severity
        
        status = "stable"
        if context.get("last_severity"):
            diff = severity - context.get("last_severity")
            if diff <= -2: status = "significantly improved"
            elif diff < 0: status = "improved"
            elif diff >= 2: status = "significantly worsened"
            elif diff > 0: status = "worsened"
            
        parts.append(f"You mentioned your {symptom.lower()} is {status} this week (Severity: {severity}/9).")
        
        # Factors
        if checkin.factors_negative:
            triggers = ", ".join(checkin.factors_negative)
            parts.append(f"It seems {triggers} might have triggered it.")
        
        if checkin.factors_positive:
            helpers = ", ".join(checkin.factors_positive)
            parts.append(f"You found that {helpers} helped.")
            
        # Wellbeing
        if checkin.overall_wellbeing:
            parts.append(f"Your overall wellbeing was {checkin.overall_wellbeing}/9.")
        
        return " ".join(parts)
