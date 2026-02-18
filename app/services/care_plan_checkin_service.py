"""Care Plan Check-in Service (daily threaded chat).

Creates/maintains one chat thread per user per local date. Stores:
- raw_messages (full message list)
- rolling_summary (sliding-window summary for older messages)
- actionable_insights (signals for plan generation/replacement)

Mobile contract:
- start: returns thread_id + history so the UI can restore immediately
- respond: appends user msg, generates bot reply (multi-bubble), returns history
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import UploadFile
from openai import AsyncOpenAI
from sqlalchemy import and_, desc
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import ActionPlan, ActionPlanItem, CarePlanCheckInThread, SymptomCheckInThread, SymptomLog, UserProfile, UserResponse, WeeklyCheckIn
from app.services.care_plan_checkin_ai import CarePlanCheckInAI, CarePlanAIResponse
from app.utils.timezone_utils import get_user_current_date
from app.utils.data_sanitization import sanitize_list_field, sanitize_string_field

logger = logging.getLogger(__name__)


class CarePlanCheckInService:
    TAIL_SIZE = 20

    def __init__(self, db: Session):
        self.db = db
        self.ai = CarePlanCheckInAI()

    # ──────────────────────────────────────────────────────────────────────────
    # Yap transcription (reuse weekly behavior)
    # ──────────────────────────────────────────────────────────────────────────

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

    # ──────────────────────────────────────────────────────────────────────────
    # Thread lifecycle
    # ──────────────────────────────────────────────────────────────────────────
    
    def _generate_personalized_opening(self, uid: str) -> str:
        """Generate a personalized opening message based on user's context.
        
        Instead of generic 'how are your actions going?', we create something like:
        'Hey Sarah! Since you're in luteal phase, I've got some calming activities 
        for your PCOS today. How are you feeling? 💜'
        """
        try:
            # Get user profile
            profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
            user_response = self.db.query(UserResponse).filter(
                UserResponse.uid == uid
            ).order_by(desc(UserResponse.created_at)).first()
            
            # Extract key info
            name = "there"
            if profile and profile.name:
                name = profile.name.split()[0]  # First name only
            
            conditions = []
            top_concern = None
            if user_response:
                raw_conditions = user_response.diagnosed_conditions or []
                if not isinstance(raw_conditions, list):
                    raw_conditions = [raw_conditions]
                conditions = sanitize_list_field(raw_conditions, "diagnosed_conditions")
                top_concern = sanitize_string_field(user_response.top_concern, "top_concern")
            
            # Get cycle phase from recent weekly check-in
            cycle_phase = None
            try:
                recent_weekly = self.db.query(WeeklyCheckIn).filter(
                    WeeklyCheckIn.uid == uid
                ).order_by(desc(WeeklyCheckIn.completed_at)).first()
                if recent_weekly:
                    insights = recent_weekly.actionable_insights or {}
                    cycle_phase = insights.get("cycle_phase")
            except:
                pass
            
            # Get today's action plan items
            user_today = get_user_current_date(uid, self.db)
            plan = self.db.query(ActionPlan).filter(
                and_(ActionPlan.uid == uid, ActionPlan.plan_date == user_today)
            ).order_by(desc(ActionPlan.created_at)).first()
            
            action_titles = []
            if plan:
                items = self.db.query(ActionPlanItem).filter(
                    ActionPlanItem.plan_id == plan.id,
                    ActionPlanItem.is_replaced != True
                ).limit(4).all()
                action_titles = [it.title for it in items if it.title]
            
            # Build personalized opening
            opening_parts = [f"Hey {name}! 💜"]
            
            # Add cycle phase context if available
            if cycle_phase:
                phase_contexts = {
                    "menstrual": "I know your period can be tough",
                    "follicular": "Your energy is building this week",
                    "ovulation": "You're at peak energy right now",
                    "luteal": "Luteal phase can bring some challenges"
                }
                if cycle_phase.lower() in phase_contexts:
                    opening_parts.append(f"{phase_contexts[cycle_phase.lower()]}.")
            
            # Add condition acknowledgment
            if conditions and len(conditions) > 0:
                main_condition = conditions[0] if isinstance(conditions, list) else conditions
                opening_parts.append(f"I've tailored today's plan specifically for your {main_condition}.")
            elif top_concern:
                opening_parts.append(f"Today's plan focuses on {top_concern}.")
            
            # Add action preview
            if action_titles:
                if len(action_titles) >= 2:
                    preview = f"{action_titles[0]} and {action_titles[1]}"
                else:
                    preview = action_titles[0]
                opening_parts.append(f"You've got {preview} on your list.")
            
            # Add warm closing question
            opening_parts.append("How are you feeling today?")
            
            return " ".join(opening_parts)
            
        except Exception as e:
            logger.warning(f"[CarePlanService] Error generating personalized opening: {e}")
            return "Hey! 💜 How are today's actions going for you?"

    def get_or_create_today_thread(self, uid: str) -> CarePlanCheckInThread:
        user_today = get_user_current_date(uid, self.db)
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        tz = getattr(profile, "timezone", None) if profile else None

        thread = (
            self.db.query(CarePlanCheckInThread)
            .filter(and_(CarePlanCheckInThread.uid == uid, CarePlanCheckInThread.local_date == user_today))
            .first()
        )

        if thread:
            return thread

        thread = CarePlanCheckInThread(uid=uid, local_date=user_today, timezone=tz)
        # Seed a PERSONALIZED opening message
        opening = {
            "id": self._new_message_id(),
            "role": "bot",
            "content": self._generate_personalized_opening(uid),
            "created_at": datetime.utcnow().isoformat(),
        }
        thread.raw_messages = [opening]

        self.db.add(thread)
        self.db.commit()
        self.db.refresh(thread)
        return thread
    
    def create_new_thread(self, uid: str) -> CarePlanCheckInThread:
        """Always create a new thread (ChatGPT-like behavior)."""
        user_today = get_user_current_date(uid, self.db)
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        tz = getattr(profile, "timezone", None) if profile else None

        thread = CarePlanCheckInThread(uid=uid, local_date=user_today, timezone=tz)
        # Seed a PERSONALIZED opening message
        opening = {
            "id": self._new_message_id(),
            "role": "bot",
            "content": self._generate_personalized_opening(uid),
            "created_at": datetime.utcnow().isoformat(),
        }
        thread.raw_messages = [opening]

        self.db.add(thread)
        self.db.commit()
        self.db.refresh(thread)
        return thread

    def get_thread_by_id(self, uid: str, thread_id: str) -> CarePlanCheckInThread:
        thread = (
            self.db.query(CarePlanCheckInThread)
            .filter(and_(CarePlanCheckInThread.id == thread_id, CarePlanCheckInThread.uid == uid))
            .first()
        )
        if not thread:
            raise ValueError("Care plan check-in thread not found")
        return thread

    def format_history_for_mobile(self, thread: CarePlanCheckInThread) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for msg in (thread.raw_messages or []):
            role = msg.get("role")
            formatted: Dict[str, Any] = {
                "id": msg.get("id") or self._new_message_id(),
                "text": msg.get("content") or "",
                "isBot": role != "user",
                "created_at": msg.get("created_at") or datetime.utcnow().isoformat(),
            }
            # Preserve per-message UI blocks so historical CTAs can render in transcript.
            ui_blocks = msg.get("ui_blocks")
            if isinstance(ui_blocks, list) and ui_blocks:
                formatted["ui_blocks"] = ui_blocks
            out.append(formatted)
        return out

    # ──────────────────────────────────────────────────────────────────────────
    # Chat + summarization
    # ──────────────────────────────────────────────────────────────────────────

    async def respond(self, uid: str, thread_id: str, message_text: str) -> Tuple[CarePlanCheckInThread, CarePlanAIResponse]:
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

        # Update rolling summary if the thread is getting long.
        await self._update_rolling_summary_if_needed(uid, thread)

        # Build context
        user_profile_context = self._build_user_profile_context(uid)
        action_plan_context = self._build_todays_action_plan_context(uid)
        recent_symptom_checkin_context = self._build_recent_symptom_checkin_context(uid)
        recent_symptom_logs_context = self._build_recent_symptom_logs_context(uid)
        historical_memory_context = self._build_historical_memory_context(uid)  # NEW: Past wins/blockers
        recent_messages = list(thread.raw_messages or [])[-self.TAIL_SIZE :]

        ai_response, _model_used = await self.ai.generate_reply(
            user_message=message_text,
            user_profile_context=user_profile_context,
            action_plan_context=action_plan_context,
            recent_symptom_checkin_context=recent_symptom_checkin_context,
            recent_symptom_logs_context=recent_symptom_logs_context,
            historical_memory_context=historical_memory_context,  # NEW: Historical memory
            rolling_summary=thread.rolling_summary,
            recent_messages=recent_messages,
        )

        # Append bot messages (multi-bubble)
        raw = list(thread.raw_messages or [])
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

        return thread, ai_response

    async def _update_rolling_summary_if_needed(self, uid: str, thread: CarePlanCheckInThread) -> None:
        """Sliding-window summarization: summarize older messages into rolling_summary."""
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

        # Keep prompt small
        chunk_for_prompt = chunk[-40:]

        from app.services.ai_service import AIService

        existing_summary = (thread.rolling_summary or "").strip()
        prompt = f"""
You are summarizing a user's daily care plan check-in chat.

Update the RUNNING SUMMARY by incorporating the NEW MESSAGES.
- Keep it concise (max ~1200 characters).
- Capture: adherence, what worked, blockers, requested changes, skips, alternates.
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

    # ──────────────────────────────────────────────────────────────────────────
    # Context helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _build_recent_symptom_checkin_context(self, uid: str, limit: int = 3) -> str:
        """Compact summary of recent daily symptom check-in threads."""
        try:
            threads = (
                self.db.query(SymptomCheckInThread)
                .filter(SymptomCheckInThread.uid == uid)
                .order_by(desc(SymptomCheckInThread.local_date), desc(SymptomCheckInThread.updated_at))
                .limit(max(1, min(limit, 7)))
                .all()
            )

            if not threads:
                return "None"

            lines: List[str] = []
            for t in threads:
                day = t.local_date.isoformat() if getattr(t, "local_date", None) else ""
                ai = t.actionable_insights or {}
                parts: List[str] = []
                if ai.get("progress"):
                    parts.append(f"progress={ai['progress']}")
                if ai.get("severity_rating"):
                    parts.append(f"severity={ai['severity_rating']}/9")
                if ai.get("symptoms_mentioned"):
                    parts.append("symptoms=" + ", ".join(ai["symptoms_mentioned"][:3]))
                if ai.get("triggers_identified"):
                    parts.append("triggers=" + ", ".join(ai["triggers_identified"][:3]))
                if ai.get("relief_factors_identified"):
                    parts.append("helped=" + ", ".join(ai["relief_factors_identified"][:3]))

                summary = (t.rolling_summary or "").strip()
                if summary:
                    parts.append(f"summary={summary}")

                if parts:
                    lines.append(f"[{day}] " + " | ".join(parts))

            return "\n".join(lines) if lines else "None"
        except Exception:
            return "None"

    def _build_recent_symptom_logs_context(self, uid: str, days: int = 14) -> str:
        """Structured symptom severity logs over the last N days (compact)."""
        try:
            if days < 3:
                days = 3
            if days > 30:
                days = 30

            end_date = get_user_current_date(uid, self.db)
            start_date = end_date - timedelta(days=days - 1)

            logs = (
                self.db.query(SymptomLog)
                .filter(
                    and_(
                        SymptomLog.user_id == uid,
                        SymptomLog.logged_date >= start_date,
                        SymptomLog.logged_date <= end_date,
                    )
                )
                .order_by(desc(SymptomLog.logged_at))
                .limit(50)
                .all()
            )

            if not logs:
                return "None"

            lines: List[str] = []
            for log in logs[:12]:
                d = log.logged_date.isoformat() if getattr(log, "logged_date", None) else ""
                st = (log.symptom_type or "").strip()
                sev = getattr(log, "severity", None)
                if st and sev:
                    lines.append(f"[{d}] {st}={sev}/9")

            return "\n".join(lines) if lines else "None"
        except Exception:
            return "None"
    # ──────────────────────────────────────────────────────────────────────────

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
            if mem.get("activities_liked"):
                lines.append(f"activities_liked={mem.get('activities_liked')}")
            if mem.get("activities_disliked"):
                lines.append(f"activities_disliked={mem.get('activities_disliked')}")
            if mem.get("common_barriers"):
                lines.append(f"common_barriers={mem.get('common_barriers')}")

        return "\n".join(lines)
    
    def _build_historical_memory_context(self, uid: str) -> str:
        """Build context from past conversations: wins, blockers, patterns learned."""
        lines = []
        user_today = get_user_current_date(uid, self.db)
        
        try:
            # Get past care plan check-in insights (last 7 days)
            past_threads = self.db.query(CarePlanCheckInThread).filter(
                and_(
                    CarePlanCheckInThread.uid == uid,
                    CarePlanCheckInThread.local_date < user_today,
                    CarePlanCheckInThread.local_date >= user_today - timedelta(days=7)
                )
            ).order_by(desc(CarePlanCheckInThread.local_date)).limit(5).all()
            
            all_wins = []
            all_blockers = []
            all_preferences = []
            
            for thread in past_threads:
                insights = thread.actionable_insights or {}
                wins = insights.get("wins", [])
                blockers = insights.get("blockers", [])
                prefs = insights.get("preferences", [])
                
                if wins:
                    all_wins.extend(wins[:3])  # Cap per thread
                if blockers:
                    all_blockers.extend(blockers[:3])
                if prefs:
                    all_preferences.extend(prefs[:2])
            
            if all_wins:
                lines.append(f"PAST WINS (things that worked): {all_wins[:8]}")
            if all_blockers:
                lines.append(f"PAST BLOCKERS (struggles mentioned): {all_blockers[:8]}")
            if all_preferences:
                lines.append(f"EXPRESSED PREFERENCES: {all_preferences[:5]}")
            
            # Get recent weekly check-in insights
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
                    lines.append(f"WHAT HELPED RECENTLY: {factors_pos[:5]}")
                if factors_neg:
                    lines.append(f"WHAT MADE SYMPTOMS WORSE: {factors_neg[:5]}")
                
                triggers = insights.get("triggers_identified", [])
                relief = insights.get("relief_factors_identified", [])
                if triggers:
                    lines.append(f"TRIGGERS IDENTIFIED: {triggers[:5]}")
                if relief:
                    lines.append(f"RELIEF FACTORS: {relief[:5]}")
            
            # Get recent symptom patterns
            recent_symptom_thread = self.db.query(SymptomCheckInThread).filter(
                and_(
                    SymptomCheckInThread.uid == uid,
                    SymptomCheckInThread.local_date < user_today
                )
            ).order_by(desc(SymptomCheckInThread.local_date)).first()
            
            if recent_symptom_thread:
                symptom_insights = recent_symptom_thread.actionable_insights or {}
                progress = symptom_insights.get("progress", "")
                if progress:
                    lines.append(f"RECENT SYMPTOM PROGRESS: {progress}")
            
        except Exception as e:
            logger.warning(f"[CarePlanService] Error building historical memory: {e}")
        
        return "\n".join(lines) if lines else "No historical data yet"

    def _build_todays_action_plan_context(self, uid: str) -> str:
        """Build COMPREHENSIVE action plan context with scientific reasoning.
        
        This gives the chatbot FULL knowledge of:
        - What actions are in the plan
        - WHY each action was chosen (purpose)
        - Which conditions each action targets
        - Scientific backing (research studies)
        - Target hormones
        
        This enables the chatbot to DEFEND and EXPLAIN the plan when users question it.
        """
        user_today = get_user_current_date(uid, self.db)

        plan = (
            self.db.query(ActionPlan)
            .filter(and_(ActionPlan.uid == uid, ActionPlan.plan_date == user_today))
            .order_by(desc(ActionPlan.created_at))
            .first()
        )

        if not plan:
            # Fallback to most recent plan
            plan = (
                self.db.query(ActionPlan)
                .filter(ActionPlan.uid == uid)
                .order_by(desc(ActionPlan.plan_date), desc(ActionPlan.created_at))
                .first()
            )

        if not plan:
            return "No action plan found."

        # Filter out replaced items - only show active items to AI
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
            f"",
            f"This plan was SPECIFICALLY created for this user's conditions.",
            f"Each item has scientific reasoning - USE THIS to explain when asked!",
            f"",
        ]
        
        for idx, it in enumerate(items[:8], 1):
            title = (it.title or "").strip()
            if not title:
                continue
            
            category = getattr(it, "category", "general") or "general"
            target_hormone = getattr(it, "target_hormone", None) or "hormones"
            purpose = getattr(it, "purpose", None) or ""
            conditions = getattr(it, "conditions", None) or []
            symptoms = getattr(it, "symptoms", None) or []
            time_slot = getattr(it, "time_slot", None) or "anytime"
            research_studies = getattr(it, "research_studies", None) or []
            
            lines.append(f"───────────────────────────────────────────────────────────────")
            lines.append(f"ACTION {idx}: {title.upper()}")
            lines.append(f"───────────────────────────────────────────────────────────────")
            lines.append(f"  📋 Category: {category}")
            lines.append(f"  ⏰ Time: {time_slot}")
            lines.append(f"  🎯 Target Hormone: {target_hormone}")
            
            if conditions:
                lines.append(f"  🩺 Conditions Targeted: {', '.join(conditions)}")
            if symptoms:
                lines.append(f"  💊 Symptoms Addressed: {', '.join(symptoms)}")
            
            # The PURPOSE is the key explanation - this is what the chatbot should cite
            if purpose:
                lines.append(f"  ")
                lines.append(f"  📖 WHY THIS WAS CHOSEN:")
                # Split purpose into manageable chunks
                purpose_clean = purpose.strip()
                lines.append(f"  {purpose_clean}")
            
            # Research studies - critical for credibility
            if research_studies and isinstance(research_studies, list):
                lines.append(f"  ")
                lines.append(f"  🔬 SCIENTIFIC BACKING:")
                for study in research_studies[:2]:  # Limit to 2 studies per item
                    if isinstance(study, dict):
                        study_title = study.get("title", "Research Study")
                        journal = study.get("journal", "")
                        year = study.get("year", "")
                        finding = study.get("finding", "")
                        participants = study.get("participants", "")
                        
                        study_ref = f"{study_title}"
                        if journal and year:
                            study_ref += f" ({journal}, {year})"
                        lines.append(f"    • {study_ref}")
                        if participants:
                            lines.append(f"      Participants: {participants} women")
                        if finding:
                            lines.append(f"      Finding: {finding[:150]}{'...' if len(finding) > 150 else ''}")
            
            lines.append(f"")
        
        lines.append(f"═══════════════════════════════════════════════════════════════")
        lines.append(f"USE THE ABOVE DATA to explain WHY each item is personalized!")
        lines.append(f"═══════════════════════════════════════════════════════════════")
        
        return "\n".join(lines)

    def get_plan_items_for_ui(self, uid: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Return a compact list of plan items to drive UI pickers."""
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
            return []

        # Filter out replaced items - they should not appear in pickers
        q = self.db.query(ActionPlanItem).filter(
            ActionPlanItem.plan_id == plan.id,
            ActionPlanItem.is_replaced != True  # noqa: E712
        )
        items = q.order_by(ActionPlanItem.slot).all()
        out: List[Dict[str, Any]] = []
        for it in items:
            title = (it.title or "").strip()
            if not title:
                continue
            out.append(
                {
                    "id": int(it.id),
                    "item_id": int(it.id),
                    "title": title,
                    "category": getattr(it, "category", "general"),
                    "target_hormone": getattr(it, "target_hormone", None),
                    "slot": getattr(it, "slot", None),
                    "time_slot": getattr(it, "time_slot", None),
                    "is_completed": bool(getattr(it, "is_completed", False)),
                }
            )
            if len(out) >= max(1, min(limit, 20)):
                break
        return out

    async def replace_action_item(self, uid: str, item_id: int, reason: str) -> Dict[str, Any]:
        """Replace a plan item with refresh-token gating (same policy as action_plan.replace)."""
        from app.services.reward_service import RewardService
        from app.services.action_plan_generator import get_action_plan_generator

        reward_service = RewardService(self.db)
        refresh_status = reward_service.get_refresh_status(uid)
        if not refresh_status.get("can_refresh"):
            return {
                "success": False,
                "error_code": "REFRESH_LIMIT",
                "error": f"Daily refresh limit reached ({refresh_status.get('limit')}/day). Try again tomorrow!",
            }

        async_db = await self._get_async_db_session()
        try:
            generator = get_action_plan_generator()
            result = await generator.replace_action(user_id=uid, item_id=item_id, reason=reason, db=async_db)
        finally:
            try:
                await async_db.close()
            except Exception:
                pass

        if not result.get("success"):
            return {
                "success": False,
                "error_code": "REPLACE_FAILED",
                "error": result.get("error", "Failed to replace action"),
                "details": result,
            }

        # Consume refresh token only on success
        try:
            reward_service.use_refresh(uid)
        except Exception:
            # Don't fail the user experience if token bookkeeping hiccups.
            pass

        return {
            "success": True,
            "original_id": result.get("original_id"),
            "replacement_id": result.get("replacement_id"),
            "replacement_action": result.get("replacement_action"),
        }

    async def generate_alternate_candidates(self, uid: str, item_id: int, reason: str, count: int = 3) -> Dict[str, Any]:
        """Generate a small set of alternate replacement candidates for a single plan item.

        This is a *preview* step: it does not mutate the plan.
        """
        from app.services.reward_service import RewardService
        from app.services.action_plan_generator import get_action_plan_generator

        count = max(2, min(int(count or 3), 6))

        # If the user cannot refresh today, don't waste a generation call.
        reward_service = RewardService(self.db)
        refresh_status = reward_service.get_refresh_status(uid)
        if not refresh_status.get("can_refresh"):
            return {
                "success": False,
                "error_code": "REFRESH_LIMIT",
                "error": f"Daily refresh limit reached ({refresh_status.get('limit')}/day). Try again tomorrow!",
            }

        async_db = await self._get_async_db_session()
        try:
            generator = get_action_plan_generator()
            result = await generator.generate_replacement_candidates(
                user_id=uid,
                item_id=item_id,
                reason=reason,
                n=count,
                db=async_db,
            )
        finally:
            try:
                await async_db.close()
            except Exception:
                pass

        if not result.get("success"):
            return {
                "success": False,
                "error_code": "CANDIDATES_FAILED",
                "error": result.get("error") or "Failed to generate alternate suggestions",
                "details": result,
            }

        actions = result.get("actions") or []
        candidates_by_id: Dict[str, Any] = {}
        candidates_ui: List[Dict[str, Any]] = []
        for idx, action in enumerate(actions[:count]):
            cid = f"alt_{idx+1}"
            candidates_by_id[cid] = action
            candidates_ui.append(
                {
                    "candidate_id": cid,
                    "title": action.get("title") or action.get("specific_action") or f"Option {idx+1}",
                }
            )

        return {
            "success": True,
            "candidates_by_id": candidates_by_id,
            "candidates_ui": candidates_ui,
        }

    async def replace_action_item_with_candidate(self, uid: str, item_id: int, candidate_action: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """Replace a plan item using a pre-generated candidate action dict."""
        from app.services.reward_service import RewardService
        from app.services.action_plan_generator import get_action_plan_generator

        reward_service = RewardService(self.db)
        refresh_status = reward_service.get_refresh_status(uid)
        if not refresh_status.get("can_refresh"):
            return {
                "success": False,
                "error_code": "REFRESH_LIMIT",
                "error": f"Daily refresh limit reached ({refresh_status.get('limit')}/day). Try again tomorrow!",
            }

        async_db = await self._get_async_db_session()
        try:
            generator = get_action_plan_generator()
            result = await generator.replace_action_from_action_dict(
                user_id=uid,
                item_id=item_id,
                replacement_action=candidate_action,
                reason=reason,
                db=async_db,
            )
        finally:
            try:
                await async_db.close()
            except Exception:
                pass

        if not result.get("success"):
            return {
                "success": False,
                "error_code": "REPLACE_FAILED",
                "error": result.get("error", "Failed to replace action"),
                "details": result,
            }

        # Consume refresh token only on success
        try:
            reward_service.use_refresh(uid)
        except Exception:
            pass

        return {
            "success": True,
            "original_id": result.get("original_id"),
            "replacement_id": result.get("replacement_id"),
            "replacement_action": result.get("replacement_action"),
        }

    @staticmethod
    async def _get_async_db_session():
        """Create an async DB session using the shared engine/session factory.
        
        Uses the centralized AsyncSessionLocal from app.core.database instead of
        creating a new engine each time, which:
        - Reuses connection pool properly
        - Avoids connection exhaustion
        - Follows SQLAlchemy best practices
        """
        from app.core.database import get_async_session_maker
        
        AsyncSessionLocal = get_async_session_maker()
        return AsyncSessionLocal()

    @staticmethod
    def _new_message_id() -> str:
        import uuid

        return str(uuid.uuid4())
