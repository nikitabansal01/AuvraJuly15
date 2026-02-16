"""
LLM Configuration - Externalized Settings
All LLM-related configuration in one place for easy tuning without code changes.

Based on best practices from:
- OpenAI Cookbook: https://cookbook.openai.com/
- 12-Factor App: https://12factor.net/config
- LaunchDarkly patterns
"""

from pydantic_settings import BaseSettings
from typing import Optional
import os

class LLMConfig(BaseSettings):
    """
    Centralized LLM configuration with environment variable overrides.
    
    Usage:
        config = get_llm_config()
        response = await client.chat.completions.create(
            model=config.classification_model,
            temperature=config.classification_temperature,
            max_tokens=config.max_tokens_classification
        )
    
    Override via environment variables:
        export LLM_CLASSIFICATION_TEMPERATURE=0.2
        export LLM_ENABLE_CACHING=true
    """
    
    # ═══════════════════════════════════════════════════════════════════
    # Model Selection
    # ═══════════════════════════════════════════════════════════════════
    classification_model: str = "gpt-4o-mini"
    generation_model: str = "gpt-4o-mini"
    fallback_model: str = "llama-3.3-70b-versatile"  # Groq
    
    # ═══════════════════════════════════════════════════════════════════
    # Temperature Settings (0.0 = deterministic, 1.0 = creative)
    # ═══════════════════════════════════════════════════════════════════
    classification_temperature: float = 0.1  # Low for consistent intent classification
    celebration_temperature: float = 0.3     # Slightly higher for variety
    explanation_temperature: float = 0.2     # Factual but engaging
    general_temperature: float = 0.4         # More creative for general chat
    
    # ═══════════════════════════════════════════════════════════════════
    # Token Limits (cost control)
    # ═══════════════════════════════════════════════════════════════════
    max_tokens_classification: int = 100     # Intent classification (function call)
    max_tokens_celebration: int = 150        # Completion celebrations
    max_tokens_skip: int = 150               # Skip warnings
    max_tokens_alternatives_intro: int = 50  # Alternatives introduction
    max_tokens_alternatives: int = 300       # Full alternatives generation
    max_tokens_negotiate: int = 400          # Negotiation responses
    max_tokens_why: int = 250                # "Why this action?" explanations
    max_tokens_clarification: int = 250      # Clarification requests
    max_tokens_cancel: int = 100             # Cancellation acknowledgments
    max_tokens_explain_plan: int = 300       # Plan explanations
    max_tokens_challenge_science: int = 350  # Science explanations
    max_tokens_health_question: int = 250    # Health Q&A
    max_tokens_general: int = 250            # General responses
    
    # ═══════════════════════════════════════════════════════════════════
    # Timeout Settings (prevent hung requests)
    # ═══════════════════════════════════════════════════════════════════
    classification_timeout: float = 12.0     # Generous for function calling
    generation_timeout: float = 15.0         # Standard generation
    fallback_timeout: float = 10.0           # Groq is faster
    
    # ═══════════════════════════════════════════════════════════════════
    # Retry Settings
    # ═══════════════════════════════════════════════════════════════════
    max_retries: int = 3                     # Function calling retries
    base_retry_delay: float = 0.5            # Base delay in seconds
    max_retry_delay: float = 5.0             # Cap on exponential backoff
    jitter_enabled: bool = True              # Add random jitter to retries
    jitter_factor: float = 0.5               # ±50% jitter
    
    # ═══════════════════════════════════════════════════════════════════
    # Circuit Breaker Settings
    # ═══════════════════════════════════════════════════════════════════
    circuit_breaker_enabled: bool = True
    circuit_breaker_fail_max: int = 5        # Open after N failures
    circuit_breaker_timeout: int = 30        # Stay open for N seconds
    
    # ═══════════════════════════════════════════════════════════════════
    # Caching Settings
    # ═══════════════════════════════════════════════════════════════════
    enable_caching: bool = True
    cache_ttl_classification: int = 300      # 5 minutes (classification stable)
    cache_ttl_explanations: int = 3600       # 1 hour (static content)
    cache_ttl_general: int = 600             # 10 minutes (general responses)
    cache_ttl_celebrations: int = 60         # 1 minute (personalized, less cacheable)
    
    # Only cache if temperature is low (deterministic responses)
    cache_temperature_threshold: float = 0.3
    
    # ═══════════════════════════════════════════════════════════════════
    # Conversation Memory Settings
    # ═══════════════════════════════════════════════════════════════════
    max_conversation_messages: int = 20      # Keep last N messages
    max_conversation_tokens: int = 4000      # Approximate token budget
    enable_conversation_summarization: bool = False  # Phase 3 feature
    
    # ═══════════════════════════════════════════════════════════════════
    # Parallelization Settings
    # ═══════════════════════════════════════════════════════════════════
    enable_parallel_llm_calls: bool = True   # Parallel independent LLM calls
    max_parallel_calls: int = 5              # Limit concurrent OpenAI requests
    
    # ═══════════════════════════════════════════════════════════════════
    # Observability Settings
    # ═══════════════════════════════════════════════════════════════════
    enable_llm_metrics: bool = True          # Track latency, cost, errors
    log_prompts: bool = False                # Log full prompts (debug only)
    log_responses: bool = False              # Log full responses (debug only)
    
    # ═══════════════════════════════════════════════════════════════════
    # Cost Tracking
    # ═══════════════════════════════════════════════════════════════════
    track_token_usage: bool = True
    gpt4o_mini_cost_per_1k_input: float = 0.00015   # $0.15 per 1M tokens
    gpt4o_mini_cost_per_1k_output: float = 0.0006   # $0.60 per 1M tokens
    
    # ═══════════════════════════════════════════════════════════════════
    # Feature Flags (gradual rollout)
    # ═══════════════════════════════════════════════════════════════════
    enable_groq_fallback: bool = True
    enable_json_mode_fallback: bool = True
    enable_degraded_responses: bool = True   # Return safe defaults when all fails
    
    class Config:
        env_prefix = "LLM_"                 # Environment variables: LLM_CLASSIFICATION_TEMPERATURE
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


# Singleton instance
_llm_config: Optional[LLMConfig] = None


def get_llm_config() -> LLMConfig:
    """
    Get singleton LLM configuration instance.
    
    This is cached for performance - config is loaded once per process.
    To reload config (useful in development), restart the server.
    """
    global _llm_config
    if _llm_config is None:
        _llm_config = LLMConfig()
    return _llm_config


def reload_llm_config() -> LLMConfig:
    """
    Force reload configuration from environment variables.
    Useful for hot-reloading in development or feature flag updates.
    """
    global _llm_config
    _llm_config = LLMConfig()
    return _llm_config


# ═══════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════

def get_cache_ttl_for_intent(intent: str, temperature: float) -> int:
    """
    Get appropriate cache TTL based on intent type and temperature.
    
    High-temperature (creative) responses shouldn't be cached as aggressively.
    Static content (explanations) can be cached longer.
    """
    config = get_llm_config()
    
    # Don't cache if temperature is too high (non-deterministic)
    if temperature > config.cache_temperature_threshold:
        return 0  # No caching
    
    # Intent-specific TTL
    ttl_map = {
        "explain_plan": config.cache_ttl_explanations,
        "challenge_science": config.cache_ttl_explanations,
        "ask_why": config.cache_ttl_explanations,
        "complete_action": config.cache_ttl_celebrations,
        "general": config.cache_ttl_general,
    }
    
    return ttl_map.get(intent, config.cache_ttl_general)


def get_max_tokens_for_intent(intent: str) -> int:
    """Get max_tokens setting for a specific intent type."""
    config = get_llm_config()
    
    token_map = {
        "complete_action": config.max_tokens_celebration,
        "skip_action": config.max_tokens_skip,
        "request_alternates": config.max_tokens_alternatives,
        "negotiate": config.max_tokens_negotiate,
        "ask_why": config.max_tokens_why,
        "request_clarification": config.max_tokens_clarification,
        "cancel_action": config.max_tokens_cancel,
        "explain_plan": config.max_tokens_explain_plan,
        "challenge_science": config.max_tokens_challenge_science,
        "health_question": config.max_tokens_health_question,
        "general": config.max_tokens_general,
    }
    
    return token_map.get(intent, config.max_tokens_general)


def get_temperature_for_intent(intent: str) -> float:
    """Get temperature setting for a specific intent type."""
    config = get_llm_config()
    
    temp_map = {
        "complete_action": config.celebration_temperature,
        "explain_plan": config.explanation_temperature,
        "challenge_science": config.explanation_temperature,
        "ask_why": config.explanation_temperature,
        "general": config.general_temperature,
    }
    
    return temp_map.get(intent, config.explanation_temperature)


# ═══════════════════════════════════════════════════════════════════════════
# Environment Variable Examples (.env file)
# ═══════════════════════════════════════════════════════════════════════════
"""
# Production settings
LLM_CLASSIFICATION_TEMPERATURE=0.1
LLM_ENABLE_CACHING=true
LLM_CACHE_TTL_EXPLANATIONS=3600
LLM_MAX_CONVERSATION_MESSAGES=20
LLM_ENABLE_PARALLEL_LLM_CALLS=true
LLM_CIRCUIT_BREAKER_ENABLED=true

# Debug settings (development only)
LLM_LOG_PROMPTS=false
LLM_LOG_RESPONSES=false
LLM_ENABLE_LLM_METRICS=true

# Cost optimization
LLM_MAX_TOKENS_CELEBRATION=100  # Reduce from 150 to save costs
LLM_CACHE_TTL_EXPLANATIONS=7200  # Increase from 1h to 2h

# Feature flags (gradual rollout)
LLM_ENABLE_GROQ_FALLBACK=true
LLM_ENABLE_PARALLEL_LLM_CALLS=true
"""
