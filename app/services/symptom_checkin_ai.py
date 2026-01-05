"""Daily Symptom Check-in AI engine.

Goal: a short, chatty daily thread focused on:
- symptom progress (better/same/worse)
- wins and difficulties
- likely triggers and relief factors

Outputs actionable insights that can be injected into:
- action plan generation/replacement
- weekly check-in follow-up questions
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


class SymptomTapOption(BaseModel):
    id: str
    text: str


class SymptomInsights(BaseModel):
    progress: Optional[str] = None  # improving|stable|worsening
    symptoms_mentioned: List[str] = Field(default_factory=list)
    severity_rating: Optional[int] = None  # 1-9 if the user gives it

    wins: List[str] = Field(default_factory=list)
    difficulties: List[str] = Field(default_factory=list)

    triggers_identified: List[str] = Field(default_factory=list)
    relief_factors_identified: List[str] = Field(default_factory=list)

    key_takeaway: Optional[str] = None


class SymptomAIResponse(BaseModel):
    messages: List[str]
    tap_options: List[SymptomTapOption] = Field(default_factory=list)
    insights: Optional[SymptomInsights] = None


def _extract_json_object(text: str) -> Optional[str]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


class SymptomCheckInAI:
    async def generate_reply(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        action_plan_context: str,
        recent_care_plan_checkin_context: str,
        recent_weekly_checkin_context: str,
        recent_symptom_logs_context: str,
        rolling_summary: Optional[str],
        recent_messages: List[Dict[str, Any]],
    ) -> Tuple[SymptomAIResponse, str]:
        summary_block = (rolling_summary or "").strip()
        recent_block = json.dumps(recent_messages[-20:], ensure_ascii=False)

        prompt = f"""
You are Auvra, a warm, practical coach.

Task: Continue a DAILY Symptom Check-in.
- Be brief and encouraging.
- Ask at most ONE follow-up question.
- Provide 3-5 tap replies.
- Extract insights that help personalize today's and tomorrow's action plan.

Safety:
- No diagnosis.
- No emergency advice.
- Keep guidance general and habit-focused.

Return STRICT JSON only with this schema:
{{
  "messages": ["string", ...],
  "tap_options": [{{"id": "string", "text": "string"}}],
  "insights": {{
    "progress": "improving"|"stable"|"worsening"|null,
    "symptoms_mentioned": ["string"],
    "severity_rating": 1-9|null,
    "wins": ["string"],
    "difficulties": ["string"],
    "triggers_identified": ["string"],
    "relief_factors_identified": ["string"],
    "key_takeaway": "string|null"
  }}
}}

USER PROFILE CONTEXT:
{user_profile_context}

TODAY'S ACTION PLAN (if available):
{action_plan_context}

RECENT CARE PLAN CHECK-INS (daily; if available):
{recent_care_plan_checkin_context}

RECENT WEEKLY CHECK-IN SUMMARY (if available):
{recent_weekly_checkin_context}

RECENT SYMPTOM LOGS CONTEXT (if any):
{recent_symptom_logs_context}

ROLLING SUMMARY (older messages; may be empty):
{summary_block}

RECENT MESSAGES (JSON; last messages in order):
{recent_block}

USER MESSAGE:
{user_message}
""".strip()

        raw, model_used = await AIService.call_ai_model(prompt, with_fallback=True)
        raw = (raw or "").strip()

        extracted = _extract_json_object(raw)
        if not extracted:
            logger.warning("[SymptomCheckInAI] Non-JSON response; falling back")
            return SymptomAIResponse(messages=[raw or "Got it — what feels like the biggest win or difficulty today?"], tap_options=[]), model_used

        try:
            data = json.loads(extracted)
            parsed = SymptomAIResponse.model_validate(data)
            if not parsed.messages:
                parsed.messages = ["Got it — what feels like the biggest win or difficulty today?"]
            return parsed, model_used
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[SymptomCheckInAI] Failed to parse structured output: {e}")
            return SymptomAIResponse(messages=[raw or "Got it — what feels like the biggest win or difficulty today?"], tap_options=[]), model_used
