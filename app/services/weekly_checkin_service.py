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
import io
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from pydantic import BaseModel

from fastapi import UploadFile
from openai import AsyncOpenAI

from app.core.database import (
    WeeklyCheckIn, WeeklyCheckInQuestion, UserProfile, 
    SymptomLog, ActionPlan, ActionPlanItem
)
from app.core.config import Settings
from app.langgraph.helpers.llm_client import call_llm_structured
from app.utils.timezone_utils import get_user_current_date

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# CONVERSATION EVALUATION HELPER (NON-BREAKING)
# ═══════════════════════════════════════════════════════════════════════════
def _evaluate_conversation_safely(
    thread_type: str,
    thread_id: str,
    uid: str,
    raw_messages: list,
    is_complete: bool,
    db: Session
) -> None:
    """
    Evaluate conversation quality without blocking or breaking main flow.
    
    This is a fire-and-forget helper that logs any errors but never raises.
    """
    try:
        from app.services.conversation_evaluation_service import get_conversation_evaluator
        evaluator = get_conversation_evaluator()
        result = evaluator.evaluate_thread_sync(
            thread_type=thread_type,
            thread_id=thread_id,
            uid=uid,
            raw_messages=raw_messages,
            is_complete=is_complete,
            db=db
        )
        if result:
            logger.info(f"📊 Conversation evaluated: quality={result.get('conversation_quality_score')}")
    except Exception as e:
        # Never let evaluation failure break the main flow
        logger.warning(f"Conversation evaluation skipped (non-critical): {e}")


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

    async def transcribe_audio(self, uid: str, file: UploadFile) -> str:
        """Transcribe an uploaded audio recording into text.

        This powers the mobile "Yap" flow:
        - client records audio (expo-av)
        - uploads here
        - we return transcript text so user can edit before sending
        
        Uses gpt-4o-transcribe for better accuracy with women's health context.
        """
        settings = Settings()
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured")

        content = await file.read()
        if not content:
            raise ValueError("Empty audio upload")

        # Basic safety limit (15MB) to protect the API.
        max_bytes = 15 * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError("Audio file too large")

        audio = io.BytesIO(content)
        audio.name = file.filename or "yap.m4a"

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Health context prompt for better transcription accuracy
        health_context = """Women's health app check-in conversation.
Common terms: cycle, period, hormone, progesterone, estrogen, testosterone,
cramps, bloating, mood, energy, sleep, cortisol, PCOS, endometriosis,
fatigue, headache, anxiety, stress, period pain, menstrual."""
        
        try:
            # Use whisper-1 for reliable transcription
            try:
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio,
                    language="en",
                    prompt=health_context,
                    response_format="verbose_json"
                )
            except TypeError as exc:
                # Keep compatibility with OpenAI-compatible clients that only
                # implement the model/file subset of the transcription API.
                if "unexpected keyword argument" not in str(exc):
                    raise
                audio.seek(0)
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio,
                )
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise ValueError(f"Transcription failed: {str(e)}")

        text = getattr(transcript, "text", None) or ""
        return text.strip()
    
    # ═══════════════════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _calculate_next_weekly_due_date(self, uid: str, current_date: date) -> date:
        """
        Calculate the next weekly check-in due date based on a fixed weekly schedule.
        
        Logic:
        1. First check-in: Set due date to same day next week
        2. Subsequent check-ins: Always align to the originally set weekly day
        3. If completed early: Next due date is still the original weekly day
        4. If completed late: Next due date is the next occurrence of the weekly day
        
        Args:
            uid: User ID
            current_date: Current date in user's timezone
            
        Returns:
            date: Next weekly check-in due date
        """
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        
        # Get the last completed check-in to determine the weekly schedule
        last_checkin = self.db.query(WeeklyCheckIn).filter(
            and_(
                WeeklyCheckIn.uid == uid,
                WeeklyCheckIn.is_complete == True
            )
        ).order_by(desc(WeeklyCheckIn.completed_at)).first()
        
        if last_checkin and last_checkin.check_in_date:
            # Use the original check-in day as the weekly anchor
            anchor_day = last_checkin.check_in_date
            anchor_weekday = anchor_day.weekday()  # 0=Monday, 6=Sunday
            
            # Calculate next occurrence of this weekday
            days_ahead = anchor_weekday - current_date.weekday()
            if days_ahead <= 0:  # Target day already happened this week or is today
                days_ahead += 7  # Move to next week
            
            next_due = current_date + timedelta(days=days_ahead)
            logger.info(f"Weekly schedule: anchor_day={anchor_day} (weekday={anchor_weekday}), "
                       f"current={current_date}, next_due={next_due}")
            return next_due
        else:
            # First check-in - set due date to same day next week
            next_due = current_date + timedelta(days=7)
            logger.info(f"First check-in: setting due date to {next_due} (7 days from {current_date})")
            return next_due
    
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
        If already completed this week, returns the completed check-in in read-only mode.
        
        Returns:
            (checkin: WeeklyCheckIn, question_or_completion: Dict)
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
        
        # Check if there's already a COMPLETED check-in for the current week
        # (i.e., the next due date hasn't arrived yet)
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        if profile and profile.weekly_checkin_due_date:
            # If today is before the next due date, the check-in is already done
            if user_today < profile.weekly_checkin_due_date:
                # Find the completed check-in
                completed = self.db.query(WeeklyCheckIn).filter(
                    and_(
                        WeeklyCheckIn.uid == uid,
                        WeeklyCheckIn.is_complete == True
                    )
                ).order_by(desc(WeeklyCheckIn.completed_at)).first()
                
                if completed:
                    logger.info(f"Returning already-completed check-in {completed.id} for user {uid}")
                    history = self._get_chat_history(completed)
                    return completed, {
                        "is_complete": True,
                        "is_already_completed": True,
                        "question_key": None,
                        "message": f"✅ Weekly check-in completed! Next check-in available on {profile.weekly_checkin_due_date.strftime('%A, %b %d')}.",
                        "messages": [f"✅ Weekly check-in completed!", f"Next check-in available on {profile.weekly_checkin_due_date.strftime('%A, %b %d')}."],
                        "history": history,
                        "next_due_date": profile.weekly_checkin_due_date.isoformat(),
                        "summary": completed.conversation_summary
                    }
        
        
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
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": first_question.message,
            "question_key": first_question.question_key,
            "timestamp": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
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
            created_at = msg.get("timestamp") or msg.get("created_at")
            history.append({
                "id": msg.get("id") or f"hist_{i}",
                "text": msg.get("content", ""),
                "isBot": is_bot,
                "created_at": created_at,
                "ui_blocks": msg.get("ui_blocks", []) if isinstance(msg.get("ui_blocks"), list) else [],
            })
        return history

    def _format_ai_question(self, ai_question, checkin: WeeklyCheckIn) -> Dict[str, Any]:
        """Format AI-generated question for API response."""
        from app.services.weekly_checkin_ai import AIQuestion
        
        history = self._get_chat_history(checkin)
        
        if ai_question is None:
            # Completion - get messages from raw_messages
            completion_messages = []
            if checkin.raw_messages:
                for msg in checkin.raw_messages:
                    if msg.get("question_key") == "completion" and msg.get("role") == "assistant":
                        completion_messages.append(msg.get("content", ""))
            
            if not completion_messages:
                completion_messages = ["Thanks for checking in! 💜", "I'll use this to personalize your plan."]
            
            return {
                "is_complete": True,
                "question_key": None,
                "message": " ".join(completion_messages),  # Combined for backward compatibility
                "messages": completion_messages,  # Array for multi-bubble display
                "history": history
            }
        
        # Get messages array if available, otherwise create from single message
        messages = getattr(ai_question, 'messages', None) or [ai_question.message]
        
        return {
            "is_complete": False,
            "question_key": ai_question.question_key,
            "question_type": ai_question.question_type.value if hasattr(ai_question.question_type, 'value') else ai_question.question_type,
            "message": ai_question.message,  # Combined for backward compatibility
            "messages": messages,  # Array for multi-bubble display
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
        
        normalized_response = response
        if question_key == "concern_severity":
            normalized_response = await self._normalize_concern_severity(response)

        # Store response based on question key
        self._store_response(checkin, question_key, normalized_response)
        
        # Add to raw messages log
        if message_text:
            messages = checkin.raw_messages or []
            messages.append({
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": message_text,
                "question_key": question_key,
                "timestamp": datetime.utcnow().isoformat(),
                "created_at": datetime.utcnow().isoformat(),
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
            previous_response=normalized_response,
            previous_question_key=question_key
        )
        
        if next_ai_question:
             # Store bot message
             messages = checkin.raw_messages or []
             messages.append({
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": next_ai_question.message,
                "question_key": next_ai_question.question_key,
                "timestamp": datetime.utcnow().isoformat(),
                "created_at": datetime.utcnow().isoformat(),
             })
             checkin.raw_messages = messages
             from sqlalchemy.orm.attributes import flag_modified
             flag_modified(checkin, "raw_messages")
        else:
            # AI returned None (completion) - it may have added a completion message
            # Flag raw_messages as modified so it gets saved
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
                val = int(response)
                checkin.concern_severity = max(1, min(9, val))
            except (ValueError, TypeError):
                checkin.concern_severity = 5
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

    async def _normalize_concern_severity(self, response: Any) -> int:
        """LLM-driven normalization for concern severity (1-9) without regex parsing."""
        if isinstance(response, int):
            return max(1, min(9, response))

        raw = str(response or "").strip()
        if not raw:
            return 5

        class SeverityExtraction(BaseModel):
            severity: Optional[int] = None

        try:
            parsed = await call_llm_structured(
                f"""Extract the user's severity score from 1 to 9.
User response: "{raw}"
Return strict JSON:
{{
  "severity": integer 1-9 or null
}}""",
                response_model=SeverityExtraction,
            )
            if parsed.severity is None:
                return 5
            return max(1, min(9, int(parsed.severity)))
        except Exception:
            return 5
    
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
        
        # Update user profile with next weekly due date
        profile = self.db.query(UserProfile).filter(
            UserProfile.uid == checkin.uid
        ).first()
        
        if profile:
            user_today = get_user_current_date(checkin.uid, self.db)
            
            # Calculate next due date using weekly schedule (same day next week)
            next_due_date = self._calculate_next_weekly_due_date(checkin.uid, user_today)
            profile.weekly_checkin_due_date = next_due_date
            profile.last_weekly_checkin_id = checkin.id
            
            logger.info(f"Weekly check-in completed: user={checkin.uid}, "
                       f"completed_today={user_today}, next_due={next_due_date} "
                       f"(will be due on {next_due_date.strftime('%A')} every week)")
        
        self.db.commit()
        self.db.refresh(checkin)
        
        logger.info(f"Completed check-in {checkin.id} for user {checkin.uid}")
        
        # ═══════════════════════════════════════════════════════════════════════════
        # EVALUATE CONVERSATION QUALITY (NON-BLOCKING)
        # ═══════════════════════════════════════════════════════════════════════════
        _evaluate_conversation_safely(
            thread_type="weekly_checkin",
            thread_id=str(checkin.id),
            uid=checkin.uid,
            raw_messages=checkin.raw_messages or [],
            is_complete=True,
            db=self.db
        )
        
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
