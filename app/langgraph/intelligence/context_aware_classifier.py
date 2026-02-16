"""
Production-Grade Context-Aware Intent Classifier for LangGraph

This replaces keyword-based intent classification with a system that:
1. Uses conversation history and context
2. Has confidence thresholds
3. Asks for clarification when unsure
4. Logs decisions for learning

NO MORE IF-ELSE KEYWORD MATCHING.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from pydantic import BaseModel, Field

from app.langgraph.helpers.llm_client import call_llm_structured, call_llm
from app.core.database import get_db, ChatMessage, CarePlanCheckInThread

logger = logging.getLogger(__name__)

# Configuration
CONFIDENCE_THRESHOLD = 0.70  # Below this, ask for clarification
MAX_RECENT_MESSAGES = 5      # How many messages to include in context
MAX_RECENT_INTENTS = 3       # How many previous intents to include


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class IntentClassificationResult(BaseModel):
    """Result from intent classification with confidence and reasoning."""
    intent: str
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0-1")
    reasoning: str = Field(description="Why this intent was chosen")
    targeted_action_index: Optional[int] = None
    needs_clarification: bool = False


class ConversationContext(BaseModel):
    """Rich context from conversation history."""
    recent_messages: List[Dict[str, str]] = []
    recent_intents: List[str] = []
    focused_action: Optional[Dict[str, Any]] = None
    conversation_flow: str = ""  # Summary of conversation flow


# ═══════════════════════════════════════════════════════════════════
# CONTEXT-AWARE INTENT CLASSIFIER
# ═══════════════════════════════════════════════════════════════════

class ContextAwareIntentClassifier:
    """
    Production-grade intent classifier that uses conversation context,
    not just keyword matching.
    
    Key improvements over keyword-based approach:
    1. Looks at conversation history to understand context
    2. Returns confidence scores
    3. Asks for clarification when unsure
    4. Logs decisions for learning
    """
    
    def __init__(self):
        self.confidence_threshold = CONFIDENCE_THRESHOLD
    
    async def classify_with_context(
        self,
        message: str,
        thread_id: str,
        user_id: str,
        action_items: List[Dict[str, Any]] = None,
    ) -> IntentClassificationResult:
        """
        Classify intent using full conversation context.
        
        This is the main entry point - replaces the old keyword-based classifier.
        """
        logger.info(f"[CONTEXT_CLASSIFIER] Classifying message: '{message[:50]}...'")
        
        # Build rich context from conversation history
        context = await self._build_conversation_context(
            thread_id=thread_id,
            user_id=user_id,
            action_items=action_items or []
        )
        
        logger.info(f"[CONTEXT_CLASSIFIER] Context: {len(context.recent_messages)} messages, {len(context.recent_intents)} intents")
        
        # Classify with context
        result = await self._classify_with_llm(message, context, action_items or [])
        
        logger.info(f"[CONTEXT_CLASSIFIER] Result: {result.intent} (confidence: {result.confidence:.2f})")
        
        # Check if we need clarification
        if result.confidence < self.confidence_threshold:
            logger.info(f"[CONTEXT_CLASSIFIER] Low confidence ({result.confidence:.2f}) - requesting clarification")
            result.needs_clarification = True
        
        # Log classification for learning
        await self._log_classification(thread_id, message, result)
        
        return result
    
    async def _build_conversation_context(
        self,
        thread_id: str,
        user_id: str,
        action_items: List[Dict[str, Any]]
    ) -> ConversationContext:
        """
        Build rich context from conversation history.
        
        This is what makes it production-grade vs keyword matching.
        """
        try:
            db = next(get_db())
            
            # Get thread
            thread = db.query(CarePlanCheckInThread).filter(
                CarePlanCheckInThread.id == thread_id
            ).first()
            
            if not thread:
                return ConversationContext()
            
            # Get recent messages
            raw_messages = thread.raw_messages or []
            recent_messages = raw_messages[-MAX_RECENT_MESSAGES:] if raw_messages else []
            
            # Extract recent intents from messages (if stored)
            recent_intents = []
            for msg in recent_messages:
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    # Try to extract intent from metadata if stored
                    metadata = msg.get("metadata", {})
                    if "intent" in metadata:
                        recent_intents.append(metadata["intent"])
            
            recent_intents = recent_intents[-MAX_RECENT_INTENTS:]
            
            # Determine focused action (if user was talking about a specific action)
            focused_action = self._infer_focused_action(recent_messages, action_items)
            
            # Build conversation flow summary
            flow_summary = self._summarize_conversation_flow(recent_messages, recent_intents)
            
            return ConversationContext(
                recent_messages=recent_messages,
                recent_intents=recent_intents,
                focused_action=focused_action,
                conversation_flow=flow_summary
            )
            
        except Exception as e:
            logger.error(f"Error building context: {e}")
            return ConversationContext()
    
    def _infer_focused_action(
        self,
        recent_messages: List[Dict],
        action_items: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Infer which action the user is currently focused on.
        
        Examples:
        - User said "Tell me about the salmon" → focused on salmon action
        - User selected action 2 → focused on action 2
        """
        if not action_items:
            return None
        
        # Look for action mentions in recent messages
        for msg in reversed(recent_messages):
            if msg.get("role") != "user":
                continue
            
            content = msg.get("content", "").lower()
            
            # Check if user mentioned a specific action by name
            for i, action in enumerate(action_items):
                title = action.get("title", "").lower()
                if title and title in content:
                    return {"index": i, **action}
            
            # Check if user mentioned by number ("action 2", "number 3", etc.)
            import re
            number_match = re.search(r'(?:action|number|item)\s+(\d+)', content)
            if number_match:
                action_num = int(number_match.group(1))
                if 1 <= action_num <= len(action_items):
                    return {"index": action_num - 1, **action_items[action_num - 1]}
        
        return None
    
    def _summarize_conversation_flow(
        self,
        recent_messages: List[Dict],
        recent_intents: List[str]
    ) -> str:
        """
        Create a summary of conversation flow.
        
        Example: "User viewed plan → asked about action 1 → now asking follow-up"
        """
        if not recent_messages:
            return "New conversation"
        
        # Build simple flow summary
        flow_parts = []
        
        if recent_intents:
            intent_str = " → ".join(recent_intents[-3:])
            flow_parts.append(f"Recent: {intent_str}")
        
        if len(recent_messages) >= 3:
            flow_parts.append(f"{len(recent_messages)} message exchange")
        
        return " | ".join(flow_parts) if flow_parts else "Ongoing conversation"
    
    async def _classify_with_llm(
        self,
        message: str,
        context: ConversationContext,
        action_items: List[Dict[str, Any]]
    ) -> IntentClassificationResult:
        """
        Use LLM to classify intent with full context.
        
        Key difference from old approach:
        - OLD: Just the message 
        - NEW: Message + conversation history + focused action + flow
        """
        # Build action list
        actions_list = "\n".join([
            f"{i+1}. {item.get('title', 'Unknown')}"
            for i, item in enumerate(action_items)
        ])
        
        # Build conversation history
        history_str = ""
        for msg in context.recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]  # Truncate long messages
            history_str +