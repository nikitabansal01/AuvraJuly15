"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - User Context Service
═══════════════════════════════════════════════════════════════════════════════
Aggregates all user data into a "patient file" for the chatbot.
Acts as the doctor's complete knowledge about the patient.
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc

from app.core.database import (
    UserProfile, UserResponse, DailyAssignment, RecommendationRecord,
    RecommendationCompletion, RecommendationAdvice, SymptomLog,
    ConversationSummary, ChatSession, ChatMessage, AssignmentSkipLog
)
from app.services.cycle_service import CycleService
from app.models.chat_models import PatientProfile, TodaysPlan, RecentSummary

logger = logging.getLogger(__name__)


class UserContextService:
    """
    Service to aggregate all user data for chatbot context.
    This is the "patient file" that the doctor-like AI has access to.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.cycle_service = CycleService(db)
    
    async def get_patient_profile(self, user_id: str) -> PatientProfile:
        """
        Get complete patient profile - everything we know about the user.
        This is the foundation of doctor-like personalization.
        """
        try:
            # Get user profile
            user_profile = self.db.query(UserProfile).filter(
                UserProfile.uid == user_id
            ).first()
            
            # Get user response (health survey data)
            user_response = self.db.query(UserResponse).filter(
                UserResponse.uid == user_id
            ).order_by(desc(UserResponse.created_at)).first()
            
            # Get cycle info
            cycle_info = self.cycle_service.get_cycle_phase_info(user_id)
            
            # Build patient profile
            profile = PatientProfile(
                user_id=user_id,
                name=user_profile.name if user_profile else None,
                age=user_response.age if user_response else None,
                
                # Cycle info - use getattr for optional fields not in CyclePhaseInfo
                cycle_day=cycle_info.cycle_day if cycle_info else None,
                phase=cycle_info.phase if cycle_info else None,
                phase_description=getattr(cycle_info, 'phase_description', None) if cycle_info else None,
                last_period_date=user_response.last_period_date_utc.date() if user_response and user_response.last_period_date_utc else None,
                cycle_length=user_response.cycle_length if user_response else None,
                
                # Health concerns
                period_description=user_response.period_description if user_response else None,
                period_concerns=self._safe_list(user_response.period_concerns if user_response else None),
                body_concerns=self._safe_list(user_response.body_concerns if user_response else None),
                skin_hair_concerns=self._safe_list(user_response.skin_hair_concerns if user_response else None),
                mental_health_concerns=self._safe_list(user_response.mental_health_concerns if user_response else None),
                other_concerns=self._safe_list(user_response.other_concerns if user_response else None),
                top_concern=user_response.top_concern if user_response else None,
                
                # Medical history
                diagnosed_conditions=user_response.diagnosed_conditions or [] if user_response else [],
                family_history=user_response.family_history or [] if user_response else [],
                birth_control=user_response.birth_control or [] if user_response else [],
                
                # Lifestyle
                workout_intensity=user_response.workout_intensity if user_response else None,
                sleep_duration=user_response.sleep_duration if user_response else None,
                stress_level=user_response.stress_level if user_response else None,
                lifestyle_focus=user_response.lifestyle_focus or [] if user_response else [],
                
                # Hormone analysis
                primary_hormone=user_response.primary_hormone if user_response else None,
                secondary_hormones=user_response.secondary_hormones or [] if user_response else [],
                
                # CHATBOT MEMORY - User preferences (diet, allergies, etc.)
                chatbot_memory=user_profile.chatbot_memory if user_profile and user_profile.chatbot_memory else {},
                
                # Timezone for context
                timezone=user_profile.current_timezone if user_profile else "UTC"
            )
            
            return profile
            
        except Exception as e:
            logger.error(f"Error getting patient profile for {user_id}: {str(e)}")
            return PatientProfile(user_id=user_id)
    
    async def get_todays_plan(self, user_id: str, timezone: str = "Asia/Seoul") -> TodaysPlan:
        """
        Get today's action plan with all assignments.
        """
        try:
            from zoneinfo import ZoneInfo
            from app.utils.timezone_utils import get_local_date
            
            # Get user's timezone
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == user_id).first()
            if user_profile and user_profile.current_timezone:
                timezone = user_profile.current_timezone
            
            # Get today's date in user's timezone
            today = get_local_date(timezone)
            
            # Get all assignments for today
            assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == user_id,
                    DailyAssignment.assignment_date == today
                )
            ).all()
            
            # Organize by time slot
            morning = []
            afternoon = []
            evening = []
            anytime = []
            
            completed_count = 0
            hormone_stats = {}
            
            for assignment in assignments:
                # Get recommendation details
                rec = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == assignment.recommendation_id
                ).first()
                
                if not rec:
                    continue
                
                assignment_data = {
                    "id": assignment.id,
                    "recommendation_id": rec.id,
                    "title": rec.title,
                    "purpose": rec.purpose,
                    "specific_action": rec.specific_action,
                    "category": rec.category,
                    "is_completed": assignment.is_completed,
                    "completed_at": assignment.completed_at.isoformat() if assignment.completed_at else None,
                    "time_slot": assignment.time_group,
                    "hormones": rec.hormones or [],
                    "conditions": rec.conditions or [],
                    "symptoms": rec.symptoms or [],
                }
                
                # Add category-specific details
                if rec.category == "food":
                    assignment_data["food_items"] = rec.food_items or []
                    assignment_data["food_amounts"] = rec.food_amounts or []
                elif rec.category == "movement":
                    assignment_data["exercise_types"] = rec.exercise_types or []
                    assignment_data["exercise_durations"] = rec.exercise_durations or []
                elif rec.category == "mindfulness":
                    assignment_data["mindfulness_techniques"] = rec.mindfulness_techniques or []
                    assignment_data["mindfulness_durations"] = rec.mindfulness_durations or []
                
                # Categorize by time slot
                if assignment.time_group == "morning":
                    morning.append(assignment_data)
                elif assignment.time_group == "afternoon":
                    afternoon.append(assignment_data)
                elif assignment.time_group in ["evening", "night"]:
                    evening.append(assignment_data)
                else:
                    anytime.append(assignment_data)
                
                # Count completions
                if assignment.is_completed:
                    completed_count += 1
                
                # Track hormone progress
                for hormone in (rec.hormones or []):
                    if hormone not in hormone_stats:
                        hormone_stats[hormone] = {"completed": 0, "total": 0}
                    hormone_stats[hormone]["total"] += 1
                    if assignment.is_completed:
                        hormone_stats[hormone]["completed"] += 1
            
            total = len(assignments)
            
            return TodaysPlan(
                date=today,
                total_assignments=total,
                completed_assignments=completed_count,
                completion_rate=completed_count / total if total > 0 else 0,
                morning=morning,
                afternoon=afternoon,
                evening=evening,
                anytime=anytime,
                hormone_stats=hormone_stats
            )
            
        except Exception as e:
            logger.error(f"Error getting today's plan for {user_id}: {str(e)}")
            from app.utils.timezone_utils import get_user_current_date
            fallback_date = get_user_current_date(user_id, self.db)
            return TodaysPlan(
                date=fallback_date,
                total_assignments=0,
                completed_assignments=0,
                completion_rate=0
            )
    
    async def get_recent_summary(self, user_id: str, days: int = 7) -> RecentSummary:
        """
        Get recent activity summary for memory context (Layer 2).
        """
        try:
            from app.utils.timezone_utils import get_user_current_date
            
            user_today = get_user_current_date(user_id, self.db)
            end_date = user_today
            start_date = end_date - timedelta(days=days)
            
            # Get recent symptom logs
            symptoms = self.db.query(SymptomLog).filter(
                and_(
                    SymptomLog.user_id == user_id,
                    SymptomLog.logged_date >= start_date
                )
            ).order_by(desc(SymptomLog.logged_at)).all()
            
            # Aggregate symptoms
            symptom_data = {}
            all_factors = []
            for log in symptoms:
                if log.symptom_type not in symptom_data:
                    symptom_data[log.symptom_type] = {"severities": [], "count": 0}
                symptom_data[log.symptom_type]["severities"].append(log.severity)
                symptom_data[log.symptom_type]["count"] += 1
                all_factors.extend(log.factors or [])
            
            symptoms_reported = []
            for stype, data in symptom_data.items():
                avg = sum(data["severities"]) / len(data["severities"])
                trend = self._calculate_trend(data["severities"])
                symptoms_reported.append({
                    "type": stype,
                    "avg_severity": round(avg, 1),
                    "count": data["count"],
                    "trend": trend
                })
            
            # Get common factors
            from collections import Counter
            factor_counts = Counter(all_factors)
            common_factors = [f for f, _ in factor_counts.most_common(5)]
            
            # Get completion stats
            completions_query = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == user_id,
                    RecommendationCompletion.completion_date >= start_date
                )
            ).all()
            
            assignments_query = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == user_id,
                    DailyAssignment.assignment_date >= start_date
                )
            ).all()
            
            total_assigned = len(assignments_query)
            total_completed = sum(1 for a in assignments_query if a.is_completed)
            
            completions = {
                "total": total_completed,
                "assigned": total_assigned,
                "rate": round(total_completed / total_assigned, 2) if total_assigned > 0 else 0
            }
            
            # Get recent skips
            recent_skips = self.db.query(AssignmentSkipLog).filter(
                and_(
                    AssignmentSkipLog.user_id == user_id,
                    AssignmentSkipLog.skip_date >= start_date
                )
            ).order_by(desc(AssignmentSkipLog.skipped_at)).limit(5).all()
            
            skips_data = []
            for skip in recent_skips:
                rec = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == skip.recommendation_id
                ).first()
                skips_data.append({
                    "title": rec.title if rec else "Unknown",
                    "reason": skip.skip_reason,
                    "date": skip.skip_date.isoformat()
                })
            
            # Get conversation themes from recent chats
            recent_messages = self.db.query(ChatMessage).join(ChatSession).filter(
                and_(
                    ChatSession.user_id == user_id,
                    ChatMessage.created_at >= datetime.utcnow() - timedelta(days=days),
                    ChatMessage.role == "user"
                )
            ).order_by(desc(ChatMessage.created_at)).limit(20).all()
            
            # Simple theme extraction (could be enhanced with NLP)
            themes = []
            theme_keywords = {
                "hormones": ["hormone", "progesterone", "estrogen", "testosterone", "cortisol"],
                "symptoms": ["bloating", "pain", "cramp", "headache", "mood", "fatigue"],
                "sleep": ["sleep", "tired", "insomnia", "rest"],
                "stress": ["stress", "anxious", "worried", "overwhelmed"],
                "diet": ["food", "eat", "diet", "hungry", "carbs"],
                "exercise": ["exercise", "workout", "yoga", "move"]
            }
            
            for msg in recent_messages:
                content_lower = msg.content.lower()
                for theme, keywords in theme_keywords.items():
                    if any(kw in content_lower for kw in keywords):
                        if theme not in themes:
                            themes.append(theme)
            
            return RecentSummary(
                symptoms_reported=symptoms_reported,
                common_factors=common_factors,
                completions=completions,
                concerns_mentioned=[],  # Could be extracted from chat history
                recent_skips=skips_data,
                conversation_themes=themes
            )
            
        except Exception as e:
            logger.error(f"Error getting recent summary for {user_id}: {str(e)}")
            return RecentSummary()
    
    async def get_full_context(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get complete context for LangGraph agent - the "doctor's full knowledge".
        """
        patient_profile = await self.get_patient_profile(user_id)
        todays_plan = await self.get_todays_plan(user_id)
        recent_summary = await self.get_recent_summary(user_id)
        
        # Get current session messages if session exists
        current_messages = []
        if session_id:
            messages = self.db.query(ChatMessage).filter(
                ChatMessage.session_id == session_id
            ).order_by(ChatMessage.created_at).limit(20).all()
            
            current_messages = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "response_type": msg.response_type,
                    "created_at": msg.created_at.isoformat()
                }
                for msg in messages
            ]
        
        return {
            "patient_profile": patient_profile.model_dump(),
            "todays_plan": todays_plan.model_dump(),
            "recent_summary": recent_summary.model_dump(),
            "current_messages": current_messages
        }
    
    def _safe_list(self, value) -> List[str]:
        """Safely convert JSONB value to list."""
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.keys())
        return []
    
    def _calculate_trend(self, values: List[int]) -> str:
        """Calculate trend from a list of values."""
        if len(values) < 2:
            return "stable"
        
        # Compare first half average to second half average
        mid = len(values) // 2
        first_half = values[:mid] if mid > 0 else values[:1]
        second_half = values[mid:] if mid > 0 else values[1:]
        
        if not first_half or not second_half:
            return "stable"
        
        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)
        
        diff = second_avg - first_avg
        if diff > 1:
            return "worsening"
        elif diff < -1:
            return "improving"
        return "stable"
