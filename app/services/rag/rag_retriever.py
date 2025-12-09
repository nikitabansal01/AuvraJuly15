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
            logger.info(f"🔍 RAG Retriever: Enhanced query built for category={category}")
            
            # Step 2: Semantic search (dense vectors)
            semantic_results = await self._semantic_search(enhanced_query, top_k * 2)
            logger.info(f"🔍 RAG Retriever: Semantic search returned {len(semantic_results)} results")
            
            # Step 3: Keyword search (if implemented) - for now, use semantic only
            # keyword_results = await self._keyword_search(enhanced_query, top_k * 2)
            
            # Step 4: Apply filters based on category and user profile
            filtered_results = self._apply_filters(semantic_results, category, user_profile)
            logger.info(f"🔍 RAG Retriever: {len(filtered_results)} results after filtering")
            
            # Step 5: Score and sort by relevance
            scored_results = self._score_results(filtered_results, user_profile, category)
            
            return scored_results[:top_k]
            
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
        
        # Convert to standard format
        papers = []
        for match in results.matches:
            metadata = match.metadata or {}
            papers.append({
                "id": match.id,
                "score": match.score,
                "pmid": metadata.get("pmid", ""),
                "pmcid": metadata.get("pmcid", ""),
                "title": metadata.get("title", ""),
                "abstract": metadata.get("abstract", ""),
                "text": metadata.get("text", ""),  # Chunk text
                "authors": metadata.get("authors", []),
                "journal": metadata.get("journal", ""),
                "publication_year": metadata.get("publication_year", 0),
                "study_type": metadata.get("study_type", ""),
                "participant_count": metadata.get("num_of_participants", 0),
                "conditions": metadata.get("condition_disease", []),
                "interventions": metadata.get("intervention_type", []),
                "hormone_focus": metadata.get("hormone_focus", []),
                "mesh_terms": metadata.get("mesh_terms", []),
                "doi": metadata.get("doi", ""),
                "section_type": metadata.get("section_type", ""),
                "chunk_summary": metadata.get("chunk_summary", "")
            })
        
        return papers
    
    def _apply_filters(
        self,
        results: List[Dict[str, Any]],
        category: str,
        user_profile: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Apply category and profile-based filters"""
        filtered = []
        
        for paper in results:
            # Category filter
            interventions = paper.get("interventions", [])
            if isinstance(interventions, str):
                interventions = [interventions]
            
            # Check if paper's intervention matches category
            category_match = False
            for intervention in interventions:
                intervention_lower = intervention.lower() if intervention else ""
                if category in intervention_lower or intervention_lower in category:
                    category_match = True
                    break
            
            # Also check text content for category keywords
            text = paper.get("text", "").lower()
            title = paper.get("title", "").lower()
            for keyword in self.DOMAIN_KEYWORDS.get(category, []):
                if keyword in text or keyword in title:
                    category_match = True
                    break
            
            if category_match or not interventions:  # Include if no filter info
                filtered.append(paper)
        
        return filtered
    
    def _score_results(
        self,
        results: List[Dict[str, Any]],
        user_profile: Dict[str, Any],
        category: str
    ) -> List[Dict[str, Any]]:
        """Score results based on relevance to user profile"""
        for paper in results:
            score = paper.get("score", 0.5)  # Base semantic score
            
            # Study type bonus
            study_type = paper.get("study_type", "").lower()
            type_weight = self.STUDY_TYPE_WEIGHTS.get(study_type, 0.5)
            score *= (0.7 + 0.3 * type_weight)
            
            # Recency bonus (papers from last 5 years get boost)
            year = paper.get("publication_year", 2020)
            if year >= 2020:
                score *= 1.1
            elif year >= 2015:
                score *= 1.0
            else:
                score *= 0.9
            
            # Hormone match bonus
            user_hormones = set([
                user_profile.get("primary_imbalance", ""),
                *user_profile.get("secondary_imbalances", [])
            ])
            paper_hormones = set(paper.get("hormone_focus", []))
            hormone_overlap = len(user_hormones & paper_hormones)
            if hormone_overlap > 0:
                score *= (1 + 0.1 * hormone_overlap)
            
            # Participant count bonus (larger studies preferred)
            participants = paper.get("participant_count", 0)
            if participants > 100:
                score *= 1.1
            elif participants > 50:
                score *= 1.05
            
            paper["final_score"] = score
        
        # Sort by final score
        return sorted(results, key=lambda x: x.get("final_score", 0), reverse=True)


# Singleton instance for easy import
_retriever_instance = None

def get_retriever() -> HybridRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
    return _retriever_instance
