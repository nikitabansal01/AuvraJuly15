"""
RAG Retriever Module
Hybrid search combining semantic and keyword-based retrieval

OPTIMIZATIONS (Production-ready):
- Singleton retriever pattern (prevents re-initialization)
- Query result caching (prevents duplicate Pinecone calls)
- Reduced verbose logging for production
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging
import os
import httpx
import hashlib
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================
# SINGLETON & CACHE (OPTIMIZATION)
# ============================================
_retriever_instance: Optional['HybridRetriever'] = None
_pinecone_query_cache: Dict[str, List[Dict[str, Any]]] = {}


def get_retriever() -> 'HybridRetriever':
    """Get singleton retriever instance (OPTIMIZATION)"""
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
        logger.info("✅ Singleton HybridRetriever created")
    return _retriever_instance


def clear_retriever_cache():
    """Clear the Pinecone query cache (call between sessions)"""
    global _pinecone_query_cache
    _pinecone_query_cache.clear()
    logger.info("🧹 Pinecone query cache cleared")


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics"""
    return {
        "cache_size": len(_pinecone_query_cache),
        "sample_keys": list(_pinecone_query_cache.keys())[:5]
    }


class StudyType(Enum):
    META_ANALYSIS = "meta-analysis"
    SYSTEMATIC_REVIEW = "systematic review"
    RCT = "randomized controlled trial"
    COHORT = "cohort study"
    CASE_STUDY = "case study"
    REVIEW = "review"
    OBSERVATIONAL = "observational study"


@dataclass
class RetrievedPaper:
    """Represents a retrieved research paper"""
    pmid: str
    pmcid: Optional[str]
    title: str
    abstract: str
    full_text: Optional[str]
    authors: List[str]
    journal: str
    publication_year: int
    study_type: str
    participant_count: Optional[int]
    conditions: List[str]
    interventions: List[str]
    hormone_focus: List[str]
    mesh_terms: List[str]
    doi: Optional[str]
    relevance_score: float = 0.0
    chunk_text: Optional[str] = None  # The specific chunk that matched


class HybridRetriever:
    """
    Hybrid retrieval combining semantic (vector) and keyword (BM25) search
    with Reciprocal Rank Fusion for combining results
    """
    
    STUDY_TYPE_WEIGHTS = {
        "meta-analysis": 1.0,
        "systematic review": 0.95,
        "randomized controlled trial": 0.9,
        "cohort study": 0.7,
        "observational study": 0.6,
        "review": 0.6,
        "case study": 0.5,
    }
    
    # PCOS/Hormone health specific keywords for query enhancement
    DOMAIN_KEYWORDS = {
        "food": ["diet", "nutrition", "dietary", "food", "supplement", "vitamin", "nutrient", "meal"],
        "movement": ["exercise", "physical activity", "workout", "training", "yoga", "aerobic", "resistance"],
        "mindfulness": ["meditation", "mindfulness", "stress reduction", "relaxation", "breathing", "yoga nidra", "cognitive behavioral"]
    }
    
    def __init__(self):
        logger.info("🚀 RAG RETRIEVER INITIALIZING")
        
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.pinecone_api_key = os.getenv("PINECONE_API_KEY")
        self.pinecone_index = os.getenv("PINECONE_INDEX")
        
        # Debug logging (reduced for production)
        logger.info(f"🔧 OPENAI_API_KEY: {'SET' if self.openai_api_key else '❌ MISSING'}")
        logger.info(f"🔧 PINECONE_API_KEY: {'SET' if self.pinecone_api_key else '❌ MISSING'}")
        logger.info(f"🔧 PINECONE_INDEX: {self.pinecone_index or '❌ MISSING'}")
    
    def _get_cache_key(self, query: str, category: str, top_k: int) -> str:
        """Generate cache key for Pinecone query"""
        content = f"{query}|{category}|{top_k}"
        return hashlib.md5(content.encode()).hexdigest()
        
    async def retrieve(
        self,
        query: str,
        user_profile: Dict[str, Any],
        category: str,
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Main retrieval method combining semantic and keyword search.
        
        OPTIMIZED: Results are cached by query hash to prevent duplicate
        Pinecone API calls within the same session.
        """
        global _pinecone_query_cache
        
        try:
            # Step 1: Build enhanced query from user profile
            enhanced_query = self._build_enhanced_query(query, user_profile, category)
            
            # Check cache first (OPTIMIZATION)
            cache_key = self._get_cache_key(enhanced_query, category, top_k)
            if cache_key in _pinecone_query_cache:
                cached_results = _pinecone_query_cache[cache_key]
                logger.info(f"✅ Pinecone Cache HIT: {len(cached_results)} results for {category}")
                return cached_results
            
            logger.info(f"📚 RAG RETRIEVE: category={category}, query={enhanced_query[:80]}...")
            
            # Step 2: Semantic search - Pinecone returns results ALREADY sorted by similarity
            semantic_results = await self._semantic_search(enhanced_query, top_k)
            
            if not semantic_results:
                logger.warning(f"⚠️ No Pinecone results for {category}")
                return []
            
            # Log top match scores (reduced verbosity)
            top_scores = [round(r.get('score', 0), 3) for r in semantic_results[:3]]
            logger.info(f"✅ Retrieved {len(semantic_results)} papers, top scores: {top_scores}")
            
            # Cache results (OPTIMIZATION)
            _pinecone_query_cache[cache_key] = semantic_results
            
            return semantic_results
            
        except Exception as e:
            logger.error(f"❌ RAG Retriever error: {str(e)}")
            return []
    
    def _build_enhanced_query(
        self,
        base_query: str,
        user_profile: Dict[str, Any],
        category: str
    ) -> str:
        """Build query enhanced with user profile and category context"""
        parts = [base_query]
        
        # Add PCOS/hormone context
        parts.append("PCOS polycystic ovary syndrome")
        
        # Add primary hormone imbalance
        if user_profile.get('primary_imbalance'):
            hormone = user_profile['primary_imbalance']
            level = user_profile.get('primary_level', '')
            parts.append(f"{hormone} {level} imbalance")
        
        # Add secondary hormones
        for hormone in user_profile.get('secondary_imbalances', [])[:2]:
            parts.append(hormone)
        
        # Add conditions
        for condition in user_profile.get('conditions', [])[:3]:
            parts.append(condition)
        
        # Add top symptoms
        for symptom in user_profile.get('symptoms', [])[:3]:
            parts.append(symptom)
        
        # Add category-specific keywords
        category_keywords = self.DOMAIN_KEYWORDS.get(category, [])
        parts.extend(category_keywords[:3])
        
        return " ".join(parts)
    
    async def _semantic_search(
        self,
        query: str,
        top_k: int
    ) -> List[Dict[str, Any]]:
        """Perform semantic search using embeddings"""
        if not self.openai_api_key or not self.pinecone_api_key:
            logger.warning("⚠️ Missing API keys for semantic search")
            return []
        
        try:
            # Generate query embedding
            query_embedding = await self._get_embedding(query)
            
            # Search Pinecone
            results = await self._search_pinecone(query_embedding, top_k)
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Semantic search failed: {str(e)}")
            return []
    
    async def _get_embedding(self, text: str) -> List[float]:
        """Generate embedding using OpenAI"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "input": text,
                    "model": "text-embedding-3-small"
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]
    
    async def _search_pinecone(
        self,
        embedding: List[float],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """Search Pinecone vector database"""
        from pinecone import Pinecone
        
        pc = Pinecone(api_key=self.pinecone_api_key)
        index = pc.Index(self.pinecone_index)
        
        results = index.query(
            vector=embedding,
            top_k=top_k,
            include_metadata=True,
            namespace="pcos-rag-gpt_4o"  # Query the namespace where vectors are indexed
        )
        
        logger.info(f"🔍 Pinecone query completed: {len(results.matches)} matches found in namespace 'pcos-rag-gpt_4o'")
        
        # Convert to standard format - ONLY map fields that actually exist in Pinecone
        papers = []
        for match in results.matches:
            metadata = match.metadata or {}
            papers.append({
                "id": match.id,
                "score": match.score,  # Semantic similarity score (0-1)
                # Actual fields from Pinecone vectors
                "pmid": metadata.get("pmid", ""),
                "title": metadata.get("title", ""),
                "text": metadata.get("text", ""),  # The chunk content
                "journal": metadata.get("journal", ""),
                "publication_year": metadata.get("publication_year", 0),
                "mesh_terms": metadata.get("mesh_terms", []),
                "url": metadata.get("url", ""),
                "chunk_id": metadata.get("chunk_id", match.id),
            })
        
        return papers


# Singleton instance for easy import
_retriever_instance = None

def get_retriever() -> HybridRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
    return _retriever_instance
