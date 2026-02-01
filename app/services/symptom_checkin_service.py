"""Symptom Check-in Service (daily threaded chat).

Creates/maintains one thread per user per local date.
Stores raw messages, rolling summary, and actionable insights.

Used by:
- mobile Symptom Check-in (Tap/Yap/Type)
- action plan generation/replacement personalization
- weekly check-in AI context enrichment
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import UploadFile
from openai import AsyncOpenAI
from sqlalchemy import and_, desc
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import ActionPlan, ActionPlanItem, CarePlanCheckInThread, SymptomCheckInThread, SymptomLog, UserProfile, UserResponse, WeeklyCheckIn
from app.services.symptom_checkin_ai import SymptomCheckInAI, SymptomAIResponse
from app.services.cycle_service import CycleService
from app.utils.timezone_utils import get_user_current_date
from app.utils.data_sanitization import sanitize_list_field, sanitize_string_field

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
    Only evaluates after every 6+ messages to avoid excessive computation.
    """
    try:
        # Skip if not enough messages to evaluate
        if len(raw_messages or []) < 6:
            return
            
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
            logger.debug(f"📊 Conversation evaluated: quality={result.get('conversation_quality_score')}")
    except Exception as e:
        # Never let evaluation failure break the main flow
        logger.debug(f"Conversation evaluation skipped: {e}")


class SymptomCheckInService:
    TAIL_SIZE = 20

    def __init__(self, db: Session):
        self.db = db
        self.ai = SymptomCheckInAI()

    async def transcribe_audio(self, uid: str, file: UploadFile) -> str:
        settings = Settings()
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured")

        content = await file.read()
        if not content:
            raise ValueError("Empty audio upload")

        max_bytes = 15 * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError("Audio file too large")

        audio = io.BytesIO(content)
        audio.name = file.filename or "yap.m4a"

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio,
        )

        text = getattr(transcript, "text", None) or ""
        return text.strip()

    def get_or_create_today_thread(self, uid: str) -> SymptomCheckInThread:
        user_today = get_user_current_date(uid, self.db)
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        tz = getattr(profile, "timezone", None) if profile else None
        user_name = getattr(profile, "name", None) if profile else None

        thread = (
            self.db.query(SymptomCheckInThread)
            .filter(and_(SymptomCheckInThread.uid == uid, SymptomCheckInThread.local_date == user_today))
            .first()
        )
        if thread:
            return thread

        thread = SymptomCheckInThread(uid=uid, local_date=user_today, timezone=tz)
        
        # Get yesterday's symptom for personalized opening
        yesterday_symptom = self._get_yesterday_symptom(uid)
        
        # Get current cycle phase for context
        cycle_phase = None
        try:
            cycle_service = CycleService(self.db)
            cycle_info = cycle_service.get_cycle_phase_info(uid)
            if cycle_info and cycle_info.phase:
                # Normalize phase name
                phase_lower = cycle_info.phase.lower()
                if "menses" in phase_lower or "menstrual" in phase_lower:
                    cycle_phase = "your period"
                elif "follicular" in phase_lower:
                    cycle_phase = "follicular phase"
                elif "ovul" in phase_lower:
                    cycle_phase = "ovulation"
                elif "luteal" in phase_lower:
                    cycle_phase = "luteal phase"
        except Exception as e:
            print(f"Error getting cycle phase for greeting: {e}")

        # Construct greeting
        greeting_text = f"Hey {user_name or 'there'}, how are you feeling today?"
        if yesterday_symptom:
            greeting_text = f"Hey {user_name or 'there'}, how is your {yesterday_symptom} feeling today?"
        elif cycle_phase:
            greeting_text = f"Hey {user_name or 'there'}, how are you feeling during {cycle_phase}?"

        opening = {
            "id": self._new_message_id(),
            "role": "bot",
            "content": greeting_text,
            "created_at": datetime.utcnow().isoformat(),
        }
        thread.raw_messages = [opening]

        self.db.add(thread)
        self.db.commit()
        self.db.refresh(thread)
        return thread

    def create_new_thread(self, uid: str) -> SymptomCheckInThread:
        """Always create a new thread for symptoms (ChatGPT-like)."""
        user_today = get_user_current_date(uid, self.db)
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        tz = getattr(profile, "timezone", None) if profile else None
        user_name = getattr(profile, "name", None) if profile else None
        
        thread = SymptomCheckInThread(uid=uid, local_date=user_today, timezone=tz)
        
        # Get yesterday's symptom for personalized opening
        yesterday_symptom = self._get_yesterday_symptom(uid)
        
        # Get current cycle phase for context
        cycle_phase = None
        try:
            cycle_service = CycleService(self.db)
            cycle_info = cycle_service.get_cycle_phase_info(uid)
            if cycle_info and cycle_info.phase:
                phase_lower = cycle_info.phase.lower()
                if "menses" in phase_lower or "menstrual" in phase_lower:
                    cycle_phase = "your period"
                elif "follicular" in phase_lower:
                    cycle_phase = "follicular phase"
                elif "ovul" in phase_lower:
                    cycle_phase = "ovulation"
                elif "luteal" in phase_lower:
                    cycle_phase = "luteal phase"
        except Exception as e:
            print(f"Error getting cycle phase for greeting: {e}")

        # Construct greeting
        greeting_text = f"Hey {user_name or 'there'}, how are you feeling today?"
        if yesterday_symptom:
            greeting_text = f"Hey {user_name or 'there'}, how is your {yesterday_symptom} feeling today?"
        elif cycle_phase:
            greeting_text = f"Hey {user_name or 'there'}, how are you feeling during {cycle_phase}?"

        opening = {
            "id": self._new_message_id(),
            "role": "bot",
            "content": greeting_text,
            "created_at": datetime.utcnow().isoformat(),
        }
        thread.raw_messages = [opening]

        self.db.add(thread)
        self.db.commit()
        self.db.refresh(thread)
        return thread

    
    def _get_yesterday_symptom(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get the most recent symptom log from yesterday or recent days."""
        try:
            user_today = get_user_current_date(uid, self.db)
            yesterday = user_today - timedelta(days=1)
            
            # Look for logs from yesterday or up to 3 days ago
            start_date = user_today - timedelta(days=3)
            
            log = (
                self.db.query(SymptomLog)
                .filter(
                    and_(
                        SymptomLog.user_id == uid,
                        SymptomLog.logged_date >= start_date,
                        SymptomLog.logged_date < user_today,
                    )
                )
                .order_by(desc(SymptomLog.logged_at))
                .first()
            )
            
            if log:
                return {
                    "symptom_type": log.symptom_type,
                    "severity": log.severity,
                    "logged_date": log.logged_date.isoformat() if log.logged_date else None,
                }
            return None
        except Exception as e:
            logger.warning(f"[SymptomCheckInService] Error getting yesterday symptom: {e}")
            return None

    def get_thread_by_id(self, uid: str, thread_id: str) -> SymptomCheckInThread:
        thread = (
            self.db.query(SymptomCheckInThread)
            .filter(and_(SymptomCheckInThread.id == thread_id, SymptomCheckInThread.uid == uid))
            .first()
        )
        if not thread:
            raise ValueError("Symptom check-in thread not found")
        return thread

    def format_history_for_mobile(self, thread: SymptomCheckInThread) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for msg in (thread.raw_messages or []):
            role = msg.get("role")
            out.append(
                {
                    "id": msg.get("id") or self._new_message_id(),
                    "text": msg.get("content") or "",
                    "isBot": role != "user",
                }
            )
        return out

    async def respond(self, uid: str, thread_id: str, message_text: str) -> Tuple[SymptomCheckInThread, SymptomAIResponse]:
        thread = self.get_thread_by_id(uid, thread_id)

        # Append user message
        raw = list(thread.raw_messages or [])
        raw.append(
            {
                "id": self._new_message_id(),
                "role": "user",
                "content": message_text,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        thread.raw_messages = raw
        self.db.add(thread)
        self.db.commit()
        self.db.refresh(thread)

        await self._update_rolling_summary_if_needed(thread)

        # If the user explicitly provides a symptom + severity (e.g., "log cramps 7/9"),
        # write a structured SymptomLog entry so it affects action plans/replacements.
        logged_note: Optional[str] = None
        try:
            parsed = self._parse_symptom_log_from_text(message_text)
            if parsed:
                created = self.create_symptom_log(
                    uid=uid,
                    symptom_type=parsed["symptom_type"],
                    severity=parsed["severity"],
                    notes=parsed.get("notes"),
                    factors=parsed.get("factors"),
                    logged_via="symptom_checkin_chat",
                )
                logged_note = f"Logged: {created.get('symptom_type')}={created.get('severity')}/9"
        except Exception:
            logged_note = None

        user_profile_context = self._build_user_profile_context(uid)
        action_plan_context = self._build_todays_action_plan_context(uid)
        recent_care_plan_checkin_context = self._build_recent_care_plan_checkin_context(uid)
        recent_weekly_checkin_context = self._build_recent_weekly_checkin_context(uid)
        recent_symptom_logs_context = self._build_recent_symptom_logs_context(uid)
        historical_memory_context = self._build_historical_memory_context(uid)  # NEW: Past triggers/relief factors
        recent_messages = list(thread.raw_messages or [])[-self.TAIL_SIZE :]

        ai_response, _model_used = await self.ai.generate_reply(
            user_message=message_text,
            user_profile_context=user_profile_context,
            action_plan_context=action_plan_context,
            recent_care_plan_checkin_context=recent_care_plan_checkin_context,
            recent_weekly_checkin_context=recent_weekly_checkin_context,
            recent_symptom_logs_context=recent_symptom_logs_context,
            historical_memory_context=historical_memory_context,  # NEW: Historical symptom patterns
            rolling_summary=thread.rolling_summary,
            recent_messages=recent_messages,
        )

        # Append bot messages
        raw = list(thread.raw_messages or [])

        # If we auto-logged a symptom entry, acknowledge it first.
        if logged_note:
            raw.append(
                {
                    "id": self._new_message_id(),
                    "role": "bot",
                    "content": logged_note,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

        for text in ai_response.messages:
            raw.append(
                {
                    "id": self._new_message_id(),
                    "role": "bot",
                    "content": text,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
        thread.raw_messages = raw

        # Merge insights
        if ai_response.insights:
            existing = dict(thread.actionable_insights or {})
            existing.update(ai_response.insights.model_dump())
            existing["updated_at"] = datetime.utcnow().isoformat()
            thread.actionable_insights = existing

        self.db.add(thread)
        self.db.commit()
        self.db.refresh(thread)

        # ═══════════════════════════════════════════════════════════════════════════
        # EVALUATE CONVERSATION QUALITY (NON-BLOCKING, SAMPLED)
        # ═══════════════════════════════════════════════════════════════════════════
        # Only evaluate periodically (every 6+ messages) to avoid excessive processing
        if len(raw) >= 6 and len(raw) % 4 == 0:  # Every 4 message pairs after initial 6
            _evaluate_conversation_safely(
                thread_type="symptom_checkin",
                thread_id=str(thread.id),
                uid=uid,
                raw_messages=raw,
                is_complete=False,  # Symptom threads are ongoing
                db=self.db
            )

        return thread, ai_response

    def _parse_symptom_log_from_text(self, text: str) -> Optional[Dict[str, Any]]:
        """Best-effort parsing for messages like:
        - "log cramps 7/9"
        - "track headache 6"
        - "cramps 8/9 today" (only if 'log'/'track' present)

        We keep this intentionally conservative to avoid accidental logging.
        """

        raw = (text or "").strip()
        if not raw:
            return None

        lowered = raw.lower()
        if not (lowered.startswith("log ") or lowered.startswith("track ") or lowered.startswith("add symptom")):
            return None

        # Extract severity: 1-9 with optional /9
        m = re.search(r"\b([1-9])\s*(?:/\s*9)?\b", lowered)
        if not m:
            return None
        severity = int(m.group(1))

        # Try to extract symptom type: take words after the leading command up to the severity
        # Example: "log cramps 7/9" -> "cramps"
        before_sev = lowered[: m.start()].strip()
        before_sev = re.sub(r"^(log|track|add symptom)\s+", "", before_sev).strip()
        symptom_type = (before_sev or "").strip(" ,.-")

        # Small normalization for common phrasing
        synonym_map = {
            "period cramps": "cramps",
            "menstrual cramps": "cramps",
            "head ache": "headache",
            "migraine": "headache",
            "tired": "fatigue",
            "low energy": "fatigue",
        }
        symptom_type = synonym_map.get(symptom_type, symptom_type)

        if not symptom_type:
            return None

        return {
            "symptom_type": symptom_type,
            "severity": severity,
            "notes": raw,
            "factors": [],
        }

    def create_symptom_log(
        self,
        *,
        uid: str,
        symptom_type: str,
        severity: int,
        notes: Optional[str] = None,
        factors: Optional[List[str]] = None,
        logged_via: str = "symptom_checkin_ui",
    ) -> Dict[str, Any]:
        symptom_type = (symptom_type or "").strip().lower()
        if not symptom_type:
            raise ValueError("symptom_type is required")
        if not isinstance(severity, int) or severity < 1 or severity > 9:
            raise ValueError("severity must be an integer 1-9")

        factors_list = [str(x).strip() for x in (factors or []) if str(x).strip()]

        # Use user's current local date for logged_date (timezone-aware)
        user_today = get_user_current_date(uid, self.db)

        log = SymptomLog(
            user_id=uid,
            symptom_type=symptom_type,
            severity=severity,
            notes=(notes or None),
            factors=factors_list,
            logged_via=logged_via,
            logged_at=datetime.utcnow(),
            logged_date=user_today,
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)

        return {
            "id": log.id,
            "symptom_type": log.symptom_type,
            "severity": log.severity,
            "notes": log.notes,
            "factors": log.factors or [],
            "logged_at": log.logged_at.isoformat() if getattr(log, "logged_at", None) else "",
        }

    def get_symptom_overview(self, *, uid: str, period_days: int = 14) -> Dict[str, Any]:
        if period_days < 3 or period_days > 60:
            raise ValueError("period_days must be between 3 and 60")

        end_date = get_user_current_date(uid, self.db)
        start_date = end_date - timedelta(days=period_days - 1)

        logs: List[SymptomLog] = (
            self.db.query(SymptomLog)
            .filter(
                and_(
                    SymptomLog.user_id == uid,
                    SymptomLog.logged_date >= start_date,
                    SymptomLog.logged_date <= end_date,
                )
            )
            .order_by(desc(SymptomLog.logged_at))
            .limit(500)
            .all()
        )

        items: List[Dict[str, Any]] = []
        by_type: Dict[str, List[SymptomLog]] = {}

        for log in logs:
            items.append(
                {
                    "symptom_type": log.symptom_type,
                    "severity": int(log.severity),
                    "logged_at": log.logged_at.isoformat() if getattr(log, "logged_at", None) else "",
                    "notes": log.notes,
                    "factors": log.factors or [],
                }
            )
            by_type.setdefault(log.symptom_type, []).append(log)

        aggregates: List[Dict[str, Any]] = []
        for stype, st_logs in by_type.items():
            # logs are in descending time order currently
            severities = [int(x.severity) for x in st_logs if x.severity is not None]
            if not severities:
                continue

            # Trend: compare last 3 entries avg vs previous 3 entries avg
            last = severities[:3]
            prev = severities[3:6]
            trend = "unknown"
            if last and prev:
                last_avg = sum(last) / len(last)
                prev_avg = sum(prev) / len(prev)
                if last_avg <= prev_avg - 0.75:
                    trend = "improving"
                elif last_avg >= prev_avg + 0.75:
                    trend = "worsening"
                else:
                    trend = "stable"

            aggregates.append(
                {
                    "symptom_type": stype,
                    "count": len(severities),
                    "avg_severity": round(sum(severities) / len(severities), 2),
                    "last_severity": severities[0] if severities else None,
                    "trend": trend,
                }
            )

        # pick top symptoms by count
        aggregates_sorted = sorted(aggregates, key=lambda a: (-a.get("count", 0), -a.get("avg_severity", 0)))
        top_symptoms = [a["symptom_type"] for a in aggregates_sorted[:3]]

        return {
            "period_days": period_days,
            "logs": items,
            "aggregates": aggregates_sorted,
            "top_symptoms": top_symptoms,
        }

    def _build_todays_action_plan_context(self, uid: str) -> str:
        """Build COMPREHENSIVE action plan context with scientific reasoning.
        
        This gives the chatbot FULL knowledge of:
        - What actions are in the plan
        - WHY each action was chosen (purpose)
        - Which conditions each action targets
        - Scientific backing (research studies)
        """
        user_today = get_user_current_date(uid, self.db)

        plan = (
            self.db.query(ActionPlan)
            .filter(and_(ActionPlan.uid == uid, ActionPlan.plan_date == user_today))
            .order_by(desc(ActionPlan.created_at))
            .first()
        )

        if not plan:
            plan = (
                self.db.query(ActionPlan)
                .filter(ActionPlan.uid == uid)
                .order_by(desc(ActionPlan.plan_date), desc(ActionPlan.created_at))
                .first()
            )

        if not plan:
            return "No action plan found."

        items = self.db.query(ActionPlanItem).filter(
            ActionPlanItem.plan_id == plan.id,
            ActionPlanItem.is_replaced != True  # noqa: E712
        ).all()
        if not items:
            return "Action plan exists, but has no items."

        lines = [
            f"═══════════════════════════════════════════════════════════════",
            f"TODAY'S ACTION PLAN (Date: {plan.plan_date})",
            f"═══════════════════════════════════════════════════════════════",
        ]
        
        for idx, it in enumerate(items[:6], 1):
            title = (it.title or "").strip()
            if not title:
                continue
            
            category = getattr(it, "category", "general") or "general"
            target_hormone = getattr(it, "target_hormone", None) or "hormones"
            purpose = getattr(it, "purpose", None) or ""
            conditions = getattr(it, "conditions", None) or []
            
            lines.append(f"")
            lines.append(f"ACTION {idx}: {title.upper()} ({category})")
            lines.append(f"  🎯 Target: {target_hormone}")
            if conditions:
                lines.append(f"  🩺 For: {', '.join(conditions)}")
            if purpose:
                # Truncate purpose to save tokens
                purpose_short = purpose.strip()[:200]
                lines.append(f"  📖 Why: {purpose_short}{'...' if len(purpose) > 200 else ''}")
        
        return "\n".join(lines)

    def _build_recent_care_plan_checkin_context(self, uid: str, limit: int = 2) -> str:
        try:
            threads = (
                self.db.query(CarePlanCheckInThread)
                .filter(CarePlanCheckInThread.uid == uid)
                .order_by(desc(CarePlanCheckInThread.local_date), desc(CarePlanCheckInThread.updated_at))
                .limit(max(1, min(limit, 5)))
                .all()
            )
            if not threads:
                return "None"

            lines: List[str] = []
            for t in threads:
                day = t.local_date.isoformat() if getattr(t, "local_date", None) else ""
                ai = t.actionable_insights or {}
                parts: List[str] = []
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

            return "\n".join(lines) if lines else "None"
        except Exception:
            return "None"

    def _build_recent_weekly_checkin_context(self, uid: str) -> str:
        """Build context including cycle phase info."""
        try:
            # Get current cycle phase
            cycle_service = CycleService(self.db)
            cycle_info = cycle_service.get_cycle_phase_info(uid)
            
            cycle_context = ""
            if cycle_info:
                phase = cycle_info.phase or "unknown"
                cycle_day = cycle_info.cycle_day
                cycle_context = f"CURRENT_CYCLE_PHASE={phase} (day {cycle_day})\n"
            
            # Get recent weekly checkin
            checkin = (
                self.db.query(WeeklyCheckIn)
                .filter(and_(WeeklyCheckIn.uid == uid, WeeklyCheckIn.is_complete == True))
                .order_by(desc(WeeklyCheckIn.completed_at))
                .first()
            )
            if not checkin:
                return cycle_context + "No recent weekly check-in."

            summary = (checkin.conversation_summary or "").strip()
            if summary:
                return cycle_context + summary

            # Fallback: minimal signals
            parts: List[str] = []
            if getattr(checkin, "top_concern", None):
                parts.append(f"concern={checkin.top_concern}")
            if getattr(checkin, "concern_severity", None):
                parts.append(f"severity={checkin.concern_severity}")
            if getattr(checkin, "factors_negative", None):
                parts.append("triggers=" + ", ".join((checkin.factors_negative or [])[:3]))
            if getattr(checkin, "factors_positive", None):
                parts.append("helped=" + ", ".join((checkin.factors_positive or [])[:3]))
            return cycle_context + (" | ".join(parts) if parts else "None")
        except Exception as e:
            logger.warning(f"[SymptomCheckInService] Error building weekly context: {e}")
            return "None"

    async def _update_rolling_summary_if_needed(self, thread: SymptomCheckInThread) -> None:
        raw = list(thread.raw_messages or [])
        if len(raw) <= self.TAIL_SIZE + 10:
            return

        summarize_upto = len(raw) - self.TAIL_SIZE
        already = int(thread.summarized_message_count or 0)
        if summarize_upto <= already:
            return

        chunk = raw[already:summarize_upto]
        if not chunk:
            return

        chunk_for_prompt = chunk[-40:]

        from app.services.ai_service import AIService

        existing_summary = (thread.rolling_summary or "").strip()
        prompt = f"""
Update the RUNNING SUMMARY for a user's daily symptom check-in by incorporating NEW MESSAGES.
- Keep it concise (max ~1200 characters).
- Capture: progress (better/same/worse), wins, difficulties, triggers, relief.
- Do not add medical claims.

Return plain text only.

EXISTING SUMMARY:
{existing_summary}

NEW MESSAGES (JSON list in order):
{chunk_for_prompt}
""".strip()

        summary_text, _ = await AIService.call_ai_model(prompt, with_fallback=True)
        summary_text = (summary_text or "").strip()

        if summary_text:
            thread.rolling_summary = summary_text
            thread.summarized_message_count = summarize_upto
            thread.last_summarized_at = datetime.utcnow()
            self.db.add(thread)
            self.db.commit()
            self.db.refresh(thread)

    def _build_user_profile_context(self, uid: str) -> str:
        """Build comprehensive user profile context with ALL relevant data."""
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_response = self.db.query(UserResponse).filter(
            UserResponse.uid == uid
        ).order_by(desc(UserResponse.created_at)).first()
        
        if not profile and not user_response:
            return ""

        lines = []
        
        # Core identity
        name = getattr(profile, "name", None) if profile else None
        if name:
            lines.append(f"name={name}")
        
        if user_response:
            # Health conditions (CRITICAL for personalization)
            # SANITIZE: Remove UI placeholders like "None of the above"
            if user_response.diagnosed_conditions:
                sanitized_conditions = sanitize_list_field(
                    user_response.diagnosed_conditions if isinstance(user_response.diagnosed_conditions, list)
                    else [user_response.diagnosed_conditions],
                    "diagnosed_conditions"
                )
                if sanitized_conditions:
                    lines.append(f"diagnosed_conditions={sanitized_conditions}")
            if user_response.top_concern:
                sanitized_concern = sanitize_string_field(user_response.top_concern, "top_concern")
                if sanitized_concern:
                    lines.append(f"top_concern={sanitized_concern}")
            if user_response.primary_hormone:
                lines.append(f"primary_hormone={user_response.primary_hormone}")
            
            # Concerns
            if user_response.period_concerns:
                lines.append(f"period_concerns={user_response.period_concerns}")
            if user_response.body_concerns:
                lines.append(f"body_concerns={user_response.body_concerns}")
            
            # Lifestyle
            if user_response.stress_level:
                lines.append(f"stress_level={user_response.stress_level}")
            if user_response.workout_intensity:
                lines.append(f"workout_intensity={user_response.workout_intensity}")
        
        # Chatbot memory preferences
        if profile:
            mem = profile.chatbot_memory or {}
            if mem.get("diet_preference"):
                lines.append(f"diet_preference={mem.get('diet_preference')}")
            if mem.get("food_allergies"):
                lines.append(f"food_allergies={mem.get('food_allergies')}")
            if mem.get("foods_liked"):
                lines.append(f"foods_liked={mem.get('foods_liked')}")
            if mem.get("foods_disliked"):
                lines.append(f"foods_disliked={mem.get('foods_disliked')}")

        return "\n".join(lines)
    
    def _build_historical_memory_context(self, uid: str) -> str:
        """Build context from past conversations: triggers, relief factors, symptom patterns."""
        lines = []
        user_today = get_user_current_date(uid, self.db)
        
        try:
            # Get past symptom check-in insights (last 7 days)
            past_symptom_threads = self.db.query(SymptomCheckInThread).filter(
                and_(
                    SymptomCheckInThread.uid == uid,
                    SymptomCheckInThread.local_date < user_today,
                    SymptomCheckInThread.local_date >= user_today - timedelta(days=7)
                )
            ).order_by(desc(SymptomCheckInThread.local_date)).limit(5).all()
            
            all_symptoms_mentioned = []
            all_progress_notes = []
            
            for thread in past_symptom_threads:
                insights = thread.actionable_insights or {}
                symptoms = insights.get("symptoms_mentioned", [])
                progress = insights.get("progress", "")
                
                if symptoms:
                    all_symptoms_mentioned.extend(symptoms[:3])
                if progress:
                    all_progress_notes.append(f"[{thread.local_date}]: {progress[:100]}")
            
            if all_symptoms_mentioned:
                lines.append(f"RECENT SYMPTOMS MENTIONED: {list(set(all_symptoms_mentioned))[:8]}")
            if all_progress_notes:
                lines.append(f"SYMPTOM PROGRESS NOTES: {all_progress_notes[:3]}")
            
            # Get recent weekly check-in insights for triggers/relief factors
            recent_weekly = self.db.query(WeeklyCheckIn).filter(
                and_(
                    WeeklyCheckIn.uid == uid,
                    WeeklyCheckIn.is_complete == True
                )
            ).order_by(desc(WeeklyCheckIn.completed_at)).first()
            
            if recent_weekly:
                factors_pos = recent_weekly.factors_positive or []
                factors_neg = recent_weekly.factors_negative or []
                insights = recent_weekly.actionable_insights or {}
                
                if factors_pos:
                    lines.append(f"WHAT HELPED SYMPTOMS: {factors_pos[:5]}")
                if factors_neg:
                    lines.append(f"WHAT MADE SYMPTOMS WORSE: {factors_neg[:5]}")
                
                triggers = insights.get("triggers_identified", [])
                relief = insights.get("relief_factors_identified", [])
                if triggers:
                    lines.append(f"KNOWN TRIGGERS: {triggers[:5]}")
                if relief:
                    lines.append(f"KNOWN RELIEF FACTORS: {relief[:5]}")
            
            # Get care plan wins/blockers for context
            recent_care_thread = self.db.query(CarePlanCheckInThread).filter(
                and_(
                    CarePlanCheckInThread.uid == uid,
                    CarePlanCheckInThread.local_date < user_today
                )
            ).order_by(desc(CarePlanCheckInThread.local_date)).first()
            
            if recent_care_thread:
                care_insights = recent_care_thread.actionable_insights or {}
                wins = care_insights.get("wins", [])
                blockers = care_insights.get("blockers", [])
                if wins:
                    lines.append(f"RECENT WINS FROM CARE PLAN: {wins[:3]}")
                if blockers:
                    lines.append(f"RECENT BLOCKERS: {blockers[:3]}")
            
        except Exception as e:
            logger.warning(f"[SymptomService] Error building historical memory: {e}")
        
        return "\n".join(lines) if lines else "No historical symptom data yet"

    def _build_recent_symptom_logs_context(self, uid: str) -> str:
        try:
            logs = (
                self.db.query(SymptomLog)
                .filter(SymptomLog.user_id == uid)
                .order_by(desc(SymptomLog.logged_at))
                .limit(5)
                .all()
            )
        except Exception:
            logs = []

        if not logs:
            return "No recent symptom logs."

        lines = []
        for log in logs:
            ts = log.logged_at.isoformat() if getattr(log, "logged_at", None) else ""
            lines.append(f"- {log.symptom_type} severity {log.severity}/9 {ts}")
        return "\n".join(lines)

    @staticmethod
    def _new_message_id() -> str:
        import uuid

        return str(uuid.uuid4())
