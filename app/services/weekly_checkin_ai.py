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
═══════════════════════════════════════════════════════════════════════════════
"""
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from openai import AsyncOpenAI

from app.core.database import (
    WeeklyCheckIn, UserProfile, SymptomLog, 
    ActionPlan, ActionPlanItem, UserResponse
)
from app.core.config import Settings
from app.utils.timezone_utils import get_user_current_date

settings = Settings()
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

logger = logging.getLogger(__name__)


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
    message: str
    tap_options: List[Dict[str, str]]
    follow_up_context: Optional[str] = None
    is_required: bool = True
    slider_labels: Optional[Dict[str, str]] = None  # For slider: {1: "None", 9: "Extreme"}


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
    ]
    
    def __init__(self, db: Session):
        self.db = db
    
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
            "primary_concern": "bloating",  # Default
            "top_symptoms": [],
            "cycle_phase": None,
            "cycle_day": None,
            "recent_severity": None,
            "improving_trend": None,
            "effective_factors": [],
            "trigger_factors": [],
            "last_checkin_summary": None,
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
                if context["primary_concern"] == "bloating" and all_concerns:
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
        
        return context
    
    # ═══════════════════════════════════════════════════════════════════════════
    # DYNAMIC QUESTION GENERATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def generate_opening_question(self, uid: str, checkin: WeeklyCheckIn) -> AIQuestion:
        """
        Generate the first question - always about their primary symptom.
        Personalized based on history and cycle phase.
        """
        context = self.get_user_context(uid)
        symptom = checkin.top_concern or context.get("primary_concern", "bloating")
        
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
        symptom = checkin.top_concern or context.get("primary_concern", "bloating")
        
        system_prompt = f"""
        You are Dr. Auvra, an empathetic and knowledgeable health companion conducting a weekly check-in.
        Your goal is to understand the user's week, focusing on their primary concern: {symptom}.
        
        Context:
        - User Name: {context.get('user_name', 'User')}
        - Cycle Phase: {context.get('cycle_phase', 'Unknown')} (Day {context.get('cycle_day', 'Unknown')})
        - Recent Trend: {context.get('improving_trend', 'Unknown')}
        
        Instructions:
        1. Analyze the conversation history.
        2. Acknowledge the user's input with empathy (keep it brief).
        3. Ask ONE relevant follow-up question to investigate potential causes (diet, sleep, stress, cycle, etc.) or what helped.
        4. Generate 3-5 short, likely answers (tap options) for the user.
        5. If you have enough information to form a comprehensive summary/plan (usually after 3-4 exchanges), set "is_complete" to true.
        
        Output JSON format:
        {{
            "message": "...",
            "tap_options": [{{"id": "...", "text": "..."}}],
            "is_complete": boolean
        }}
        """
        
        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    *conversation_so_far
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            data = json.loads(content)
            
            if data.get("is_complete"):
                # Generate a closing summary/question
                return self._generate_closing_question(checkin, context)
            
            # Map to AIQuestion
            tap_options = data.get("tap_options", [])
            # Ensure tap options have IDs
            for i, opt in enumerate(tap_options):
                if "id" not in opt:
                    opt["id"] = f"opt_{i}"
            
            return AIQuestion(
                question_key=f"dynamic_{len(conversation_so_far)}",
                question_type=QuestionType.TAP_CHOICE if tap_options else QuestionType.FREE_TEXT,
                message=data.get("message", ""),
                tap_options=tap_options,
                is_required=True
            )
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            # Fallback to rule-based if LLM fails
            return self._generate_post_severity_question(checkin, 5, context)

    
    # ═══════════════════════════════════════════════════════════════════════════
    # SUMMARY GENERATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def generate_summary(self, checkin: WeeklyCheckIn, context: Dict[str, Any]) -> str:
        """
        Generate a natural language summary of the check-in.
        This is stored and used for future personalization.
        """
        parts = []
        
        # Symptom summary
        symptom = checkin.top_concern or "symptoms"
        severity = checkin.concern_severity
        if severity:
            if severity <= 3:
                parts.append(f"Had minimal {symptom.lower()} this week")
            elif severity <= 6:
                parts.append(f"Experienced moderate {symptom.lower()}")
            else:
                parts.append(f"Struggled with significant {symptom.lower()}")
        
        # Factors
        if checkin.factors_negative:
            triggers = ", ".join(checkin.factors_negative[:3])
            parts.append(f"Triggers included: {triggers}")
        
        if checkin.factors_positive:
            helpers = ", ".join(checkin.factors_positive[:3])
            parts.append(f"Helpful factors: {helpers}")
        
        # Wellbeing
        if checkin.overall_wellbeing:
            if checkin.overall_wellbeing >= 7:
                parts.append("Overall feeling positive")
            elif checkin.overall_wellbeing >= 4:
                parts.append("Overall feeling okay")
            else:
                parts.append("Overall feeling low")
        
        # Concerns
        if checkin.concerns_next_week and checkin.concerns_next_week != "Nothing specific":
            parts.append(f"Looking ahead: {checkin.concerns_next_week}")
        
        return ". ".join(parts) + "." if parts else "Check-in completed."
