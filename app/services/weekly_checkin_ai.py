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

from app.core.database import (
    WeeklyCheckIn, UserProfile, SymptomLog, 
    ActionPlan, ActionPlanItem, UserResponse
)
from app.utils.timezone_utils import get_user_current_date

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
            if user_response.current_symptoms:
                symptoms = user_response.current_symptoms
                if isinstance(symptoms, list) and symptoms:
                    context["top_symptoms"] = symptoms[:3]
                    context["primary_concern"] = symptoms[0] if symptoms else "bloating"
        
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
    
    def generate_followup_question(
        self, 
        uid: str, 
        checkin: WeeklyCheckIn,
        previous_response: Any,
        previous_question_key: str
    ) -> Optional[AIQuestion]:
        """
        Generate the next question based on previous response.
        This is where the AI "decides" what to ask next.
        """
        context = self.get_user_context(uid)
        
        # Decision tree based on conversation state
        if previous_question_key == "concern_severity":
            return self._generate_post_severity_question(checkin, previous_response, context)
        
        elif previous_question_key == "factors_negative":
            return self._generate_positive_factors_question(checkin, context)
        
        elif previous_question_key == "factors_positive":
            return self._generate_action_reflection_question(checkin, context)
        
        elif previous_question_key == "action_reflection":
            return self._generate_wellbeing_question(checkin, context)
        
        elif previous_question_key == "overall_wellbeing":
            return self._generate_closing_question(checkin, context)
        
        elif previous_question_key == "closing" or previous_question_key == "concerns_next_week":
            # End of check-in
            return None
        
        return None
    
    def _generate_post_severity_question(
        self, 
        checkin: WeeklyCheckIn, 
        severity: int,
        context: Dict[str, Any]
    ) -> AIQuestion:
        """Generate follow-up after severity rating."""
        
        # Categorize severity
        if severity <= 3:
            category = "low"
            message = "That's encouraging! 🌟 Were there things that made it worse at times?"
        elif severity <= 6:
            category = "medium"
            message = "I see. Did any of these affect it?"
        else:
            category = "high"
            message = "I'm sorry to hear that. Let's figure out what might have triggered it."
        
        # Get personalized negative factors
        # Prioritize factors that have been triggers before
        options = self._get_personalized_factors(
            self.NEGATIVE_FACTORS,
            context.get("trigger_factors", []),
            limit=6
        )
        
        return AIQuestion(
            question_key="factors_negative",
            question_type=QuestionType.MULTI_SELECT,
            message=message,
            tap_options=options,
            is_required=True
        )
    
    def _generate_positive_factors_question(
        self, 
        checkin: WeeklyCheckIn,
        context: Dict[str, Any]
    ) -> AIQuestion:
        """Generate question about positive factors."""
        
        # Adjust message based on severity
        if checkin.concern_severity and checkin.concern_severity <= 4:
            message = "What helped you feel better this week? 💜"
        else:
            message = "Despite the challenges, was there anything that helped?"
        
        # Prioritize factors that have worked before
        options = self._get_personalized_factors(
            self.POSITIVE_FACTORS,
            context.get("effective_factors", []),
            limit=6
        )
        
        return AIQuestion(
            question_key="factors_positive",
            question_type=QuestionType.MULTI_SELECT,
            message=message,
            tap_options=options,
            is_required=True
        )
    
    def _generate_action_reflection_question(
        self, 
        checkin: WeeklyCheckIn,
        context: Dict[str, Any]
    ) -> AIQuestion:
        """Generate question about action plan reflection."""
        
        message = "How did you find this week's action plan?"
        
        options = [
            {"id": "really_helpful", "text": "Really helpful 🌟"},
            {"id": "somewhat_helpful", "text": "Somewhat helpful"},
            {"id": "neutral", "text": "Neutral"},
            {"id": "too_difficult", "text": "Too difficult"},
            {"id": "didnt_follow", "text": "Didn't follow it"},
        ]
        
        return AIQuestion(
            question_key="action_reflection",
            question_type=QuestionType.TAP_CHOICE,
            message=message,
            tap_options=options,
            is_required=True
        )
    
    def _generate_wellbeing_question(
        self, 
        checkin: WeeklyCheckIn,
        context: Dict[str, Any]
    ) -> AIQuestion:
        """Generate overall wellbeing question."""
        
        message = "Overall, how would you rate your wellbeing this week?"
        
        return AIQuestion(
            question_key="overall_wellbeing",
            question_type=QuestionType.SLIDER,
            message=message,
            tap_options=[],
            slider_labels={
                "1": "Really low",
                "5": "Okay",
                "9": "Great!"
            },
            is_required=True
        )
    
    def _generate_closing_question(
        self, 
        checkin: WeeklyCheckIn,
        context: Dict[str, Any]
    ) -> AIQuestion:
        """Generate closing question or summary."""
        
        # Build a personalized closing
        symptom = checkin.top_concern or "bloating"
        severity = checkin.concern_severity or 5
        wellbeing = checkin.overall_wellbeing or 5
        
        # Create encouraging closing based on data
        if wellbeing >= 7 and severity <= 3:
            message = "You're doing amazing! 🌟 I'll keep optimizing your plan. Anything you want me to focus on next week?"
        elif wellbeing >= 5:
            message = "Thanks for sharing! I'll adjust your plan based on what you've told me. Any specific concerns for next week?"
        else:
            message = "I hear you. I'll make sure next week's plan addresses what you're going through. Is there anything specific you'd like help with?"
        
        return AIQuestion(
            question_key="concerns_next_week",
            question_type=QuestionType.FREE_TEXT,
            message=message,
            tap_options=[
                {"id": "nothing", "text": "Nothing specific"},
                {"id": "more_energy", "text": "More energy"},
                {"id": "better_sleep", "text": "Better sleep"},
                {"id": "less_stress", "text": "Less stress"},
            ],
            is_required=False
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _get_slider_labels_for_symptom(self, symptom: str) -> Dict[str, str]:
        """Get appropriate slider labels for different symptoms."""
        symptom_lower = symptom.lower()
        
        if symptom_lower in ["bloating", "cramps", "headaches", "pain"]:
            return {"1": "None", "3": "Mild", "5": "Moderate", "7": "Strong", "9": "Extreme"}
        elif symptom_lower in ["fatigue", "tiredness"]:
            return {"1": "Energetic", "3": "Okay", "5": "Tired", "7": "Exhausted", "9": "Drained"}
        elif symptom_lower in ["mood", "mood swings", "anxiety"]:
            return {"1": "Stable", "3": "Minor", "5": "Noticeable", "7": "Difficult", "9": "Overwhelming"}
        elif symptom_lower in ["acne", "skin"]:
            return {"1": "Clear", "3": "Few spots", "5": "Moderate", "7": "Significant", "9": "Severe"}
        else:
            return {"1": "None", "3": "Mild", "5": "Moderate", "7": "Strong", "9": "Extreme"}
    
    def _get_personalized_factors(
        self, 
        all_factors: List[Dict], 
        user_history: List[str],
        limit: int = 6
    ) -> List[Dict[str, str]]:
        """
        Get personalized factor options.
        Prioritizes factors the user has selected before.
        """
        # Count frequency of each factor in history
        factor_counts = {}
        for factor in user_history:
            factor_lower = factor.lower().replace(" ", "_")
            factor_counts[factor_lower] = factor_counts.get(factor_lower, 0) + 1
        
        # Sort factors by historical relevance
        sorted_factors = sorted(
            all_factors,
            key=lambda f: factor_counts.get(f["id"], 0),
            reverse=True
        )
        
        # Take top factors
        selected = sorted_factors[:limit]
        
        # Format for response
        return [
            {"id": f["id"], "text": f.get("emoji", "") + " " + f["text"]}
            for f in selected
        ]
    
    # ═══════════════════════════════════════════════════════════════════════════
    # LLM INTEGRATION (FUTURE ENHANCEMENT)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def generate_with_llm(
        self, 
        uid: str, 
        checkin: WeeklyCheckIn,
        conversation_so_far: List[Dict]
    ) -> Optional[AIQuestion]:
        """
        Use LLM to generate truly dynamic questions.
        
        This is a future enhancement that will:
        1. Send conversation context to GPT
        2. Get a structured response with question + type + options
        3. Validate and return
        
        For now, we use the rule-based system above.
        """
        # TODO: Implement LLM-powered question generation
        # 
        # prompt = f'''
        # You are Auvra, a caring health companion conducting a weekly check-in.
        # 
        # User Context:
        # {json.dumps(self.get_user_context(uid))}
        # 
        # Conversation so far:
        # {json.dumps(conversation_so_far)}
        # 
        # Generate the next question. Respond in JSON:
        # {{
        #     "message": "your question",
        #     "question_type": "slider|tap_choice|multi_select|free_text",
        #     "tap_options": [{{id, text}}] if applicable,
        #     "question_key": "unique_key"
        # }}
        # '''
        # 
        # response = await openai_client.chat.completions.create(...)
        # return parse_response(response)
        
        pass
    
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
