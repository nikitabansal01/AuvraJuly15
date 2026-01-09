"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA MEMORY ENGINE - Multi-Layer Memory System
═══════════════════════════════════════════════════════════════════════════════
A doctor remembers. Not just facts, but meanings, patterns, and feelings.

MEMORY LAYERS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Layer 1: EPISODIC MEMORY - Specific conversations and events
         "Last week you mentioned your sister's wedding stress..."
         
Layer 2: SEMANTIC MEMORY - Learned patterns and preferences
         "You tend to have more energy in the morning..."
         
Layer 3: EMOTIONAL MEMORY - How they've felt over time
         "I noticed you've been feeling more anxious lately..."
         
Layer 4: PREDICTIVE MEMORY - Anticipated needs and concerns
         "Based on your patterns, you might experience X soon..."
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import json

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, func

logger = logging.getLogger(__name__)


@dataclass
class MemoryFragment:
    """A single memory unit."""
    content: str
    memory_type: str  # episodic, semantic, emotional, predictive
    importance: float  # 0-1 scale
    timestamp: datetime
    context: Dict[str, Any] = field(default_factory=dict)
    decay_rate: float = 0.1  # How fast this memory fades
    
    def current_importance(self) -> float:
        """Calculate current importance with time decay."""
        days_old = (datetime.utcnow() - self.timestamp).days
        return self.importance * (1 - self.decay_rate * min(days_old / 30, 1))


@dataclass
class ConversationInsight:
    """Insight extracted from conversations."""
    insight_type: str  # concern, preference, milestone, trigger, pattern
    content: str
    confidence: float
    first_mentioned: datetime
    mention_count: int = 1
    related_phase: Optional[str] = None
    related_symptoms: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# DEEP PROFILING SCHEMA (Module-level for import)
# ═══════════════════════════════════════════════════════════════════════════════
IDEAL_PROFILE_FIELDS = {
    "fitness_habits": {"name": "Fitness Habits", "description": "Current movement level and comfort with exercise"},
    "stress_landscape": {"name": "Stress Landscape", "description": "Common stressors and coping mechanisms"},
    "circadian_rhythm": {"name": "Circadian Rhythm", "description": "Natural energy peaks and morning/evening habits"},
    "sleep_profile": {"name": "Sleep Profile", "description": "Typical sleep duration and quality issues"},
    "long_term_goals": {"name": "Long-Term Goals", "description": "Deep motivations (e.g., managing symptoms, fertility)"},
    "advice_style": {"name": "Advice Style", "description": "Response style (data-driven vs supportive vs direct)"},
    "life_archetype": {"name": "Life Archetype", "description": "Busy professional, student, parent, fitness enthusiast, etc."}
}


class MemoryEngine:
    """
    The memory system that makes AUVRA remember like a doctor.
    
    A good doctor doesn't just see the patient - they remember:
    - The context of past visits
    - What matters to this person
    - Patterns they've observed
    - What to watch out for
    """
    
    # Reference to module-level schema
    IDEAL_PROFILE_FIELDS = IDEAL_PROFILE_FIELDS
    
    def __init__(self, db: Session):
        self.db = db
        
        # Memory layers
        self.episodic_memories: List[MemoryFragment] = []
        self.semantic_memories: List[MemoryFragment] = []
        self.emotional_memories: List[MemoryFragment] = []
        self.predictive_memories: List[MemoryFragment] = []
        
        # Extracted insights
        self.insights: Dict[str, ConversationInsight] = {}

    
    async def load_full_memory(self, user_id: str, current_session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Load all memory layers for comprehensive context.
        This is the doctor opening the patient's complete file.
        """
        try:
            from app.core.database import ChatSession, ChatMessage, SymptomLog, ConversationSummary
            
            memory = {
                "episodic": await self._load_episodic_memory(user_id, current_session_id),
                "semantic": await self._load_semantic_memory(user_id),
                "emotional": await self._load_emotional_memory(user_id),
                "predictive": await self._load_predictive_memory(user_id),
                "weekly_checkins": await self._load_weekly_checkin_memory(user_id),
                "insights": await self._extract_insights(user_id),
                "relationship": await self._load_relationship_context(user_id),
                "profile_gaps": await self._load_profile_gaps(user_id)
            }
            
            return memory
            
        except Exception as e:
            logger.error(f"Error loading memory for {user_id}: {str(e)}")
            return self._empty_memory()
    
    async def _load_episodic_memory(self, user_id: str, current_session_id: Optional[str]) -> Dict[str, Any]:
        """
        Layer 1: Specific memories from past conversations.
        "Remember last time we talked about..."
        """
        from app.core.database import ChatSession, ChatMessage
        
        # Get recent sessions (excluding current)
        sessions = self.db.query(ChatSession).filter(
            and_(
                ChatSession.user_id == user_id,
                ChatSession.id != current_session_id if current_session_id else True
            )
        ).order_by(desc(ChatSession.created_at)).limit(10).all()
        
        episodic = {
            "last_conversation": None,
            "recent_topics": [],
            "unresolved_concerns": [],
            "memorable_moments": [],
            "session_summaries": []
        }
        
        for i, session in enumerate(sessions):
            # Get messages from session
            messages = self.db.query(ChatMessage).filter(
                ChatMessage.session_id == session.id
            ).order_by(ChatMessage.created_at).all()
            
            if not messages:
                continue
            
            # Extract key information
            user_messages = [m for m in messages if m.role == "user"]
            
            if i == 0:
                # Most recent conversation
                episodic["last_conversation"] = {
                    "when": session.created_at,
                    "context": session.conversation_context,
                    "key_messages": [m.content for m in user_messages[-3:]],
                    "summary": session.summary
                }
            
            # Extract topics mentioned
            topics = self._extract_topics_from_messages(user_messages)
            episodic["recent_topics"].extend(topics)
            
            # Check for unresolved concerns
            concerns = self._find_unresolved_concerns(messages)
            episodic["unresolved_concerns"].extend(concerns)
            
            # Add session summary if available
            if session.summary:
                episodic["session_summaries"].append({
                    "date": session.created_at.date().isoformat(),
                    "context": session.conversation_context,
                    "summary": session.summary
                })
        
        # Deduplicate and prioritize
        episodic["recent_topics"] = list(set(episodic["recent_topics"]))[:10]
        episodic["unresolved_concerns"] = episodic["unresolved_concerns"][:5]
        
        return episodic
    
    async def _load_semantic_memory(self, user_id: str) -> Dict[str, Any]:
        """
        Layer 2: Patterns and learned preferences.
        "I've noticed you prefer..." / "You tend to..."
        """
        from app.core.database import (
            DailyAssignment, RecommendationCompletion, AssignmentSkipLog,
            SymptomLog, RecommendationRecord
        )
        
        semantic = {
            "preferences": {},
            "patterns": {},
            "typical_behaviors": {},
            "strengths": [],
            "challenges": []
        }
        
        # Analyze completion patterns (last 30 days)
        from app.utils.timezone_utils import get_user_current_date
        user_today = get_user_current_date(user_id, self.db)
        thirty_days_ago = user_today - timedelta(days=30)
        
        assignments = self.db.query(DailyAssignment).filter(
            and_(
                DailyAssignment.uid == user_id,
                DailyAssignment.assignment_date >= thirty_days_ago
            )
        ).all()
        
        # Category preferences
        category_stats = defaultdict(lambda: {"completed": 0, "total": 0})
        time_slot_stats = defaultdict(lambda: {"completed": 0, "total": 0})
        
        for assignment in assignments:
            rec = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == assignment.recommendation_id
            ).first()
            
            if rec:
                category_stats[rec.category]["total"] += 1
                if assignment.is_completed:
                    category_stats[rec.category]["completed"] += 1
            
            time_slot_stats[assignment.time_group]["total"] += 1
            if assignment.is_completed:
                time_slot_stats[assignment.time_group]["completed"] += 1
        
        # Calculate preferences
        semantic["preferences"]["categories"] = {}
        for cat, stats in category_stats.items():
            if stats["total"] > 0:
                rate = stats["completed"] / stats["total"]
                semantic["preferences"]["categories"][cat] = {
                    "completion_rate": round(rate, 2),
                    "engagement": "high" if rate > 0.7 else "medium" if rate > 0.4 else "low"
                }
                
                if rate > 0.7:
                    semantic["strengths"].append(f"Great at {cat} tasks")
                elif rate < 0.3 and stats["total"] >= 5:
                    semantic["challenges"].append(f"Struggles with {cat} consistency")
        
        semantic["preferences"]["time_slots"] = {}
        for slot, stats in time_slot_stats.items():
            if stats["total"] > 0:
                rate = stats["completed"] / stats["total"]
                semantic["preferences"]["time_slots"][slot] = {
                    "completion_rate": round(rate, 2),
                    "is_preferred": rate > 0.6
                }
        
        # Find best time of day
        best_slot = max(time_slot_stats.items(), 
                       key=lambda x: x[1]["completed"]/x[1]["total"] if x[1]["total"] > 0 else 0,
                       default=(None, {}))
        if best_slot[0]:
            semantic["typical_behaviors"]["best_time"] = best_slot[0]
        
        # Analyze skip patterns
        skips = self.db.query(AssignmentSkipLog).filter(
            and_(
                AssignmentSkipLog.user_id == user_id,
                AssignmentSkipLog.skip_date >= thirty_days_ago
            )
        ).all()
        
        skip_reasons = defaultdict(int)
        for skip in skips:
            if skip.skip_reason:
                skip_reasons[skip.skip_reason] += 1
        
        if skip_reasons:
            most_common_reason = max(skip_reasons.items(), key=lambda x: x[1])
            semantic["patterns"]["common_skip_reason"] = most_common_reason[0]
        
        # Symptom patterns
        symptoms = self.db.query(SymptomLog).filter(
            and_(
                SymptomLog.user_id == user_id,
                SymptomLog.logged_date >= thirty_days_ago
            )
        ).all()
        
        symptom_by_phase = defaultdict(list)
        for log in symptoms:
            if log.cycle_phase:
                symptom_by_phase[log.cycle_phase].append({
                    "type": log.symptom_type,
                    "severity": log.severity
                })
        
        semantic["patterns"]["symptoms_by_phase"] = dict(symptom_by_phase)
        
        return semantic
    
    async def _load_emotional_memory(self, user_id: str) -> Dict[str, Any]:
        """
        Layer 3: Emotional history and patterns.
        "I've noticed you've been feeling..."
        """
        from app.core.database import ChatMessage, ChatSession, SymptomLog
        
        emotional = {
            "recent_mood_trend": "unknown",
            "emotional_patterns": [],
            "support_moments": [],
            "celebration_worthy": [],
            "needs_extra_care": False
        }
        
        # Analyze recent messages for emotional content
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        
        sessions = self.db.query(ChatSession).filter(
            and_(
                ChatSession.user_id == user_id,
                ChatSession.created_at >= seven_days_ago
            )
        ).all()
        
        emotional_keywords = {
            "positive": ["happy", "great", "amazing", "good", "better", "excited", "relieved", "grateful", "proud"],
            "negative": ["sad", "anxious", "stressed", "worried", "frustrated", "tired", "exhausted", "overwhelmed", "struggling"],
            "neutral": ["okay", "fine", "alright", "so-so"]
        }
        
        mood_scores = []
        
        for session in sessions:
            messages = self.db.query(ChatMessage).filter(
                and_(
                    ChatMessage.session_id == session.id,
                    ChatMessage.role == "user"
                )
            ).all()
            
            for msg in messages:
                content_lower = msg.content.lower()
                score = 0
                
                for word in emotional_keywords["positive"]:
                    if word in content_lower:
                        score += 1
                
                for word in emotional_keywords["negative"]:
                    if word in content_lower:
                        score -= 1
                
                if score != 0:
                    mood_scores.append({
                        "score": score,
                        "date": msg.created_at
                    })
        
        # Calculate mood trend
        if mood_scores:
            avg_score = sum(m["score"] for m in mood_scores) / len(mood_scores)
            if avg_score > 0.5:
                emotional["recent_mood_trend"] = "positive"
            elif avg_score < -0.5:
                emotional["recent_mood_trend"] = "struggling"
                emotional["needs_extra_care"] = True
            else:
                emotional["recent_mood_trend"] = "stable"
        
        # Check mental health symptoms
        from app.utils.timezone_utils import get_user_current_date
        user_today = get_user_current_date(user_id, self.db)
        mental_symptoms = self.db.query(SymptomLog).filter(
            and_(
                SymptomLog.user_id == user_id,
                SymptomLog.symptom_type.in_(["anxiety", "mood_swings", "irritability", "sadness", "stress"]),
                SymptomLog.logged_date >= user_today - timedelta(days=7)
            )
        ).all()
        
        if mental_symptoms:
            avg_severity = sum(s.severity for s in mental_symptoms) / len(mental_symptoms)
            if avg_severity > 6:
                emotional["needs_extra_care"] = True
                emotional["emotional_patterns"].append({
                    "pattern": "elevated_emotional_symptoms",
                    "avg_severity": round(avg_severity, 1)
                })
        
        return emotional
    
    async def _load_predictive_memory(self, user_id: str) -> Dict[str, Any]:
        """
        Layer 4: Anticipated needs based on patterns.
        "Based on your cycle, you might want to..."
        """
        from app.services.cycle_service import CycleService
        from app.core.database import SymptomLog, UserResponse
        
        predictive = {
            "upcoming_phase": None,
            "expected_symptoms": [],
            "suggested_focus": [],
            "timing_predictions": {}
        }
        
        # Get cycle info
        cycle_service = CycleService(self.db)
        cycle_info = cycle_service.get_cycle_phase_info(user_id)
        
        if cycle_info:
            # Predict upcoming phase
            current_day = cycle_info.cycle_day
            
            # Get user's cycle length
            user_response = self.db.query(UserResponse).filter(
                UserResponse.uid == user_id
            ).order_by(desc(UserResponse.created_at)).first()
            
            cycle_length = 28  # default
            if user_response and user_response.cycle_length:
                length_map = {
                    "Less than 21 days": 19,
                    "21-25 days": 23,
                    "26-30 days": 28,
                    "31-35 days": 33,
                    "35+ days": 40
                }
                cycle_length = length_map.get(user_response.cycle_length, 28)
            
            # Calculate upcoming phase
            ovulation_day = cycle_length - 14
            
            if current_day < 5:
                predictive["upcoming_phase"] = {
                    "phase": "late_menstrual",
                    "days_until": 5 - current_day,
                    "preparation_tip": "Energy levels will start rising soon"
                }
            elif current_day < ovulation_day - 2:
                predictive["upcoming_phase"] = {
                    "phase": "ovulation",
                    "days_until": ovulation_day - current_day,
                    "preparation_tip": "Peak energy and confidence approaching"
                }
            elif current_day < ovulation_day + 2:
                predictive["upcoming_phase"] = {
                    "phase": "luteal",
                    "days_until": ovulation_day + 2 - current_day,
                    "preparation_tip": "Self-care and rest will become more important"
                }
            else:
                days_until_period = cycle_length - current_day
                predictive["upcoming_phase"] = {
                    "phase": "menstrual",
                    "days_until": days_until_period,
                    "preparation_tip": "Gentle movement and comfort foods recommended"
                }
            
            # Predict symptoms based on history
            from app.utils.timezone_utils import get_user_current_date
            from datetime import timedelta
            
            user_today = get_user_current_date(user_id, self.db)
            thirty_days_ago = user_today - timedelta(days=30)
            symptoms = self.db.query(SymptomLog).filter(
                and_(
                    SymptomLog.user_id == user_id,
                    SymptomLog.logged_date >= thirty_days_ago
                )
            ).all()
            
            # Find symptoms that typically occur in the upcoming phase
            upcoming_phase_name = predictive["upcoming_phase"]["phase"] if predictive["upcoming_phase"] else None
            if upcoming_phase_name:
                phase_symptoms = defaultdict(list)
                for symptom in symptoms:
                    if symptom.cycle_phase and upcoming_phase_name.lower() in symptom.cycle_phase.lower():
                        phase_symptoms[symptom.symptom_type].append(symptom.severity)
                
                for symptom_type, severities in phase_symptoms.items():
                    avg_severity = sum(severities) / len(severities)
                    if avg_severity >= 4:  # Only predict notable symptoms
                        predictive["expected_symptoms"].append({
                            "symptom": symptom_type,
                            "expected_severity": round(avg_severity, 1),
                            "likelihood": min(len(severities) / 3, 1.0)  # Based on how often it occurred
                        })
        
        return predictive
    
    async def _extract_insights(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Extract high-level insights from all memory layers.
        These are the "clinical observations" a doctor would note.
        """
        insights = []
        
        # Combine patterns from semantic and emotional memory
        semantic = await self._load_semantic_memory(user_id)
        emotional = await self._load_emotional_memory(user_id)
        
        # Generate insights
        if semantic.get("strengths"):
            insights.append({
                "type": "strength",
                "content": f"Shows consistent effort in: {', '.join(semantic['strengths'][:2])}",
                "importance": "high"
            })
        
        if semantic.get("challenges"):
            insights.append({
                "type": "challenge",
                "content": f"May benefit from support with: {', '.join(semantic['challenges'][:2])}",
                "importance": "medium"
            })
        
        if emotional.get("needs_extra_care"):
            insights.append({
                "type": "attention_needed",
                "content": "Showing signs of elevated stress/emotional burden - approach with extra warmth",
                "importance": "high"
            })
        
        if semantic.get("typical_behaviors", {}).get("best_time"):
            insights.append({
                "type": "preference",
                "content": f"Most productive during {semantic['typical_behaviors']['best_time']}",
                "importance": "medium"
            })
        
        return insights

    async def _load_profile_gaps(self, user_id: str) -> Dict[str, Any]:
        """
        Identify what we DON'T know about the user yet.
        This drives the Deep Profiling diagnostic protocol.
        """
        from app.core.database import UserProfile
        
        profile = self.db.query(UserProfile).filter(UserProfile.uid == user_id).first()
        chatbot_memory = profile.chatbot_memory or {} if profile else {}
        
        gaps = {}
        for field_key, description in self.IDEAL_PROFILE_FIELDS.items():
            # Check if we have an explicit or inferred value for this
            if field_key not in chatbot_memory:
                gaps[field_key] = description
        
        return {
            "missing_fields": gaps,
            "profile_density": round(1 - (len(gaps) / len(self.IDEAL_PROFILE_FIELDS)), 2),
            "priority_gap": list(gaps.keys())[0] if gaps else None
        }
    
    async def _load_relationship_context(self, user_id: str) -> Dict[str, Any]:
        """
        Load the relationship context - how long have we known this user?
        """
        from app.core.database import UserProfile, ChatSession
        
        user = self.db.query(UserProfile).filter(UserProfile.uid == user_id).first()
        sessions = self.db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).count()
        
        days_known = 0
        if user and user.created_at:
            days_known = (datetime.utcnow() - user.created_at).days
        
        return {
            "days_since_joined": days_known,
            "total_conversations": sessions,
            "relationship_stage": self._determine_relationship_stage(days_known, sessions)
        }
    
    async def _load_weekly_checkin_memory(self, user_id: str) -> Dict[str, Any]:
        """
        Load weekly check-in history for personalized context.
        This gives us structured symptom progression data.
        """
        from app.core.database import WeeklyCheckIn
        
        checkin_memory = {
            "recent_checkins": [],
            "symptom_progression": [],
            "recurring_concerns": [],
            "positive_factors": [],
            "negative_factors": [],
            "overall_trend": "stable"
        }
        
        try:
            # Get recent check-ins (last 8 weeks)
            checkins = self.db.query(WeeklyCheckIn).filter(
                and_(
                    WeeklyCheckIn.uid == user_id,
                    WeeklyCheckIn.is_complete == True
                )
            ).order_by(desc(WeeklyCheckIn.completed_at)).limit(8).all()
            
            if not checkins:
                return checkin_memory
            
            # Process check-ins for memory
            severity_values = []
            wellbeing_values = []
            concern_counts = {}
            positive_factor_counts = {}
            negative_factor_counts = {}
            
            for checkin in checkins:
                # Add to recent checkins
                checkin_memory["recent_checkins"].append({
                    "date": checkin.check_in_date.isoformat() if checkin.check_in_date else None,
                    "concern": checkin.top_concern,
                    "severity": checkin.concern_severity,
                    "wellbeing": checkin.overall_wellbeing,
                    "phase": checkin.phase_at_checkin,
                    "summary": checkin.conversation_summary
                })
                
                # Track severity progression
                if checkin.concern_severity:
                    severity_values.append(checkin.concern_severity)
                    checkin_memory["symptom_progression"].append({
                        "week": f"W{checkin.week_number}",
                        "concern": checkin.top_concern,
                        "severity": checkin.concern_severity
                    })
                
                if checkin.overall_wellbeing:
                    wellbeing_values.append(checkin.overall_wellbeing)
                
                # Count concerns
                if checkin.top_concern:
                    concern = checkin.top_concern.lower()
                    concern_counts[concern] = concern_counts.get(concern, 0) + 1
                
                # Count factors
                if checkin.factors_positive:
                    for factor in checkin.factors_positive:
                        positive_factor_counts[factor] = positive_factor_counts.get(factor, 0) + 1
                
                if checkin.factors_negative:
                    for factor in checkin.factors_negative:
                        negative_factor_counts[factor] = negative_factor_counts.get(factor, 0) + 1
            
            # Find recurring concerns (mentioned 2+ times)
            checkin_memory["recurring_concerns"] = [
                {"concern": c, "count": count}
                for c, count in concern_counts.items()
                if count >= 2
            ]
            
            # Top positive factors
            checkin_memory["positive_factors"] = sorted(
                [{"factor": f, "count": c} for f, c in positive_factor_counts.items()],
                key=lambda x: x["count"],
                reverse=True
            )[:5]
            
            # Top negative factors
            checkin_memory["negative_factors"] = sorted(
                [{"factor": f, "count": c} for f, c in negative_factor_counts.items()],
                key=lambda x: x["count"],
                reverse=True
            )[:5]
            
            # Calculate overall trend
            if len(severity_values) >= 3:
                recent_avg = sum(severity_values[:3]) / 3
                older_avg = sum(severity_values[3:]) / len(severity_values[3:]) if len(severity_values) > 3 else recent_avg
                
                if recent_avg < older_avg - 1:
                    checkin_memory["overall_trend"] = "improving"
                elif recent_avg > older_avg + 1:
                    checkin_memory["overall_trend"] = "worsening"
                else:
                    checkin_memory["overall_trend"] = "stable"
            
        except Exception as e:
            logger.warning(f"Error loading weekly check-in memory: {e}")
        
        return checkin_memory
    
    def _determine_relationship_stage(self, days: int, conversations: int) -> str:
        """Determine the relationship stage for appropriate tone."""
        if days < 7 or conversations < 3:
            return "new_acquaintance"  # More formal, educational
        elif days < 30 or conversations < 15:
            return "building_trust"  # Warmer, more personalized
        elif days < 90 or conversations < 50:
            return "established"  # Comfortable, can be more direct
        else:
            return "deep_relationship"  # Very familiar, like a trusted friend
    
    def _extract_topics_from_messages(self, messages: List) -> List[str]:
        """Extract topics mentioned in messages."""
        topics = []
        topic_keywords = {
            "sleep": ["sleep", "tired", "insomnia", "rest", "fatigue"],
            "stress": ["stress", "anxiety", "worried", "overwhelmed", "pressure"],
            "work": ["work", "job", "office", "career", "busy"],
            "exercise": ["workout", "exercise", "gym", "yoga", "movement"],
            "food": ["food", "diet", "eating", "meal", "nutrition"],
            "period": ["period", "cycle", "menstrual", "cramps", "bleeding"],
            "mood": ["mood", "feeling", "emotions", "happy", "sad"],
            "relationships": ["partner", "family", "friend", "relationship"]
        }
        
        for msg in messages:
            content_lower = msg.content.lower()
            for topic, keywords in topic_keywords.items():
                if any(kw in content_lower for kw in keywords):
                    topics.append(topic)
        
        return list(set(topics))
    
    def _find_unresolved_concerns(self, messages: List) -> List[Dict[str, Any]]:
        """Find concerns that weren't fully addressed."""
        concerns = []
        concern_indicators = [
            "worried about", "concerned about", "struggling with",
            "having trouble", "can't seem to", "still having"
        ]
        
        for msg in messages:
            if msg.role != "user":
                continue
            
            content_lower = msg.content.lower()
            for indicator in concern_indicators:
                if indicator in content_lower:
                    # Extract the concern
                    idx = content_lower.find(indicator)
                    concern_text = msg.content[idx:idx+100]
                    concerns.append({
                        "text": concern_text[:80] + "..." if len(concern_text) > 80 else concern_text,
                        "mentioned": msg.created_at.isoformat()
                    })
                    break
        
        return concerns[:5]  # Limit to 5 most recent
    
    def _empty_memory(self) -> Dict[str, Any]:
        """Return empty memory structure."""
        return {
            "episodic": {"last_conversation": None, "recent_topics": [], "unresolved_concerns": []},
            "semantic": {"preferences": {}, "patterns": {}, "strengths": [], "challenges": []},
            "emotional": {"recent_mood_trend": "unknown", "needs_extra_care": False},
            "predictive": {"upcoming_phase": None, "expected_symptoms": []},
            "insights": [],
            "relationship": {"days_since_joined": 0, "total_conversations": 0, "relationship_stage": "new_acquaintance"}
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def format_memory_for_prompt(memory: Dict[str, Any]) -> str:
    """
    Format memory context into a readable prompt section.
    """
    sections = []
    
    # Relationship context
    rel = memory.get("relationship", {})
    if rel.get("relationship_stage") == "new_acquaintance":
        sections.append("🆕 NEW USER - Be extra welcoming and educational")
    elif rel.get("relationship_stage") == "deep_relationship":
        sections.append(f"💜 ESTABLISHED RELATIONSHIP ({rel.get('total_conversations', 0)} conversations)")
    
    # Weekly Check-in Memory (doctor's notes from structured check-ins)
    checkins = memory.get("weekly_checkins", {})
    if checkins.get("recent_checkins"):
        sections.append("\n📋 WEEKLY CHECK-IN HISTORY:")
        for i, checkin in enumerate(checkins["recent_checkins"][:3]):
            sections.append(f"   - {checkin.get('date', 'Unknown')}: {checkin.get('concern', 'No concern')} "
                          f"(severity: {checkin.get('severity', '?')}/9, wellbeing: {checkin.get('wellbeing', '?')}/9)")
        
        if checkins.get("overall_trend"):
            trend_emoji = {"improving": "📈", "worsening": "📉", "stable": "➡️"}.get(checkins["overall_trend"], "")
            sections.append(f"   {trend_emoji} Overall trend: {checkins['overall_trend']}")
    
    if checkins.get("recurring_concerns"):
        concerns = [c["concern"] for c in checkins["recurring_concerns"][:3]]
        sections.append(f"   🔄 Recurring concerns: {', '.join(concerns)}")
    
    if checkins.get("positive_factors"):
        factors = [f["factor"] for f in checkins["positive_factors"][:3]]
        sections.append(f"   ✅ What helps: {', '.join(factors)}")
    
    if checkins.get("negative_factors"):
        factors = [f["factor"] for f in checkins["negative_factors"][:3]]
        sections.append(f"   ⚠️ What hurts: {', '.join(factors)}")
    
    # Episodic memories
    episodic = memory.get("episodic", {})
    if episodic.get("last_conversation"):
        last = episodic["last_conversation"]
        sections.append(f"\n📝 LAST CONVERSATION ({last.get('context', 'general')}):")
        if last.get("summary"):
            sections.append(f"   Summary: {last['summary']}")
    
    if episodic.get("unresolved_concerns"):
        sections.append("\n⚠️ UNRESOLVED CONCERNS:")
        for concern in episodic["unresolved_concerns"][:2]:
            sections.append(f"   - {concern['text']}")
    
    # Semantic patterns
    semantic = memory.get("semantic", {})
    if semantic.get("strengths"):
        sections.append(f"\n✨ STRENGTHS: {', '.join(semantic['strengths'][:3])}")
    if semantic.get("challenges"):
        sections.append(f"🎯 AREAS TO SUPPORT: {', '.join(semantic['challenges'][:2])}")
    
    prefs = semantic.get("preferences", {})
    if prefs.get("time_slots"):
        best_times = [slot for slot, data in prefs["time_slots"].items() if data.get("is_preferred")]
        if best_times:
            sections.append(f"⏰ BEST TIME: {', '.join(best_times)}")
    
    # Emotional state
    emotional = memory.get("emotional", {})
    if emotional.get("needs_extra_care"):
        sections.append("\n💝 NEEDS EXTRA CARE - Use gentler tone, more empathy")
        
    # Profile Gaps (The Deep Profiling Goals)
    gaps = memory.get("profile_gaps", {})
    if gaps and gaps.get("missing_fields"):
        sections.append("\n🔍 PROFILING OPPORTUNITIES (What we don't know yet):")
        for field, desc in list(gaps["missing_fields"].items())[:3]:  # Limit to top 3 gaps
            sections.append(f"   - {field}: {desc}")
    
    # Predictive insights
    predictive = memory.get("predictive", {})
    if predictive.get("upcoming_phase"):
        phase = predictive["upcoming_phase"]
        sections.append(f"\n🔮 UPCOMING: {phase['phase']} in {phase['days_until']} days")
        sections.append(f"   Tip: {phase['preparation_tip']}")
    
    if predictive.get("expected_symptoms"):
        symptoms = [s["symptom"] for s in predictive["expected_symptoms"][:3]]
        sections.append(f"   May experience: {', '.join(symptoms)}")
    
    # Key insights
    insights = memory.get("insights", [])
    high_priority = [i for i in insights if i.get("importance") == "high"]
    if high_priority:
        sections.append("\n🎯 KEY INSIGHTS:")
        for insight in high_priority[:2]:
            sections.append(f"   - {insight['content']}")
    
    return "\n".join(sections) if sections else "First interaction with user"

