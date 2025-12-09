"""
RAG Retriever Module
Hybrid search combining semantic and keyword-based retrieval
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging
import os
import httpx
from enum import Enum

logger = logging.getLogger(__name__)


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
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.pinecone_api_key = os.getenv("PINECONE_API_KEY")
        self.pinecone_index = os.getenv("PINECONE_INDEX")
        
        # Debug logging for environment variables
        logger.info(f"🔧 RAG Retriever Init: OPENAI_API_KEY={'SET' if self.openai_api_key else 'MISSING'}")
        logger.info(f"🔧 RAG Retriever Init: PINECONE_API_KEY={'SET' if self.pinecone_api_key else 'MISSING'}")
        logger.info(f"🔧 RAG Retriever Init: PINECONE_INDEX={self.pinecone_index or 'MISSING'}")
        
    async def retrieve(
        self,
        query: str,
        user_profile: Dict[str, Any],
        category: str,
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Main retrieval method combining semantic and keyword search
        
        Args:
            query: Base search query
            user_profile: User's health profile from survey
            category: food, movement, or mindfulness
            top_k: Number of results to return
            
        Returns:
            List of retrieved paper chunks with metadata
        """
        try:
            # Step 1: Build enhanced query from user profile
            enhanced_query = self._build_enhanced_query(query, user_profile, category)
            logger.info(f"🔍 RAG Retriever: Enhanced query for {category}: {enhanced_query[:100]}...")
            
            # Step 2: Semantic search - Pinecone returns results ALREADY sorted by similarity
            semantic_results = await self._semantic_search(enhanced_query, top_k)
            
            if not semantic_results:
                logger.warning(f"⚠️ RAG Retriever: No results from Pinecone for {category}")
                return []
            
            # Log top match scores
            if semantic_results:
                top_scores = [r.get('score', 0) for r in semantic_results[:3]]
                logger.info(f"🔬 RAG Retriever: Retrieved {len(semantic_results)} papers for {category}, top scores: {top_scores}")
            
            # Return directly - Pinecone already sorted by relevance!
            # No need for extra filtering that might remove good papers
            return semantic_results
            
        except Exception as e:
            logger.error(f"❌ RAG Retriever failed: {str(e)}")
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
    
    def _apply_filters(
        self,
        results: List[Dict[str, Any]],
        category: str,
        user_profile: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Apply category and profile-based filters - LENIENT to avoid filtering out valid papers"""
        if not results:
            logger.warning(f"⚠️ Filter: No results to filter for {category}")
            return []
        
        filtered = []
        
        for paper in results:
            # Category filter - be LENIENT
            interventions = paper.get("interventions", [])
            if isinstance(interventions, str):
                interventions = [interventions]
            
            # Start with assumption: include unless proven irrelevant
            category_match = True  # Default to INCLUDE
            
            # Only exclude if interventions exist AND don't match
            if interventions:
                category_match = False
                for intervention in interventions:
                    intervention_lower = intervention.lower() if intervention else ""
                    if category in intervention_lower or intervention_lower in category:
                        category_match = True
                        break
            
            # Also check text content for category keywords (bonus match)
            text = paper.get("text", "").lower()
            title = paper.get("title", "").lower()
            for keyword in self.DOMAIN_KEYWORDS.get(category, []):
                if keyword in text or keyword in title:
                    category_match = True
                    break
            
            if category_match:
                filtered.append(paper)
        
        logger.info(f"🔍 Filter: {len(filtered)}/{len(results)} papers passed for category={category}")
        
        return filtered
    
    def _score_results(
        self,
        results: List[Dict[str, Any]],
        user_profile: Dict[str, Any],
        category: str
    ) -> List[Dict[str, Any]]:
        """Score results based on relevance - adapted for actual Pinecone metadata"""
        
        # PCOS/hormone relevant mesh terms for bonus scoring
        PCOS_RELEVANT_TERMS = {
            "polycystic ovary syndrome", "pcos", "insulin resistance", "hyperandrogenism",
            "menstrual cycle", "ovulation", "fertility", "diet therapy", "exercise therapy",
            "dietary supplements", "stress", "cortisol", "thyroid", "estrogen", "progesterone",
            "testosterone", "obesity", "weight loss", "blood glucose", "inflammation"
        }
        
        for paper in results:
            score = paper.get("score", 0.5)  # Base semantic similarity score from Pinecone
            
            # Recency bonus (papers from last 5 years get boost)
            year = paper.get("publication_year", 2015)
            if year >= 2020:
                score *= 1.15  # Recent papers get 15% boost
            elif year >= 2015:
                score *= 1.0
            else:
                score *= 0.9  # Older papers slightly penalized
            
            # MeSH terms relevance bonus
            mesh_terms = paper.get("mesh_terms", [])
            mesh_terms_lower = [t.lower() for t in mesh_terms if t]
            
            # Count how many PCOS-relevant terms appear in mesh terms
            relevance_count = sum(1 for term in PCOS_RELEVANT_TERMS if any(term in m for m in mesh_terms_lower))
            if relevance_count >= 3:
                score *= 1.2  # Highly relevant
            elif relevance_count >= 1:
                score *= 1.1  # Moderately relevant
            
            # Category-specific mesh term bonus
            category_mesh = {
                "food": ["diet", "nutrition", "supplement", "vitamin", "dietary"],
                "movement": ["exercise", "physical activity", "training", "yoga", "sports"],
                "mindfulness": ["stress", "anxiety", "meditation", "relaxation", "psychology"]
            }
            category_terms = category_mesh.get(category, [])
            if any(term in " ".join(mesh_terms_lower) for term in category_terms):
                score *= 1.1  # Category match bonus
            
            paper["final_score"] = score
        
        # Sort by final score (highest first)
        return sorted(results, key=lambda x: x.get("final_score", 0), reverse=True)


# Singleton instance for easy import
_retriever_instance = None

def get_retriever() -> HybridRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
    return _retriever_instance
