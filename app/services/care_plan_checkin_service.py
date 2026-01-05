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
from app.core.database import ActionPlan, ActionPlanItem, CarePlanCheckInThread, SymptomCheckInThread, SymptomLog, UserProfile
from app.services.care_plan_checkin_ai import CarePlanCheckInAI, CarePlanAIResponse
from app.utils.timezone_utils import get_user_current_date

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
        # Seed an opening message
        opening = {
            "id": self._new_message_id(),
            "role": "bot",
            "content": "Quick care plan check-in for today — how did your actions feel so far?",
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
            out.append(
                {
                    "id": msg.get("id") or self._new_message_id(),
                    "text": msg.get("content") or "",
                    "isBot": role != "user",
                }
            )
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
        recent_messages = list(thread.raw_messages or [])[-self.TAIL_SIZE :]

        ai_response, _model_used = await self.ai.generate_reply(
            user_message=message_text,
            user_profile_context=user_profile_context,
            action_plan_context=action_plan_context,
            recent_symptom_checkin_context=recent_symptom_checkin_context,
            recent_symptom_logs_context=recent_symptom_logs_context,
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
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        if not profile:
            return ""

        mem = profile.chatbot_memory or {}
        # Keep it lightweight: only a few stable preference fields if present.
        diet = mem.get("diet_preference")
        allergies = mem.get("food_allergies")
        prefs = []
        if diet:
            prefs.append(f"diet_preference={diet}")
        if allergies:
            prefs.append(f"food_allergies={allergies}")

        name = getattr(profile, "name", None)
        return "\n".join([
            f"name={name}" if name else "",
            f"preferences={prefs}" if prefs else "",
        ]).strip()

    def _build_todays_action_plan_context(self, uid: str) -> str:
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

        items = self.db.query(ActionPlanItem).filter(ActionPlanItem.plan_id == plan.id).all()
        if not items:
            return "Action plan exists, but has no items."

        lines = [f"Plan date: {plan.plan_date}"]
        for it in items[:12]:
            title = (it.title or "").strip()
            if not title:
                continue
            lines.append(f"- {title}")
        if len(items) > 12:
            lines.append(f"(+{len(items) - 12} more)")
        return "\n".join(lines)

    @staticmethod
    def _new_message_id() -> str:
        import uuid

        return str(uuid.uuid4())
