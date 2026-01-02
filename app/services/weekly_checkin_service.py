"""
Weekly Check-in Service
Manages the conversational check-in flow that captures weekly symptom patterns,
lifestyle factors, and action plan reflections.

This data powers:
1. Personalized action plan generation
2. Insights screen visualizations
3. LLM memory for contextual conversations
"""
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from app.core.database import (
    WeeklyCheckIn, WeeklyCheckInQuestion, UserProfile, 
    SymptomLog, ActionPlan, ActionPlanItem
)
from app.utils.timezone_utils import get_user_current_date

logger = logging.getLogger(__name__)


class WeeklyCheckInService:
    """
    Manages weekly check-in sessions.
    
    Check-in flow:
    1. Check if user is due for check-in (every 7 days after first completion)
    2. Start check-in session → creates WeeklyCheckIn record
    3. Progress through questions → update responses
    4. Complete check-in → summarize, link to SymptomLogs, schedule next
    """
    
    CHECK_IN_INTERVAL_DAYS = 7  # Weekly check-ins
    
    def __init__(self, db: Session):
        self.db = db
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STATUS & SCHEDULING
    # ═══════════════════════════════════════════════════════════════════════════
    
    def is_checkin_due(self, uid: str) -> Tuple[bool, Optional[date]]:
        """
        Check if user is due for a weekly check-in.
        
        Returns:
            (is_due: bool, due_date: Optional[date])
        """
        user_today = get_user_current_date(uid, self.db)
        
        # Get user profile
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        if not profile:
            return False, None
        
        # Check for incomplete check-in (resume it)
        incomplete = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == False
            )
        ).first()
        
        if incomplete:
            logger.info(f"User {uid} has incomplete check-in {incomplete.id}")
            return True, incomplete.check_in_date
        
        # Check due date
        if profile.weekly_checkin_due_date:
            is_due = user_today >= profile.weekly_checkin_due_date
            return is_due, profile.weekly_checkin_due_date
        
        # No due date set - check last completed check-in
        last_checkin = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == True
            )
        ).order_by(desc(WeeklyCheckIn.completed_at)).first()
        
        if last_checkin and last_checkin.completed_at:
            next_due = last_checkin.completed_at.date() + timedelta(days=self.CHECK_IN_INTERVAL_DAYS)
            is_due = user_today >= next_due
            return is_due, next_due
        
        # Never done a check-in - always due immediately
        return True, user_today
    
    def get_checkin_status(self, uid: str) -> Dict[str, Any]:
        """
        Get comprehensive check-in status for action plan card.
        
        Returns status dict with:
        - is_available: bool (user has unlocked check-ins)
        - is_due: bool
        - due_date: str or None
        - incomplete_id: str or None (if there's a resumable session)
        - last_completed: str or None
        - streak_of_checkins: int
        """
        user_today = get_user_current_date(uid, self.db)
        
        # Feature is always available
        is_available = True
        
        # Check for incomplete session
        incomplete = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == False
            )
        ).first()
        
        # Get last completed
        last_completed = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == True
            )
        ).order_by(desc(WeeklyCheckIn.completed_at)).first()
        
        is_due, due_date = self.is_checkin_due(uid)
        
        # Calculate check-in streak (consecutive weeks)
        checkin_streak = self._calculate_checkin_streak(uid)
        
        return {
            "is_available": True,
            "is_due": is_due,
            "due_date": due_date.isoformat() if due_date else None,
            "incomplete_id": incomplete.id if incomplete else None,
            "last_completed": last_completed.completed_at.isoformat() if last_completed else None,
            "checkin_streak": checkin_streak,
            "unlock_days_remaining": 0
        }
    
    def _get_days_until_unlock(self, uid: str) -> int:
        """Calculate days until symptom_patterns reward is unlocked."""
        from app.services.streak_service import StreakService
        streak_service = StreakService(self.db)
        streak_data = streak_service.get_or_create_streak_data(uid)
        
        # symptom_patterns unlocks at 14 days
        required_days = 14
        remaining = max(0, required_days - streak_data.current_streak)
        return remaining
    
    def _calculate_checkin_streak(self, uid: str) -> int:
        """Calculate consecutive weeks of completed check-ins."""
        checkins = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == True
            )
        ).order_by(desc(WeeklyCheckIn.completed_at)).all()
        
        if not checkins:
            return 0
        
        streak = 1
        for i in range(1, len(checkins)):
            prev_checkin = checkins[i-1]
            curr_checkin = checkins[i]
            
            # Check if consecutive weeks
            days_diff = (prev_checkin.completed_at.date() - curr_checkin.completed_at.date()).days
            if 5 <= days_diff <= 9:  # Within a week (with some tolerance)
                streak += 1
            else:
                break
        
        return streak
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CHECK-IN SESSION MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════════
    
    def start_checkin(self, uid: str) -> Tuple[WeeklyCheckIn, Dict[str, Any]]:
        """
        Start a new check-in session or resume incomplete one.
        
        Returns:
            (checkin: WeeklyCheckIn, first_question: Dict)
        """
        user_today = get_user_current_date(uid, self.db)
        iso_week = user_today.isocalendar()
        
        # Check for existing incomplete session
        existing = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == False
            )
        ).first()
        
        if existing:
            logger.info(f"Resuming existing check-in {existing.id} for user {uid}")
            current_question = self.get_question_at_index(existing.current_question_index)
            return existing, self._format_question(current_question, existing)
        
        # Get cycle context
        from app.services.cycle_service import CycleService
        cycle_service = CycleService(self.db)
        cycle_info = cycle_service.get_cycle_phase_info(uid)
        
        # Get user's primary concern from their profile/onboarding
        from app.core.database import UserResponse
        user_response = self.db.query(UserResponse).filter(UserResponse.uid == uid).first()
        user_top_concern = "your symptoms"  # Fallback
        if user_response:
            # Check top_concern first
            if user_response.top_concern:
                user_top_concern = user_response.top_concern
            # Fallback to body_concerns if available
            elif user_response.body_concerns:
                if isinstance(user_response.body_concerns, list) and len(user_response.body_concerns) > 0:
                    user_top_concern = user_response.body_concerns[0]
                elif isinstance(user_response.body_concerns, str):
                    user_top_concern = user_response.body_concerns
        
        logger.info(f"User {uid} top concern for check-in: {user_top_concern}")
        
        # Create new check-in
        checkin = WeeklyCheckIn(
            id=str(uuid.uuid4()),
            uid=uid,
            week_number=iso_week.week,
            year=iso_week.year,
            check_in_date=user_today,
            cycle_day_at_checkin=cycle_info.cycle_day,
            phase_at_checkin=cycle_info.phase,
            current_question_index=0,
            is_complete=False,
            started_at=datetime.utcnow(),
            raw_messages=[],
            top_concern=user_top_concern  # Use user's actual primary concern
        )
        
        self.db.add(checkin)
        self.db.commit()
        self.db.refresh(checkin)
        
        logger.info(f"Started new check-in {checkin.id} for user {uid}")
        
        # Use AI engine to generate first question
        from app.services.weekly_checkin_ai import WeeklyCheckInAI
        ai_engine = WeeklyCheckInAI(self.db)
        first_question = ai_engine.generate_opening_question(uid, checkin)
        
        # Store initial bot message
        messages = [{
            "role": "assistant",
            "content": first_question.message,
            "question_key": first_question.question_key,
            "timestamp": datetime.utcnow().isoformat()
        }]
        checkin.raw_messages = messages
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(checkin, "raw_messages")
        self.db.commit()
        
        return checkin, self._format_ai_question(first_question, checkin)
    
    def _get_chat_history(self, checkin: WeeklyCheckIn) -> List[Dict[str, Any]]:
        """Convert raw messages to frontend chat format."""
        history = []
        if not checkin.raw_messages:
            return history
            
        for i, msg in enumerate(checkin.raw_messages):
            is_bot = msg.get("role") == "assistant"
            history.append({
                "id": f"hist_{i}",
                "text": msg.get("content", ""),
                "isBot": is_bot
            })
        return history

    def _format_ai_question(self, ai_question, checkin: WeeklyCheckIn) -> Dict[str, Any]:
        """Format AI-generated question for API response."""
        from app.services.weekly_checkin_ai import AIQuestion
        
        history = self._get_chat_history(checkin)
        
        if ai_question is None:
            return {
                "is_complete": True,
                "question_key": None,
                "message": "Thanks for checking in! 💜 I'll use this to personalize your plan.",
                "history": history
            }
        
        return {
            "is_complete": False,
            "question_key": ai_question.question_key,
            "question_type": ai_question.question_type.value if hasattr(ai_question.question_type, 'value') else ai_question.question_type,
            "message": ai_question.message,
            "tap_options": ai_question.tap_options,
            "is_required": ai_question.is_required,
            "slider_labels": ai_question.slider_labels,
            "current_index": checkin.current_question_index,
            "total_questions": 0,  # Dynamic - LLM decides when complete
            "history": history
        }
    
    def get_question_at_index(self, index: int) -> Optional[WeeklyCheckInQuestion]:
        """Get the question at a specific index in the flow."""
        return self.db.query(WeeklyCheckInQuestion).filter(
            and_(
                WeeklyCheckInQuestion.is_active == True,
                WeeklyCheckInQuestion.question_order == index + 1  # 1-indexed in DB
            )
        ).first()
    
    def get_all_questions(self) -> List[WeeklyCheckInQuestion]:
        """Get all active questions in order."""
        return self.db.query(WeeklyCheckInQuestion).filter(
            WeeklyCheckInQuestion.is_active == True
        ).order_by(WeeklyCheckInQuestion.question_order).all()
    
    def _format_question(self, question: Optional[WeeklyCheckInQuestion], checkin: WeeklyCheckIn) -> Dict[str, Any]:
        """Format question for API response with personalized tap options."""
        if not question:
            return {
                "is_complete": True,
                "question_key": None,
                "message": "Thanks for checking in! 💜"
            }
        
        # Personalize template
        message = question.question_template
        if checkin.top_concern and "{top_concern}" in message:
            message = message.replace("{top_concern}", checkin.top_concern.lower())
        
        # Get personalized tap options (or defaults)
        tap_options = self._get_personalized_options(question, checkin)
        
        return {
            "is_complete": False,
            "question_key": question.question_key,
            "question_type": question.question_type,
            "message": message,
            "tap_options": tap_options,
            "is_required": question.is_required,
            "current_index": checkin.current_question_index,
            "total_questions": len(self.get_all_questions()),
            "history": self._get_chat_history(checkin)
        }
    
    def _get_personalized_options(self, question: WeeklyCheckInQuestion, checkin: WeeklyCheckIn) -> List[Dict[str, str]]:
        """
        Get personalized tap options based on user history.
        Falls back to defaults if personalization fails.
        """
        import json
        
        # Parse default options
        try:
            if isinstance(question.default_tap_options, list):
                default_options = question.default_tap_options
            elif isinstance(question.default_tap_options, str):
                default_options = json.loads(question.default_tap_options) if question.default_tap_options else []
            else:
                default_options = []
        except (json.JSONDecodeError, TypeError):
            default_options = []
        
        # Format as objects with id and text
        formatted = []
        for opt in default_options:
            if isinstance(opt, str):
                formatted.append({
                    "id": opt.lower().replace(" ", "_"),
                    "text": opt
                })
            elif isinstance(opt, dict):
                formatted.append(opt)
        
        # TODO: Phase 9 - Use LLM to generate personalized options based on:
        # - User's past check-in responses
        # - Recent symptom logs
        # - Current cycle phase
        # For now, return defaults
        
        return formatted
    
    # ═══════════════════════════════════════════════════════════════════════════
    # RESPONSE HANDLING
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def submit_response(
        self, 
        checkin_id: str, 
        question_key: str, 
        response: Any,
        message_text: Optional[str] = None
    ) -> Tuple[WeeklyCheckIn, Dict[str, Any]]:
        """
        Submit a response to a check-in question.
        
        Args:
            checkin_id: The check-in session ID
            question_key: Which question is being answered
            response: The response value (string, int, list depending on question type)
            message_text: Optional raw message text for conversation log
        
        Returns:
            (updated_checkin, next_question_or_complete)
        """
        checkin = self.db.query(WeeklyCheckIn).filter(
            WeeklyCheckIn.id == checkin_id
        ).first()
        
        if not checkin:
            raise ValueError(f"Check-in {checkin_id} not found")
        
        if checkin.is_complete:
            raise ValueError(f"Check-in {checkin_id} is already complete")
        
        # Store response based on question key
        self._store_response(checkin, question_key, response)
        
        # Add to raw messages log
        if message_text:
            messages = checkin.raw_messages or []
            messages.append({
                "role": "user",
                "content": message_text,
                "question_key": question_key,
                "timestamp": datetime.utcnow().isoformat()
            })
            checkin.raw_messages = messages
        
        # Advance to next question
        checkin.current_question_index += 1
        
        # Use AI engine to generate next question dynamically
        from app.services.weekly_checkin_ai import WeeklyCheckInAI
        ai_engine = WeeklyCheckInAI(self.db)
        next_ai_question = await ai_engine.generate_followup_question(
            uid=checkin.uid,
            checkin=checkin,
            previous_response=response,
            previous_question_key=question_key
        )
        
        if next_ai_question:
             # Store bot message
             messages = checkin.raw_messages or []
             messages.append({
                "role": "assistant",
                "content": next_ai_question.message,
                "question_key": next_ai_question.question_key,
                "timestamp": datetime.utcnow().isoformat()
             })
             checkin.raw_messages = messages
             from sqlalchemy.orm.attributes import flag_modified
             flag_modified(checkin, "raw_messages")
        
        self.db.commit()
        self.db.refresh(checkin)
        
        if not next_ai_question:
            # AI says we're done - complete the check-in
            return self.complete_checkin(checkin_id)
        
        return checkin, self._format_ai_question(next_ai_question, checkin)
    
    def _store_response(self, checkin: WeeklyCheckIn, question_key: str, response: Any):
        """Store response in the appropriate field."""
        from sqlalchemy.orm.attributes import flag_modified
        
        if question_key == "top_concern":
            checkin.top_concern = response
        elif question_key == "concern_severity":
            try:
                # Try to convert to int directly
                val = int(response)
                checkin.concern_severity = val
            except (ValueError, TypeError):
                # If response is text (e.g. from "Type" mode), try to extract a number
                import re
                if isinstance(response, str):
                    numbers = re.findall(r'\d+', response)
                    if numbers:
                        val = int(numbers[0])
                        # Clamp to 1-9
                        val = max(1, min(9, val))
                        checkin.concern_severity = val
                        # Update response variable so next steps use the parsed int
                        response = val
                    else:
                        # Fallback if no number found - default to Moderate (5)
                        # This ensures the flow continues even if user just typed "It was bad"
                        logger.warning(f"Could not parse severity from '{response}', defaulting to 5")
                        checkin.concern_severity = 5
                        response = 5
                else:
                    checkin.concern_severity = 5
                    response = 5
        elif question_key == "overall_wellbeing":
            checkin.overall_wellbeing = int(response)
        elif question_key == "factors_positive":
            checkin.factors_positive = response if isinstance(response, list) else [response]
            flag_modified(checkin, "factors_positive")
        elif question_key == "factors_negative":
            checkin.factors_negative = response if isinstance(response, list) else [response]
            flag_modified(checkin, "factors_negative")
        elif question_key == "action_reflection":
            # Store as simple dict for now
            reflections = checkin.action_reflections or {}
            reflections["overall"] = response
            checkin.action_reflections = reflections
            flag_modified(checkin, "action_reflections")
        elif question_key == "concerns_next_week":
            checkin.concerns_next_week = response
        
        # Other keys (greeting, closing) don't need storage
    
    def _should_show_question(self, question: WeeklyCheckInQuestion, checkin: WeeklyCheckIn) -> bool:
        """Check if question should be shown based on conditions."""
        if not question.show_condition:
            return True
        
        import json
        try:
            condition = json.loads(question.show_condition) if isinstance(question.show_condition, str) else question.show_condition
        except:
            return True
        
        # Example condition: {"requires": "top_concern", "not_empty": true}
        if condition.get("requires"):
            field_value = getattr(checkin, condition["requires"], None)
            if condition.get("not_empty") and not field_value:
                return False
        
        return True
    
    # ═══════════════════════════════════════════════════════════════════════════
    # COMPLETION & SUMMARY
    # ═══════════════════════════════════════════════════════════════════════════
    
    def complete_checkin(self, checkin_id: str) -> Tuple[WeeklyCheckIn, Dict[str, Any]]:
        """
        Complete a check-in session.
        
        This:
        1. Creates SymptomLog entries from the check-in data
        2. Generates a summary for memory using AI
        3. Schedules the next check-in
        4. Returns completion message
        """
        checkin = self.db.query(WeeklyCheckIn).filter(
            WeeklyCheckIn.id == checkin_id
        ).first()
        
        if not checkin:
            raise ValueError(f"Check-in {checkin_id} not found")
        
        # Mark as complete
        checkin.is_complete = True
        checkin.completed_at = datetime.utcnow()
        
        # Create symptom log from primary concern
        if checkin.top_concern and checkin.concern_severity:
            self._create_symptom_log_from_checkin(checkin)
        
        # Extract summary from last AI message (AI naturally includes findings in completion message)
        # The AI's final message already contains the summary in patient-facing format
        if checkin.raw_messages and len(checkin.raw_messages) > 0:
            last_message = checkin.raw_messages[-1]
            if last_message.get('role') == 'assistant':
                # Store the AI's completion message as the summary
                checkin.conversation_summary = last_message.get('content', '')
        
        # Update user profile
        profile = self.db.query(UserProfile).filter(
            UserProfile.uid == checkin.uid
        ).first()
        
        if profile:
            user_today = get_user_current_date(checkin.uid, self.db)
            profile.weekly_checkin_due_date = user_today + timedelta(days=self.CHECK_IN_INTERVAL_DAYS)
            profile.last_weekly_checkin_id = checkin.id
        
        self.db.commit()
        self.db.refresh(checkin)
        
        logger.info(f"Completed check-in {checkin.id} for user {checkin.uid}")
        
        # Use AI's completion message directly (it already includes findings)
        completion_message = checkin.conversation_summary or "Thanks for sharing! I've updated your health profile. 💜"
        
        return checkin, {
            "is_complete": True,
            "question_key": None,
            "message": completion_message,
            "summary": checkin.conversation_summary
        }
    

    
    def _create_symptom_log_from_checkin(self, checkin: WeeklyCheckIn):
        """Create a SymptomLog entry from the check-in data."""
        symptom_log = SymptomLog(
            id=str(uuid.uuid4()),
            user_id=checkin.uid,
            symptom_type=checkin.top_concern.lower() if checkin.top_concern else "general",
            severity=checkin.concern_severity,
            factors=checkin.factors_negative or [],
            cycle_day=checkin.cycle_day_at_checkin,
            phase=checkin.phase_at_checkin,
            logged_via="weekly_checkin",
            weekly_checkin_id=checkin.id,
            logged_at=datetime.utcnow(),
            logged_date=checkin.check_in_date,
            notes=f"Weekly check-in week {checkin.week_number}"
        )
        
        self.db.add(symptom_log)
        logger.info(f"Created symptom log from check-in {checkin.id}")
    
    def _generate_summary(self, checkin: WeeklyCheckIn) -> str:
        """Generate a human-readable summary of the check-in for memory."""
        parts = []
        
        if checkin.top_concern:
            severity_word = "mild" if checkin.concern_severity and checkin.concern_severity <= 3 else \
                           "moderate" if checkin.concern_severity and checkin.concern_severity <= 6 else "severe"
            parts.append(f"Main concern: {severity_word} {checkin.top_concern.lower()}")
        
        if checkin.overall_wellbeing:
            wellbeing_word = "low" if checkin.overall_wellbeing <= 3 else \
                            "moderate" if checkin.overall_wellbeing <= 6 else "good"
            parts.append(f"Overall wellbeing: {wellbeing_word} ({checkin.overall_wellbeing}/9)")
        
        if checkin.factors_negative:
            parts.append(f"Negative factors: {', '.join(checkin.factors_negative)}")
        
        if checkin.factors_positive:
            parts.append(f"Positive factors: {', '.join(checkin.factors_positive)}")
        
        if checkin.action_reflections:
            overall = checkin.action_reflections.get("overall", "no feedback")
            parts.append(f"Action plan feedback: {overall}")
        
        if checkin.concerns_next_week:
            parts.append(f"Concerns for next week: {checkin.concerns_next_week}")
        
        if checkin.phase_at_checkin:
            parts.append(f"Cycle phase: {checkin.phase_at_checkin}")
        
        return " | ".join(parts) if parts else "No data collected"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # HISTORY & ANALYTICS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_checkin_history(
        self, 
        uid: str, 
        limit: int = 12,
        include_incomplete: bool = False
    ) -> List[WeeklyCheckIn]:
        """Get user's check-in history for insights and memory."""
        query = self.db.query(WeeklyCheckIn).filter(WeeklyCheckIn.uid == uid)
        
        if not include_incomplete:
            query = query.filter(WeeklyCheckIn.is_complete == True)
        
        return query.order_by(desc(WeeklyCheckIn.completed_at)).limit(limit).all()
    
    def get_severity_trends(self, uid: str, weeks: int = 8) -> List[Dict[str, Any]]:
        """Get severity trends over time for insights visualization."""
        checkins = self.get_checkin_history(uid, limit=weeks)
        
        trends = []
        for checkin in reversed(checkins):  # Oldest first for chart
            trends.append({
                "week": f"W{checkin.week_number}",
                "date": checkin.check_in_date.isoformat(),
                "concern": checkin.top_concern,
                "severity": checkin.concern_severity,
                "wellbeing": checkin.overall_wellbeing,
                "phase": checkin.phase_at_checkin
            })
        
        return trends
    
    def get_factor_correlations(self, uid: str, weeks: int = 12) -> Dict[str, Any]:
        """Analyze which factors correlate with better/worse symptoms."""
        checkins = self.get_checkin_history(uid, limit=weeks)
        
        factor_impact = {
            "positive": {},  # factor -> avg wellbeing when present
            "negative": {}   # factor -> avg severity when present
        }
        
        for checkin in checkins:
            if checkin.factors_positive:
                for factor in checkin.factors_positive:
                    if factor not in factor_impact["positive"]:
                        factor_impact["positive"][factor] = {"total": 0, "count": 0}
                    if checkin.overall_wellbeing:
                        factor_impact["positive"][factor]["total"] += checkin.overall_wellbeing
                        factor_impact["positive"][factor]["count"] += 1
            
            if checkin.factors_negative:
                for factor in checkin.factors_negative:
                    if factor not in factor_impact["negative"]:
                        factor_impact["negative"][factor] = {"total": 0, "count": 0}
                    if checkin.concern_severity:
                        factor_impact["negative"][factor]["total"] += checkin.concern_severity
                        factor_impact["negative"][factor]["count"] += 1
        
        # Calculate averages
        result = {
            "helps": [],
            "hurts": []
        }
        
        for factor, data in factor_impact["positive"].items():
            if data["count"] > 0:
                result["helps"].append({
                    "factor": factor,
                    "avg_wellbeing": round(data["total"] / data["count"], 1),
                    "occurrences": data["count"]
                })
        
        for factor, data in factor_impact["negative"].items():
            if data["count"] > 0:
                result["hurts"].append({
                    "factor": factor,
                    "avg_severity": round(data["total"] / data["count"], 1),
                    "occurrences": data["count"]
                })
        
        # Sort by impact
        result["helps"].sort(key=lambda x: x["avg_wellbeing"], reverse=True)
        result["hurts"].sort(key=lambda x: x["avg_severity"], reverse=True)
        
        return result
    
    def get_memory_context(self, uid: str, limit: int = 4) -> str:
        """Get check-in history formatted for LLM memory context."""
        checkins = self.get_checkin_history(uid, limit=limit)
        
        if not checkins:
            return "No previous weekly check-ins."
        
        context_parts = ["Recent weekly check-in summaries:"]
        for checkin in checkins:
            date_str = checkin.check_in_date.strftime("%b %d")
            context_parts.append(f"- {date_str}: {checkin.conversation_summary or 'No summary'}")
        
        return "\n".join(context_parts)
