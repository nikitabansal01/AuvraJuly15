"""
═══════════════════════════════════════════════════════════════════════════════
DAILY SYMPTOM CHECK-IN AI ENGINE
═══════════════════════════════════════════════════════════════════════════════
Doctor-like conversational check-in that feels like talking to a caring physician.

The AI dynamically:
1. References recent symptom logs to ask about progress
2. Uses multi-bubble short messages (empathize + question)
3. Limits conversation to 3 doctor turns (like weekly check-in)
4. Extracts actionable insights for action plan personalization

This mirrors the weekly_checkin_ai.py "Dr. Auvra" pattern but for daily use.
═══════════════════════════════════════════════════════════════════════════════
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
    is_complete: bool = False  # True when conversation should wrap up


def _extract_json_object(text: str) -> Optional[str]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


class SymptomCheckInAI:
    """
    Doctor-like daily symptom check-in AI.
    
    Mirrors the weekly_checkin_ai.py pattern:
    - "Dr. Auvra" persona
    - Multi-bubble short messages
    - 3-turn hard limit
    - References actual symptom data
    """

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

        # ═══════════════════════════════════════════════════════════════════
        # HARD LIMIT: Force completion after 3 doctor turns
        # ═══════════════════════════════════════════════════════════════════
        doctor_turns = sum(1 for msg in recent_messages if msg.get("role") == "assistant")
        force_complete = doctor_turns >= 3

        if force_complete:
            logger.info(f"[SymptomCheckInAI] Forcing completion after {doctor_turns} doctor turns")

        prompt = f"""
You are Dr. Auvra, an empathetic women's health specialist conducting a brief daily symptom check-in.

{"⚠️ THIS IS YOUR FINAL RESPONSE. Set is_complete: true and give a warm closing summary." if force_complete else ""}

YOUR GOAL: Quickly understand how the patient's symptoms are TODAY compared to recently.
- If better → What helped? (to reinforce)
- If worse → What triggered it? (to address)
- If same → Any patterns noticed?

CRITICAL RESPONSE RULES:
1. KEEP RESPONSES SHORT - Max 2 sentences per message
2. SPLIT INTO MULTIPLE MESSAGES - Return an array of 2 short messages, not 1 long one
3. First message: Acknowledge/empathize (1 sentence)
4. Second message: Ask ONE specific question (1 sentence)
5. Generate 3-5 tap options
6. COMPLETE AFTER 2-3 EXCHANGES - Don't drag on!

EXAMPLE GOOD RESPONSE (Opening):
{{
    "messages": [
        "Hey! I noticed your bloating was 6/9 yesterday. 💜",
        "How's it feeling today?"
    ],
    "tap_options": [
        {{"id": "better", "text": "Better today"}},
        {{"id": "same", "text": "About the same"}},
        {{"id": "worse", "text": "Worse today"}},
        {{"id": "different_symptom", "text": "Different symptom today"}}
    ],
    "is_complete": false
}}

EXAMPLE GOOD RESPONSE (After user says "Better"):
{{
    "messages": [
        "That's wonderful to hear! 🎉",
        "What do you think helped the most?"
    ],
    "tap_options": [
        {{"id": "good_sleep", "text": "Good sleep"}},
        {{"id": "less_stress", "text": "Less stress"}},
        {{"id": "better_food", "text": "Ate better"}},
        {{"id": "exercise", "text": "Exercise helped"}},
        {{"id": "not_sure", "text": "Not sure"}}
    ],
    "is_complete": false
}}

EXAMPLE COMPLETION (After 2-3 exchanges):
{{
    "messages": [
        "Thanks for sharing! 💜",
        "I've noted that good sleep helped your bloating. I'll keep that in mind for your plan!"
    ],
    "tap_options": [],
    "is_complete": true,
    "insights": {{
        "progress": "improving",
        "symptoms_mentioned": ["bloating"],
        "wins": ["good sleep"],
        "relief_factors_identified": ["good sleep"],
        "key_takeaway": "Good sleep reduces bloating"
    }}
}}

EXAMPLE BAD RESPONSE (TOO LONG - DON'T DO THIS):
{{
    "messages": ["I noticed your bloating was 6/9 yesterday and I wanted to check in with you today to see how you're feeling. Has it improved at all since then, or is it about the same, or perhaps worse?"],
    ...
}}

Safety:
- No diagnosis.
- No emergency advice.
- Keep guidance general and habit-focused.

Return STRICT JSON only:
{{
  "messages": ["short msg 1", "short msg 2"],
  "tap_options": [{{"id": "string", "text": "string"}}],
  "is_complete": boolean,
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

RECENT SYMPTOM LOGS (IMPORTANT - reference these!):
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
            return SymptomAIResponse(
                messages=["Hey! 💜", "How are your symptoms feeling today?"],
                tap_options=[
                    SymptomTapOption(id="better", text="Better today"),
                    SymptomTapOption(id="same", text="About the same"),
                    SymptomTapOption(id="worse", text="Worse today"),
                ]
            ), model_used

        try:
            data = json.loads(extracted)
            parsed = SymptomAIResponse.model_validate(data)
            if not parsed.messages:
                parsed.messages = ["Hey! 💜", "How are your symptoms feeling today?"]
            return parsed, model_used
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[SymptomCheckInAI] Failed to parse structured output: {e}")
            return SymptomAIResponse(
                messages=["Hey! 💜", "How are your symptoms feeling today?"],
                tap_options=[
                    SymptomTapOption(id="better", text="Better today"),
                    SymptomTapOption(id="same", text="About the same"),
                    SymptomTapOption(id="worse", text="Worse today"),
                ]
            ), model_used
