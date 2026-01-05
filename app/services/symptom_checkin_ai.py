"""
═══════════════════════════════════════════════════════════════════════════════
DAILY SYMPTOM CHECK-IN AI ENGINE - DR. AUVRA
═══════════════════════════════════════════════════════════════════════════════
A personalized, doctor-like daily conversation about symptoms.

NOT a generic "better/worse/same" quiz - this is a REAL conversation that:
- Knows your specific symptoms and concerns (bloating, cramps, etc.)
- References your cycle phase and how it affects you
- Remembers what helped/triggered symptoms before
- Feels like talking to a doctor who KNOWS you

Conversation Flow (2 user turns max):
Turn 1: User responds to personalized opening → Dr. Auvra follows up
Turn 2: User responds → Dr. Auvra wraps up with practical advice
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
    severity_today: Optional[int] = None
    
    triggers_today: List[str] = Field(default_factory=list)
    relief_today: List[str] = Field(default_factory=list)
    
    # Legacy compatibility
    wins: List[str] = Field(default_factory=list)
    difficulties: List[str] = Field(default_factory=list)
    triggers_identified: List[str] = Field(default_factory=list)
    relief_factors_identified: List[str] = Field(default_factory=list)
    severity_rating: Optional[int] = None
    
    key_takeaway: Optional[str] = None


class SymptomAIResponse(BaseModel):
    messages: List[str]
    tap_options: List[SymptomTapOption] = Field(default_factory=list)
    insights: Optional[SymptomInsights] = None
    is_complete: bool = False


def _extract_json_object(text: str) -> Optional[str]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _parse_symptom_logs(recent_symptom_logs_context: str) -> List[Dict[str, Any]]:
    """Parse all recent symptom logs."""
    symptoms = []
    if not recent_symptom_logs_context or recent_symptom_logs_context == "No recent symptom logs.":
        return symptoms
    
    for line in recent_symptom_logs_context.strip().split("\n"):
        line = line.strip()
        if line.startswith("- "):
            parts = line[2:].split(" severity ")
            if len(parts) >= 2:
                symptom_type = parts[0].strip()
                severity_part = parts[1].split("/")[0].strip()
                try:
                    severity = int(severity_part)
                    symptoms.append({"symptom_type": symptom_type, "severity": severity})
                except ValueError:
                    continue
    return symptoms


class SymptomCheckInAI:
    """
    Dr. Auvra - Your personalized women's health companion.
    
    This is NOT a generic questionnaire. This is a doctor who:
    - Knows your specific symptoms (bloating, cramps, fatigue, etc.)
    - Understands your cycle phase and how it affects you
    - Remembers what worked and what didn't
    - Gives practical, personalized advice
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
        """Generate Dr. Auvra's personalized response."""
        
        # Count user turns (not bot messages - we use multi-bubble)
        user_turns = sum(1 for msg in recent_messages if msg.get("role") == "user")
        
        # After 2 user responses, wrap up
        if user_turns >= 2:
            logger.info(f"[SymptomCheckInAI] Completing after {user_turns} user turns")
            return await self._generate_completion(
                user_message=user_message,
                user_profile_context=user_profile_context,
                recent_weekly_checkin_context=recent_weekly_checkin_context,
                recent_symptom_logs_context=recent_symptom_logs_context,
                recent_messages=recent_messages,
            )
        
        # Generate personalized doctor response
        return await self._generate_doctor_response(
            user_message=user_message,
            user_profile_context=user_profile_context,
            action_plan_context=action_plan_context,
            recent_weekly_checkin_context=recent_weekly_checkin_context,
            recent_symptom_logs_context=recent_symptom_logs_context,
            rolling_summary=rolling_summary,
            recent_messages=recent_messages,
            user_turns=user_turns,
        )
    
    def _extract_user_context(
        self,
        user_profile_context: str,
        recent_weekly_checkin_context: str,
        recent_symptom_logs_context: str,
    ) -> Dict[str, Any]:
        """Extract all relevant user info for personalization."""
        
        context = {
            "user_name": "there",
            "top_concern": None,
            "cycle_phase": None,
            "recent_symptoms": [],
            "highest_severity_symptom": None,
        }
        
        # Parse user profile
        if user_profile_context:
            for line in user_profile_context.split("\n"):
                if line.startswith("name="):
                    context["user_name"] = line.split("=", 1)[1].strip() or "there"
                elif line.startswith("top_concern="):
                    context["top_concern"] = line.split("=", 1)[1].strip()
        
        # Try to detect cycle phase from weekly checkin
        if recent_weekly_checkin_context and recent_weekly_checkin_context != "None":
            text_lower = recent_weekly_checkin_context.lower()
            if "menstrual" in text_lower or "period" in text_lower or "menses" in text_lower:
                context["cycle_phase"] = "menstrual"
            elif "follicular" in text_lower:
                context["cycle_phase"] = "follicular"
            elif "ovulation" in text_lower or "ovulat" in text_lower:
                context["cycle_phase"] = "ovulation"
            elif "luteal" in text_lower or "pms" in text_lower:
                context["cycle_phase"] = "luteal"
        
        # Parse recent symptoms
        symptoms = _parse_symptom_logs(recent_symptom_logs_context)
        if symptoms:
            context["recent_symptoms"] = symptoms
            # Find highest severity symptom
            highest = max(symptoms, key=lambda x: x.get("severity", 0))
            context["highest_severity_symptom"] = highest
        
        return context
    
    def _get_cycle_insight(self, cycle_phase: Optional[str]) -> str:
        """Get cycle-specific health insight."""
        insights = {
            "menstrual": "During your period, cramps and fatigue are common. Heat therapy, gentle movement, and iron-rich foods can help.",
            "follicular": "Energy typically increases now. Good time for activity, but watch for any lingering symptoms from your period.",
            "ovulation": "Mid-cycle - energy peaks but some experience bloating or mild discomfort. Stay hydrated!",
            "luteal": "PMS territory - mood swings, bloating, and cravings may appear. Self-care and magnesium-rich foods help.",
        }
        return insights.get(cycle_phase, "")

    def _default_followup_tap_options(self, *, user_message: str, ctx: Dict[str, Any]) -> List[SymptomTapOption]:
        """Deterministic tap options that match the *current question*.

        Used when the model forgets to include tap options.
        """

        msg = (user_message or "").strip().lower()

        # If user is expressing progress, ask about what helped / what triggered.
        if "better" in msg or msg in {"improving", "feeling better"}:
            return [
                SymptomTapOption(id="sleep", text="😴 Slept better"),
                SymptomTapOption(id="stress", text="🧘 Less stress"),
                SymptomTapOption(id="food", text="🥗 Food choices"),
                SymptomTapOption(id="not_sure", text="🤷 Not sure"),
            ]

        if "worse" in msg or msg in {"worsening", "feeling worse"}:
            return [
                SymptomTapOption(id="poor_sleep", text="😴 Poor sleep"),
                SymptomTapOption(id="stress", text="😰 Stress"),
                SymptomTapOption(id="food", text="🍔 Food"),
                SymptomTapOption(id="cycle", text="🌙 Cycle timing"),
            ]

        # Otherwise we assume they selected/typed a symptom; ask severity.
        return [
            SymptomTapOption(id="mild", text="😊 Mild, manageable"),
            SymptomTapOption(id="moderate", text="😐 Moderate, annoying"),
            SymptomTapOption(id="severe", text="😣 Really uncomfortable"),
        ]
    
    async def _generate_doctor_response(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        action_plan_context: str,
        recent_weekly_checkin_context: str,
        recent_symptom_logs_context: str,
        rolling_summary: Optional[str],
        recent_messages: List[Dict[str, Any]],
        user_turns: int,
    ) -> Tuple[SymptomAIResponse, str]:
        """Generate a personalized, doctor-like response."""
        
        # Extract all user context
        ctx = self._extract_user_context(
            user_profile_context,
            recent_weekly_checkin_context,
            recent_symptom_logs_context,
        )
        
        user_name = ctx["user_name"]
        cycle_phase = ctx["cycle_phase"]
        recent_symptoms = ctx["recent_symptoms"]
        top_symptom = ctx["highest_severity_symptom"]
        top_concern = ctx["top_concern"]
        
        # Build rich context blocks
        cycle_block = ""
        if cycle_phase:
            cycle_block = f"""
═══ CYCLE CONTEXT ═══
Phase: {cycle_phase.upper()}
Medical Insight: {self._get_cycle_insight(cycle_phase)}
"""
        
        symptom_block = ""
        if recent_symptoms:
            symptom_list = ", ".join([f"{s['symptom_type']} ({s['severity']}/9)" for s in recent_symptoms[:5]])
            symptom_block = f"""
═══ RECENT SYMPTOMS ═══
{symptom_list}
Most Severe: {top_symptom['symptom_type']} at {top_symptom['severity']}/9
"""
        elif top_concern:
            symptom_block = f"""
═══ USER'S MAIN CONCERN ═══
{top_concern}
"""
        
        summary_block = (rolling_summary or "").strip()
        recent_block = json.dumps(recent_messages[-8:], ensure_ascii=False)
        
        # IMPORTANT: `user_turns` counts USER messages in the thread *including the current one*.
        # So the first real user reply is `user_turns == 1`.
        # We must treat that as the FIRST FOLLOW-UP stage (question + matching taps).
        if user_turns <= 1:
            stage_instruction = f'''
═══ CONVERSATION STAGE: FIRST RESPONSE ═══

The user just told you: "{user_message}"

CRITICAL: Your response must have TWO PARTS:
1. MESSAGE 1: Short empathetic acknowledgment (NO generic advice!)
2. MESSAGE 2: A SPECIFIC FOLLOW-UP QUESTION about their symptom

DO NOT give tips or advice yet - ASK A QUESTION FIRST!

For example, if user said "Bloating":
✅ GOOD: "Bloating during {cycle_phase or 'your cycle'} is common. 💜" + "How severe is it today - mild, moderate, or really uncomfortable?"
❌ BAD: "Try peppermint tea" (too generic, no question)

If user said "better":
✅ GOOD: "That's wonderful to hear! 🎉" + "What do you think made the difference - sleep, food, or less stress?"

If user said "worse":
✅ GOOD: "I'm sorry it's tough today. 💜" + "Any idea what might have triggered it - sleep, stress, or food?"

YOUR TAP OPTIONS should be ANSWERS to your question, not generic options.

PERSONALIZATION:
- Their cycle phase is: {cycle_phase or 'unknown'} - MENTION THIS if relevant
- Their logged symptoms: {', '.join([s['symptom_type'] for s in recent_symptoms]) if recent_symptoms else 'none yet'}
'''
        else:
            stage_instruction = f'''
═══ CONVERSATION STAGE: WRAPPING UP (TURN 2) ═══

User's response: "{user_message}"

NOW you can give advice! Your response should:
1. MESSAGE 1: Acknowledge what they shared
2. MESSAGE 2: ONE specific, actionable tip based on what they said + their cycle phase ({cycle_phase or 'unknown'})

Set is_complete: true and provide EMPTY tap_options.
'''

        system_prompt = f"""You are Dr. Auvra, a warm and knowledgeable women's health specialist.

═══════════════════════════════════════════════════════════════════════════════
PATIENT PROFILE
═══════════════════════════════════════════════════════════════════════════════
Name: {user_name}
{cycle_block}
{symptom_block}

Today's Action Plan:
{action_plan_context or "Not set"}

Previous Conversation Summary:
{summary_block or "First conversation"}

═══════════════════════════════════════════════════════════════════════════════
YOUR IDENTITY - YOU ARE A REAL DOCTOR
═══════════════════════════════════════════════════════════════════════════════

You are NOT:
❌ A generic chatbot asking "how do you feel?"
❌ A robotic questionnaire
❌ Just checking boxes

You ARE:
✅ A caring doctor who remembers {user_name}'s history
✅ Someone who asks about SPECIFIC symptoms they've logged
✅ A professional who connects symptoms to cycle phases
✅ Warm, knowledgeable, and gives practical medical insights

{stage_instruction}

═══════════════════════════════════════════════════════════════════════════════
RESPONSE FORMAT (STRICT JSON)
═══════════════════════════════════════════════════════════════════════════════

Return EXACTLY this format:
{{
    "messages": [
        "First message - empathetic, personalized (max 15 words)",
        "Second message - follow-up question or insight (max 20 words)"
    ],
    "tap_options": [
        {{"id": "option1", "text": "emoji + specific option"}},
        {{"id": "option2", "text": "emoji + specific option"}},
        {{"id": "option3", "text": "emoji + specific option"}}
    ],
    "is_complete": {'false' if user_turns <= 1 else 'true'},
    "insights": {{
        "progress": "better"|"same"|"worse"|null,
        "symptoms_mentioned": ["list symptoms user mentioned"],
        "triggers_today": ["any triggers user mentioned"],
        "relief_today": ["any relief factors user mentioned"],
        "key_takeaway": "one sentence medical summary"
    }}
}}

═══════════════════════════════════════════════════════════════════════════════
EXAMPLES - FOLLOW THE PATTERN EXACTLY:
═══════════════════════════════════════════════════════════════════════════════

EXAMPLE 1: User said "Bloating" (first turn - ASK A QUESTION)
{{
    "messages": [
        "Bloating during menses is so common, {user_name}. 💜",
        "How bad is it - mild discomfort or really uncomfortable?"
    ],
    "tap_options": [
        {{"id": "mild", "text": "😊 Mild, I can manage"}},
        {{"id": "moderate", "text": "😐 Moderate, it's annoying"}},
        {{"id": "severe", "text": "😣 Really uncomfortable"}}
    ],
    "is_complete": false,
    "insights": {{"symptoms_mentioned": ["bloating"]}}
}}

EXAMPLE 2: User said "Feeling better" (first turn - ASK WHAT HELPED)
{{
    "messages": [
        "That's great to hear, {user_name}! 🎉",
        "What do you think helped - better sleep, less stress, or food choices?"
    ],
    "tap_options": [
        {{"id": "sleep", "text": "😴 Slept better"}},
        {{"id": "stress", "text": "🧘 Less stressed"}},
        {{"id": "food", "text": "🥗 Ate well"}},
        {{"id": "not_sure", "text": "🤷 Not sure"}}
    ],
    "is_complete": false,
    "insights": {{"progress": "better"}}
}}

EXAMPLE 3: User said "Worse" (first turn - ASK ABOUT TRIGGERS)
{{
    "messages": [
        "I'm sorry you're not feeling well today. 💜",
        "Any idea what might have triggered it?"
    ],
    "tap_options": [
        {{"id": "poor_sleep", "text": "😴 Poor sleep"}},
        {{"id": "stress", "text": "😰 Stressed"}},
        {{"id": "food", "text": "🍔 Food choices"}},
        {{"id": "cycle", "text": "🌙 Cycle timing"}}
    ],
    "is_complete": false,
    "insights": {{"progress": "worse"}}
}}

EXAMPLE 4: User answered your question "Stressed" (second turn - NOW GIVE ADVICE)
{{
    "messages": [
        "Stress definitely affects bloating! 💜",
        "Try some deep breathing today - even 5 minutes can help. You've got this!"
    ],
    "tap_options": [],
    "is_complete": true,
    "insights": {{"triggers_today": ["stress"], "key_takeaway": "Stress triggering bloating"}}
}}

═══════════════════════════════════════════════════════════════════════════════
CONVERSATION HISTORY:
{recent_block}

USER'S CURRENT MESSAGE:
{user_message}
═══════════════════════════════════════════════════════════════════════════════
""".strip()

        # Call AI
        raw, model_used = await AIService.call_ai_model(system_prompt, with_fallback=True)
        raw = (raw or "").strip()
        
        extracted = _extract_json_object(raw)
        if not extracted:
            logger.warning("[SymptomCheckInAI] Non-JSON response; using fallback")
            return self._get_fallback_response(ctx, user_turns), model_used
        
        try:
            data = json.loads(extracted)
            parsed = SymptomAIResponse.model_validate(data)
            
            if not parsed.messages:
                return self._get_fallback_response(ctx, user_turns), model_used

            # Guardrails for the follow-up stage:
            # - Do NOT complete on the first user reply
            # - Ensure tap options exist and match the question
            if user_turns <= 1:
                parsed.is_complete = False
                if not parsed.tap_options:
                    parsed.tap_options = self._default_followup_tap_options(user_message=user_message, ctx=ctx)
            
            return parsed, model_used
            
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[SymptomCheckInAI] Parse error: {e}")
            return self._get_fallback_response(ctx, user_turns), model_used
    
    async def _generate_completion(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        recent_weekly_checkin_context: str,
        recent_symptom_logs_context: str,
        recent_messages: List[Dict[str, Any]],
    ) -> Tuple[SymptomAIResponse, str]:
        """Generate warm, personalized completion with practical tip."""
        
        ctx = self._extract_user_context(
            user_profile_context,
            recent_weekly_checkin_context,
            recent_symptom_logs_context,
        )
        user_name = ctx["user_name"]
        cycle_phase = ctx["cycle_phase"]
        
        conversation_text = " ".join([
            msg.get("content", "") for msg in recent_messages[-8:]
        ])
        
        prompt = f"""Generate a warm, personalized closing for {user_name}'s daily symptom check-in.

CONVERSATION:
{conversation_text}

USER'S FINAL MESSAGE: {user_message}

CYCLE PHASE: {cycle_phase or 'unknown'}
CYCLE INSIGHT: {self._get_cycle_insight(cycle_phase) if cycle_phase else 'General health tip'}

Create a supportive closing that:
1. Thanks them for sharing
2. Acknowledges the SPECIFIC thing they mentioned (symptom, trigger, relief factor)
3. Gives ONE practical, actionable tip based on their cycle phase and symptoms
4. Ends encouragingly

Return STRICT JSON:
{{
    "messages": [
        "Thanks for checking in, {user_name}! 💜",
        "[Specific, personalized tip based on what they shared and their cycle phase]"
    ],
    "tap_options": [],
    "is_complete": true,
    "insights": {{
        "progress": "better"|"same"|"worse"|null,
        "symptoms_mentioned": [],
        "triggers_today": [],
        "relief_today": [],
        "key_takeaway": "Summary of what user shared and actionable insight"
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
                if parsed.messages:
                    return parsed, model_used
            except (json.JSONDecodeError, ValidationError):
                pass
        
        # Personalized fallback
        tip = self._get_cycle_insight(cycle_phase) if cycle_phase else "Stay hydrated and get good rest tonight."
        return SymptomAIResponse(
            messages=[
                f"Thanks for sharing today, {user_name}! 💜",
                f"Quick tip: {tip[:60]}... Take care!"
            ],
            tap_options=[],
            is_complete=True,
            insights=SymptomInsights(key_takeaway="Daily symptom check-in completed"),
        ), "fallback"
    
    def _get_fallback_response(
        self,
        ctx: Dict[str, Any],
        user_turns: int,
    ) -> SymptomAIResponse:
        """Context-aware fallback response - always ASK QUESTIONS on turn 1."""
        
        user_name = ctx.get("user_name", "there")
        recent_symptoms = ctx.get("recent_symptoms", [])
        top_symptom = ctx.get("highest_severity_symptom")
        cycle_phase = ctx.get("cycle_phase")
        
        # Human-friendly cycle phase
        cycle_text = ""
        if cycle_phase:
            phase_map = {
                "menstrual": "during your period",
                "follicular": "in follicular phase",
                "ovulation": "around ovulation",
                "luteal": "in luteal phase"
            }
            cycle_text = phase_map.get(cycle_phase, f"in {cycle_phase}")
        
        if user_turns == 0:
            # First response - ALWAYS ask a follow-up question
            if top_symptom:
                symptom = top_symptom["symptom_type"]
                if cycle_text:
                    return SymptomAIResponse(
                        messages=[
                            f"{symptom.title()} {cycle_text} is common, {user_name}. 💜",
                            "How bad is it today - mild, moderate, or really uncomfortable?"
                        ],
                        tap_options=[
                            SymptomTapOption(id="mild", text="😊 Mild, manageable"),
                            SymptomTapOption(id="moderate", text="😐 Moderate, annoying"),
                            SymptomTapOption(id="severe", text="😣 Really uncomfortable"),
                        ],
                        is_complete=False,
                    )
                else:
                    return SymptomAIResponse(
                        messages=[
                            f"Thanks for sharing about {symptom}, {user_name}! 💜",
                            "How bad is it - mild, moderate, or really uncomfortable?"
                        ],
                        tap_options=[
                            SymptomTapOption(id="mild", text="😊 Mild, manageable"),
                            SymptomTapOption(id="moderate", text="😐 Moderate, annoying"),
                            SymptomTapOption(id="severe", text="😣 Really uncomfortable"),
                        ],
                        is_complete=False,
                    )
            else:
                return SymptomAIResponse(
                    messages=[
                        f"Got it, {user_name}! 💜",
                        "Which symptom is bothering you most today?"
                    ],
                    tap_options=[
                        SymptomTapOption(id="bloating", text="🫄 Bloating"),
                        SymptomTapOption(id="cramps", text="😣 Cramps/pain"),
                        SymptomTapOption(id="fatigue", text="😴 Fatigue"),
                        SymptomTapOption(id="mood", text="😔 Mood changes"),
                    ],
                    is_complete=False,
                )
        else:
            # Turn 2 - wrap up with advice
            tip = self._get_cycle_insight(cycle_phase) if cycle_phase else "Stay hydrated and rest well tonight."
            return SymptomAIResponse(
                messages=[
                    f"Thanks for sharing, {user_name}! 💜",
                    f"I'll note this for your plan. {tip[:50]}..."
                ],
                tap_options=[],
                is_complete=True,
                insights=SymptomInsights(key_takeaway="User shared symptom update"),
            )
