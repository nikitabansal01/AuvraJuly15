"""Daily Care Plan Check-in AI engine.

This powers a lightweight, ongoing daily chat thread that:
- references the user's current action plan
- captures blockers/wins/requests (skip/change/alternates)
- maintains a rolling summary (sliding window) for long threads

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


class CarePlanInsights(BaseModel):
    """Actionable signals for plan updates/replacements."""

    # User-requested changes
    plan_changes_requested: List[str] = Field(default_factory=list)
    actions_to_skip: List[str] = Field(default_factory=list)
    alternate_suggestions_requested: bool = False
    
    # Specific item user is referring to (extracted from their message)
    # Should match exactly one of the item titles from the action plan
    selected_item_title: Optional[str] = None
    
    # User action intent detected from message
    # Values: "confirm" (yes/ok), "cancel" (no/nevermind), "select_item", "general_chat"
    user_action: Optional[str] = None

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
You are Auvra, a warm, practical health coach.

Task: Continue a DAILY Care Plan Check-in chat.
- Reference the user's current action plan.
- Be brief and chatty.
- Ask at most ONE follow-up question.
- Provide 0-3 suggested tap replies when helpful (e.g., change, alternate).
- Extract actionable insights for plan updates.
- **IMPORTANT: When suggesting alternatives, keep the SAME CATEGORY (food→food, movement→movement, mindfulness→mindfulness).**

Safety:
- No diagnosis.
- No medical emergencies guidance.
- Keep advice general and habit-focused.

Return STRICT JSON only with this schema:
{{
  "messages": ["string", ...],
  "tap_options": [{{"id": "string", "text": "string"}}],
  "insights": {{
    "plan_changes_requested": ["string"],
    "actions_to_skip": ["string"],
    "alternate_suggestions_requested": true|false,
    "selected_item_title": "string|null",
    "user_action": "confirm|cancel|select_item|general_chat|null",
    "wins": ["string"],
    "blockers": ["string"],
    "preferences": ["string"],
    "key_takeaway": "string|null"
  }}
}}

IMPORTANT RULES:
1. If the user mentions a SPECIFIC action item from their plan (like "walking", "salmon", "broccoli", etc), 
   set "selected_item_title" to the EXACT title from TODAY'S ACTION PLAN above.
2. Set "user_action" based on user's intent:
   - "confirm": user says yes, ok, sure, do it, go ahead, sounds good, I'll take it
   - "cancel": user says no, nevermind, cancel, skip, forget it
   - "select_item": user is selecting/mentioning a specific action item
   - "general_chat": user is just chatting, not making a specific selection

USER PROFILE CONTEXT:
{user_profile_context}

TODAY'S ACTION PLAN:
{action_plan_context}

RECENT SYMPTOM CHECK-INS (daily; if available):
{recent_symptom_checkin_context}

RECENT SYMPTOM LOGS (structured; if available):
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


# ═══════════════════════════════════════════════════════════════════════════════
# LLM-Based Semantic Matching with TOOL CALLING
# Uses OpenAI function calling for reliable structured output
# ═══════════════════════════════════════════════════════════════════════════════

from openai import AsyncOpenAI
import os

# OpenAI client for tool calling
_openai_client = None

def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


# Tool definitions for intent classification
INTENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "classify_user_intent",
            "description": "Classify the user's intent and optionally select an item from the available options",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": ["select_item", "select_candidate", "confirm", "cancel", "want_change", "want_alternates", "general_chat"],
                        "description": "User's primary intent"
                    },
                    "selected_index": {
                        "type": ["integer", "null"],
                        "description": "0-based index of selected item/candidate if user is selecting one, null otherwise"
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence in the classification (0.0 to 1.0)"
                    }
                },
                "required": ["intent", "confidence"]
            }
        }
    }
]


class UserIntentClassification(BaseModel):
    """LLM-classified user intent from their message."""
    intent: str = Field(default="general_chat")
    selected_index: Optional[int] = Field(default=None)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CarePlanSemanticMatcher:
    """Uses LLM TOOL CALLING to semantically match user input to available options."""
    
    @staticmethod
    async def classify_intent(
        user_message: str,
        available_items: List[Dict[str, Any]] = None,
        available_candidates: List[Dict[str, Any]] = None,
        current_context: str = "general"
    ) -> UserIntentClassification:
        """
        Use LLM FUNCTION CALLING to classify user's intent and match to available options.
        
        Uses OpenAI's tool_choice="required" to force the model to call our function,
        ensuring we always get structured, typed output.
        """
        items_list = ""
        if available_items:
            items_list = "\n".join([
                f"{i}. {item.get('title', 'Unknown')}"
                for i, item in enumerate(available_items)
            ])
        
        candidates_list = ""
        if available_candidates:
            candidates_list = "\n".join([
                f"{i}. {c.get('title', c.get('specific_action', 'Unknown'))}"
                for i, c in enumerate(available_candidates)
            ])
        
        system_prompt = """You are an intent classifier for a wellness app. 
Analyze the user's message and call the classify_user_intent function with the appropriate parameters.

INTENT MEANINGS:
- select_item: User is selecting a specific action from their plan (set selected_index to the item number)
- select_candidate: User is choosing a replacement option (set selected_index to the option number)
- confirm: User is agreeing (yes, ok, sure, sounds good, do it, perfect, etc.)
- cancel: User is declining (no, nevermind, forget it, skip, cancel, etc.)
- want_change: User wants to change/replace something in their plan
- want_alternates: User wants to see alternatives/suggestions
- general_chat: User is just chatting, not making a specific action

MATCHING RULES:
- "first", "1", "the first one", "option 1" → selected_index: 0
- "second", "2", "the second one" → selected_index: 1
- "last one", "the bottom" → selected_index: (last available index)
- If user mentions a word from an item title, match that item
- Be generous - if there's any reasonable connection, make the match
- Only use general_chat if truly uncertain"""

        context_msg = f"Current context: {current_context}"
        if items_list:
            context_msg += f"\n\nAvailable plan items:\n{items_list}"
        if candidates_list:
            context_msg += f"\n\nAvailable replacement options:\n{candidates_list}"
        
        try:
            client = _get_openai_client()
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{context_msg}\n\nUser message: \"{user_message}\""}
                ],
                tools=INTENT_TOOLS,
                tool_choice={"type": "function", "function": {"name": "classify_user_intent"}}
            )
            
            # Extract tool call result
            if response.choices and response.choices[0].message.tool_calls:
                tool_call = response.choices[0].message.tool_calls[0]
                if tool_call.function.name == "classify_user_intent":
                    args = json.loads(tool_call.function.arguments)
                    logger.info(f"[CarePlanSemanticMatcher] Tool call result: {args}")
                    return UserIntentClassification(
                        intent=args.get("intent", "general_chat"),
                        selected_index=args.get("selected_index"),
                        confidence=args.get("confidence", 0.8)
                    )
        except Exception as e:
            logger.warning(f"[CarePlanSemanticMatcher] Tool calling failed: {e}")
        
        # Fallback: return uncertain classification
        return UserIntentClassification(intent="general_chat", selected_index=None, confidence=0.3)
    
    @staticmethod
    async def match_item_selection(
        user_message: str,
        items: List[Dict[str, Any]]
    ) -> Optional[int]:
        """
        Use LLM to match user's message to a plan item.
        Returns the item_id if matched, None otherwise.
        Falls back to simple text matching if LLM fails.
        """
        if not items:
            return None
        
        # Try LLM-based classification first
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            available_items=items,
            current_context="choose_item"
        )
        
        if classification.intent in ("select_item", "confirm") and classification.selected_index is not None:
            if 0 <= classification.selected_index < len(items):
                return items[classification.selected_index].get("item_id")
        
        # FALLBACK: Simple text matching if LLM didn't match
        # This ensures the flow works even if LLM is uncertain
        user_text_lower = (user_message or "").strip().lower()
        if len(user_text_lower) > 2:
            for item in items:
                item_title = (item.get("title") or "").strip().lower()
                # Match if: user text is in title, or title is in user text, or any word matches
                if (user_text_lower in item_title or 
                    item_title in user_text_lower or
                    any(word in item_title for word in user_text_lower.split() if len(word) > 2)):
                    logger.info(f"[CarePlanSemanticMatcher] Fallback match: '{user_message}' -> '{item.get('title')}'")
                    return item.get("item_id")
        
        return None
    
    @staticmethod
    async def match_candidate_selection(
        user_message: str,
        candidates_by_id: Dict[str, Any]
    ) -> Optional[str]:
        """
        Use LLM to match user's message to a replacement candidate.
        Returns the candidate_id if matched, None otherwise.
        Falls back to simple text matching if LLM fails.
        """
        if not candidates_by_id:
            return None
        
        # Convert to list for indexing
        candidates_list = [
            {"id": cid, **candidate}
            for cid, candidate in candidates_by_id.items()
        ]
        
        # Try LLM-based classification first
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            available_candidates=candidates_list,
            current_context="choose_candidate"
        )
        
        if classification.intent in ("select_candidate", "confirm") and classification.selected_index is not None:
            if 0 <= classification.selected_index < len(candidates_list):
                return candidates_list[classification.selected_index]["id"]
        
        # If user confirms without selection, use first candidate
        if classification.intent == "confirm" and classification.confidence > 0.6:
            if candidates_list:
                return candidates_list[0]["id"]
        
        # FALLBACK: Simple text matching if LLM didn't match
        user_text_lower = (user_message or "").strip().lower()
        if len(user_text_lower) > 2:
            for candidate in candidates_list:
                candidate_title = (candidate.get("title") or candidate.get("specific_action") or "").strip().lower()
                if candidate_title and (
                    user_text_lower in candidate_title or 
                    candidate_title in user_text_lower or
                    any(word in candidate_title for word in user_text_lower.split() if len(word) > 2)
                ):
                    logger.info(f"[CarePlanSemanticMatcher] Fallback candidate match: '{user_message}' -> '{candidate.get('title')}'")
                    return candidate["id"]
        
        return None
    
    @staticmethod
    async def is_cancellation(user_message: str) -> bool:
        """Check if user wants to cancel the current flow."""
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            current_context="general"
        )
        return classification.intent == "cancel" and classification.confidence > 0.5
    
    @staticmethod
    async def is_confirmation(user_message: str) -> bool:
        """Check if user is confirming."""
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            current_context="general"
        )
        return classification.intent == "confirm" and classification.confidence > 0.5
    
    @staticmethod
    async def wants_change(user_message: str) -> bool:
        """Check if user wants to change something in their plan."""
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            current_context="general"
        )
        return classification.intent == "want_change" and classification.confidence > 0.5
    
    @staticmethod
    async def wants_alternates(user_message: str) -> bool:
        """Check if user wants to see alternative suggestions."""
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            current_context="general"
        )
        return classification.intent == "want_alternates" and classification.confidence > 0.5

