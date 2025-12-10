"""
Central Cache Management Service
================================

This service manages all session-level caches across the application.
Call clear_session_caches() between sessions to ensure fresh data.

CACHES MANAGED:
1. Hormone Analysis Cache (root_cause_engine.py)
   - Prevents duplicate LLM calls for same symptom/family text
   
2. RAG Query Cache (rag_retriever.py)
   - Prevents duplicate Pinecone API calls for same queries
   
3. RAG Component Cache (retrieval_component.py)
   - Higher-level caching for expert module queries

4. V3 Engine Instance (v3_orchestrator.py)
   - Singleton pattern - optionally reset between sessions

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


def clear_session_caches(clear_engine: bool = False) -> Dict[str, bool]:
    """
    Clear all session-level caches.
    
    Call this between sessions to ensure fresh data.
    
    Args:
        clear_engine: If True, also reset V3 engine singleton
                     (usually not needed, only for major config changes)
    
    Returns:
        Dict showing which caches were cleared
    """
    results = {}
    
    # 1. Clear Hormone Analysis Cache
    try:
        from app.services.root_cause_engine import RootCauseEngine
        RootCauseEngine.clear_cache()
        results['hormone_analysis'] = True
        logger.info("✅ Hormone analysis cache cleared")
    except Exception as e:
        results['hormone_analysis'] = False
        logger.warning(f"⚠️ Could not clear hormone cache: {e}")
    
    # 2. Clear RAG Retriever Cache
    try:
        from app.services.rag.rag_retriever import clear_retriever_cache
        clear_retriever_cache()
        results['rag_retriever'] = True
        logger.info("✅ RAG retriever cache cleared")
    except Exception as e:
        results['rag_retriever'] = False
        logger.warning(f"⚠️ Could not clear RAG cache: {e}")
    
    # 3. Clear RAG Component Cache
    try:
        from app.services.recommendation_engine_v3.components.retrieval_component import clear_rag_cache
        clear_rag_cache()
        results['rag_component'] = True
        logger.info("✅ RAG component cache cleared")
    except Exception as e:
        results['rag_component'] = False
        logger.warning(f"⚠️ Could not clear RAG component cache: {e}")
    
    # 4. Optionally reset V3 Engine
    if clear_engine:
        try:
            from app.services.recommendation_engine_v3.core.v3_orchestrator import reset_v3_engine
            reset_v3_engine()
            results['v3_engine'] = True
            logger.info("✅ V3 engine singleton reset")
        except Exception as e:
            results['v3_engine'] = False
            logger.warning(f"⚠️ Could not reset V3 engine: {e}")
    
    logger.info(f"🧹 Cache clear results: {results}")
    return results


def get_cache_stats() -> Dict[str, Any]:
    """
    Get statistics for all caches.
    
    Useful for debugging and monitoring cache effectiveness.
    
    Returns:
        Dict with cache statistics
    """
    stats = {}
    
    # 1. Hormone Analysis Cache
    try:
        from app.services.root_cause_engine import _hormone_analysis_cache
        stats['hormone_analysis'] = {
            'size': len(_hormone_analysis_cache),
            'keys': list(_hormone_analysis_cache.keys())[:5]
        }
    except Exception as e:
        stats['hormone_analysis'] = {'error': str(e)}
    
    # 2. RAG Retriever Cache
    try:
        from app.services.rag.rag_retriever import get_cache_stats as get_rag_stats
        stats['rag_retriever'] = get_rag_stats()
    except Exception as e:
        stats['rag_retriever'] = {'error': str(e)}
    
    # 3. RAG Component Cache
    try:
        from app.services.recommendation_engine_v3.components.retrieval_component import get_rag_cache_stats
        stats['rag_component'] = get_rag_cache_stats()
    except Exception as e:
        stats['rag_component'] = {'error': str(e)}
    
    return stats


def warm_up_caches() -> None:
    """
    Pre-warm caches with common queries (optional optimization).
    
    This can reduce latency for the first request.
    """
    logger.info("🔥 Cache warm-up not implemented (optional)")
    # Could pre-load common Pinecone queries here
    pass
