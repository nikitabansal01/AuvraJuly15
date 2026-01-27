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

NATURAL CONVERSATION FLOW:
- NO artificial turn limits - the LLM decides when the conversation naturally ends
- Keep asking follow-up questions, exploring symptoms, providing tips
- ONLY complete when the user says bye/thanks/done OR the conversation naturally concludes
- Always provide good conversational tap options (never just UI-triggering ones)
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
        historical_memory_context: str = "",  # NEW: Past triggers, relief factors, symptom patterns
        rolling_summary: Optional[str],
        recent_messages: List[Dict[str, Any]],
    ) -> Tuple[SymptomAIResponse, str]:
        """Generate Dr. Auvra's personalized response.
        
        NO artificial turn limits - the LLM decides naturally when to complete.
        """
        
        # Count user turns for context (but NOT to force completion)
        def _is_counted_user_turn(m: Dict[str, Any]) -> bool:
            if (m or {}).get("role") != "user":
                return False
            meta = (m or {}).get("meta") or {}
            kind = (meta.get("kind") or "").strip().lower()
            if kind in {"ui_symptom_pick", "symptom_select"}:
                return False
            return True

        user_turns = sum(1 for msg in recent_messages if _is_counted_user_turn(msg))
        
        # Generate personalized doctor response - let LLM decide flow naturally
        return await self._generate_doctor_response(
            user_message=user_message,
            user_profile_context=user_profile_context,
            action_plan_context=action_plan_context,
            recent_weekly_checkin_context=recent_weekly_checkin_context,
            recent_symptom_logs_context=recent_symptom_logs_context,
            historical_memory_context=historical_memory_context,  # NEW: Historical symptom memory
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
            "first_name": "there",
            "top_concern": None,
            "cycle_phase": None,
            "diagnosed_conditions": [],
            "recent_symptoms": [],
            "highest_severity_symptom": None,
        }
        
        # Parse user profile
        if user_profile_context:
            for line in user_profile_context.split("\n"):
                line = line.strip()
                if line.startswith("name="):
                    full_name = line.split("=", 1)[1].strip() or "there"
                    context["user_name"] = full_name
                    context["first_name"] = full_name.split()[0] if full_name else "there"
                elif line.startswith("top_concern="):
                    context["top_concern"] = line.split("=", 1)[1].strip()
                elif line.startswith("diagnosed_conditions="):
                    cond_str = line.split("=", 1)[1].strip()
                    if cond_str and cond_str != "[]":
                        try:
                            context["diagnosed_conditions"] = json.loads(cond_str)
                        except:
                            context["diagnosed_conditions"] = [c.strip() for c in cond_str.split(",") if c.strip()]
        
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

        def _symptom_choices_from_ctx() -> List[str]:
            # Prefer recent symptom types if available.
            recent = []
            for s in (ctx or {}).get("recent_symptoms") or []:
                st = (s or {}).get("symptom_type")
                if st and st not in recent:
                    recent.append(st)
            if recent:
                return recent[:5]
            return ["bloating", "cramps", "fatigue", "headache", "mood"]

        # If user is expressing overall progress, we need to know *which symptom* to rate.
        if (
            "better" in msg
            or "same" in msg
            or "worse" in msg
            or msg in {"improving", "feeling better", "stable", "about the same", "worsening", "feeling worse"}
        ):
            opts: List[SymptomTapOption] = []
            for st in _symptom_choices_from_ctx():
                opts.append(SymptomTapOption(id=f"choose_symptom::{st}", text=f"Log {st}"))
            opts.append(SymptomTapOption(id="other", text="✍️ Something else"))
            return opts

        # Otherwise, we assume they selected/typed a symptom.
        # Offer a structured "choose_symptom" action so the UI can show the 1–9 slider.
        typed = (user_message or "").strip()
        if typed and len(typed) <= 40:
            normalized = typed.strip().lower()
            return [
                SymptomTapOption(id=f"choose_symptom::{normalized}", text=f"Log {typed}"),
                SymptomTapOption(id="other", text="✍️ Something else"),
            ]

        return []
    
    async def _generate_doctor_response(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        action_plan_context: str,
        recent_weekly_checkin_context: str,
        recent_symptom_logs_context: str,
        historical_memory_context: str = "",  # NEW: Past triggers/relief/patterns
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
        
        user_name = ctx.get("first_name", "there")
        cycle_phase = ctx["cycle_phase"]
        recent_symptoms = ctx["recent_symptoms"]
        top_symptom = ctx["highest_severity_symptom"]
        top_concern = ctx["top_concern"]
        diagnosed_conditions = ctx.get("diagnosed_conditions", [])
        conditions_str = ", ".join(diagnosed_conditions) if diagnosed_conditions else "None specified"
        
        # Build rich context blocks
        cycle_block = ""
        if cycle_phase:
            cycle_block = f"""
Phase: {cycle_phase.upper()}
Medical Insight: {self._get_cycle_insight(cycle_phase)}
"""
        
        symptom_block = ""
        if recent_symptoms:
            symptom_list = ", ".join([f"{s['symptom_type']} ({s['severity']}/9)" for s in recent_symptoms[:5]])
            symptom_block = f"""
Recent Symptoms: {symptom_list}
Most Severe: {top_symptom['symptom_type']} at {top_symptom['severity']}/9
"""
        elif top_concern:
            symptom_block = f"""
Main Concern: {top_concern}
"""
        
        summary_block = (rolling_summary or "").strip()
        recent_block = json.dumps(recent_messages[-12:], ensure_ascii=False)
        
        # Build natural conversation context
        conversation_stage = "early" if user_turns <= 1 else "ongoing"
        is_first_message = user_turns == 0

        # Build simplified, data-driven prompt
        system_prompt = f"""You are Auvra, {user_name}'s personal symptom companion. You know them deeply.

WHO IS {user_name.upper()}
Name: {user_name}
Health Conditions: {conditions_str}
Top Concern: {top_concern or 'General wellness'}
Cycle Phase: {cycle_phase or 'Unknown'}
{cycle_block}
{symptom_block}

WHAT {user_name.upper()} HAS SHARED BEFORE (HISTORICAL MEMORY)
{historical_memory_context or "No previous conversations yet."}

TODAY'S ACTION PLAN
{action_plan_context or "Not set yet."}

CONVERSATION SO FAR
{summary_block or "Just starting."}

HOW TO BE A GREAT SYMPTOM COMPANION
- Use {user_name}'s name naturally
- Reference their conditions: "With your {conditions_str}..."
- Connect symptoms to cycle: "In {cycle_phase or 'your'} phase..."
- If they mention a symptom, ask about severity (mild/moderate/severe)
- If they share severity, ask about triggers
- If they share triggers, offer a helpful tip
- Celebrate improvements, empathize with struggles
- Keep messages SHORT (2 sentences max each)

{"🌟 FIRST MESSAGE! Greet warmly: 'Hey " + user_name + "! 💜 How are your symptoms today?'" if is_first_message else "Continue the conversation naturally."}

CRITICAL RULES
1. ALWAYS provide 3-5 tap_options that continue the conversation
2. Only set is_complete: true when user says bye/thanks/done
3. Never leave tap_options empty unless completing

RESPONSE FORMAT (STRICT JSON)
{{
    "messages": ["Short message 1", "Short message 2"],
    "tap_options": [{{"id": "opt1", "text": "😊 Option 1"}}, {{"id": "opt2", "text": "😐 Option 2"}}],
    "is_complete": false,
    "insights": {{"progress": null, "symptoms_mentioned": [], "triggers_today": [], "relief_today": []}}
}}

CONVERSATION HISTORY:
{recent_block}

{user_name.upper()}'S MESSAGE: {user_message}
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

            # CRITICAL: Ensure tap_options are ALWAYS present to keep conversation flowing
            # Only allow empty tap_options if the LLM explicitly marked is_complete
            if not parsed.is_complete and not parsed.tap_options:
                parsed.tap_options = self._default_conversational_tap_options(user_message=user_message, ctx=ctx)
            
            return parsed, model_used
            
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[SymptomCheckInAI] Parse error: {e}")
            return self._get_fallback_response(ctx, user_turns), model_used
    
    def _default_conversational_tap_options(self, *, user_message: str, ctx: Dict[str, Any]) -> List[SymptomTapOption]:
        """Good conversational tap options that keep the chat flowing.
        
        These are NEVER just UI-triggering actions - they're real conversation continuers.
        """
        return [
            SymptomTapOption(id="feeling_better", text="😊 Feeling better today"),
            SymptomTapOption(id="about_same", text="😐 About the same"),
            SymptomTapOption(id="feeling_worse", text="😟 Feeling worse"),
            SymptomTapOption(id="share_something", text="💬 I want to share something"),
            SymptomTapOption(id="get_tips", text="💡 Give me some tips"),
        ]
    
    def _get_fallback_response(
        self,
        ctx: Dict[str, Any],
        user_turns: int,
    ) -> SymptomAIResponse:
        """Context-aware fallback response - always provides good conversational options."""
        
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
        
        # Always provide good conversational tap options
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
                        SymptomTapOption(id="varies", text="🔄 It comes and goes"),
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
                        SymptomTapOption(id="varies", text="🔄 It comes and goes"),
                    ],
                    is_complete=False,
                )
        else:
            return SymptomAIResponse(
                messages=[
                    f"Got it, {user_name}! 💜",
                    "Tell me more - what's bothering you most today?"
                ],
                tap_options=[
                    SymptomTapOption(id="feeling_better", text="😊 Feeling better today"),
                    SymptomTapOption(id="about_same", text="😐 About the same"),
                    SymptomTapOption(id="feeling_worse", text="😟 Feeling worse"),
                    SymptomTapOption(id="share_symptom", text="🩺 Share a symptom"),
                    SymptomTapOption(id="get_tips", text="💡 Give me some tips"),
                ],
                is_complete=False,
            )
