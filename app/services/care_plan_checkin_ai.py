"""Daily Care Plan Check-in AI engine.

This powers a lightweight, ongoing daily chat thread that:
- references the user's current action plan
- captures blockers/wins/requests (skip/change/alternates)
- maintains a rolling summary (sliding window) for long threads
- DIRECTLY suggests replacement actions when user wants to change something

The goal is to produce actionable insights that can be injected into
ActionPlan generation and replacement.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


class CarePlanTapOption(BaseModel):
    id: str
    text: str


class ReplacementSuggestion(BaseModel):
    """A suggested replacement action when user wants to change something."""
    original_item: str  # Title of item to replace
    suggestions: List[str]  # 2-4 alternative action titles


class CarePlanInsights(BaseModel):
    """Actionable signals for plan updates/replacements."""

    # User-requested changes
    plan_changes_requested: List[str] = Field(default_factory=list)
    actions_to_skip: List[str] = Field(default_factory=list)
    
    # Direct replacement suggestions when user wants to change an item
    replacement_suggestion: Optional[ReplacementSuggestion] = None

    # Conversation extraction
    wins: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    preferences: List[str] = Field(default_factory=list)

    key_takeaway: Optional[str] = None


class CarePlanAIResponse(BaseModel):
    messages: List[str]
    tap_options: List[CarePlanTapOption] = Field(default_factory=list)
    insights: Optional[CarePlanInsights] = None


def _extract_json_object(text: str) -> Optional[str]:
    """Best-effort extraction of a JSON object from a model response."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


class CarePlanCheckInAI:
    """AI helper that generates daily care-plan check-in responses."""

    async def generate_reply(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        action_plan_context: str,
        recent_symptom_checkin_context: str,
        recent_symptom_logs_context: str,
        rolling_summary: Optional[str],
        recent_messages: List[Dict[str, Any]],
    ) -> Tuple[CarePlanAIResponse, str]:
        """Generate the next assistant messages, tap suggestions, and insights."""

        summary_block = rolling_summary.strip() if rolling_summary else ""
        recent_block = json.dumps(recent_messages[-20:], ensure_ascii=False)

        prompt = f"""
You are Auvra, a warm, practical health coach helping with a women's hormonal health app.

Task: Continue a DAILY Care Plan Check-in chat.

CAPABILITIES:
1. If user wants to REPLACE/CHANGE an action item:
   - Identify which item they want to replace
   - Suggest 2-4 SPECIFIC alternative actions (same category, hormone-supportive)
   - Provide these as tap_options so user can pick one
   
2. If user is just chatting or giving feedback:
   - Be brief and chatty
   - Ask at most ONE follow-up question
   - Extract insights for future plan improvements

RESPONSE FORMAT - Return STRICT JSON only:
{{
  "messages": ["Your chat response(s)..."],
  "tap_options": [
    {{"id": "replace_with_TITLE", "text": "🔄 Replace with: [Alternative Title]"}},
    {{"id": "keep_current", "text": "✅ Keep current plan"}}
  ],
  "insights": {{
    "replacement_suggestion": {{
      "original_item": "Item title user wants to replace",
      "suggestions": ["Alternative 1", "Alternative 2", "Alternative 3"]
    }},
    "plan_changes_requested": ["description of changes"],
    "actions_to_skip": ["item titles to skip"],
    "wins": ["what's working"],
    "blockers": ["what's not working"],
    "preferences": ["user preferences learned"],
    "key_takeaway": "summary of conversation"
  }}
}}

RULES:
- When user asks to replace something, suggest alternatives DIRECTLY in tap_options
- Use tap_option id format: "replace_with_[action_title]" for replacements
- Make suggestions contextual to user's condition and cycle phase
- No medical diagnosis. Keep advice habit-focused.
- Be concise - max 2-3 short sentences in messages.

USER PROFILE:
{user_profile_context}

TODAY'S ACTION PLAN (items user can replace):
{action_plan_context}

RECENT SYMPTOM LOGS:
{recent_symptom_logs_context}

ROLLING SUMMARY:
{summary_block}

RECENT MESSAGES:
{recent_block}

USER MESSAGE:
{user_message}
""".strip()

        raw, model_used = await AIService.call_ai_model(prompt, with_fallback=True)
        raw = (raw or "").strip()

        extracted = _extract_json_object(raw)
        if not extracted:
            logger.warning("[CarePlanCheckInAI] Non-JSON response; falling back to plain message")
            return CarePlanAIResponse(messages=[raw or "Got it — tell me a bit more about what feels hardest today."], tap_options=[]), model_used

        try:
            data = json.loads(extracted)
            parsed = CarePlanAIResponse.model_validate(data)
            # Ensure we always return at least one message
            if not parsed.messages:
                parsed.messages = ["Got it. What would you like to adjust about today?"]
            return parsed, model_used
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[CarePlanCheckInAI] Failed to parse structured output: {e}")
            return CarePlanAIResponse(messages=[raw or "Got it — what would you like to adjust about today?"], tap_options=[]), model_used
