"""
PubMed Service - Fetches real research citations from NIH PubMed database.
Uses caching to handle rate limits at scale.
"""
import asyncio
import hashlib
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Any
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.ext.declarative import declarative_base

logger = logging.getLogger(__name__)

# PubMed API endpoints (free, no auth required)
PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PUBMED_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class PubMedService:
    """
    Service for fetching real research papers from PubMed.
    Implements caching to avoid hitting rate limits.
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self.timeout = 10.0  # 10 second timeout
        
    async def get_citation_for_action(
        self,
        title: str,
        category: str,
        hormone: str,
        db: Optional[Session] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get a real PubMed citation for an action.
        Uses cache first, falls back to API.
        
        Args:
            title: Action title (e.g., "Chia Pudding")
            category: food, movement, mindfulness
            hormone: Target hormone (e.g., "androgens")
            db: Database session for caching
            
        Returns:
            Citation dict with title, journal, year, participants, finding
        """
        db = db or self.db
        
        # Generate cache key
        cache_key = self._generate_cache_key(title, category, hormone)
        
        # Check cache first
        if db:
            cached = self._get_cached_citation(cache_key, db)
            if cached:
                logger.info(f"📚 PubMed cache hit: {cache_key}")
                return cached
        
        # Build search query
        search_query = self._build_search_query(title, category, hormone)
        logger.info(f"🔍 PubMed search: {search_query}")
        
        try:
            # Search PubMed
            pmids = await self._search_pubmed(search_query)
            
            if not pmids:
                # Try simpler query
                simple_query = self._build_simple_query(category, hormone)
                pmids = await self._search_pubmed(simple_query)
            
            if not pmids:
                logger.warning(f"No PubMed results for: {search_query}")
                return None
            
            # Fetch paper details
            citation = await self._fetch_paper_details(pmids[0])
            
            if citation and db:
                # Cache the result
                self._cache_citation(cache_key, citation, db)
            
            return citation
            
        except Exception as e:
            logger.error(f"PubMed API error: {e}")
            return None
    
    def _generate_cache_key(self, title: str, category: str, hormone: str) -> str:
        """Generate a cache key from action details."""
        # Extract main keyword from title
        keywords = self._extract_keywords(title)
        key_string = f"{keywords}_{category}_{hormone}".lower()
        return hashlib.md5(key_string.encode()).hexdigest()[:16]
    
    def _extract_keywords(self, title: str) -> str:
        """Extract main keywords from action title."""
        # Remove common words
        stop_words = {"a", "an", "the", "and", "or", "with", "for", "in", "on", "to", 
                     "session", "routine", "exercise", "practice", "daily", "gentle",
                     "morning", "evening", "quick", "simple", "healthy", "nutritious"}
        
        words = re.findall(r'\b[a-zA-Z]+\b', title.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Return first 2 meaningful keywords
        return "_".join(keywords[:2]) if keywords else "wellness"
    
    def _build_search_query(self, title: str, category: str, hormone: str) -> str:
        """Build PubMed search query."""
        keywords = self._extract_keywords(title)
        keywords_spaced = keywords.replace("_", " ")
        
        # Build targeted query
        hormone_map = {
            "androgens": "androgen OR testosterone",
            "estrogen": "estrogen OR estradiol",
            "progesterone": "progesterone",
            "cortisol": "cortisol OR stress hormone",
            "insulin": "insulin OR glucose",
            "thyroid": "thyroid OR T3 OR T4"
        }
        
        hormone_terms = hormone_map.get(hormone.lower(), hormone)
        
        # Construct query - focus on women
        query = f'({keywords_spaced}) AND (women OR female) AND ({hormone_terms})'
        
        return query
    
    def _build_simple_query(self, category: str, hormone: str) -> str:
        """Build a simpler fallback query."""
        category_terms = {
            "food": "diet OR nutrition OR food",
            "movement": "exercise OR physical activity",
            "mindfulness": "mindfulness OR meditation OR stress reduction"
        }
        
        hormone_map = {
            "androgens": "androgen",
            "estrogen": "estrogen", 
            "progesterone": "progesterone",
            "cortisol": "cortisol",
            "insulin": "insulin",
            "thyroid": "thyroid"
        }
        
        cat_term = category_terms.get(category.lower(), "health")
        hormone_term = hormone_map.get(hormone.lower(), hormone)
        
        return f'({cat_term}) AND (women) AND ({hormone_term})'
    
    async def _search_pubmed(self, query: str, max_results: int = 3) -> List[str]:
        """Search PubMed and return PMIDs."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance"
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(PUBMED_SEARCH_URL, params=params)
            response.raise_for_status()
            
            data = response.json()
            pmids = data.get("esearchresult", {}).get("idlist", [])
            
            logger.info(f"PubMed found {len(pmids)} papers")
            return pmids
    
    async def _fetch_paper_details(self, pmid: str) -> Optional[Dict[str, Any]]:
        """Fetch paper details from PubMed."""
        params = {
            "db": "pubmed",
            "id": pmid,
            "retmode": "json"
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(PUBMED_SUMMARY_URL, params=params)
            response.raise_for_status()
            
            data = response.json()
            result = data.get("result", {})
            paper = result.get(pmid, {})
            
            if not paper or "error" in paper:
                return None
            
            # Extract relevant fields
            title = paper.get("title", "").strip()
            
            # Get journal name
            source = paper.get("source", "")
            
            # Get publication year
            pubdate = paper.get("pubdate", "")
            year_match = re.search(r'(\d{4})', pubdate)
            year = int(year_match.group(1)) if year_match else 2023
            
            # Get authors (first 3)
            authors = paper.get("authors", [])
            author_names = [a.get("name", "") for a in authors[:3]]
            authors_str = ", ".join(author_names)
            if len(authors) > 3:
                authors_str += " et al."
            
            # Try to get abstract snippet for finding
            # PubMed summary doesn't include abstract, so we use a generic finding
            finding = self._generate_finding_from_title(title)
            
            return {
                "title": title,
                "journal": source,
                "year": year,
                "participants": "Women",  # Most papers we search are about women
                "finding": finding,
                "pmid": pmid,
                "authors": authors_str
            }
    
    def _generate_finding_from_title(self, title: str) -> str:
        """Generate a brief finding description from paper title."""
        # Simple extraction of key claim from title
        title_lower = title.lower()
        
        if "improve" in title_lower or "enhance" in title_lower:
            return "Shows improvement in health outcomes"
        elif "reduce" in title_lower or "decrease" in title_lower:
            return "Demonstrates reduction in symptoms"
        elif "effect" in title_lower:
            return "Examines effects on women's health"
        elif "association" in title_lower or "relationship" in title_lower:
            return "Identifies health associations in women"
        else:
            return "Relevant research on women's health"
    
    def _get_cached_citation(self, cache_key: str, db: Session) -> Optional[Dict[str, Any]]:
        """Get citation from cache."""
        from app.core.database import PubMedCache
        
        try:
            cached = db.query(PubMedCache).filter(
                PubMedCache.cache_key == cache_key
            ).first()
            
            if cached:
                # Update access count
                cached.access_count += 1
                db.commit()
                
                return {
                    "title": cached.title,
                    "journal": cached.journal,
                    "year": cached.year,
                    "participants": cached.participants or "Women",
                    "finding": cached.finding
                }
        except Exception as e:
            logger.warning(f"Cache lookup failed: {e}")
        
        return None
    
    def _cache_citation(self, cache_key: str, citation: Dict[str, Any], db: Session) -> None:
        """Store citation in cache."""
        from app.core.database import PubMedCache
        
        try:
            cache_entry = PubMedCache(
                cache_key=cache_key,
                pubmed_id=citation.get("pmid", ""),
                title=citation.get("title", ""),
                journal=citation.get("journal", ""),
                year=citation.get("year", 2023),
                authors=citation.get("authors", ""),
                participants=citation.get("participants", "Women"),
                finding=citation.get("finding", ""),
                created_at=datetime.utcnow(),
                access_count=1
            )
            db.add(cache_entry)
            db.commit()
            logger.info(f"📚 Cached PubMed citation: {cache_key}")
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")
            db.rollback()


# Singleton for easy import
_pubmed_service: Optional[PubMedService] = None

def get_pubmed_service() -> PubMedService:
    """Get singleton PubMed service instance."""
    global _pubmed_service
    if _pubmed_service is None:
        _pubmed_service = PubMedService()
    return _pubmed_service
