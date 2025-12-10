"""
Central Cache Management Service
================================

This service manages all session-level caches across the application.
Call clear_session_caches() between sessions to ensure fresh data.

CACHES MANAGED (Production-Ready):
All caches are now implemented with:
- Thread-safety (via threading.RLock)
- TTL expiration (auto-cleanup of stale entries)  
- Size limits (LRU eviction to prevent memory leaks)

1. Hormone Analysis Cache (cache_utils.hormone_analysis_cache)
   - TTL: 10 minutes | Max: 50 entries
   - Prevents duplicate LLM calls for same symptom/family text
   
2. RAG Query Cache (cache_utils.rag_query_cache)
   - TTL: 5 minutes | Max: 200 entries
   - Prevents duplicate Pinecone API calls for same queries

3. V3 Engine Instance (v3_orchestrator.py)
   - Thread-safe singleton pattern
   - Optionally reset between sessions

USAGE:
    from app.services.cache_service import clear_session_caches, get_cache_stats
    
    # Before processing new session
    clear_session_caches()
    
    # Debug cache state
    stats = get_cache_stats()
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def clear_session_caches(clear_engine: bool = False) -> Dict[str, Any]:
    """
    Clear all session-level caches.
    
    Call this between sessions to ensure fresh data.
    
    Args:
        clear_engine: If True, also reset V3 engine singleton
                     (usually not needed, only for major config changes)
    
    Returns:
        Dict showing which caches were cleared and how many entries
    """
    results = {}
    
    # 1. Clear Hormone Analysis Cache (via centralized utility)
    try:
        from app.services.root_cause_engine import RootCauseEngine
        RootCauseEngine.clear_cache()
        results['hormone_analysis'] = {'cleared': True}
        logger.info("✅ Hormone analysis cache cleared")
    except Exception as e:
        results['hormone_analysis'] = {'cleared': False, 'error': str(e)}
        logger.warning(f"⚠️ Could not clear hormone cache: {e}")
    
    # 2. Clear RAG Retriever Cache (via centralized utility)
    try:
        from app.services.rag.rag_retriever import clear_retriever_cache
        clear_retriever_cache()
        results['rag_retriever'] = {'cleared': True}
        logger.info("✅ RAG retriever cache cleared")
    except Exception as e:
        results['rag_retriever'] = {'cleared': False, 'error': str(e)}
        logger.warning(f"⚠️ Could not clear RAG cache: {e}")
    
    # 3. RAG Component Cache - Now delegates to rag_retriever (no separate cache)
    # Left for backward compatibility but just confirms rag_retriever is cleared
    results['rag_component'] = {'cleared': True, 'note': 'Shares cache with rag_retriever'}
    
    # 4. Optionally reset V3 Engine
    if clear_engine:
        try:
            from app.services.recommendation_engine_v3.core.v3_orchestrator import reset_v3_engine
            reset_v3_engine()
            results['v3_engine'] = {'cleared': True}
            logger.info("✅ V3 engine singleton reset")
        except Exception as e:
            results['v3_engine'] = {'cleared': False, 'error': str(e)}
            logger.warning(f"⚠️ Could not reset V3 engine: {e}")
    
    logger.info(f"🧹 Cache clear results: {results}")
    return results


def get_cache_stats() -> Dict[str, Any]:
    """
    Get statistics for all caches.
    
    Useful for debugging and monitoring cache effectiveness.
    Includes: size, maxsize, TTL, hit rate, evictions.
    
    Returns:
        Dict with cache statistics
    """
    stats = {}
    
    # 1. Hormone Analysis Cache (via centralized utility)
    try:
        from app.services.root_cause_engine import RootCauseEngine
        stats['hormone_analysis'] = RootCauseEngine.get_cache_stats()
    except Exception as e:
        stats['hormone_analysis'] = {'error': str(e)}
    
    # 2. RAG Retriever Cache (via centralized utility)
    try:
        from app.services.rag.rag_retriever import get_cache_stats as get_rag_stats
        stats['rag_retriever'] = get_rag_stats()
    except Exception as e:
        stats['rag_retriever'] = {'error': str(e)}
    
    # 3. Global cache utility stats
    try:
        from app.utils.cache_utils import get_all_cache_stats
        stats['global_caches'] = get_all_cache_stats()
    except Exception as e:
        stats['global_caches'] = {'error': str(e)}
    
    return stats


def warm_up_caches() -> None:
    """
    Pre-warm caches with common queries (optional optimization).
    
    This can reduce latency for the first request.
    """
    logger.info("🔥 Cache warm-up not implemented (optional)")
    # Could pre-load common Pinecone queries here
    pass
