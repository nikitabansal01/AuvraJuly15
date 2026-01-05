"""
═══════════════════════════════════════════════════════════════════════════════
DAILY SYMPTOM CHECK-IN AI ENGINE
═══════════════════════════════════════════════════════════════════════════════
Dr. Auvra persona for DAILY symptom progress conversations.

KEY DIFFERENCES FROM WEEKLY CHECK-IN:
- Daily = QUICK (2 doctor turns max, not 3)
- Focus on TODAY vs YESTERDAY (not whole week)
- Specific reference to yesterday's symptom logs
- Quick pulse check, not deep analysis

Conversation Flow:
1. Turn 1: "Hey! Your [symptom] was X/9 yesterday. How's it today?"
   → User picks: Better / Same / Worse
2. Turn 2: Based on response:
   - Better → "What helped?" → Done
   - Worse → "What triggered it?" → Done
   - Same → Quick encouragement → Done

Outputs actionable insights that feed into:
- Today's/tomorrow's action plan personalization
- Weekly check-in context
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SymptomTapOption(BaseModel):
    id: str
    text: str


class SymptomInsights(BaseModel):
    """Actionable signals extracted from conversation."""
    progress: Optional[str] = None  # better|same|worse
    symptoms_mentioned: List[str] = Field(default_factory=list)
    severity_today: Optional[int] = None  # 1-9 if user gives it
    
    # What affected symptoms today
    triggers_today: List[str] = Field(default_factory=list)  # what made it worse
    relief_today: List[str] = Field(default_factory=list)    # what helped
    
    # Legacy fields for compatibility
    wins: List[str] = Field(default_factory=list)
    difficulties: List[str] = Field(default_factory=list)
    triggers_identified: List[str] = Field(default_factory=list)
    relief_factors_identified: List[str] = Field(default_factory=list)
    severity_rating: Optional[int] = None  # alias for severity_today
    
    key_takeaway: Optional[str] = None


class SymptomAIResponse(BaseModel):
    messages: List[str]  # Multi-bubble: 2 short messages
    tap_options: List[SymptomTapOption] = Field(default_factory=list)
    insights: Optional[SymptomInsights] = None
    is_complete: bool = False  # True after 2 turns


# ═══════════════════════════════════════════════════════════════════════════════
# TAP OPTION TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

# Progress options (Turn 1 response)
PROGRESS_TAP_OPTIONS = [
    {"id": "better", "text": "😊 Better than yesterday"},
    {"id": "same", "text": "😐 About the same"},
    {"id": "worse", "text": "😟 Worse than yesterday"},
]

# Relief factors (when user says "better")
RELIEF_TAP_OPTIONS = [
    {"id": "good_sleep", "text": "😴 Slept well"},
    {"id": "less_stress", "text": "🧘 Less stressed"},
    {"id": "healthy_food", "text": "🥗 Ate healthier"},
    {"id": "exercise", "text": "🏃 Exercised"},
    {"id": "hydration", "text": "💧 Drank more water"},
    {"id": "not_sure", "text": "🤷 Not sure"},
]

# Trigger factors (when user says "worse")
TRIGGER_TAP_OPTIONS = [
    {"id": "poor_sleep", "text": "😴 Poor sleep"},
    {"id": "more_stress", "text": "😰 More stressed"},
    {"id": "ate_out", "text": "🍔 Ate out/junk food"},
    {"id": "skipped_actions", "text": "⏭️ Skipped my actions"},
    {"id": "cycle_timing", "text": "🌙 Cycle timing"},
    {"id": "not_sure", "text": "🤷 Not sure"},
]

# First-time user (no symptom history)
FIRST_TIME_TAP_OPTIONS = [
    {"id": "bloating", "text": "🫄 Bloating"},
    {"id": "cramps", "text": "😣 Cramps"},
    {"id": "fatigue", "text": "😴 Fatigue"},
    {"id": "headache", "text": "🤕 Headache"},
    {"id": "mood", "text": "😔 Mood changes"},
    {"id": "other", "text": "✍️ Something else"},
]


def _extract_json_object(text: str) -> Optional[str]:
    """Extract JSON object from model response."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _parse_yesterday_symptom(recent_symptom_logs_context: str) -> Optional[Dict[str, Any]]:
    """Extract yesterday's most recent symptom log for reference."""
    if not recent_symptom_logs_context or recent_symptom_logs_context == "No recent symptom logs.":
        return None
    
    # Parse format: "- bloating severity 6/9 2026-01-05T10:30:00"
    lines = recent_symptom_logs_context.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("- "):
            parts = line[2:].split(" severity ")
            if len(parts) >= 2:
                symptom_type = parts[0].strip()
                severity_part = parts[1].split("/")[0].strip()
                try:
                    severity = int(severity_part)
                    return {"symptom_type": symptom_type, "severity": severity}
                except ValueError:
                    continue
    return None


class SymptomCheckInAI:
    """
    Dr. Auvra Daily Symptom Check-in AI.
    
    CRITICAL: Max 2 doctor turns (quick daily pulse check).
    Uses multi-bubble messages (empathize first, then question).
    """
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN ENTRY POINT
    # ═══════════════════════════════════════════════════════════════════════════
    
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
        """Generate Dr. Auvra's response with 2-turn limit."""
        
        # Count conversation turns by USER messages (not bot messages)
        # Because we use multi-bubble (2 bot messages per turn), counting bot messages is wrong
        # Turn 0: Opening (2 bot bubbles) → User responds → Turn 1
        # Turn 1: Follow-up (2 bot bubbles) → User responds → Turn 2 (complete)
        user_turns = sum(1 for msg in recent_messages if msg.get("role") == "user")
        
        # HARD LIMIT: Force completion after 2 user responses
        # This means: Opening → User1 → Follow-up → User2 → Complete
        if user_turns >= 2:
            logger.info(f"[SymptomCheckInAI] Forcing completion after {user_turns} user turns")
            return await self._generate_completion_message(
                user_message=user_message,
                user_profile_context=user_profile_context,
                recent_messages=recent_messages,
            )
        
        # Parse yesterday's symptom for context
        yesterday_symptom = _parse_yesterday_symptom(recent_symptom_logs_context)
        
        # Build the Dr. Auvra prompt
        return await self._generate_dr_auvra_response(
            user_message=user_message,
            user_profile_context=user_profile_context,
            action_plan_context=action_plan_context,
            recent_symptom_logs_context=recent_symptom_logs_context,
            rolling_summary=rolling_summary,
            recent_messages=recent_messages,
            yesterday_symptom=yesterday_symptom,
            user_turns=user_turns,
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # DR. AUVRA RESPONSE GENERATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _generate_dr_auvra_response(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        action_plan_context: str,
        recent_symptom_logs_context: str,
        rolling_summary: Optional[str],
        recent_messages: List[Dict[str, Any]],
        yesterday_symptom: Optional[Dict[str, Any]],
        user_turns: int,
    ) -> Tuple[SymptomAIResponse, str]:
        """Generate contextual Dr. Auvra response."""
        
        # Extract user name
        user_name = "there"
        if user_profile_context:
            for line in user_profile_context.split("\n"):
                if line.startswith("name="):
                    user_name = line.split("=", 1)[1].strip() or "there"
                    break
        
        # Build context for prompt
        yesterday_context = ""
        if yesterday_symptom:
            yesterday_context = f"""
YESTERDAY'S SYMPTOM LOG:
- Symptom: {yesterday_symptom['symptom_type']}
- Severity: {yesterday_symptom['severity']}/9
"""
        else:
            yesterday_context = "YESTERDAY'S SYMPTOM LOG: None (first time user)"
        
        summary_block = (rolling_summary or "").strip()
        recent_block = json.dumps(recent_messages[-10:], ensure_ascii=False)
        
        system_prompt = f"""
You are Dr. Auvra, an empathetic women's health specialist doing a QUICK daily check-in.

PATIENT: {user_name}
{yesterday_context}

TODAY'S ACTION PLAN:
{action_plan_context or "None"}

RECENT SYMPTOM LOGS:
{recent_symptom_logs_context or "None"}

═══════════════════════════════════════════════════════════════════════════════
YOUR TASK (USER HAS RESPONDED {user_turns} TIME(S)):
═══════════════════════════════════════════════════════════════════════════════

{f'''THIS IS USER'S FIRST RESPONSE.
They said: "{user_message}"

Based on what they said:
- If they chose BETTER/improving: Celebrate briefly, then ask what helped
- If they chose WORSE/worsening: Show empathy, then ask what triggered it  
- If they chose SAME/stable: Acknowledge, then offer brief encouragement
- If they mentioned a specific symptom: Acknowledge it and ask how severe (1-9)

RESPOND WITH EXACTLY 2 SHORT MESSAGES + 3-4 TAP OPTIONS.''' if user_turns == 0 else f'''THIS IS USER'S SECOND RESPONSE - TIME TO WRAP UP.
They said: "{user_message}"

Thank them briefly and acknowledge what they shared.
Set is_complete: true

RESPOND WITH EXACTLY 2 SHORT MESSAGES + EMPTY TAP OPTIONS.'''}

═══════════════════════════════════════════════════════════════════════════════
RESPONSE FORMAT RULES:
═══════════════════════════════════════════════════════════════════════════════

1. ALWAYS return exactly 2 messages (multi-bubble style)
   - Message 1: Short acknowledgment (max 8 words, include emoji)
   - Message 2: One question OR brief closing (max 12 words)
2. Keep tap_options to 3-4 maximum
3. Use emojis in tap option text

═══════════════════════════════════════════════════════════════════════════════
EXAMPLES:
═══════════════════════════════════════════════════════════════════════════════

User said "better":
{{
    "messages": ["That's wonderful! 🎉", "What do you think helped?"],
    "tap_options": [
        {{"id": "good_sleep", "text": "😴 Slept well"}},
        {{"id": "less_stress", "text": "🧘 Less stressed"}},
        {{"id": "healthy_food", "text": "🥗 Ate healthier"}},
        {{"id": "not_sure", "text": "🤷 Not sure"}}
    ],
    "is_complete": false,
    "insights": {{"progress": "better"}}
}}

User said "worse":
{{
    "messages": ["I'm sorry to hear that. 💜", "Any idea what triggered it?"],
    "tap_options": [
        {{"id": "poor_sleep", "text": "😴 Poor sleep"}},
        {{"id": "more_stress", "text": "😰 More stressed"}},
        {{"id": "ate_out", "text": "🍔 Ate out/junk food"}},
        {{"id": "not_sure", "text": "🤷 Not sure"}}
    ],
    "is_complete": false,
    "insights": {{"progress": "worse"}}
}}

User said "same":
{{
    "messages": ["Got it, hanging in there. 💜", "Anything feel different today?"],
    "tap_options": [
        {{"id": "slightly_better", "text": "📈 Slightly better"}},
        {{"id": "slightly_worse", "text": "📉 Slightly worse"}},
        {{"id": "exactly_same", "text": "➡️ Exactly the same"}}
    ],
    "is_complete": false,
    "insights": {{"progress": "same"}}
}}

Final response (after user's 2nd reply):
{{
    "messages": ["Thanks for sharing, {user_name}! 💜", "I'll use this for your plan."],
    "tap_options": [],
    "is_complete": true,
    "insights": {{"key_takeaway": "User shared their progress"}}
}}

═══════════════════════════════════════════════════════════════════════════════
OUTPUT (STRICT JSON ONLY):
═══════════════════════════════════════════════════════════════════════════════

ROLLING SUMMARY:
{summary_block or "None"}

RECENT MESSAGES:
{recent_block}

USER MESSAGE:
{user_message}
""".strip()

        # Call AI
        raw, model_used = await AIService.call_ai_model(system_prompt, with_fallback=True)
        raw = (raw or "").strip()
        
        extracted = _extract_json_object(raw)
        if not extracted:
            logger.warning("[SymptomCheckInAI] Non-JSON response; using fallback")
            return self._fallback_response(yesterday_symptom, user_name), model_used
        
        try:
            data = json.loads(extracted)
            parsed = SymptomAIResponse.model_validate(data)
            
            # Ensure we have messages
            if not parsed.messages:
                return self._fallback_response(yesterday_symptom, user_name), model_used
            
            # Ensure tap options exist
            if not parsed.tap_options:
                if yesterday_symptom:
                    parsed.tap_options = [SymptomTapOption(**o) for o in PROGRESS_TAP_OPTIONS]
                else:
                    parsed.tap_options = [SymptomTapOption(**o) for o in FIRST_TIME_TAP_OPTIONS]
            
            return parsed, model_used
            
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[SymptomCheckInAI] Parse error: {e}")
            return self._fallback_response(yesterday_symptom, user_name), model_used
    
    # ═══════════════════════════════════════════════════════════════════════════
    # COMPLETION MESSAGE (After 2 turns)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _generate_completion_message(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        recent_messages: List[Dict[str, Any]],
    ) -> Tuple[SymptomAIResponse, str]:
        """Generate final completion message after 2 turns."""
        
        # Extract user name
        user_name = "there"
        if user_profile_context:
            for line in user_profile_context.split("\n"):
                if line.startswith("name="):
                    user_name = line.split("=", 1)[1].strip() or "there"
                    break
        
        # Analyze the conversation to extract insights
        conversation_text = " ".join([
            msg.get("content", "") for msg in recent_messages[-6:]
        ])
        
        prompt = f"""
Analyze this daily symptom check-in conversation and generate a SHORT completion message.

CONVERSATION:
{conversation_text}

USER'S FINAL MESSAGE:
{user_message}

Generate:
1. A brief acknowledgment (1 sentence, max 12 words)
2. A personalized tip based on what they shared (1 sentence, max 15 words)

Also extract insights from the conversation.

Return STRICT JSON:
{{
    "messages": ["Thanks for sharing, {user_name}! 💜", "I'll factor [what they said] into your plan."],
    "tap_options": [],
    "is_complete": true,
    "insights": {{
        "progress": "better"|"same"|"worse"|null,
        "symptoms_mentioned": ["string"],
        "triggers_today": ["what made symptoms worse"],
        "relief_today": ["what helped symptoms"],
        "key_takeaway": "one sentence summary"
    }}
}}
""".strip()
        
        raw, model_used = await AIService.call_ai_model(prompt, with_fallback=True)
        raw = (raw or "").strip()
        
        extracted = _extract_json_object(raw)
        if extracted:
            try:
                data = json.loads(extracted)
                parsed = SymptomAIResponse.model_validate(data)
                parsed.is_complete = True
                if not parsed.messages:
                    parsed.messages = [
                        f"Thanks for sharing, {user_name}! 💜",
                        "I'll use this to personalize your plan."
                    ]
                return parsed, model_used
            except (json.JSONDecodeError, ValidationError):
                pass
        
        # Fallback completion
        return SymptomAIResponse(
            messages=[
                f"Thanks for checking in, {user_name}! 💜",
                "I'll use this to personalize your plan."
            ],
            tap_options=[],
            is_complete=True,
            insights=SymptomInsights(key_takeaway="Daily check-in completed"),
        ), "fallback"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # FALLBACK RESPONSES
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _fallback_response(
        self,
        yesterday_symptom: Optional[Dict[str, Any]],
        user_name: str,
    ) -> SymptomAIResponse:
        """Generate fallback response when AI fails."""
        
        if yesterday_symptom:
            symptom = yesterday_symptom["symptom_type"]
            severity = yesterday_symptom["severity"]
            return SymptomAIResponse(
                messages=[
                    f"Hey {user_name}! Your {symptom} was {severity}/9 yesterday. 💜",
                    "How's it feeling today?"
                ],
                tap_options=[SymptomTapOption(**o) for o in PROGRESS_TAP_OPTIONS],
                is_complete=False,
            )
        else:
            return SymptomAIResponse(
                messages=[
                    f"Hey {user_name}! 👋",
                    "What's bothering you most today?"
                ],
                tap_options=[SymptomTapOption(**o) for o in FIRST_TIME_TAP_OPTIONS],
                is_complete=False,
            )
