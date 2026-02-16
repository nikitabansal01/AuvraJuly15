"""
Conversation Memory Management
Prevents token limit errors and context overflow.

Based on patterns from:
- ChatGPT: Sliding window of last 8,000 tokens
- Claude: Automatic summarization after 10k tokens
- LangChain: ConversationSummaryMemory

Strategies:
1. Message Count Limit: Keep last N messages
2. Token Budget: Truncate when exceeding token limit
3. Summarization: Compress old messages (Phase 3)
"""

import logging
from typing import List, Dict, Any, Optional

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    tiktoken = None

logger = logging.getLogger(__name__)

# Token estimation (rough approximation: 1 token ≈ 4 characters)
CHARS_PER_TOKEN = 4


def count_tokens_approx(text: str) -> int:
    """
    Approximate token count without tiktoken overhead.
    
    For production use, this is faster than tiktoken for every message.
    Accuracy: ~90% (good enough for budgeting).
    """
    return len(text) // CHARS_PER_TOKEN


def count_tokens_exact(text: str, model: str = "gpt-4o") -> int:
    """
    Exact token count using tiktoken.
    
    Use sparingly (has overhead). Prefer count_tokens_approx() for budgeting.
    Falls back to approximation if tiktoken is not available.
    """
    if not TIKTOKEN_AVAILABLE:
        logger.debug("tiktoken not available, using approximation")
        return count_tokens_approx(text)
    try:
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(f"Tiktoken error: {e}. Using approximation.")
        return count_tokens_approx(text)


def truncate_conversation_by_count(
    messages: List[Dict[str, str]],
    max_messages: int = 20,
    preserve_system: bool = True
) -> List[Dict[str, str]]:
    """
    Keep only the last N messages.
    
    Args:
        messages: List of message dicts [{"role": "user", "content": "..."}, ...]
        max_messages: Maximum number of messages to keep
        preserve_system: Always keep system message (index 0)
    
    Returns:
        Truncated message list
    """
    if len(messages) <= max_messages:
        return messages
    
    if preserve_system and messages and messages[0].get("role") == "system":
        # Keep system message + last (max_messages - 1)
        return [messages[0]] + messages[-(max_messages - 1):]
    else:
        # Just keep last N
        return messages[-max_messages:]


def truncate_conversation_by_tokens(
    messages: List[Dict[str, str]],
    max_tokens: int = 4000,
    preserve_system: bool = True
) -> List[Dict[str, str]]:
    """
    Keep messages within token budget, starting from most recent.
    
    Args:
        messages: List of message dicts
        max_tokens: Maximum total tokens
        preserve_system: Always keep system message
    
    Returns:
        Truncated message list
    """
    if not messages:
        return []
    
    # Separate system message if present
    system_msg = None
    if preserve_system and messages[0].get("role") == "system":
        system_msg = messages[0]
        messages = messages[1:]
    
    # Count tokens backwards (most recent first)
    result = []
    total_tokens = 0
    
    if system_msg:
        system_tokens = count_tokens_approx(system_msg.get("content", ""))
        total_tokens += system_tokens
    
    # Add messages from most recent
    for msg in reversed(messages):
        content = msg.get("content", "")
        msg_tokens = count_tokens_approx(content)
        
        if total_tokens + msg_tokens > max_tokens:
            break  # Would exceed budget
        
        result.insert(0, msg)
        total_tokens += msg_tokens
    
    # Reconstruct with system message
    if system_msg:
        return [system_msg] + result
    return result


def truncate_conversation_smart(
    messages: List[Dict[str, str]],
    max_messages: int = 20,
    max_tokens: int = 4000,
    preserve_system: bool = True
) -> List[Dict[str, str]]:
    """
    Apply both message count and token limits.
    
    This is the recommended truncation strategy for production.
    
    Args:
        messages: List of message dicts
        max_messages: Maximum number of messages
        max_tokens: Maximum total tokens
        preserve_system: Always keep system message
    
    Returns:
        Truncated message list
    """
    # First truncate by count (fast)
    messages = truncate_conversation_by_count(messages, max_messages, preserve_system)
    
    # Then truncate by tokens (more expensive)
    messages = truncate_conversation_by_tokens(messages, max_tokens, preserve_system)
    
    return messages


def get_conversation_stats(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Get statistics about conversation memory.
    
    Useful for monitoring and debugging.
    
    Returns:
        {
            "message_count": int,
            "total_tokens_approx": int,
            "user_messages": int,
            "assistant_messages": int,
            "system_messages": int
        }
    """
    total_tokens = 0
    role_counts = {"user": 0, "assistant": 0, "system": 0, "other": 0}
    
    for msg in messages:
        content = msg.get("content", "")
        total_tokens += count_tokens_approx(content)
        
        role = msg.get("role", "other")
        role_counts[role] = role_counts.get(role, 0) + 1
    
    return {
        "message_count": len(messages),
        "total_tokens_approx": total_tokens,
        "user_messages": role_counts["user"],
        "assistant_messages": role_counts["assistant"],
        "system_messages": role_counts["system"]
    }


# ═══════════════════════════════════════════════════════════════════════════
# Summarization (Phase 3 - Advanced Feature)
# ═══════════════════════════════════════════════════════════════════════════

async def summarize_old_messages(
    messages: List[Dict[str, str]],
    llm_func
) -> str:
    """
    Compress old messages into a summary.
    
    This is an advanced feature for Phase 3. For now, simple truncation is sufficient.
    
    Args:
        messages: List of old messages to summarize
        llm_func: LLM function to use for summarization
    
    Returns:
        Summary string
    """
    if not messages:
        return ""
    
    # Extract key facts from old messages
    conversation_text = "\n".join([
        f"{msg.get('role', 'unknown')}: {msg.get('content', '')}"
        for msg in messages
    ])
    
    summary_prompt = f"""Summarize this conversation history in 3-4 sentences, preserving key facts about the user's health, preferences, and action plan progress:

{conversation_text[:2000]}

Focus on:
1. User's health concerns and goals
2. Actions they've completed or skipped
3. Barriers or preferences mentioned
4. Any important context for future responses

Keep the summary factual and concise."""
    
    try:
        summary = await llm_func(summary_prompt, max_tokens=150, temperature=0.1)
        return f"[Previous conversation summary: {summary}]"
    except Exception as e:
        logger.error(f"Summarization error: {e}")
        return "[Previous conversation context unavailable]"
