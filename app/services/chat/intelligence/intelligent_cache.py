"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA INTELLIGENT CACHE - Response Optimization System
═══════════════════════════════════════════════════════════════════════════════
Smart caching for faster responses without sacrificing personalization.

CACHING STRATEGY:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. COMMON QUERIES - Cache educational content, cycle explanations
2. USER CONTEXT - Cache recent user profile, cycle state, patterns
3. RESPONSE TEMPLATES - Cache personalized templates with placeholders
4. INVALIDATION - Smart invalidation when user data changes
5. TTL MANAGEMENT - Different TTLs for different data types
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
import hashlib
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)


class IntelligentCache:
    """
    Smart caching system for AUVRA.
    
    Balances speed with personalization by caching strategically.
    """
    
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ttls = {
            "educational": 86400,  # 24 hours
            "user_context": 300,  # 5 minutes
            "cycle_state": 3600,  # 1 hour
            "common_query": 1800,  # 30 minutes
            "templates": 7200  # 2 hours
        }
    
    def get(self, key: str, cache_type: str = "common_query") -> Optional[Any]:
        """Get item from cache if not expired."""
        if key not in self._cache:
            return None
        
        cached_item = self._cache[key]
        expiry = cached_item.get("expiry")
        
        if expiry and datetime.now() > expiry:
            # Expired - remove
            del self._cache[key]
            logger.debug(f"Cache expired: {key}")
            return None
        
        logger.debug(f"Cache hit: {key}")
        return cached_item.get("value")
    
    def set(
        self,
        key: str,
        value: Any,
        cache_type: str = "common_query",
        ttl_override: Optional[int] = None
    ):
        """Set item in cache with TTL."""
        ttl = ttl_override or self._ttls.get(cache_type, 1800)
        expiry = datetime.now() + timedelta(seconds=ttl)
        
        self._cache[key] = {
            "value": value,
            "expiry": expiry,
            "cache_type": cache_type,
            "created": datetime.now()
        }
        
        logger.debug(f"Cache set: {key} (TTL: {ttl}s)")
    
    def invalidate(self, key: str):
        """Invalidate specific cache key."""
        if key in self._cache:
            del self._cache[key]
            logger.debug(f"Cache invalidated: {key}")
    
    def invalidate_user(self, user_id: str):
        """Invalidate all caches for a specific user."""
        keys_to_remove = [
            k for k in self._cache.keys()
            if user_id in k
        ]
        for key in keys_to_remove:
            del self._cache[key]
        
        logger.info(f"Invalidated {len(keys_to_remove)} cache entries for user {user_id}")
    
    def invalidate_type(self, cache_type: str):
        """Invalidate all caches of a specific type."""
        keys_to_remove = [
            k for k, v in self._cache.items()
            if v.get("cache_type") == cache_type
        ]
        for key in keys_to_remove:
            del self._cache[key]
        
        logger.info(f"Invalidated {len(keys_to_remove)} cache entries of type {cache_type}")
    
    def clear_expired(self):
        """Clear all expired cache entries."""
        now = datetime.now()
        keys_to_remove = [
            k for k, v in self._cache.items()
            if v.get("expiry") and v["expiry"] < now
        ]
        for key in keys_to_remove:
            del self._cache[key]
        
        logger.debug(f"Cleared {len(keys_to_remove)} expired cache entries")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = len(self._cache)
        by_type = {}
        
        for item in self._cache.values():
            cache_type = item.get("cache_type", "unknown")
            by_type[cache_type] = by_type.get(cache_type, 0) + 1
        
        return {
            "total_entries": total,
            "by_type": by_type,
            "memory_estimate_kb": len(str(self._cache)) / 1024
        }
    
    @staticmethod
    def generate_key(*args, **kwargs) -> str:
        """Generate cache key from arguments."""
        # Create a stable hash from arguments
        key_data = json.dumps({
            "args": args,
            "kwargs": kwargs
        }, sort_keys=True)
        
        return hashlib.md5(key_data.encode()).hexdigest()


# Global cache instance
_global_cache = IntelligentCache()


def cached(cache_type: str = "common_query", ttl: Optional[int] = None):
    """
    Decorator for caching function results.
    
    Usage:
        @cached(cache_type="educational", ttl=3600)
        async def get_cycle_explanation(phase: str):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = _global_cache.generate_key(
                func.__name__,
                *args,
                **kwargs
            )
            
            # Try to get from cache
            cached_result = _global_cache.get(cache_key, cache_type)
            if cached_result is not None:
                logger.debug(f"Returning cached result for {func.__name__}")
                return cached_result
            
            # Not in cache - call function
            result = await func(*args, **kwargs)
            
            # Cache result
            _global_cache.set(cache_key, result, cache_type, ttl)
            
            return result
        
        return wrapper
    return decorator


# Singleton access
def get_cache() -> IntelligentCache:
    """Get global cache instance."""
    return _global_cache


# ═══════════════════════════════════════════════════════════════════════════════
# COMMON CACHEABLE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

@cached(cache_type="educational", ttl=86400)
async def get_cycle_explanation(phase: str) -> str:
    """Get cached cycle phase explanation."""
    explanations = {
        "menstrual": "Your menstrual phase - hormones are at their lowest. Energy may be low, focus on rest and gentle care.",
        "follicular": "Your follicular phase - rising estrogen brings energy and optimism. Great time for new projects!",
        "ovulatory": "Your ovulatory phase - peak energy and social confidence. You're at your strongest!",
        "luteal": "Your luteal phase - progesterone rises, then drops. Energy decreases, self-care becomes crucial."
    }
    return explanations.get(phase, "Unknown phase")


@cached(cache_type="common_query", ttl=3600)
async def get_symptom_info(symptom: str) -> Dict[str, Any]:
    """Get cached symptom information."""
    symptom_db = {
        "cramps": {
            "name": "Cramps",
            "description": "Uterine contractions causing abdominal pain",
            "common_in": ["menstrual", "luteal"],
            "remedies": ["heating pad", "magnesium", "gentle movement"]
        },
        "bloating": {
            "name": "Bloating",
            "description": "Fluid retention and digestive slowdown",
            "common_in": ["luteal", "menstrual"],
            "remedies": ["reduce sodium", "hydration", "gentle movement"]
        },
        # Add more...
    }
    return symptom_db.get(symptom, {})
