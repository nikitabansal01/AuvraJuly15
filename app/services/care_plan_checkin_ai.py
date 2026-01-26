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
    
    def _extract_user_info(self, user_profile_context: str) -> Dict[str, Any]:
        """Extract key user info for personalization."""
        info = {
            "name": "there",
            "first_name": "there",
            "conditions": [],
            "top_concern": None,
            "cycle_phase": None,
        }
        
        if not user_profile_context:
            return info
            
        for line in user_profile_context.split("\n"):
            line = line.strip()
            if line.startswith("name="):
                full_name = line.split("=", 1)[1].strip()
                info["name"] = full_name
                info["first_name"] = full_name.split()[0] if full_name else "there"
            elif line.startswith("top_concern="):
                info["top_concern"] = line.split("=", 1)[1].strip()
            elif line.startswith("diagnosed_conditions="):
                cond_str = line.split("=", 1)[1].strip()
                if cond_str and cond_str != "[]":
                    try:
                        info["conditions"] = json.loads(cond_str)
                    except:
                        info["conditions"] = [c.strip() for c in cond_str.split(",") if c.strip()]
            elif "phase" in line.lower():
                # Try to detect cycle phase
                if "luteal" in line.lower():
                    info["cycle_phase"] = "luteal"
                elif "follicular" in line.lower():
                    info["cycle_phase"] = "follicular"
                elif "ovulation" in line.lower() or "ovulat" in line.lower():
                    info["cycle_phase"] = "ovulation"
                elif "menstrual" in line.lower() or "period" in line.lower():
                    info["cycle_phase"] = "menstrual"
        
        return info

    async def generate_reply(
        self,
        *,
        user_message: str,
        user_profile_context: str,
        action_plan_context: str,
        recent_symptom_checkin_context: str,
        recent_symptom_logs_context: str,
        historical_memory_context: str = "",  # NEW: Past wins, blockers, triggers
        rolling_summary: Optional[str],
        recent_messages: List[Dict[str, Any]],
    ) -> Tuple[CarePlanAIResponse, str]:
        """Generate the next assistant messages, tap suggestions, and insights."""

        summary_block = rolling_summary.strip() if rolling_summary else ""
        recent_block = json.dumps(recent_messages[-20:], ensure_ascii=False)
        
        # Extract user info for personalization
        user_info = self._extract_user_info(user_profile_context)
        user_name = user_info["first_name"]
        conditions_str = ", ".join(user_info["conditions"]) if user_info["conditions"] else "None specified"
        top_concern = user_info.get("top_concern") or "general wellness"
        cycle_phase = user_info.get("cycle_phase") or "unknown"
        
        # Determine conversation stage
        is_first_message = len(recent_messages) == 0

        prompt = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  🧑‍⚕️ YOU ARE TALKING TO: {user_name.upper():^52} ║
║  CONDITIONS: {conditions_str[:50]:^55} ║
║  TOP CONCERN: {top_concern[:50]:^54} ║
║  CYCLE PHASE: {cycle_phase.upper():^54} ║
╚══════════════════════════════════════════════════════════════════════════════╝

You are Auvra, {user_name}'s personal women's health coach who KNOWS them deeply.
You are NOT a generic chatbot - you are {user_name}'s trusted wellness companion.

═══════════════════════════════════════════════════════════════════════════════
YOUR PERSONALITY (Warm, Personal, Empowering)
═══════════════════════════════════════════════════════════════════════════════

• USE {user_name}'S NAME naturally (especially in greetings and celebrations)
• REFERENCE their specific conditions when relevant: "{user_name}, with your {conditions_str}..."
• CELEBRATE wins genuinely: "That's amazing, {user_name}! 🎉"
• EMPATHIZE with struggles: "I hear you, {user_name}. {cycle_phase} phase can be tough..."
• KEEP IT SHORT: 1-2 sentences per message bubble, max 3 bubbles total

═══════════════════════════════════════════════════════════════════════════════
PERSONALIZATION RULES (CRITICAL!)
═══════════════════════════════════════════════════════════════════════════════

{"🌟 THIS IS YOUR FIRST MESSAGE! Start with: 'Hey {user_name}! 💜'" if is_first_message else "Continue the conversation warmly, use their name occasionally"}

• ALWAYS connect advice to THEIR specific situation:
  ❌ WRONG: "Try eating more protein"
  ✅ RIGHT: "{user_name}, with your {top_concern}, adding some salmon today could really help! 🐟"

• REFERENCE their cycle phase naturally:
  - Luteal: "Since you're in luteal phase, you might be craving comfort..."
  - Menstrual: "During your period, gentle movement like walking is perfect..."
  - Follicular: "Your energy is building now - great time to tackle that workout!"
  - Ovulation: "You're at peak energy! Let's use that momentum 💪"

• ACKNOWLEDGE their conditions when giving advice:
  - PCOS: "This will help with insulin sensitivity"
  - Endometriosis: "Gentle on inflammation"
  - Thyroid: "Good for your metabolism"

═══════════════════════════════════════════════════════════════════════════════
TASK: Daily Care Plan Check-in
═══════════════════════════════════════════════════════════════════════════════

• Help {user_name} review and adjust their daily wellness plan
• Be brief and chatty (like a supportive friend)
• Ask at most ONE follow-up question
• Provide 2-4 tap options when helpful
• Extract actionable insights for plan updates
• When suggesting alternatives, keep SAME CATEGORY (food→food, movement→movement)

╔══════════════════════════════════════════════════════════════════════════════╗
║  CRITICAL: DO NOT SUGGEST DUPLICATES!                                        ║
║  • NEVER suggest actions that are ALREADY in TODAY'S ACTION PLAN below       ║
║  • Make alternatives DIFFERENT from existing plan items                       ║
╚══════════════════════════════════════════════════════════════════════════════╝

═══════════════════════════════════════════════════════════════════════════════
RESPONSE FORMAT (STRICT JSON)
═══════════════════════════════════════════════════════════════════════════════

Return EXACTLY this JSON format:
{{
  "messages": [
    "First bubble - greeting/acknowledgment (use {user_name}!)",
    "Second bubble - question or advice (short!)"
  ],
  "tap_options": [
    {{"id": "option1", "text": "✅ Emoji + clear action"}},
    {{"id": "option2", "text": "🔄 Change something"}},
    {{"id": "option3", "text": "💡 Get a tip"}}
  ],
  "insights": {{
    "plan_changes_requested": ["list changes user wants"],
    "actions_to_skip": ["items user wants to skip"],
    "alternate_suggestions_requested": true|false,
    "selected_item_title": "exact title from plan if user mentioned one",
    "user_action": "confirm|cancel|select_item|general_chat",
    "wins": ["things user accomplished"],
    "blockers": ["struggles user mentioned"],
    "preferences": ["preferences expressed"],
    "key_takeaway": "one sentence summary"
  }}
}}

═══════════════════════════════════════════════════════════════════════════════
CONTEXT DATA
═══════════════════════════════════════════════════════════════════════════════

USER PROFILE:
{user_profile_context}

TODAY'S ACTION PLAN:
{action_plan_context}

RECENT SYMPTOM CHECK-INS:
{recent_symptom_checkin_context}

RECENT SYMPTOM LOGS:
{recent_symptom_logs_context}

═══════════════════════════════════════════════════════════════════════════════
⭐ HISTORICAL MEMORY (CRITICAL - USE THIS TO PERSONALIZE!) ⭐
═══════════════════════════════════════════════════════════════════════════════
This is what {user_name} has told you in past conversations. REFERENCE THIS!

{historical_memory_context}

HOW TO USE THIS DATA:
• If they said something WORKED before → Recommend it again! "Remember how walking helped last time? Try that again today!"
• If they had BLOCKERS → Be empathetic: "I know you mentioned feeling too tired before - how about something lighter?"
• If they have TRIGGERS → Avoid them: "Since stress made your symptoms worse, let's focus on calming activities"
• If they expressed PREFERENCES → Honor them: "You mentioned liking yoga - here's a gentle option!"
═══════════════════════════════════════════════════════════════════════════════

CONVERSATION SUMMARY:
{summary_block or "Fresh conversation"}

RECENT MESSAGES:
{recent_block}

═══════════════════════════════════════════════════════════════════════════════
{user_name.upper()}'S MESSAGE:
{user_message}
═══════════════════════════════════════════════════════════════════════════════
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
# Production-hardened with retries, timeouts, and edge case handling
# ═══════════════════════════════════════════════════════════════════════════════

from openai import AsyncOpenAI
from openai import APIError, APITimeoutError, RateLimitError
import os
import asyncio

# OpenAI client for tool calling
_openai_client = None

def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=10.0,  # 10 second timeout
            max_retries=2  # Built-in retry for transient errors
        )
    return _openai_client


# Tool definitions for intent classification
INTENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "classify_user_intent",
            "description": "Classify the user's intent and optionally select an item from the available options",
            "strict": True,  # Enforce strict schema validation
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": ["select_item", "select_candidate", "confirm", "cancel", "want_change", "want_alternates", "change_different_item", "general_chat"],
                        "description": "User's primary intent"
                    },
                    "selected_index": {
                        "type": ["integer", "null"],
                        "description": "0-based index of selected item/candidate if user is selecting one, null otherwise"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence in the classification (0.0 to 1.0)"
                    }
                },
                "required": ["intent", "selected_index", "confidence"],
                "additionalProperties": False
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
    """
    Production-hardened LLM TOOL CALLING for semantic matching.
    
    Features:
    - Retry logic with exponential backoff
    - Timeout handling (10s default)
    - Input validation and sanitization
    - Edge case handling (empty inputs, single item, etc.)
    - Graceful degradation on API failures
    - Index bounds validation
    """
    
    MAX_RETRIES = 2
    RETRY_DELAY = 0.5  # seconds
    
    @staticmethod
    def _validate_index(index: Optional[int], max_length: int) -> Optional[int]:
        """Validate and clamp index to valid range."""
        if index is None:
            return None
        if not isinstance(index, int):
            return None
        if max_length == 0:
            return None
        # Clamp to valid range
        return max(0, min(index, max_length - 1))
    
    @staticmethod
    def _sanitize_message(message: str) -> str:
        """Sanitize user message for LLM input."""
        if not message:
            return ""
        # Truncate very long messages
        message = str(message).strip()[:500]
        return message
    
    @staticmethod
    async def classify_intent(
        user_message: str,
        available_items: List[Dict[str, Any]] = None,
        available_candidates: List[Dict[str, Any]] = None,
        current_context: str = "general"
    ) -> UserIntentClassification:
        """
        Use LLM FUNCTION CALLING to classify user's intent.
        
        Production-ready with:
        - Input validation
        - Retry logic for transient failures
        - Timeout handling
        - Index bounds validation
        - Graceful degradation
        """
        # Input validation
        user_message = CarePlanSemanticMatcher._sanitize_message(user_message)
        if not user_message:
            return UserIntentClassification(intent="general_chat", confidence=0.1)
        
        available_items = available_items or []
        available_candidates = available_candidates or []
        
        # Build context with categories for better semantic matching
        items_list = ""
        if available_items:
            items_list = "\n".join([
                f"{i}. [{item.get('category', 'general').upper()}] {(item.get('title') or 'Unknown')[:60]}"
                for i, item in enumerate(available_items[:10])  # Limit to 10 items
            ])
        
        candidates_list = ""
        if available_candidates:
            candidates_list = "\n".join([
                f"{i}. {(c.get('title') or c.get('specific_action') or 'Unknown')[:60]}"
                for i, c in enumerate(available_candidates[:10])
            ])
        
        system_prompt = """You are an intent classifier for a wellness app. 
Analyze the user's message and call the classify_user_intent function.

INTENT MEANINGS:
- select_item: User is selecting a specific action from their plan (set selected_index)
- select_candidate: User is choosing a replacement option (set selected_index)
- confirm: User is agreeing (yes, ok, sure, sounds good, do it, perfect, etc.)
- cancel: User is declining (no, nevermind, forget it, skip, cancel, etc.)
- want_change: User wants to change/replace something in their plan (e.g., "change food", "don't like this")
- want_alternates: User wants to see alternatives/suggestions
- change_different_item: User is viewing replacement options but wants to change a DIFFERENT item instead
  Examples: "I want to change other thing", "change different item", "not this one, something else"
  "I don't want to change running, I want to change food", "wrong item", "different action"
- general_chat: User is just chatting, not making a specific action

MATCHING RULES:
- "first", "1", "option 1" → selected_index: 0
- "second", "2" → selected_index: 1
- "third", "3" → selected_index: 2
- "last one" → selected_index: (last available index)
- USE CATEGORIES: If user says "food" or "diet", match with [FOOD] items.
- If user says "movement", "exercise", "workout", match with [MOVEMENT] items.
- If user mentions a word from an item title, match that item.
- Be generous/smart - match "food" to "Eat protein", "run" to "Movement".
- If user says "change [item]", use intent: want_change + selected_index of that item.
- If user says "other thing", "different item", "not this", use intent: change_different_item
- Only use general_chat if truly uncertain."""

        context_msg = f"Context: {current_context}"
        if items_list:
            context_msg += f"\n\nPlan items:\n{items_list}"
        if candidates_list:
            context_msg += f"\n\nReplacement options:\n{candidates_list}"
        
        # Retry loop
        last_error = None
        for attempt in range(CarePlanSemanticMatcher.MAX_RETRIES + 1):
            try:
                client = _get_openai_client()
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model="gpt-5-mini",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"{context_msg}\n\nUser: \"{user_message}\""}
                        ],
                        tools=INTENT_TOOLS,
                        tool_choice={"type": "function", "function": {"name": "classify_user_intent"}},
                        temperature=0.1  # Low temperature for consistent classification
                    ),
                    timeout=12.0  # Overall timeout including retries
                )
                
                # Extract and validate tool call result
                if response.choices and response.choices[0].message.tool_calls:
                    tool_call = response.choices[0].message.tool_calls[0]
                    if tool_call.function.name == "classify_user_intent":
                        args = json.loads(tool_call.function.arguments)
                        
                        # Validate intent
                        intent = args.get("intent", "general_chat")
                        valid_intents = ["select_item", "select_candidate", "confirm", "cancel", "want_change", "want_alternates", "change_different_item", "general_chat"]
                        if intent not in valid_intents:
                            intent = "general_chat"
                        
                        # Validate and clamp index - keep it if provided for any selection-related intent
                        selected_index = args.get("selected_index")
                        if selected_index is not None:
                            if available_items:
                                selected_index = CarePlanSemanticMatcher._validate_index(selected_index, len(available_items))
                            elif available_candidates:
                                selected_index = CarePlanSemanticMatcher._validate_index(selected_index, len(available_candidates))
                            else:
                                selected_index = None
                        
                        # Validate confidence
                        confidence = args.get("confidence", 0.8)
                        if not isinstance(confidence, (int, float)):
                            confidence = 0.8
                        confidence = max(0.0, min(1.0, float(confidence)))
                        
                        logger.info(f"[SemanticMatcher] Tool call: intent={intent}, index={selected_index}, conf={confidence:.2f}")
                        return UserIntentClassification(
                            intent=intent,
                            selected_index=selected_index,
                            confidence=confidence
                        )
                
            except asyncio.TimeoutError:
                last_error = "Timeout"
                logger.warning(f"[SemanticMatcher] Timeout on attempt {attempt + 1}")
            except RateLimitError as e:
                last_error = f"Rate limit: {e}"
                logger.warning(f"[SemanticMatcher] Rate limited, attempt {attempt + 1}")
                await asyncio.sleep(CarePlanSemanticMatcher.RETRY_DELAY * (attempt + 1))
            except (APIError, APITimeoutError) as e:
                last_error = f"API error: {e}"
                logger.warning(f"[SemanticMatcher] API error on attempt {attempt + 1}: {e}")
            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                logger.warning(f"[SemanticMatcher] Failed to parse tool call args: {e}")
            except Exception as e:
                last_error = f"Unexpected: {e}"
                logger.error(f"[SemanticMatcher] Unexpected error: {e}")
                break  # Don't retry on unexpected errors
            
            if attempt < CarePlanSemanticMatcher.MAX_RETRIES:
                await asyncio.sleep(CarePlanSemanticMatcher.RETRY_DELAY)
        
        logger.warning(f"[SemanticMatcher] All attempts failed, last error: {last_error}")
        return UserIntentClassification(intent="general_chat", selected_index=None, confidence=0.2)
    
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
            
        user_text_lower = (user_message or "").strip().lower()
        
        # 1. TEXT MATCHING FIRST (Deterministic & Fast)
        # If user types the name of an item, match it immediately without LLM
        if len(user_text_lower) > 2:
            # Check for exact matches first
            for item in items:
                item_title = (item.get("title") or "").strip().lower()
                if item_title == user_text_lower:
                    logger.info(f"[CarePlanSemanticMatcher] Exact match found: '{item.get('title')}'")
                    return item.get("item_id")
            
            # Check for substring matches
            for item in items:
                item_title = (item.get("title") or "").strip().lower()
                if user_text_lower in item_title or item_title in user_text_lower:
                    logger.info(f"[CarePlanSemanticMatcher] Substring match found: '{user_message}' -> '{item.get('title')}'")
                    return item.get("item_id")
        
        # 2. Try LLM-based classification if text match failed
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            available_items=items,
            current_context="choose_item"
        )
        
        # Relaxed intent check: If ANY valid index was returned, trust it.
        # The user might say "replace Pilates" (intent=want_change) but we know it refers to item 3
        if classification.selected_index is not None:
             if 0 <= classification.selected_index < len(items):
                logger.info(f"[CarePlanSemanticMatcher] LLM selected index {classification.selected_index} (intent={classification.intent})")
                return items[classification.selected_index].get("item_id")
        
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
        
        user_text_lower = (user_message or "").strip().lower()
        
        # 1. TEXT MATCHING FIRST (Deterministic & Fast)
        if len(user_text_lower) > 2:
            # Check for exact matches first
            for candidate in candidates_list:
                candidate_title = (candidate.get("title") or candidate.get("specific_action") or "").strip().lower()
                if candidate_title and candidate_title == user_text_lower:
                    logger.info(f"[CarePlanSemanticMatcher] Candidate exact match: '{candidate_title}'")
                    return candidate["id"]
            
            # Check for substring matches
            for candidate in candidates_list:
                candidate_title = (candidate.get("title") or candidate.get("specific_action") or "").strip().lower()
                if candidate_title and (user_text_lower in candidate_title or candidate_title in user_text_lower):
                    logger.info(f"[CarePlanSemanticMatcher] Candidate substring match: '{user_message}' -> '{candidate_title}'")
                    return candidate["id"]
        
        # 2. Try LLM-based classification if text match failed
        classification = await CarePlanSemanticMatcher.classify_intent(
            user_message=user_message,
            available_candidates=candidates_list,
            current_context="choose_candidate"
        )
        
        # Relaxed intent check: If ANY valid index was returned, trust it.
        if classification.selected_index is not None:
            if 0 <= classification.selected_index < len(candidates_list):
                logger.info(f"[CarePlanSemanticMatcher] LLM selected candidate index {classification.selected_index} (intent={classification.intent})")
                return candidates_list[classification.selected_index]["id"]
        
        # If user confirms without selection, use first candidate
        if classification.intent == "confirm" and classification.confidence > 0.6:
            if candidates_list:
                logger.info(f"[CarePlanSemanticMatcher] User confirmed, selecting first candidate")
                return candidates_list[0]["id"]
        
        return None
        
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


async def generate_contextual_bot_response(
    user_message: str,
    situation: str,
    context_data: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> str:
    """
    Generate natural, contextual bot responses using LLM.
    
    Args:
        user_message: What the user just said
        situation: Current situation code (showing_alternatives, replacement_complete, no_match_item, no_match_candidate)
        context_data: Dict with situation-specific data (matched_title, category, candidates, etc.)
        conversation_history: Recent conversation messages
        
    Returns:
        Natural, contextual bot response string
    """
    import asyncio
    from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError
    import os
    
    # Build situation-specific prompts
    situation_prompts = {
        "showing_alternatives": """The user wants to change an item in their wellness plan. You matched their request to a specific item and now have alternatives ready.

User said: "{user_message}"
Matched item: {matched_title} (Category: {category})
Actual alternatives generated: {alternative_names}

Generate a warm, natural response that:
- Acknowledges what they want to change
- Confirms you found the right item
- DO NOT mention specific alternative names (they'll see them as buttons below)
- Just say something like "I found some great options for you" or "Here are a few swaps"
- Keep it SHORT (1-2 sentences). Be warm, not robotic.

CRITICAL: Do NOT list alternative names in your response. The user will see them as buttons.""",

        "replacement_complete": """The user just selected a replacement for their wellness plan item, and it has been successfully replaced.

User said: "{user_message}"
Old item: {old_title}
New item: {new_title}
Category: {category}

Generate an enthusiastic, confirming response that:
- Celebrates the change
- Confirms what was replaced
- Makes them feel good about personalizing their plan

Keep it SHORT (1 sentence). Add an emoji if appropriate.""",

        "no_match_item": """The user tried to select an item to change, but you couldn't match what they said to any item in their plan.

User said: "{user_message}"
Available items: {num_items}

Generate a helpful, friendly response that:
- Acknowledges you didn't catch that
- Asks them to pick from the visible options or rephrase
- Stays encouraging, not frustrating

Keep it SHORT (1 sentence).""",

        "no_match_candidate": """The user tried to pick a replacement option, but you couldn't match what they said to the available candidates.

User said: "{user_message}"
Available candidates: {num_candidates}

Generate a helpful, gentle response that:
- Says you didn't catch their choice
- Guides them to tap an option or rephrase
- Keeps the tone light

Keep it SHORT (1 sentence)."""
    }
    
    # Get situation-specific prompt
    prompt_template = situation_prompts.get(situation, "")
    if not prompt_template:
        # Fallback to generic response
        return "I'm here to help! What would you like to do?"
    
    # Fill in template with context data
    try:
        prompt = prompt_template.format(
            user_message=user_message,
            **context_data
        )
    except KeyError as e:
        logger.warning(f"Missing context data for situation {situation}: {e}")
        return "Let me know what you'd like to do!"
    
    # Build conversation context if available
    context_msg = ""
    if conversation_history and len(conversation_history) > 0:
        recent = conversation_history[-3:]  # Last 3 messages
        context_msg = "\n\nRecent conversation:\n" + "\n".join([
            f"{msg.get('role', 'unknown')}: {msg.get('content', '')[:80]}"
            for msg in recent
        ])
    
    system_prompt = """You are Auvra, a warm, empathetic wellness assistant helping women with their personalized action plans.

Your tone is:
- Warm and encouraging
- Natural and conversational (like a supportive friend, not a robot)
- Brief and to-the-point
- Celebratory when they make changes

CRITICAL: Keep responses SHORT (1-2 sentences max). No long explanations."""
    
    try:
        client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            timeout=8.0,
            max_retries=2
        )
        
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt + context_msg}
                ],
                temperature=0.7,  # Slightly higher for natural variation
                max_completion_tokens=60  # Force brevity
            ),
            timeout=10.0
        )
        
        if response.choices and response.choices[0].message.content:
            generated = response.choices[0].message.content.strip()
            logger.info(f"[ContextualResponse] Generated for {situation}: {generated[:80]}")
            return generated
        
    except asyncio.TimeoutError:
        logger.warning(f"[ContextualResponse] Timeout generating response for {situation}")
    except (APIError, APITimeoutError, RateLimitError) as e:
        logger.warning(f"[ContextualResponse] API error: {e}")
    except Exception as e:
        logger.error(f"[ContextualResponse] Unexpected error: {e}")
    
    # Fallback responses if LLM fails
    fallbacks = {
        "showing_alternatives": f"Here are some alternatives for {context_data.get('matched_title', 'that item')}. Pick the one you like!",
        "replacement_complete": f"Done! Your plan now has {context_data.get('new_title', 'the new option')} 🎉",
        "no_match_item": "I didn't catch that. Could you pick from the options shown or rephrase?",
        "no_match_candidate": "Hmm, I didn't get that. Please tap one of the choices below!"
    }
    
    return fallbacks.get(situation, "Let me know what you'd like to do!")
