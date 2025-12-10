"""
Production-Ready Cache Utilities
================================

Thread-safe, TTL-enabled, size-limited caches for production use.
These utilities replace ad-hoc dictionary caches with professional
implementations that prevent memory leaks and race conditions.

USAGE:
    from app.utils.cache_utils import TTLCache, SingletonMeta
    
    # TTL Cache with 5-minute expiration and 100-entry limit
    my_cache = TTLCache(maxsize=100, ttl_seconds=300)
    my_cache.set("key", "value")
    value = my_cache.get("key")  # Returns None if expired
    
    # Thread-safe singleton
    class MyService(metaclass=SingletonMeta):
        pass
"""

import time
import hashlib
import threading
from typing import Dict, Any, Optional, TypeVar, Generic
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class TTLCache(Generic[T]):
    """
    Thread-safe cache with TTL (Time To Live) and size limits.
    
    Features:
    - Automatic expiration of stale entries
    - Maximum size limit with LRU eviction
    - Thread-safe operations
    - Statistics tracking
    
    Args:
        maxsize: Maximum number of entries (default: 100)
        ttl_seconds: Time to live in seconds (default: 300 = 5 minutes)
        name: Cache name for logging (default: "unnamed")
    """
    
    def __init__(
        self,
        maxsize: int = 100,
        ttl_seconds: int = 300,
        name: str = "unnamed"
    ):
        self._cache: OrderedDict[str, tuple[T, float]] = OrderedDict()
        self._lock = threading.RLock()  # Reentrant lock for nested calls
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self.name = name
        
        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0
    
    def _is_expired(self, timestamp: float) -> bool:
        """Check if entry has expired"""
        return time.time() - timestamp > self.ttl_seconds
    
    def _evict_expired(self) -> int:
        """Remove expired entries. Returns count of evicted entries."""
        evicted = 0
        keys_to_remove = []
        
        for key, (_, timestamp) in self._cache.items():
            if self._is_expired(timestamp):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self._cache[key]
            evicted += 1
        
        self._evictions += evicted
        return evicted
    
    def _evict_lru(self) -> None:
        """Remove oldest entry if over size limit"""
        while len(self._cache) >= self.maxsize:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            self._evictions += 1
    
    def get(self, key: str) -> Optional[T]:
        """
        Get value from cache.
        
        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            value, timestamp = self._cache[key]
            
            if self._is_expired(timestamp):
                del self._cache[key]
                self._misses += 1
                return None
            
            # Move to end (LRU update)
            self._cache.move_to_end(key)
            self._hits += 1
            return value
    
    def set(self, key: str, value: T) -> None:
        """
        Store value in cache.
        
        Automatically evicts expired and LRU entries if needed.
        """
        with self._lock:
            # Evict expired entries periodically
            if len(self._cache) % 10 == 0:  # Every 10 sets
                self._evict_expired()
            
            # Evict LRU if at capacity
            if key not in self._cache:
                self._evict_lru()
            
            self._cache[key] = (value, time.time())
            self._cache.move_to_end(key)
    
    def delete(self, key: str) -> bool:
        """Remove key from cache. Returns True if key existed."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> int:
        """Clear all entries. Returns count of entries cleared."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0
            logger.info(f"🧹 Cache '{self.name}' cleared ({count} entries)")
            return count
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0
            
            return {
                "name": self.name,
                "size": len(self._cache),
                "maxsize": self.maxsize,
                "ttl_seconds": self.ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_percent": round(hit_rate, 1),
                "evictions": self._evictions,
                "sample_keys": list(self._cache.keys())[:5]
            }
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists and is not expired"""
        return self.get(key) is not None
    
    def __len__(self) -> int:
        """Return current cache size (may include expired entries)"""
        return len(self._cache)


class SingletonMeta(type):
    """
    Thread-safe singleton metaclass.
    
    Usage:
        class MyService(metaclass=SingletonMeta):
            def __init__(self):
                # Only called once
                pass
        
        # Both return the same instance
        instance1 = MyService()
        instance2 = MyService()
    """
    _instances: Dict[type, Any] = {}
    _locks: Dict[type, threading.Lock] = {}
    _global_lock = threading.Lock()
    
    def __call__(cls, *args, **kwargs):
        # Double-checked locking pattern
        if cls not in cls._instances:
            with cls._global_lock:
                if cls not in cls._locks:
                    cls._locks[cls] = threading.Lock()
            
            with cls._locks[cls]:
                if cls not in cls._instances:
                    instance = super().__call__(*args, **kwargs)
                    cls._instances[cls] = instance
        
        return cls._instances[cls]
    
    @classmethod
    def reset(mcs, cls: type) -> None:
        """Reset a singleton instance (for testing)"""
        with mcs._global_lock:
            if cls in mcs._instances:
                del mcs._instances[cls]
                logger.info(f"🔄 Singleton '{cls.__name__}' reset")


def generate_cache_key(*args, **kwargs) -> str:
    """
    Generate a stable cache key from arguments.
    
    Uses MD5 hash for consistent, collision-resistant keys.
    Unlike Python's hash(), this is stable across restarts.
    
    Args:
        *args: Positional arguments to include in key
        **kwargs: Keyword arguments to include in key
    
    Returns:
        32-character hex string
    """
    # Convert all args to stable string representation
    parts = [str(arg) for arg in args]
    parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    content = "|".join(parts)
    
    return hashlib.md5(content.encode()).hexdigest()


# ============================================
# GLOBAL CACHE INSTANCES (Use these across the app)
# ============================================

# Cache for hormone analysis LLM responses
# TTL: 10 minutes (hormone analysis doesn't change within a session)
# Max: 50 entries (one per unique symptom+family combination)
hormone_analysis_cache: TTLCache[Dict[str, int]] = TTLCache(
    maxsize=50,
    ttl_seconds=600,  # 10 minutes
    name="hormone_analysis"
)

# Cache for Pinecone/RAG query results  
# TTL: 5 minutes (research papers don't change frequently)
# Max: 200 entries (different queries for food/movement/mindfulness)
rag_query_cache: TTLCache[list] = TTLCache(
    maxsize=200,
    ttl_seconds=300,  # 5 minutes
    name="rag_query"
)


def clear_all_caches() -> Dict[str, int]:
    """
    Clear all global caches.
    
    Returns:
        Dict with count of entries cleared per cache
    """
    results = {
        "hormone_analysis": hormone_analysis_cache.clear(),
        "rag_query": rag_query_cache.clear(),
    }
    logger.info(f"🧹 All caches cleared: {results}")
    return results


def get_all_cache_stats() -> Dict[str, Dict[str, Any]]:
    """
    Get statistics for all global caches.
    
    Returns:
        Dict with stats per cache
    """
    return {
        "hormone_analysis": hormone_analysis_cache.stats(),
        "rag_query": rag_query_cache.stats(),
    }
