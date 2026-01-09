"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CONTEXT ENGINE - Deep Personalization & Pattern Recognition
═══════════════════════════════════════════════════════════════════════════════
Understanding the whole person, not just the data points.

CAPABILITIES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. CYCLE INTELLIGENCE - Deep awareness of where they are in their cycle
2. TIME AWARENESS - Time of day, day of week, life moment awareness
3. STREAK PSYCHOLOGY - Understanding and leveraging habit momentum
4. PATTERN RECOGNITION - Identifying meaningful patterns in behavior
5. PERSONALIZATION ENGINE - Adapting everything to this unique individual
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from datetime import datetime, date, timedelta, time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, func

logger = logging.getLogger(__name__)


class TimeOfDay(Enum):
    """Time of day categories."""
    EARLY_MORNING = "early_morning"  # 5-8am
    MORNING = "morning"  # 8-12pm
    AFTERNOON = "afternoon"  # 12-5pm
    EVENING = "evening"  # 5-9pm
    NIGHT = "night"  # 9pm-12am
    LATE_NIGHT = "late_night"  # 12-5am


class DayType(Enum):
    """Type of day."""
    WEEKDAY = "weekday"
    WEEKEND = "weekend"
    MONDAY = "monday"  # Special - often hard
    FRIDAY = "friday"  # Special - often relaxed


@dataclass
class CycleContext:
    """Comprehensive cycle awareness."""
    current_day: int
    phase: str
    phase_day: int  # Day within phase (e.g., day 2 of luteal)
    days_until_next_phase: int
    phase_percentage: float  # How far through this phase (0-1)
    
    # Phase characteristics
    energy_expectation: str  # "rising", "peak", "declining", "low"
    hormone_status: Dict[str, str]  # {"estrogen": "rising", "progesterone": "peak"}
    typical_experience: str  # What most people feel
    personalized_experience: Optional[str]  # What THIS person typically feels
    
    # Timing relevance
    approaching_period: bool
    just_finished_period: bool
    in_fertile_window: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_day": self.current_day,
            "phase": self.phase,
            "phase_day": self.phase_day,
            "days_until_next_phase": self.days_until_next_phase,
            "phase_percentage": self.phase_percentage,
            "energy_expectation": self.energy_expectation,
            "hormone_status": self.hormone_status,
            "typical_experience": self.typical_experience,
            "personalized_experience": self.personalized_experience,
            "approaching_period": self.approaching_period,
            "just_finished_period": self.just_finished_period,
            "in_fertile_window": self.in_fertile_window
        }


@dataclass
class TimeContext:
    """Rich time awareness."""
    local_time: datetime
    timezone: str
    time_of_day: TimeOfDay
    day_type: DayType
    
    # Specific timing
    is_early: bool  # Before 7am
    is_late: bool  # After 10pm
    is_meal_time: bool
    is_wind_down_time: bool  # Evening before bed
    
    # Relevance hints
    greeting_style: str  # "good_morning", "hey", "hope_youre_winding_down"
    energy_assumption: str  # "waking_up", "fully_awake", "winding_down"
    activity_relevance: Dict[str, float]  # {"movement": 0.8, "mindfulness": 0.9}


@dataclass
class StreakContext:
    """Habit momentum awareness."""
    current_streak: int
    longest_streak: int
    streak_health: str  # "strong", "building", "at_risk", "broken"
    
    # Engagement patterns
    days_since_last_activity: int
    consistency_rate: float  # 7-day rolling average
    trend: str  # "improving", "stable", "declining"
    
    # Motivation insights
    motivation_state: str  # "motivated", "needs_encouragement", "struggling"
    celebration_opportunity: bool  # Milestone reached?
    risk_alert: bool  # About to break streak?


@dataclass 
class PersonalizationContext:
    """What makes this person unique."""
    name: Optional[str]
    preferred_name: Optional[str]  # What they like to be called
    communication_style: str  # "direct", "gentle", "detailed"
    
    # Preferences learned
    responds_well_to: List[str]  # ["emojis", "encouragement", "data"]
    sensitive_topics: List[str]
    favorite_activities: List[str]
    
    # Health specifics
    primary_concerns: List[str]
    chronic_symptoms: List[str]
    diagnosed_conditions: List[str]
    
    # Lifestyle markers
    is_busy_professional: bool
    is_fitness_focused: bool
    is_new_to_tracking: bool


class ContextEngine:
    """
    The personalization brain that makes AUVRA feel like she truly knows you.
    
    This isn't about data - it's about understanding:
    - Where you are in your cycle journey
    - What time means for your energy
    - How your habits are building
    - What makes YOU unique
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    async def build_full_context(
        self,
        user_id: str,
        timezone: str = "UTC",
        profile_gaps: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build comprehensive context for deeply personalized responses.
        
        Args:
            user_id: The user's ID
            timezone: User's timezone
            profile_gaps: Optional gaps from MemoryEngine._load_profile_gaps()
        """
        context = {
            "cycle": await self._build_cycle_context(user_id),
            "time": self._build_time_context(timezone),
            "streak": await self._build_streak_context(user_id),
            "personalization": await self._build_personalization_context(user_id),
            "moment": await self._assess_current_moment(user_id),
            "profiling": self._build_profiling_context(profile_gaps) if profile_gaps else None
        }
        
        return context

    
    async def _build_cycle_context(self, user_id: str) -> CycleContext:
        """Build deep cycle awareness."""
        from app.services.cycle_service import CycleService
        from app.core.database import UserResponse, SymptomLog
        
        cycle_service = CycleService(self.db)
        cycle_info = cycle_service.get_cycle_phase_info(user_id)
        
        if not cycle_info:
            # Return default context for users without cycle data
            return CycleContext(
                current_day=0,
                phase="unknown",
                phase_day=0,
                days_until_next_phase=0,
                phase_percentage=0,
                energy_expectation="unknown",
                hormone_status={},
                typical_experience="",
                personalized_experience=None,
                approaching_period=False,
                just_finished_period=False,
                in_fertile_window=False
            )
        
        # Get cycle length for calculations
        user_response = self.db.query(UserResponse).filter(
            UserResponse.uid == user_id
        ).order_by(desc(UserResponse.created_at)).first()
        
        cycle_length = 28
        if user_response and user_response.cycle_length:
            length_map = {
                "Less than 21 days": 19,
                "21-25 days": 23,
                "26-30 days": 28,
                "31-35 days": 33,
                "35+ days": 40
            }
            cycle_length = length_map.get(user_response.cycle_length, 28)
        
        current_day = cycle_info.cycle_day
        phase = cycle_info.phase.lower() if cycle_info.phase else "unknown"
        
        # Calculate phase details
        ovulation_day = cycle_length - 14
        
        # Determine phase boundaries
        phase_boundaries = {
            "menstrual": (1, 5),
            "follicular": (6, ovulation_day - 1),
            "ovulation": (ovulation_day - 1, ovulation_day + 2),
            "luteal": (ovulation_day + 2, cycle_length)
        }
        
        # Find current phase details
        phase_start, phase_end = phase_boundaries.get(phase, (1, cycle_length))
        phase_day = current_day - phase_start + 1
        phase_length = phase_end - phase_start + 1
        phase_percentage = (phase_day / phase_length) if phase_length > 0 else 0
        
        # Calculate days until next phase
        days_until_next = phase_end - current_day + 1
        
        # Determine energy expectation
        energy_map = {
            "menstrual": "low" if current_day <= 2 else "rising",
            "follicular": "rising",
            "ovulation": "peak",
            "luteal": "declining" if phase_percentage > 0.5 else "stable"
        }
        energy_expectation = energy_map.get(phase, "unknown")
        
        # Determine hormone status
        hormone_status = self._get_hormone_status(phase, phase_percentage)
        
        # Get typical experience for this phase
        typical_experience = self._get_typical_phase_experience(phase, phase_percentage)
        
        # Check for personalized patterns
        personalized_experience = await self._get_personalized_experience(
            user_id, phase, phase_day
        )
        
        return CycleContext(
            current_day=current_day,
            phase=phase,
            phase_day=phase_day,
            days_until_next_phase=max(0, days_until_next),
            phase_percentage=round(phase_percentage, 2),
            energy_expectation=energy_expectation,
            hormone_status=hormone_status,
            typical_experience=typical_experience,
            personalized_experience=personalized_experience,
            approaching_period=current_day > cycle_length - 3,
            just_finished_period=current_day <= 6,
            in_fertile_window=abs(current_day - ovulation_day) <= 2
        )
    
    def _get_hormone_status(self, phase: str, phase_pct: float) -> Dict[str, str]:
        """Get hormone levels for current phase."""
        status_map = {
            "menstrual": {"estrogen": "low", "progesterone": "low", "fsh": "rising"},
            "follicular": {"estrogen": "rising", "progesterone": "low", "fsh": "high"},
            "ovulation": {"estrogen": "peak", "progesterone": "rising", "lh": "surge"},
            "luteal": {
                "estrogen": "moderate" if phase_pct < 0.5 else "declining",
                "progesterone": "peak" if phase_pct < 0.7 else "declining"
            }
        }
        return status_map.get(phase, {})
    
    def _get_typical_phase_experience(self, phase: str, phase_pct: float) -> str:
        """Get typical experience description."""
        experiences = {
            "menstrual": "Energy might be lower - your body is doing important work. Rest is valuable now.",
            "follicular": "Energy is building! Many people feel increasingly motivated and creative.",
            "ovulation": "Peak energy time for many. You might feel more social and confident.",
            "luteal": "Time to slow down a bit. Self-care becomes extra important." if phase_pct > 0.5 
                     else "Energy may be steady. Good time for focused, routine tasks."
        }
        return experiences.get(phase, "")
    
    async def _get_personalized_experience(
        self, user_id: str, phase: str, phase_day: int
    ) -> Optional[str]:
        """Get this specific user's historical experience in this phase."""
        from app.core.database import SymptomLog
        
        # Look at symptoms from the same phase in past cycles
        # Note: SymptomLog uses 'phase' column, not 'cycle_phase'
        similar_symptoms = self.db.query(SymptomLog).filter(
            and_(
                SymptomLog.user_id == user_id,
                SymptomLog.phase.ilike(f"%{phase}%")
            )
        ).order_by(desc(SymptomLog.logged_at)).limit(20).all()
        
        if not similar_symptoms:
            return None
        
        # Aggregate common symptoms
        symptom_counts: Dict[str, List[int]] = {}
        for log in similar_symptoms:
            if log.symptom_type not in symptom_counts:
                symptom_counts[log.symptom_type] = []
            symptom_counts[log.symptom_type].append(log.severity)
        
        # Find most common with notable severity
        notable_symptoms = []
        for symptom, severities in symptom_counts.items():
            avg_severity = sum(severities) / len(severities)
            if avg_severity >= 4 and len(severities) >= 2:
                notable_symptoms.append((symptom, avg_severity))
        
        if notable_symptoms:
            notable_symptoms.sort(key=lambda x: x[1], reverse=True)
            symptoms_text = ", ".join(s[0].replace("_", " ") for s, _ in notable_symptoms[:2])
            return f"Based on your history, you may experience {symptoms_text} during this phase."
        
        return None
    
    def _build_time_context(self, timezone: str) -> TimeContext:
        """Build time awareness context."""
        try:
            tz = ZoneInfo(timezone)
        except Exception:
            tz = ZoneInfo("UTC")
        
        local_now = datetime.now(tz)
        hour = local_now.hour
        weekday = local_now.weekday()
        
        # Determine time of day
        if 5 <= hour < 8:
            time_of_day = TimeOfDay.EARLY_MORNING
        elif 8 <= hour < 12:
            time_of_day = TimeOfDay.MORNING
        elif 12 <= hour < 17:
            time_of_day = TimeOfDay.AFTERNOON
        elif 17 <= hour < 21:
            time_of_day = TimeOfDay.EVENING
        elif 21 <= hour < 24:
            time_of_day = TimeOfDay.NIGHT
        else:
            time_of_day = TimeOfDay.LATE_NIGHT
        
        # Determine day type
        if weekday == 0:
            day_type = DayType.MONDAY
        elif weekday == 4:
            day_type = DayType.FRIDAY
        elif weekday >= 5:
            day_type = DayType.WEEKEND
        else:
            day_type = DayType.WEEKDAY
        
        # Meal time check
        is_meal_time = hour in [7, 8, 12, 13, 18, 19]
        
        # Greeting style
        greeting_styles = {
            TimeOfDay.EARLY_MORNING: "early_riser",
            TimeOfDay.MORNING: "good_morning",
            TimeOfDay.AFTERNOON: "checking_in",
            TimeOfDay.EVENING: "winding_down",
            TimeOfDay.NIGHT: "relaxing",
            TimeOfDay.LATE_NIGHT: "late_night_care"
        }
        
        # Activity relevance by time
        activity_relevance = {
            TimeOfDay.EARLY_MORNING: {"movement": 0.7, "mindfulness": 0.9, "food": 0.8},
            TimeOfDay.MORNING: {"movement": 0.9, "mindfulness": 0.7, "food": 0.8},
            TimeOfDay.AFTERNOON: {"movement": 0.6, "mindfulness": 0.5, "food": 0.7},
            TimeOfDay.EVENING: {"movement": 0.4, "mindfulness": 0.8, "food": 0.6},
            TimeOfDay.NIGHT: {"movement": 0.2, "mindfulness": 0.9, "food": 0.3},
            TimeOfDay.LATE_NIGHT: {"movement": 0.1, "mindfulness": 0.5, "food": 0.2}
        }
        
        # Energy assumption
        energy_assumptions = {
            TimeOfDay.EARLY_MORNING: "waking_up",
            TimeOfDay.MORNING: "energizing",
            TimeOfDay.AFTERNOON: "potentially_sluggish",
            TimeOfDay.EVENING: "winding_down",
            TimeOfDay.NIGHT: "relaxing",
            TimeOfDay.LATE_NIGHT: "should_be_resting"
        }
        
        return TimeContext(
            local_time=local_now,
            timezone=timezone,
            time_of_day=time_of_day,
            day_type=day_type,
            is_early=hour < 7,
            is_late=hour >= 22,
            is_meal_time=is_meal_time,
            is_wind_down_time=hour >= 20,
            greeting_style=greeting_styles.get(time_of_day, "neutral"),
            energy_assumption=energy_assumptions.get(time_of_day, "unknown"),
            activity_relevance=activity_relevance.get(time_of_day, {})
        )
    
    async def _build_streak_context(self, user_id: str) -> StreakContext:
        """Build habit momentum context."""
        from app.core.database import DailyAssignment, UserProfile
        
        # Get recent assignment history
        from app.utils.timezone_utils import get_user_current_date
        from datetime import timedelta
        
        user_today = get_user_current_date(user_id, self.db)
        fourteen_days_ago = user_today - timedelta(days=14)
        
        assignments = self.db.query(DailyAssignment).filter(
            and_(
                DailyAssignment.uid == user_id,
                DailyAssignment.assignment_date >= fourteen_days_ago
            )
        ).order_by(desc(DailyAssignment.assignment_date)).all()
        
        # Calculate daily completion rates
        daily_completion: Dict[date, float] = {}
        for assignment in assignments:
            d = assignment.assignment_date
            if d not in daily_completion:
                daily_completion[d] = {"completed": 0, "total": 0}
            daily_completion[d]["total"] += 1
            if assignment.is_completed:
                daily_completion[d]["completed"] += 1
        
        # Calculate streak (days with >50% completion)
        current_streak = 0
        longest_streak = 0
        temp_streak = 0
        
        sorted_dates = sorted(daily_completion.keys(), reverse=True)
        for i, d in enumerate(sorted_dates):
            stats = daily_completion[d]
            rate = stats["completed"] / stats["total"] if stats["total"] > 0 else 0
            
            if rate >= 0.5:
                temp_streak += 1
                if i == 0 or (i > 0 and sorted_dates[i-1] - d == timedelta(days=1)):
                    current_streak = temp_streak
            else:
                longest_streak = max(longest_streak, temp_streak)
                temp_streak = 0
                if i == 0:
                    current_streak = 0
        
        longest_streak = max(longest_streak, temp_streak)
        
        # Calculate 7-day consistency
        from app.utils.timezone_utils import get_user_current_date
        from datetime import timedelta
        
        user_today = get_user_current_date(user_id, self.db)
        seven_days_ago = user_today - timedelta(days=7)
        recent_completions = [
            daily_completion[d]["completed"] / daily_completion[d]["total"]
            for d in daily_completion
            if d >= seven_days_ago and daily_completion[d]["total"] > 0
        ]
        consistency_rate = sum(recent_completions) / len(recent_completions) if recent_completions else 0
        
        # Days since last activity
        days_since = 0
        if sorted_dates:
            from app.utils.timezone_utils import get_user_current_date
            user_today = get_user_current_date(user_id, self.db)
            days_since = (user_today - sorted_dates[0]).days
        
        # Determine streak health
        if current_streak >= 7:
            streak_health = "strong"
        elif current_streak >= 3:
            streak_health = "building"
        elif days_since <= 1:
            streak_health = "at_risk"
        else:
            streak_health = "broken"
        
        # Determine trend
        if len(recent_completions) >= 3:
            first_half = sum(recent_completions[:len(recent_completions)//2]) / (len(recent_completions)//2)
            second_half = sum(recent_completions[len(recent_completions)//2:]) / (len(recent_completions) - len(recent_completions)//2)
            if second_half > first_half + 0.1:
                trend = "improving"
            elif second_half < first_half - 0.1:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"
        
        # Motivation state
        if consistency_rate > 0.7 and current_streak >= 3:
            motivation_state = "motivated"
        elif consistency_rate > 0.4 or current_streak >= 1:
            motivation_state = "needs_encouragement"
        else:
            motivation_state = "struggling"
        
        # Check for milestone
        milestone_days = [3, 7, 14, 21, 30, 60, 90]
        celebration_opportunity = current_streak in milestone_days
        
        # Risk alert if streak might break
        risk_alert = streak_health == "at_risk" and current_streak >= 3
        
        return StreakContext(
            current_streak=current_streak,
            longest_streak=longest_streak,
            streak_health=streak_health,
            days_since_last_activity=days_since,
            consistency_rate=round(consistency_rate, 2),
            trend=trend,
            motivation_state=motivation_state,
            celebration_opportunity=celebration_opportunity,
            risk_alert=risk_alert
        )
    
    async def _build_personalization_context(self, user_id: str) -> PersonalizationContext:
        """Build the personalization context."""
        from app.core.database import UserProfile, UserResponse, ChatMessage, ChatSession
        
        user_profile = self.db.query(UserProfile).filter(
            UserProfile.uid == user_id
        ).first()
        
        user_response = self.db.query(UserResponse).filter(
            UserResponse.uid == user_id
        ).order_by(desc(UserResponse.created_at)).first()
        
        name = user_profile.name if user_profile else None
        
        # Analyze communication preferences from chat history
        recent_messages = self.db.query(ChatMessage).join(ChatSession).filter(
            and_(
                ChatSession.user_id == user_id,
                ChatMessage.role == "user"
            )
        ).order_by(desc(ChatMessage.created_at)).limit(50).all()
        
        responds_well_to = []
        
        # Check if user uses emojis
        emoji_count = sum(1 for m in recent_messages if any(c in m.content for c in "😊💜❤️🎉👍"))
        if emoji_count > len(recent_messages) * 0.2:
            responds_well_to.append("emojis")
        
        # Check message length preference
        avg_length = sum(len(m.content) for m in recent_messages) / len(recent_messages) if recent_messages else 50
        if avg_length > 100:
            communication_style = "detailed"
        elif avg_length < 30:
            communication_style = "direct"
        else:
            communication_style = "balanced"
        
        # Build concerns list
        primary_concerns = []
        if user_response:
            if user_response.top_concern:
                primary_concerns.append(user_response.top_concern)
            primary_concerns.extend(user_response.period_concerns or [])
            primary_concerns.extend(user_response.body_concerns or [])
        
        diagnosed_conditions = user_response.diagnosed_conditions if user_response else []
        
        # Lifestyle markers
        is_busy = user_response and user_response.stress_level in ["Very High", "High"]
        is_fitness = user_response and user_response.workout_intensity in ["High", "Very High"]
        
        # Check if new user
        conversation_count = self.db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).count()
        is_new = conversation_count < 5
        
        return PersonalizationContext(
            name=name,
            preferred_name=name.split()[0] if name else None,
            communication_style=communication_style,
            responds_well_to=responds_well_to,
            sensitive_topics=[],  # Could be learned over time
            favorite_activities=[],  # Could be learned from completions
            primary_concerns=primary_concerns[:5],
            chronic_symptoms=[],  # Could be derived from symptom history
            diagnosed_conditions=diagnosed_conditions or [],
            is_busy_professional=is_busy,
            is_fitness_focused=is_fitness,
            is_new_to_tracking=is_new
        )
    
    async def _assess_current_moment(self, user_id: str) -> Dict[str, Any]:
        """
        Assess user engagement state for calibrating conversation depth.
        
        Detects:
        - Flow State: User is engaged, giving detailed responses
        - Resistance State: User is brief, dismissive, or off-topic
        - Celebration State: User just hit a milestone
        """
        from app.core.database import ChatSession, ChatMessage
        
        # Analyze recent messages to determine engagement state
        try:
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            recent_sessions = self.db.query(ChatSession).filter(
                and_(
                    ChatSession.user_id == user_id,
                    ChatSession.created_at >= one_hour_ago
                )
            ).all()
            
            if not recent_sessions:
                return {
                    "engagement_state": "neutral",
                    "profiling_depth": "exploratory",
                    "relevance_focus": [],
                    "conversation_hints": ["Start with a warm check-in"],
                    "avoid_topics": []
                }
            
            # Get user messages from recent sessions
            user_messages = []
            for session in recent_sessions:
                msgs = self.db.query(ChatMessage).filter(
                    and_(
                        ChatMessage.session_id == session.id,
                        ChatMessage.role == "user"
                    )
                ).order_by(ChatMessage.created_at.desc()).limit(10).all()
                user_messages.extend(msgs)
            
            if not user_messages:
                return {
                    "engagement_state": "neutral",
                    "profiling_depth": "exploratory",
                    "relevance_focus": [],
                    "conversation_hints": [],
                    "avoid_topics": []
                }
            
            # Calculate average message length
            avg_length = sum(len(m.content) for m in user_messages) / len(user_messages)
            
            # Determine engagement state
            if avg_length > 80:
                engagement_state = "flow"
                profiling_depth = "deep"  # User is engaged, ask deeper questions
            elif avg_length < 20:
                engagement_state = "resistance"
                profiling_depth = "minimal"  # User is brief, back off
            else:
                engagement_state = "neutral"
                profiling_depth = "exploratory"
            
            return {
                "engagement_state": engagement_state,
                "profiling_depth": profiling_depth,
                "avg_message_length": round(avg_length),
                "relevance_focus": [],
                "conversation_hints": ["Match user's energy level"],
                "avoid_topics": []
            }
            
        except Exception as e:
            logger.warning(f"Error assessing current moment: {e}")
            return {
                "engagement_state": "neutral",
                "profiling_depth": "exploratory",
                "relevance_focus": [],
                "conversation_hints": [],
                "avoid_topics": []
            }
    
    def _build_profiling_context(self, profile_gaps: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform profile gaps into actionable session goals for the LLM.
        
        This enables the Deep Profiling Protocol by giving the LLM
        specific areas to explore conversationally.
        """
        if not profile_gaps:
            return None
        
        missing_fields = profile_gaps.get("missing_fields", {})
        priority_gap = profile_gaps.get("priority_gap")
        profile_density = profile_gaps.get("profile_density", 0)
        
        # Generate natural question hints for each gap
        question_strategies = {
            "fitness_habits": {
                "observation": "Notice their activity completion patterns",
                "question_hint": "How does movement feel for you during different phases of your cycle?"
            },
            "stress_landscape": {
                "observation": "Look for mentions of work, deadlines, or overwhelm",
                "question_hint": "What tends to trigger stress for you most?"
            },
            "circadian_rhythm": {
                "observation": "Note when they're most active vs. tired",
                "question_hint": "Are you more of a morning person or night owl?"
            },
            "sleep_profile": {
                "observation": "Listen for mentions of tiredness or sleep issues",
                "question_hint": "How has your sleep been lately?"
            },
            "long_term_goals": {
                "observation": "Understand their deeper motivation for using AUVRA",
                "question_hint": "What's your biggest hope from tracking your cycle?"
            },
            "advice_style": {
                "observation": "See if they prefer data, encouragement, or direct advice",
                "question_hint": "Do you prefer I give you the science behind things, or keep it simple?"
            },
            "life_archetype": {
                "observation": "Infer their lifestyle from context clues",
                "question_hint": "Tell me about a typical day for you."
            }
        }
        
        # Build profiling goals
        profiling_goals = []
        for gap_key in list(missing_fields.keys())[:2]:  # Focus on top 2 gaps
            strategy = question_strategies.get(gap_key, {
                "observation": f"Explore {gap_key.replace('_', ' ')}",
                "question_hint": f"Can you tell me about your {gap_key.replace('_', ' ')}?"
            })
            profiling_goals.append({
                "target_field": gap_key,
                **strategy
            })
        
        return {
            "profile_density": profile_density,
            "priority_focus": priority_gap,
            "session_goals": profiling_goals,
            "approach": "observational" if profile_density > 0.5 else "exploratory"
        }



# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

def format_context_for_prompt(context: Dict[str, Any]) -> str:
    """Format rich context into prompt-ready text."""
    lines = []
    
    # Cycle context
    cycle = context.get("cycle")
    if cycle and isinstance(cycle, CycleContext) and cycle.phase != "unknown":
        lines.append(f"\n🌙 CYCLE AWARENESS:")
        lines.append(f"   Day {cycle.current_day}, {cycle.phase.title()} Phase (day {cycle.phase_day})")
        lines.append(f"   Energy expectation: {cycle.energy_expectation}")
        if cycle.typical_experience:
            lines.append(f"   Context: {cycle.typical_experience}")
        if cycle.personalized_experience:
            lines.append(f"   Personal pattern: {cycle.personalized_experience}")
        if cycle.approaching_period:
            lines.append("   ⚡ Period approaching - be mindful of PMS")
    
    # Time context
    time_ctx = context.get("time")
    if time_ctx and isinstance(time_ctx, TimeContext):
        lines.append(f"\n⏰ TIME AWARENESS:")
        lines.append(f"   {time_ctx.time_of_day.value.replace('_', ' ').title()} ({time_ctx.day_type.value})")
        if time_ctx.is_late:
            lines.append("   ⚡ Late hour - encourage rest")
        if time_ctx.is_wind_down_time:
            lines.append("   ⚡ Wind-down time - calmer energy")
    
    # Streak context
    streak = context.get("streak")
    if streak and isinstance(streak, StreakContext):
        lines.append(f"\n🔥 HABIT MOMENTUM:")
        lines.append(f"   {streak.current_streak} day streak ({streak.streak_health})")
        if streak.celebration_opportunity:
            lines.append("   🎉 MILESTONE! Celebrate this achievement!")
        if streak.risk_alert:
            lines.append("   ⚠️ Streak at risk - gentle encouragement")
        lines.append(f"   Motivation: {streak.motivation_state}")
    
    # Personalization
    person = context.get("personalization")
    if person and isinstance(person, PersonalizationContext):
        if person.preferred_name:
            lines.append(f"\n👤 USER: {person.preferred_name}")
        if person.is_new_to_tracking:
            lines.append("   🆕 New user - be educational and welcoming")
        if person.primary_concerns:
            lines.append(f"   Top concerns: {', '.join(person.primary_concerns[:3])}")
        if person.diagnosed_conditions:
            lines.append(f"   Conditions: {', '.join(person.diagnosed_conditions[:2])}")
    
    # Moment Assessment (Engagement State)
    moment = context.get("moment", {})
    if moment and moment.get("engagement_state"):
        engagement = moment["engagement_state"]
        if engagement == "flow":
            lines.append("\n💬 ENGAGEMENT: User is in FLOW state - go deeper!")
        elif engagement == "resistance":
            lines.append("\n💬 ENGAGEMENT: User is BRIEF - back off, be supportive")
    
    # Deep Profiling Goals (for personalise context)
    profiling = context.get("profiling")
    if profiling and profiling.get("session_goals"):
        lines.append("\n🎯 PROFILING SESSION GOALS:")
        lines.append(f"   Profile density: {profiling.get('profile_density', 0):.0%}")
        lines.append(f"   Approach: {profiling.get('approach', 'exploratory')}")
        for goal in profiling.get("session_goals", [])[:2]:
            lines.append(f"   → {goal.get('target_field', 'unknown')}: {goal.get('observation', '')}")
            lines.append(f"      Hint: \"{goal.get('question_hint', '')}\"")
    
    return "\n".join(lines) if lines else ""

