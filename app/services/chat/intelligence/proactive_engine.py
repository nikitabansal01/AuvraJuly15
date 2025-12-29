"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA PROACTIVE ENGINE - Anticipating Needs & Gentle Engagement
═══════════════════════════════════════════════════════════════════════════════
The best doctors don't wait for you to ask - they reach out when you need them.

CAPABILITIES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. PHASE TRANSITIONS - Awareness of cycle phase changes
2. MILESTONE CELEBRATIONS - Recognizing and celebrating achievements
3. CONCERN ANTICIPATION - Predicting and addressing upcoming issues
4. GENTLE NUDGES - Encouraging engagement without being pushy
5. CHECK-IN TRIGGERS - Knowing when to reach out
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, func

logger = logging.getLogger(__name__)


class TriggerType(Enum):
    """Types of proactive triggers."""
    PHASE_TRANSITION = "phase_transition"
    MILESTONE = "milestone"
    STREAK_CELEBRATION = "streak_celebration"
    STREAK_AT_RISK = "streak_at_risk"
    SYMPTOM_PATTERN = "symptom_pattern"
    CHECK_IN = "check_in"
    ENCOURAGEMENT = "encouragement"
    EDUCATION = "education"


class TriggerPriority(Enum):
    """Priority levels for triggers."""
    HIGH = 1
    MEDIUM = 2
    LOW = 3


@dataclass
class ProactiveTrigger:
    """A trigger for proactive engagement."""
    trigger_type: TriggerType
    priority: TriggerPriority
    title: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
    action_suggestion: Optional[str] = None
    expires_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger_type": self.trigger_type.value,
            "priority": self.priority.value,
            "title": self.title,
            "message": self.message,
            "context": self.context,
            "action_suggestion": self.action_suggestion,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None
        }


class ProactiveEngine:
    """
    Anticipates user needs and generates proactive engagement.
    
    Like a thoughtful doctor who:
    - Calls to check in after a difficult visit
    - Reminds you before an important health milestone
    - Celebrates your progress with genuine enthusiasm
    - Reaches out when patterns suggest you might need support
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    async def check_all_triggers(self, user_id: str) -> List[ProactiveTrigger]:
        """
        Check all possible triggers for a user.
        Returns prioritized list of triggers.
        """
        triggers = []
        
        # Check each trigger category
        triggers.extend(await self._check_phase_transitions(user_id))
        triggers.extend(await self._check_milestones(user_id))
        triggers.extend(await self._check_streak_status(user_id))
        triggers.extend(await self._check_symptom_patterns(user_id))
        triggers.extend(await self._check_engagement_needs(user_id))
        
        # Sort by priority
        triggers.sort(key=lambda t: t.priority.value)
        
        return triggers
    
    async def _check_phase_transitions(self, user_id: str) -> List[ProactiveTrigger]:
        """Check for cycle phase transitions worth mentioning."""
        from app.services.cycle_service import CycleService
        from app.core.database import UserResponse
        
        triggers = []
        
        cycle_service = CycleService(self.db)
        cycle_info = cycle_service.get_cycle_phase_info(user_id)
        
        if not cycle_info:
            return triggers
        
        current_day = cycle_info.cycle_day
        phase = cycle_info.phase.lower() if cycle_info.phase else "unknown"
        
        # Get cycle length
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
        
        ovulation_day = cycle_length - 14
        
        # Phase transition triggers
        phase_messages = {
            1: {
                "title": "New cycle starting",
                "message": "Day 1! Your new cycle begins. Be gentle with yourself today 💜",
                "priority": TriggerPriority.HIGH
            },
            5: {
                "title": "Entering follicular phase",
                "message": "Your period is wrapping up! Energy typically starts rising now 🌱",
                "priority": TriggerPriority.MEDIUM
            },
            ovulation_day - 1: {
                "title": "Ovulation approaching",
                "message": f"Day {ovulation_day - 1}! You're entering your power phase - energy and confidence often peak here ✨",
                "priority": TriggerPriority.MEDIUM
            },
            ovulation_day + 2: {
                "title": "Entering luteal phase",
                "message": "Luteal phase is beginning. Self-care becomes extra important over the next two weeks 💜",
                "priority": TriggerPriority.MEDIUM
            },
            cycle_length - 3: {
                "title": "Period approaching",
                "message": "Your period is likely coming in a few days. How are you feeling?",
                "priority": TriggerPriority.HIGH
            }
        }
        
        if current_day in phase_messages:
            msg = phase_messages[current_day]
            triggers.append(ProactiveTrigger(
                trigger_type=TriggerType.PHASE_TRANSITION,
                priority=msg["priority"],
                title=msg["title"],
                message=msg["message"],
                context={"cycle_day": current_day, "phase": phase}
            ))
        
        return triggers
    
    async def _check_milestones(self, user_id: str) -> List[ProactiveTrigger]:
        """Check for achievement milestones."""
        from app.core.database import UserProfile, RecommendationCompletion, ChatSession
        
        triggers = []
        
        # Check total completions
        total_completions = self.db.query(func.count(RecommendationCompletion.id)).filter(
            RecommendationCompletion.uid == user_id
        ).scalar()
        
        completion_milestones = {
            10: "10 completions! You're building great habits! 🎉",
            25: "25 actions completed! That's real commitment! 💪",
            50: "50 completions! Half a hundred - incredible! 🌟",
            100: "100 COMPLETIONS! You're an AUVRA rockstar! 🎊",
            250: "250 completions! Your dedication is inspiring! 💜",
            500: "500 completions! You're a wellness warrior! 🏆"
        }
        
        for milestone, message in completion_milestones.items():
            if total_completions == milestone:
                triggers.append(ProactiveTrigger(
                    trigger_type=TriggerType.MILESTONE,
                    priority=TriggerPriority.HIGH,
                    title=f"{milestone} completions!",
                    message=message,
                    context={"milestone_type": "completions", "count": milestone}
                ))
                break  # Only one milestone at a time
        
        # Check days since signup
        user = self.db.query(UserProfile).filter(UserProfile.uid == user_id).first()
        if user and user.created_at:
            days_active = (datetime.utcnow() - user.created_at).days
            
            day_milestones = {
                7: "One week with AUVRA! 🎉 How's it going so far?",
                30: "One month together! 💜 You're doing amazing!",
                90: "3 months! Your dedication to your health is inspiring!",
                180: "6 months! Half a year of wellness together! 🌟",
                365: "ONE YEAR! 🎊 Happy AUVRA-versary! 💜"
            }
            
            if days_active in day_milestones:
                triggers.append(ProactiveTrigger(
                    trigger_type=TriggerType.MILESTONE,
                    priority=TriggerPriority.HIGH,
                    title=f"{days_active} days with AUVRA",
                    message=day_milestones[days_active],
                    context={"milestone_type": "days_active", "count": days_active}
                ))
        
        return triggers
    
    async def _check_streak_status(self, user_id: str) -> List[ProactiveTrigger]:
        """Check streak-related triggers."""
        from app.core.database import DailyAssignment
        from app.utils.timezone_utils import get_user_current_date
        
        triggers = []
        
        # Calculate current streak
        today = get_user_current_date(user_id, self.db)
        fourteen_days_ago = today - timedelta(days=14)
        
        assignments = self.db.query(DailyAssignment).filter(
            and_(
                DailyAssignment.uid == user_id,
                DailyAssignment.assignment_date >= fourteen_days_ago
            )
        ).all()
        
        # Calculate daily completion rates
        daily_rates = {}
        for assignment in assignments:
            d = assignment.assignment_date
            if d not in daily_rates:
                daily_rates[d] = {"completed": 0, "total": 0}
            daily_rates[d]["total"] += 1
            if assignment.is_completed:
                daily_rates[d]["completed"] += 1
        
        # Calculate streak (days with >50% completion)
        streak = 0
        current_date = today
        while current_date in daily_rates:
            stats = daily_rates[current_date]
            rate = stats["completed"] / stats["total"] if stats["total"] > 0 else 0
            if rate >= 0.5:
                streak += 1
                current_date -= timedelta(days=1)
            else:
                break
        
        # Streak celebrations
        streak_messages = {
            3: ("3-day streak!", "You're building momentum! 🔥", TriggerPriority.MEDIUM),
            7: ("One week streak!", "7 days strong! That's habit-forming territory! 🎉", TriggerPriority.HIGH),
            14: ("Two week streak!", "14 days! Your consistency is amazing! 💪", TriggerPriority.HIGH),
            21: ("3 week streak!", "21 days - a new habit is born! 🌟", TriggerPriority.HIGH),
            30: ("One month streak!", "30 DAYS! You're absolutely incredible! 🏆", TriggerPriority.HIGH)
        }
        
        if streak in streak_messages:
            title, message, priority = streak_messages[streak]
            triggers.append(ProactiveTrigger(
                trigger_type=TriggerType.STREAK_CELEBRATION,
                priority=priority,
                title=title,
                message=message,
                context={"streak_days": streak}
            ))
        
        # Streak at risk warning
        if streak >= 3 and today not in daily_rates:
            # They have a streak but haven't completed anything today
            triggers.append(ProactiveTrigger(
                trigger_type=TriggerType.STREAK_AT_RISK,
                priority=TriggerPriority.MEDIUM,
                title="Keep your streak going!",
                message=f"You're on a {streak}-day streak! Want to keep it going today?",
                context={"streak_days": streak, "at_risk": True}
            ))
        
        return triggers
    
    async def _check_symptom_patterns(self, user_id: str) -> List[ProactiveTrigger]:
        """Check for symptom patterns worth mentioning."""
        from app.core.database import SymptomLog
        from app.services.cycle_service import CycleService
        from app.utils.timezone_utils import get_user_current_date
        
        triggers = []
        
        # Get recent symptoms
        user_today = get_user_current_date(user_id, self.db)
        seven_days_ago = user_today - timedelta(days=7)
        recent_symptoms = self.db.query(SymptomLog).filter(
            and_(
                SymptomLog.user_id == user_id,
                SymptomLog.logged_date >= seven_days_ago
            )
        ).order_by(desc(SymptomLog.logged_at)).all()
        
        if not recent_symptoms:
            return triggers
        
        # Check for concerning patterns
        high_severity = [s for s in recent_symptoms if s.severity >= 7]
        if len(high_severity) >= 3:
            symptom_types = list(set(s.symptom_type for s in high_severity))
            triggers.append(ProactiveTrigger(
                trigger_type=TriggerType.SYMPTOM_PATTERN,
                priority=TriggerPriority.HIGH,
                title="Checking in on your symptoms",
                message=f"I've noticed some high severity {', '.join(symptom_types[:2])} recently. How are you doing today?",
                context={"symptoms": symptom_types, "severity": "high"}
            ))
        
        # Check for improving patterns
        if len(recent_symptoms) >= 4:
            recent_severities = [s.severity for s in recent_symptoms[:4]]
            if all(recent_severities[i] <= recent_severities[i+1] for i in range(len(recent_severities)-1)):
                triggers.append(ProactiveTrigger(
                    trigger_type=TriggerType.ENCOURAGEMENT,
                    priority=TriggerPriority.LOW,
                    title="Symptoms improving!",
                    message="Your symptoms seem to be improving! That's great progress 🌟",
                    context={"trend": "improving"}
                ))
        
        return triggers
    
    async def _check_engagement_needs(self, user_id: str) -> List[ProactiveTrigger]:
        """Check if user needs engagement encouragement."""
        from app.core.database import ChatSession, DailyAssignment
        
        triggers = []
        
        # Check last activity
        last_session = self.db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).order_by(desc(ChatSession.created_at)).first()
        
        last_assignment = self.db.query(DailyAssignment).filter(
            and_(
                DailyAssignment.uid == user_id,
                DailyAssignment.is_completed == True
            )
        ).order_by(desc(DailyAssignment.completed_at)).first()
        
        # Determine last activity date
        last_activity = None
        if last_session and last_assignment:
            last_activity = max(last_session.created_at, last_assignment.completed_at or last_assignment.created_at)
        elif last_session:
            last_activity = last_session.created_at
        elif last_assignment:
            last_activity = last_assignment.completed_at or last_assignment.created_at
        
        if last_activity:
            days_inactive = (datetime.utcnow() - last_activity).days
            
            if days_inactive == 3:
                triggers.append(ProactiveTrigger(
                    trigger_type=TriggerType.CHECK_IN,
                    priority=TriggerPriority.LOW,
                    title="Miss you!",
                    message="Hey! Haven't seen you in a few days. Everything okay? 💜",
                    context={"days_inactive": days_inactive}
                ))
            elif days_inactive == 7:
                triggers.append(ProactiveTrigger(
                    trigger_type=TriggerType.CHECK_IN,
                    priority=TriggerPriority.MEDIUM,
                    title="Checking in",
                    message="It's been a week! Just wanted to check in and see how you're doing 💜",
                    context={"days_inactive": days_inactive}
                ))
        
        return triggers
    
    async def get_proactive_greeting(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Generate a proactive greeting when user opens the chat.
        Returns the highest priority trigger as a greeting.
        """
        triggers = await self.check_all_triggers(user_id)
        
        if not triggers:
            return None
        
        # Get highest priority trigger
        top_trigger = triggers[0]
        
        return {
            "greeting": top_trigger.message,
            "trigger_type": top_trigger.trigger_type.value,
            "context": top_trigger.context,
            "has_triggers": True,
            "trigger_count": len(triggers),
            "top_trigger": top_trigger.to_dict()
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PROACTIVE MESSAGE TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

PROACTIVE_TEMPLATES = {
    TriggerType.PHASE_TRANSITION: {
        "menstrual_start": [
            "Day 1 is here 💜 Be extra gentle with yourself today.",
            "New cycle beginning! Rest is your friend right now.",
        ],
        "follicular_start": [
            "Your energy is about to rise! 🌱 Great time to start something new.",
            "Follicular phase starting - you might feel more motivated soon!",
        ],
        "ovulation_approaching": [
            "Ovulation phase approaching! You're about to hit your stride ✨",
            "Your power phase is coming! Energy and confidence often peak here.",
        ],
        "luteal_start": [
            "Luteal phase beginning 💜 Self-care becomes extra important now.",
            "Time to embrace rest mode. Your body is working hard behind the scenes.",
        ],
        "period_approaching": [
            "Period's coming in a few days. How are you feeling?",
            "Heads up - your period is approaching. Taking care of yourself?",
        ]
    },
    TriggerType.STREAK_CELEBRATION: {
        3: "3 days in a row! You're building real momentum 🔥",
        7: "ONE WEEK! 🎉 You're officially in habit-forming territory!",
        14: "Two weeks of consistency! That's incredible dedication 💪",
        21: "21 days - science says you've built a habit! 🌟",
        30: "30 DAYS! You're absolutely crushing it! 🏆"
    },
    TriggerType.ENCOURAGEMENT: {
        "general": [
            "Just wanted to say - you're doing great 💜",
            "Remember: every small step counts!",
            "Proud of you for showing up for yourself 🌟"
        ],
        "struggling": [
            "Tough days happen. You're still amazing 💜",
            "Progress isn't always linear. You've got this.",
            "Even on hard days, you're doing better than you think."
        ]
    }
}


def get_proactive_message(trigger: ProactiveTrigger) -> str:
    """Get an appropriate proactive message for a trigger."""
    import random
    
    templates = PROACTIVE_TEMPLATES.get(trigger.trigger_type, {})
    
    if trigger.trigger_type == TriggerType.STREAK_CELEBRATION:
        days = trigger.context.get("streak_days", 0)
        if days in templates:
            return templates[days]
    
    if trigger.trigger_type == TriggerType.PHASE_TRANSITION:
        phase_key = trigger.context.get("phase", "general")
        if phase_key in templates:
            return random.choice(templates[phase_key])
    
    if trigger.trigger_type == TriggerType.ENCOURAGEMENT:
        category = "struggling" if trigger.context.get("is_struggling") else "general"
        if category in templates:
            return random.choice(templates[category])
    
    return trigger.message
