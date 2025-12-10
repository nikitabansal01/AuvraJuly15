"""
Retrieval Component - Reusable Semantic Search
==============================================

This component provides reusable retrieval functionality that can be
used across all expert modules.

OPTIMIZATIONS (Production-ready):
- Query-level caching to prevent duplicate Pinecone calls
- Singleton retriever pattern
- Async-first design
"""

from typing import List, Dict, Any, Optional
import logging
import hashlib

logger = logging.getLogger(__name__)


# ============================================
# RAG QUERY CACHE (OPTIMIZATION)
# ============================================
# Caches Pinecone query results by query hash
# Same query in same session returns cached results
_rag_query_cache: Dict[str, List[Dict[str, Any]]] = {}


def clear_rag_cache():
    """Clear the RAG query cache (call between sessions)"""
    global _rag_query_cache
    _rag_query_cache.clear()
    logger.info("🧹 RAG query cache cleared")


def get_rag_cache_stats() -> Dict[str, Any]:
    """Get RAG cache statistics"""
    return {
        "cache_size": len(_rag_query_cache),
        "keys": list(_rag_query_cache.keys())[:10]  # First 10 keys
    }


class RetrievalComponent:
    """
    Reusable semantic search component with configurable parameters.
    
    This component wraps the existing Pinecone/hybrid search infrastructure
    and provides a clean interface for expert modules.
    
    OPTIMIZATION: Query results are cached by query hash to prevent
    duplicate Pinecone API calls within the same session.
    """
    
    def __init__(
        self,
        vector_store: str = "pinecone",
        namespace: str = "default"
    ):
        self.vector_store = vector_store
        self.namespace = namespace
        self._retriever = None
        self._initialize_retriever()
    
    def _initialize_retriever(self):
        """Initialize the underlying retriever"""
        try:
            from app.services.rag.rag_retriever import get_retriever
            self._retriever = get_retriever()
            logger.info("✅ RetrievalComponent: Retriever initialized")
        except ImportError as e:
            logger.warning(f"⚠️ RetrievalComponent: Retriever not available: {e}")
            self._retriever = None
    
    def _get_cache_key(self, query: str, category: str, top_k: int) -> str:
        """Generate cache key for RAG query"""
        content = f"{query}|{category}|{top_k}"
        return hashlib.md5(content.encode()).hexdigest()
    
    async def retrieve(
        self,
        query: str,
        top_k: int = 20,
        rerank: bool = True,
        min_score: float = 0.3,
        category: str = "food"
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant documents for a query.
        
        OPTIMIZED: Results are cached by query+category+top_k hash.
        
        Args:
            query: Search query
            top_k: Number of results to return
            rerank: Whether to rerank results
            min_score: Minimum relevance score
            category: Category for search (food, movement, mindfulness)
        
        Returns:
            List of relevant documents with metadata
        """
        global _rag_query_cache
        
        if self._retriever is None:
            logger.warning("⚠️ Retriever not available, returning empty results")
            return []
        
        # Check cache first (OPTIMIZATION)
        cache_key = self._get_cache_key(query, category, top_k)
        if cache_key in _rag_query_cache:
            cached_results = _rag_query_cache[cache_key]
            logger.info(f"✅ RAG Cache HIT: {len(cached_results)} results for category={category}")
            return cached_results
        
        try:
            # Use the existing RAG retriever's signature
            # retrieve(query, user_profile, category, top_k)
            results = await self._retriever.retrieve(
                query=query,
                user_profile={},  # Empty profile for general retrieval
                category=category,
                top_k=top_k
            )
            
            # Filter by minimum score
            filtered = [
                r for r in results 
                if r.get('score', 0) >= min_score or r.get('relevance_score', 0) >= min_score
            ]
            
            # Cache results (OPTIMIZATION)
            _rag_query_cache[cache_key] = filtered
            logger.info(f"📚 RAG Cache MISS: Retrieved and cached {len(filtered)} documents for category={category}")
            
            return filtered
            
        except Exception as e:
            logger.error(f"❌ Retrieval failed: {e}")
            return []
    
    async def retrieve_for_module(
        self,
        module_config: Dict[str, Any],
        category: str = "food",
        focused_problem = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve documents using module-specific configuration.
        
        Args:
            module_config: Module's RETRIEVAL_CONFIG
            category: Category for search (food, movement, mindfulness)
            focused_problem: Optional problem context for personalization
        
        Returns:
            Aggregated relevant documents
        """
        all_results = []
        
        primary_queries = module_config.get('primary_queries', [])
        must_include = module_config.get('must_include_terms', [])
        
        for query in primary_queries[:3]:  # Limit to top 3 queries
            results = await self.retrieve(query, top_k=10, category=category)
            
            # Filter to ensure must-include terms
            if must_include:
                results = [
                    r for r in results
                    if any(term.lower() in str(r).lower() for term in must_include)
                ]
            
            all_results.extend(results)
        
        # Deduplicate by document ID
        seen_ids = set()
        unique_results = []
        for r in all_results:
            doc_id = r.get('id', r.get('pmid', str(r)))
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                unique_results.append(r)
        
        return unique_results
