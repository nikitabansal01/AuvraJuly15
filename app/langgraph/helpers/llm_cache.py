"""
Redis-Based LLM Response Caching Layer
Implements intelligent caching to reduce costs and improve latency.

Based on patterns from:
- OpenAI Cookbook: https://cookbook.openai.com/examples/how_to_cache_embeddings
- Vercel AI SDK: Response caching with TTL
- Stripe API: Cache-Control headers

Key Features:
- Deterministic cache keys (hash of prompt + params)
- TTL-based expiration
- Temperature-aware caching (only cache low-temperature responses)
- Intent-specific TTL (static content cached longer)
- Automatic cache warming for common queries
"""

import hashlib
import json
import logging
from typing import Optional, Any, Dict
import asyncio

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    aioredis = None

from app.langgraph.helpers.llm_config import get_llm_config, get_cache_ttl_for_intent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Redis Client Singleton
# ═══════════════════════════════════════════════════════════════════════════

_redis_client: Optional[Any] = None
_redis_enabled: bool = False


async def get_redis_client():
    """
    Get singleton Redis client with connection pooling.
    
    Returns None if Redis is not available (graceful degradation).
    """
    global _redis_client, _redis_enabled
    
    if not REDIS_AVAILABLE:
        if _redis_enabled:  # Only log once
            logger.warning("Redis library not installed - caching disabled")
            _redis_enabled = False
        return None
    
    if _redis_client is None:
        try:
            from app.core.config import settings
            _redis_client = await aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # Test connection
            await _redis_client.ping()
            _redis_enabled = True
            logger.info(f"Redis client initialized: {settings.REDIS_URL}")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Caching disabled.")
            _redis_client = None
            _redis_enabled = False
    
    return _redis_client


async def close_redis_client():
    """Close Redis connection (call on shutdown)."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis client closed")


# ═══════════════════════════════════════════════════════════════════════════
# Cache Key Generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_cache_key(
    prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    **kwargs
) -> str:
    """
    Generate deterministic cache key from LLM request parameters.
    
    Args:
        prompt: The prompt text
        model: Model name (gpt-4o-mini, etc.)
        temperature: Temperature setting
        max_tokens: Max tokens to generate
        **kwargs: Additional parameters that affect output
    
    Returns:
        Cache key string (e.g., "llm:abc123def456")
    """
    # Include all parameters that affect the output
    cache_data = {
        "prompt": prompt.strip(),
        "model": model,
        "temperature": round(temperature, 2),  # Round to avoid float precision issues
        "max_tokens": max_tokens,
        **{k: v for k, v in sorted(kwargs.items())}  # Sort for determinism
    }
    
    # Create deterministic JSON string
    json_str = json.dumps(cache_data, sort_keys=True)
    
    # Hash to reasonable length
    hash_hex = hashlib.sha256(json_str.encode()).hexdigest()[:16]
    
    return f"llm:cache:{hash_hex}"


# ═══════════════════════════════════════════════════════════════════════════
# Core Caching Functions
# ═══════════════════════════════════════════════════════════════════════════

async def get_cached_response(
    prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    **kwargs
) -> Optional[str]:
    """
    Try to get response from cache.
    
    Returns:
        Cached response string if found, None otherwise
    """
    config = get_llm_config()
    
    # Check if caching is enabled
    if not config.enable_caching:
        return None
    
    # Don't cache high-temperature responses (non-deterministic)
    if temperature > config.cache_temperature_threshold:
        return None
    
    redis = await get_redis_client()
    if not redis:
        return None  # Redis not available
    
    try:
        cache_key = generate_cache_key(prompt, model, temperature, max_tokens, **kwargs)
        cached = await redis.get(cache_key)
        
        if cached:
            logger.info(f"Cache HIT for key {cache_key[:24]}...")
            return cached
        
        logger.debug(f"Cache MISS for key {cache_key[:24]}...")
        return None
    
    except Exception as e:
        logger.warning(f"Cache get error: {e}")
        return None


async def set_cached_response(
    prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    response: str,
    ttl: Optional[int] = None,
    intent: Optional[str] = None,
    **kwargs
) -> bool:
    """
    Store response in cache with TTL.
    
    Args:
        prompt: The prompt text
        model: Model name
        temperature: Temperature setting
        max_tokens: Max tokens
        response: The LLM response to cache
        ttl: Time-to-live in seconds (None = use config default)
        intent: Intent type for determining TTL (optional)
        **kwargs: Additional request parameters
    
    Returns:
        True if cached successfully, False otherwise
    """
    config = get_llm_config()
    
    # Check if caching is enabled
    if not config.enable_caching:
        return False
    
    # Don't cache high-temperature responses
    if temperature > config.cache_temperature_threshold:
        return False
    
    # Don't cache empty responses
    if not response or len(response.strip()) < 10:
        return False
    
    redis = await get_redis_client()
    if not redis:
        return False
    
    try:
        cache_key = generate_cache_key(prompt, model, temperature, max_tokens, **kwargs)
        
        # Determine TTL
        if ttl is None:
            if intent:
                ttl = get_cache_ttl_for_intent(intent, temperature)
            else:
                ttl = config.cache_ttl_general
        
        if ttl <= 0:
            return False  # Don't cache if TTL is 0
        
        # Store in Redis with TTL
        await redis.setex(cache_key, ttl, response)
        logger.debug(f"Cache SET for key {cache_key[:24]}... (TTL: {ttl}s)")
        return True
    
    except Exception as e:
        logger.warning(f"Cache set error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# High-Level Wrapper
# ═══════════════════════════════════════════════════════════════════════════

async def call_llm_with_cache(
    llm_func,
    prompt: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.1,
    max_tokens: int = 150,
    intent: Optional[str] = None,
    **kwargs
) -> str:
    """
    Call LLM function with automatic caching.
    
    Usage:
        response = await call_llm_with_cache(
            call_llm,
            prompt="What is photosynthesis?",
            model="gpt-4o-mini",
            temperature=0.1,
            max_tokens=200,
            intent="challenge_science"
        )
    
    Args:
        llm_func: The actual LLM function to call (call_llm, call_llm_structured, etc.)
        prompt: The prompt text
        model: Model name
        temperature: Temperature setting
        max_tokens: Max tokens
        intent: Intent type for TTL determination
        **kwargs: Additional parameters passed to llm_func
    
    Returns:
        LLM response string (from cache or fresh)
    """
    # Try cache first
    cached = await get_cached_response(
        prompt, model, temperature, max_tokens
    )
    if cached:
        return cached
    
    # Cache miss - call LLM
    response = await llm_func(prompt, max_tokens=max_tokens, temperature=temperature, **kwargs)
    
    # Store in cache (fire-and-forget)
    asyncio.create_task(
        set_cached_response(
            prompt, model, temperature, max_tokens, response, intent=intent
        )
    )
    
    return response


# ═══════════════════════════════════════════════════════════════════════════
# Cache Management
# ═══════════════════════════════════════════════════════════════════════════

async def clear_cache(pattern: str = "llm:cache:*") -> int:
    """
    Clear cache entries matching pattern.
    
    Args:
        pattern: Redis key pattern (default: all LLM caches)
    
    Returns:
        Number of keys deleted
    """
    redis = await get_redis_client()
    if not redis:
        return 0
    
    try:
        keys = await redis.keys(pattern)
        if keys:
            deleted = await redis.delete(*keys)
            logger.info(f"Cleared {deleted} cache entries matching '{pattern}'")
            return deleted
        return 0
    except Exception as e:
        logger.error(f"Cache clear error: {e}")
        return 0


async def get_cache_stats() -> Dict[str, Any]:
    """
    Get cache statistics for monitoring.
    
    Returns:
        {
            "enabled": bool,
            "total_keys": int,
            "memory_usage": str,
            "hit_rate": float  # (future: requires tracking)
        }
    """
    redis = await get_redis_client()
    if not redis:
        return {"enabled": False}
    
    try:
        # Count LLM cache keys
        keys = await redis.keys("llm:cache:*")
        
        # Get Redis info
        info = await redis.info("memory")
        
        return {
            "enabled": True,
            "total_keys": len(keys),
            "memory_usage": info.get("used_memory_human", "unknown"),
            "peak_memory": info.get("used_memory_peak_human", "unknown")
        }
    except Exception as e:
        logger.error(f"Cache stats error: {e}")
        return {"enabled": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# Cache Warming (Pre-populate common queries)
# ═══════════════════════════════════════════════════════════════════════════

COMMON_QUERIES = [
    # Science explanations that are frequently asked
    {
        "prompt": "Explain why cinnamon helps with insulin sensitivity in 2-3 sentences.",
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "max_tokens": 250,
        "intent": "challenge_science"
    },
    {
        "prompt": "Explain why omega-3 fatty acids support progesterone in 2-3 sentences.",
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "max_tokens": 250,
        "intent": "challenge_science"
    }
]


async def warm_cache():
    """
    Pre-populate cache with common queries.
    Call this on server startup to improve first-user experience.
    """
    config = get_llm_config()
    if not config.enable_caching:
        return
    
    redis = await get_redis_client()
    if not redis:
        return
    
    logger.info(f"Warming cache with {len(COMMON_QUERIES)} common queries...")
    
    for query in COMMON_QUERIES:
        try:
            # Check if already cached
            cached = await get_cached_response(**query)
            if not cached:
                # Generate response (this would normally call LLM)
                # For now, we'll skip actual LLM calls and just log
                logger.debug(f"Would warm cache for: {query['prompt'][:50]}...")
        except Exception as e:
            logger.warning(f"Cache warming error: {e}")
    
    logger.info("Cache warming complete")
