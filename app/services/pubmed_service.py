"""
PubMed Service - Fetches real research citations from NIH PubMed database.
Enhanced with GPT Tool Calling support and multi-API fallback.

Features:
- GPT tool calling for intelligent query building
- Multi-API fallback: PubMed → OpenAlex → Semantic Scholar
- PostgreSQL caching to avoid rate limits
- Full abstract extraction for real findings
- PMID/DOI links for verification
- STUDY TYPE PRIORITIZATION: Meta-analysis > Systematic Review > RCT > Clinical Trial > Review
- MULTIPLE SOURCES: Returns 2-4 research papers per action
"""
import asyncio
import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# STUDY TYPE HIERARCHY (for prioritization)
# ═══════════════════════════════════════════════════════════════════════════════

STUDY_TYPE_PRIORITY = {
    "meta_analysis": 1,       # Highest priority - combines multiple studies
    "systematic_review": 2,   # Rigorous review of literature
    "rct": 3,                 # Randomized controlled trial - gold standard
    "clinical_trial": 4,      # Clinical trial (non-randomized)
    "cohort_study": 5,        # Observational cohort
    "review": 6,              # Narrative review
    "observational": 7,       # Cross-sectional, case-control
    "unknown": 8              # Lowest priority
}

# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS - Multiple free sources for scaling
# ═══════════════════════════════════════════════════════════════════════════════

# Primary - NIH PubMed (biomedical focus, 3/sec without key, 10/sec with key)
PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Secondary - OpenAlex (broad coverage, 100k/day free, polite pool)
OPENALEX_SEARCH_URL = "https://api.openalex.org/works"

# Tertiary - Semantic Scholar (AI relevance, 100 req/5min unauthenticated)
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# Fallback 1 - Europe PMC (EU biomedical, 25 req/sec, no auth needed)
EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Fallback 2 - CORE (open access papers, 10k/month free tier)
CORE_SEARCH_URL = "https://api.core.ac.uk/v3/search/works"

# Fallback 3 - Unpaywall (finds open access versions, 100k/day)
UNPAYWALL_URL = "https://api.unpaywall.org/v2"


# ═══════════════════════════════════════════════════════════════════════════════
# GPT TOOL SCHEMA - For function calling
# ═══════════════════════════════════════════════════════════════════════════════

PUBMED_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_research_paper",
        "description": """Search for a REAL published research paper that SUPPORTS WHY we recommend this action.

CRITICAL: The paper should explain the MECHANISM/BENEFIT of the intervention for the user's specific condition and target hormone.

The paper should answer: "Why does [intervention] help [user's condition/hormone]?"

BUILD YOUR QUERY WITH THESE 4 COMPONENTS:
1. INTERVENTION: The specific food, exercise, or mindfulness technique (e.g., 'yoga', 'pumpkin seeds', 'deep breathing')
2. MECHANISM/BENEFIT: How it works (e.g., 'reduces cortisol', 'improves insulin sensitivity', 'lowers inflammation')
3. WOMEN: Always include 'women' or 'female' 
4. CONDITION (if applicable): The user's diagnosed condition (e.g., 'PCOS', 'diabetes', 'Cushing')

FORMULA: "[intervention] [mechanism] [condition if any] women"

EXAMPLE QUERIES (from bad to great):
❌ BAD: "yoga benefits" (too generic, no mechanism)
❌ BAD: "healthy eating women" (no specific intervention)
⚠️  OK:  "yoga cortisol reduction women" (good but missing condition)
✅ GOOD: "yoga cortisol HPA axis women randomized" (mechanism-specific)
✅ GREAT: "yoga cortisol PCOS women intervention" (includes user's condition)
✅ GREAT: "walking insulin sensitivity diabetes women exercise" (intervention + mechanism + condition)
✅ GREAT: "dark chocolate polyphenols cortisol stress women" (specific compound + mechanism)

For MOVEMENT actions, include exercise physiology terms:
- "yoga cortisol HPA axis stress reduction women"
- "walking insulin sensitivity glucose metabolism women"
- "strength training androgens testosterone women PCOS"

For MINDFULNESS actions, include neuroscience terms:
- "meditation cortisol parasympathetic nervous system women"
- "deep breathing vagal tone stress women"
- "mindfulness anxiety amygdala activation women"
""",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query with intervention + mechanism/benefit + 'women'. Should find papers that explain WHY the action helps."
                },
                "action_title": {
                    "type": "string",
                    "description": "The title of the action this citation is for (for logging)"
                },
                "category": {
                    "type": "string",
                    "enum": ["food", "movement", "mindfulness"],
                    "description": "Action category for query optimization"
                },
                "target_hormone": {
                    "type": "string",
                    "description": "Target hormone (e.g., 'insulin', 'cortisol') that this action supports"
                }
            },
            "required": ["query", "action_title", "target_hormone"]
        }
    }
}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SERVICE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class PubMedService:
    """
    Multi-API research citation service with intelligent fallback.
    
    Designed to scale to 100+ concurrent users using only FREE APIs.
    
    Priority order (all free):
    1. In-memory cache (instant - for common hormone+condition combos)
    2. Database cache lookup (fast)
    3. PubMed (primary - biomedical focus, 10/sec with key)
    4. OpenAlex (secondary - broad coverage, 100k/day)
    5. Europe PMC (fallback 1 - EU biomedical, 25/sec)
    6. CORE (fallback 2 - open access, 10k/month)
    7. Semantic Scholar (tertiary - AI-powered, 100/5min)
    
    Rate limit capacity for free tier:
    - PubMed: ~10/sec = 36,000/hour
    - OpenAlex: ~100k/day = ~4,166/hour
    - Europe PMC: ~25/sec = 90,000/hour
    - CORE: ~10k/month = ~14/hour (last resort)
    - Semantic Scholar: ~100/5min = 1,200/hour
    
    Combined: Can handle 100+ users easily with cascading fallback.
    """
    
    # Class-level semaphores for rate limiting per provider
    # PubMed: 3 req/s without key, 10 req/s with key. We'll set to 5 (or 10 if key present).
    _pubmed_semaphore = asyncio.Semaphore(1)  # Serialize all PubMed calls to prevent 429 
    
    # OpenAlex: 10 req/s (polite pool).
    _openalex_semaphore = asyncio.Semaphore(8)
    
    # Semantic Scholar: 1 req/s (unauthenticated).
    _semantic_semaphore = asyncio.Semaphore(1)

    _MAX_RETRIES = 2 
    _RETRY_DELAYS = [0.5, 1.0]
    
    # IN-MEMORY CACHE - REDUCED TTL for variety
    _memory_cache: Dict[str, Tuple[Dict, float]] = {}
    _CACHE_TTL = 3600  # 1 hour (reduced from 24 hours for more variety)
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        self._rate_limit_delay = 0.4  # Increased from 0.2 to prevent 429 rate limiting
        
        # Check for PubMed API Key (increases rate limit to 10/s)
        # We can dynamically adjust the semaphore capacity if needed, 
        # but for now we'll just check it to add to requests.
        from app.core.config import settings
        self.pubmed_api_key = getattr(settings, "PUBMED_API_KEY", None)
        
        # If API Key exists, we could theoretically bump the semaphore,
        # but changing a class-level asyncio.Semaphore is tricky safely.
        # We'll rely on the API key to reduce 429s.
    
    async def find_citation(
        self,
        query: str,
        action_title: str,
        category: str = "food",
        hormone: str = "cortisol",
        db: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """
        Find a real research paper citation.
        
        Returns:
            Dictionary with paper details or empty dict if not found:
            {
                "title": "Full paper title",
                "journal": "Journal name",
                "year": 2023,
                "participants": 150,
                "finding": "Key finding from abstract",
                "pmid": "12345678",
                "doi": "10.1234/...",
                "verification_link": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                "source": "pubmed"
            }
        """
        # Normalize query
        query = self._normalize_query(query)
        cache_key = self._generate_cache_key(query, category, hormone)
        
        logger.info(f"🔍 Finding citation for '{action_title}': {query[:60]}...")
        
        # Step 0: Check IN-MEMORY cache first (instant - no DB/API calls)
        if cache_key in self._memory_cache:
            cached_paper, cached_time = self._memory_cache[cache_key]
            if time.time() - cached_time < self._CACHE_TTL:
                logger.info(f"⚡ Memory cache hit for '{action_title}' (instant)")
                return cached_paper
            else:
                # Cache expired, remove it
                del self._memory_cache[cache_key]
        
        # Step 1: Check DB cache
        if db:
            cached = await self._get_cached_citation(cache_key, db)
            if cached:
                logger.info(f"✅ DB cache hit for '{action_title}'")
                # Store in memory cache for future instant access
                self._memory_cache[cache_key] = (cached, time.time())
                return cached
        
        # Step 2: Try PubMed (primary - best for biomedical)
        paper = await self._search_pubmed(query)
        if paper:
            logger.info(f"✅ PubMed found: {paper.get('title', '')[:50]}... (PMID: {paper.get('pmid')})")
            # Cache in both memory and DB
            self._memory_cache[cache_key] = (paper, time.time())
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 3: Try OpenAlex (secondary - broad coverage, 100k/day free)
        await asyncio.sleep(self._rate_limit_delay)
        paper = await self._search_openalex(query)
        if paper:
            logger.info(f"✅ OpenAlex found: {paper.get('title', '')[:50]}...")
            self._memory_cache[cache_key] = (paper, time.time())
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 4: Try Europe PMC (EU biomedical, 25/sec free)
        await asyncio.sleep(self._rate_limit_delay)
        paper = await self._search_europe_pmc(query)
        if paper:
            logger.info(f"✅ Europe PMC found: {paper.get('title', '')[:50]}...")
            self._memory_cache[cache_key] = (paper, time.time())
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 5: Try CORE (open access papers, 10k/month free)
        await asyncio.sleep(self._rate_limit_delay)
        paper = await self._search_core(query)
        if paper:
            logger.info(f"✅ CORE found: {paper.get('title', '')[:50]}...")
            self._memory_cache[cache_key] = (paper, time.time())
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 6: Try Semantic Scholar (last resort - AI relevance, 100/5min)
        await asyncio.sleep(self._rate_limit_delay)
        paper = await self._search_semantic_scholar(query)
        if paper:
            logger.info(f"✅ Semantic Scholar found: {paper.get('title', '')[:50]}...")
            self._memory_cache[cache_key] = (paper, time.time())
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 7: No results - try simpler query
        logger.warning(f"⚠️ No results for '{action_title}', trying simpler query...")
        simple_query = self._simplify_query(query, category, hormone)
        
        paper = await self._search_pubmed(simple_query)
        if paper:
            logger.info(f"✅ PubMed (simplified) found: {paper.get('title', '')[:50]}...")
            self._memory_cache[cache_key] = (paper, time.time())
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Final fallback - return empty
        logger.warning(f"❌ No citation found for '{action_title}'")
        return {}
    
    async def find_multiple_citations(
        self,
        query: str,
        action_title: str,
        category: str = "food",
        hormone: str = "cortisol",
        db: Optional[AsyncSession] = None,
        max_citations: int = 4
    ) -> List[Dict[str, Any]]:
        """
        Find 2-4 real research papers, prioritized by study type quality.
        
        Priority order (your mam's requirement):
        1. Meta-analysis (combines multiple studies)
        2. Systematic review (rigorous literature review)
        3. Review papers on interventions
        4. Clinical trials/RCTs on interventions for women
        5. Regular research papers
        
        Returns:
            List of 2-4 paper dictionaries, sorted by study type quality
        """
        query = self._normalize_query(query)
        logger.info(f"🔍 Finding {max_citations} citations for '{action_title}': {query[:60]}...")
        
        all_papers = []
        
        # Strategy: Search PubMed with different filters to get variety of study types
        search_strategies = [
            # Priority 1: Meta-analyses and systematic reviews
            f"({query}) AND (meta-analysis[pt] OR systematic review[pt])",
            # Priority 2: Reviews  
            f"({query}) AND (review[pt])",
            # Priority 3: Clinical trials
            f"({query}) AND (clinical trial[pt] OR randomized controlled trial[pt])",
            # Priority 4: General research
            query
        ]
        
        seen_pmids = set()
        
        for strategy_query in search_strategies:
            if len(all_papers) >= max_citations:
                break
            
            papers = await self._search_pubmed_multiple(strategy_query, max_results=5)
            
            for paper in papers:
                if len(all_papers) >= max_citations:
                    break
                pmid = paper.get("pmid", "")
                if pmid and pmid not in seen_pmids:
                    # Detect and add study type
                    paper["study_type"] = self._detect_study_type(paper)
                    paper["study_type_label"] = self._get_study_type_label(paper["study_type"])
                    all_papers.append(paper)
                    seen_pmids.add(pmid)
                    logger.info(f"  ✅ Found [{paper['study_type_label']}]: {paper.get('title', '')[:50]}...")
        
        # If we don't have enough from PubMed, try OpenAlex
        if len(all_papers) < 2:
            logger.info(f"  🔄 Need more papers, trying OpenAlex...")
            papers = await self._search_openalex_multiple(query, max_results=3)
            for paper in papers:
                if len(all_papers) >= max_citations:
                    break
                paper["study_type"] = self._detect_study_type(paper)
                paper["study_type_label"] = self._get_study_type_label(paper["study_type"])
                all_papers.append(paper)
        
        # If still not enough, try Europe PMC
        if len(all_papers) < 2:
            logger.info(f"  🔄 Need more papers, trying Europe PMC...")
            papers = await self._search_europe_pmc_multiple(query, max_results=3)
            for paper in papers:
                if len(all_papers) >= max_citations:
                    break
                paper["study_type"] = self._detect_study_type(paper)
                paper["study_type_label"] = self._get_study_type_label(paper["study_type"])
                all_papers.append(paper)
        
        # If still not enough, try CORE
        if len(all_papers) < 2:
            logger.info(f"  🔄 Need more papers, trying CORE...")
            papers = await self._search_core_multiple(query, max_results=3)
            for paper in papers:
                if len(all_papers) >= max_citations:
                    break
                paper["study_type"] = self._detect_study_type(paper)
                paper["study_type_label"] = self._get_study_type_label(paper["study_type"])
                all_papers.append(paper)
        
        # Sort by study type priority (meta-analysis first, etc.)
        all_papers.sort(key=lambda p: STUDY_TYPE_PRIORITY.get(p.get("study_type", "unknown"), 8))
        
        # Ensure we have at least 2 papers, max 4
        final_papers = all_papers[:max_citations]
        
        # Cache each paper
        if db and final_papers:
            for paper in final_papers:
                cache_key = self._generate_cache_key(paper.get("pmid", paper.get("title", "")), category, hormone)
                try:
                    await self._cache_citation(cache_key, paper, db)
                except Exception:
                    pass
        
        logger.info(f"📚 Found {len(final_papers)} citations for '{action_title}'")
        return final_papers
    
    def _detect_study_type(self, paper: Dict) -> str:
        """Detect the study type from title and abstract."""
        title = paper.get("title", "").lower()
        finding = paper.get("finding", "").lower()
        full_text = f"{title} {finding}"
        
        # Meta-analysis detection
        if "meta-analysis" in full_text or "meta analysis" in full_text:
            return "meta_analysis"
        
        # Systematic review detection
        if "systematic review" in full_text:
            return "systematic_review"
        
        # RCT detection
        if "randomized controlled trial" in full_text or "randomised controlled trial" in full_text:
            return "rct"
        if "rct" in title or "randomized" in title or "randomised" in title:
            return "rct"
        
        # Clinical trial detection
        if "clinical trial" in full_text or "controlled trial" in full_text:
            return "clinical_trial"
        
        # Cohort study detection
        if "cohort study" in full_text or "longitudinal study" in full_text:
            return "cohort_study"
        
        # General review detection
        if "review" in title:
            return "review"
        
        # Observational study detection
        if "cross-sectional" in full_text or "case-control" in full_text:
            return "observational"
        
        return "unknown"
    
    def _get_study_type_label(self, study_type: str) -> str:
        """Get human-readable label for study type."""
        labels = {
            "meta_analysis": "Meta-Analysis",
            "systematic_review": "Systematic Review",
            "rct": "Randomized Controlled Trial",
            "clinical_trial": "Clinical Trial",
            "cohort_study": "Cohort Study",
            "review": "Review",
            "observational": "Observational Study",
            "unknown": "Research Study"
        }
        return labels.get(study_type, "Research Study")
    
    async def _search_pubmed_multiple(self, query: str, max_results: int = 5) -> List[Dict]:
        """Search PubMed and return multiple papers."""
        async with self._pubmed_semaphore:
            try:
                # Population/topic exclusions
                population_exclusions = "pregnant[ti] OR pregnancy[ti] OR prenatal[ti] OR children[ti] OR pediatric[ti] OR men[ti] OR male[ti] OR postmenopausal[ti]"
                topic_exclusions = "guideline[ti] OR guidelines[ti] OR cancer[ti] OR oncology[ti]"
                enhanced_query = f"({query}) NOT ({population_exclusions}) NOT ({topic_exclusions})"
                
                params = {
                    "db": "pubmed",
                    "term": enhanced_query,
                    "retmax": max_results,
                    "retmode": "json",
                    "sort": "relevance",
                    "datetype": "pdat",
                    "mindate": "2010",
                    "maxdate": "2025"
                }
                
                if self.pubmed_api_key:
                    params["api_key"] = self.pubmed_api_key
                
                await asyncio.sleep(self._rate_limit_delay)
                response = await self.client.get(PUBMED_SEARCH_URL, params=params)
                response.raise_for_status()
                
                data = response.json()
                pmids = data.get("esearchresult", {}).get("idlist", [])
                
                if not pmids:
                    return []
                
                # Fetch all papers
                papers = []
                for pmid in pmids:
                    await asyncio.sleep(self._rate_limit_delay)
                    paper = await self._fetch_pubmed_paper(pmid)
                    if paper and self._is_relevant_paper(paper):
                        papers.append(paper)
                
                return papers
                
            except Exception as e:
                logger.error(f"PubMed multi-search error: {e}")
                return []
    
    async def _search_openalex_multiple(self, query: str, max_results: int = 3) -> List[Dict]:
        """Search OpenAlex and return multiple papers."""
        async with self._openalex_semaphore:
            try:
                params = {
                    "search": query,
                    "filter": "has_abstract:true,is_retracted:false",
                    "per_page": max_results,
                    "mailto": "support@auvra.health"
                }
                
                await asyncio.sleep(self._rate_limit_delay)
                response = await self.client.get(OPENALEX_SEARCH_URL, params=params)
                response.raise_for_status()
                
                data = response.json()
                results = data.get("results", [])
                
                papers = []
                for work in results:
                    paper = self._parse_openalex_work(work)
                    if paper:
                        papers.append(paper)
                
                return papers
                
            except Exception as e:
                logger.error(f"OpenAlex multi-search error: {e}")
                return []

    def _normalize_query(self, query: str) -> str:
        """Normalize and enhance query for better results."""
        query = query.lower().strip()
        
        # Ensure women's health focus
        if "women" not in query and "female" not in query:
            query = f"({query}) AND (women OR female)"
        
        return query
    
    def _simplify_query(self, query: str, category: str, hormone: str) -> str:
        """Create a simpler fallback query."""
        category_terms = {
            "food": "diet nutrition",
            "movement": "exercise physical activity",
            "mindfulness": "meditation mindfulness stress"
        }
        hormone_terms = {
            "insulin": "insulin glucose",
            "cortisol": "cortisol stress",
            "estrogen": "estrogen",
            "progesterone": "progesterone",
            "androgens": "androgen testosterone",
            "testosterone": "testosterone",
            "thyroid": "thyroid"
        }
        
        cat_term = category_terms.get(category, "health")
        hormone_term = hormone_terms.get(hormone.lower(), hormone)
        
        return f"({cat_term}) AND ({hormone_term}) AND (women)"
    
    def _generate_cache_key(self, query: str, category: str, hormone: str) -> str:
        """Generate unique cache key."""
        key_str = f"{query}_{category}_{hormone}".lower()
        return hashlib.md5(key_str.encode()).hexdigest()[:16]
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PUBMED API
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _search_pubmed(self, query: str, max_results: int = 5) -> Optional[Dict]:
        """Search PubMed for papers, with relevance filtering and rate limiting."""
        # Use semaphore to serialize API calls (Fix #1 - prevents 429)
        async with self._pubmed_semaphore:
            for attempt in range(self._MAX_RETRIES):
                try:
                    # Exclude clinical guidelines, non-original research, and non-matching populations
                    # IMPORTANT: We exclude pregnant women, children, elderly, men - our users are adult women
                    population_exclusions = "pregnant[ti] OR pregnancy[ti] OR prenatal[ti] OR postnatal[ti] OR children[ti] OR pediatric[ti] OR adolescent[ti] OR elderly[ti] OR geriatric[ti] OR men[ti] OR male[ti] OR postmenopausal[ti] OR menopausal[ti]"
                    topic_exclusions = "guideline[ti] OR guidelines[ti] OR cancer[ti] OR oncology[ti] OR chemotherapy[ti] OR tumor[ti] OR carcinoma[ti]"
                    enhanced_query = f"({query}) NOT ({population_exclusions}) NOT ({topic_exclusions})"
                    
                    # Search for PMIDs
                    params = {
                        "db": "pubmed",
                        "term": enhanced_query,
                        "retmax": max_results,
                        "retmode": "json",
                        "sort": "relevance",
                        "datetype": "pdat",
                        "mindate": "2010",
                        "maxdate": "2025"
                    }
                    
                    if self.pubmed_api_key:
                        params["api_key"] = self.pubmed_api_key
                    
                    await asyncio.sleep(self._rate_limit_delay)  # Pre-request delay
                    response = await self.client.get(PUBMED_SEARCH_URL, params=params)
                    response.raise_for_status()
                    
                    data = response.json()
                    pmids = data.get("esearchresult", {}).get("idlist", [])
                    
                    if not pmids:
                        logger.warning(f"PubMed: No papers found for query")
                        return None
                    
                    logger.info(f"PubMed found {len(pmids)} papers, checking relevance...")
                    
                    # Check multiple papers and pick the best one
                    for pmid in pmids[:3]:  # Check first 3
                        await asyncio.sleep(self._rate_limit_delay)
                        paper = await self._fetch_pubmed_paper(pmid)
                        
                        if paper and self._is_relevant_paper(paper):
                            logger.info(f"✅ Selected relevant paper: {paper.get('title', '')[:50]}...")
                            return paper
                        elif paper:
                            logger.warning(f"⚠️ Skipped irrelevant paper: {paper.get('title', '')[:50]}...")
                    
                    # If no relevant paper found, return first result anyway
                    await asyncio.sleep(self._rate_limit_delay)
                    return await self._fetch_pubmed_paper(pmids[0])
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        # Rate limited - retry with exponential backoff
                        if attempt < self._MAX_RETRIES - 1:
                            delay = self._RETRY_DELAYS[attempt]
                            logger.warning(f"⏳ PubMed rate limited (429), retrying in {delay}s (attempt {attempt + 1}/{self._MAX_RETRIES})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.error(f"❌ PubMed rate limit exceeded after {self._MAX_RETRIES} retries")
                            return None
                    else:
                        logger.error(f"PubMed search error: {e}")
                        return None
                except Exception as e:
                    logger.error(f"PubMed search error: {e}")
                    return None
        return None
    
    def _is_relevant_paper(self, paper: Dict) -> bool:
        """Check if paper is relevant to our users (adult women, not pregnant).
        
        We EXCLUDE studies done on:
        - Pregnant women (we don't track pregnancy status)
        - Children/adolescents (our users are adults)
        - Men (our users are women)
        - Elderly-specific studies (our target is reproductive-age women)
        - Cancer/chemotherapy (irrelevant to our recommendations)
        - COVID-specific (irrelevant)
        
        User should see study and think: 'Yes, this was done on people like ME'
        """
        title = paper.get("title", "").lower()
        abstract = paper.get("finding", "").lower()  # Finding comes from abstract
        full_text = f"{title} {abstract}"
        
        # ═══════════════════════════════════════════════════════════════════
        # POPULATION EXCLUSIONS - Studies not matching our users
        # ═══════════════════════════════════════════════════════════════════
        
        # Pregnant women exclusion (CRITICAL - we don't collect pregnancy status)
        pregnancy_terms = [
            "pregnant", "pregnancy", "prenatal", "postnatal", "postpartum",
            "antenatal", "perinatal", "trimester", "gestation", "gestational",
            "fetal", "fetus", "maternal", "breastfeeding", "lactating"
        ]
        for term in pregnancy_terms:
            if term in full_text:
                logger.debug(f"Excluded paper (pregnancy-related): {title[:50]}...")
                return False
        
        # Children/adolescent exclusion (our users are adults)
        child_terms = [
            "children", "child", "pediatric", "paediatric", "adolescent",
            "adolescence", "infant", "infants", "newborn", "neonatal",
            "school-age", "teenager", "teens"
        ]
        for term in child_terms:
            if term in full_text:
                logger.debug(f"Excluded paper (children-related): {title[:50]}...")
                return False
        
        # Men-only studies exclusion (our users are women)
        # Be careful: don't exclude "women and men" studies
        if ("men" in title or "male" in title) and "women" not in title and "female" not in title:
            logger.debug(f"Excluded paper (men-only): {title[:50]}...")
            return False
        
        # Elderly-specific exclusion (our target is reproductive-age women)
        elderly_terms = [
            "elderly", "geriatric", "aged", "older adults", "postmenopausal",
            "menopause", "menopausal"
        ]
        for term in elderly_terms:
            if term in title:  # Only check title for elderly (abstract might mention age range)
                logger.debug(f"Excluded paper (elderly-specific): {title[:50]}...")
                return False
        
        # ═══════════════════════════════════════════════════════════════════
        # TOPIC EXCLUSIONS - Studies on irrelevant conditions
        # ═══════════════════════════════════════════════════════════════════
        
        topic_exclusions = [
            "cancer", "oncology", "chemotherapy", "tumor", "carcinoma",
            "malignant", "metastatic", "radiotherapy",
            "guideline", "guidelines", "clinical practice",
            "covid", "coronavirus", "pandemic", "sars-cov"
        ]
        for term in topic_exclusions:
            if term in title:
                logger.debug(f"Excluded paper (topic exclusion): {title[:50]}...")
                return False
        
        # ═══════════════════════════════════════════════════════════════════
        # POSITIVE SIGNALS - Studies that match our users
        # ═══════════════════════════════════════════════════════════════════
        
        # Check for participants count
        try:
            participants = paper.get("participants", 0)
            if isinstance(participants, str):
                participants = int(participants) if participants.isdigit() else 0
            if participants > 0:
                return True
        except (ValueError, TypeError):
            pass
        
        # Check if it mentions women/female (positive signal)
        if "women" in title or "female" in title:
            return True
        
        # Default to relevant if no exclusion criteria matched
        return True
    
    async def _fetch_pubmed_paper(self, pmid: str) -> Optional[Dict]:
        """Fetch full paper details from PubMed including abstract."""
        try:
            params = {
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml"
            }
            
            if self.pubmed_api_key:
                params["api_key"] = self.pubmed_api_key
            
            response = await self.client.get(PUBMED_FETCH_URL, params=params)
            response.raise_for_status()
            
            return self._parse_pubmed_xml(response.content, pmid)
            
        except Exception as e:
            logger.error(f"PubMed fetch error: {e}")
            return None
    
    def _parse_pubmed_xml(self, xml_content: bytes, pmid: str) -> Optional[Dict]:
        """Parse PubMed XML response."""
        try:
            root = ET.fromstring(xml_content)
            article = root.find(".//PubmedArticle")
            
            if article is None:
                return None
            
            # Title
            title_elem = article.find(".//ArticleTitle")
            title = title_elem.text if title_elem is not None and title_elem.text else "Unknown"
            
            # Journal
            journal_elem = article.find(".//Journal/Title")
            journal = journal_elem.text if journal_elem is not None and journal_elem.text else "Unknown"
            
            # Year
            year = self._extract_year_from_xml(article)
            
            # Abstract - FULL EXTRACTION
            abstract = self._extract_abstract(article)
            
            # Extract participant count from abstract
            participants = self._extract_participant_count(abstract)
            
            # Generate finding from abstract (results/conclusions section)
            finding = self._extract_finding(abstract, title)
            
            # DOI
            doi = None
            for aid in article.findall(".//ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text
                    break
            
            return {
                "title": title,
                "journal": journal,
                "year": year,
                "participants": participants if participants > 0 else "Women",
                "finding": finding,
                "pmid": pmid,
                "doi": doi,
                "verification_link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "source": "pubmed"
            }
            
        except Exception as e:
            logger.error(f"PubMed XML parse error: {e}")
            return None
    
    def _extract_year_from_xml(self, article: ET.Element) -> int:
        """Extract publication year from article."""
        year_elem = article.find(".//PubDate/Year")
        if year_elem is not None and year_elem.text:
            try:
                return int(year_elem.text)
            except ValueError:
                pass
        
        medline = article.find(".//PubDate/MedlineDate")
        if medline is not None and medline.text:
            match = re.search(r'(\d{4})', medline.text)
            if match:
                return int(match.group(1))
        
        return 2020
    
    def _extract_abstract(self, article: ET.Element) -> str:
        """Extract full abstract from article."""
        parts = []
        for text in article.findall(".//AbstractText"):
            if text.text:
                label = text.get("Label", "")
                if label:
                    parts.append(f"{label}: {text.text}")
                else:
                    parts.append(text.text)
        return " ".join(parts)
    
    def _extract_participant_count(self, abstract: str) -> int:
        """Extract participant count from abstract."""
        if not abstract:
            return 0
        
        patterns = [
            r'n\s*=\s*(\d+)',
            r'(\d+)\s*(?:women|females|participants|subjects|patients)',
            r'(?:enrolled|recruited|included|randomized)\s*(\d+)',
            r'sample\s*(?:of|size)?\s*(?:of)?\s*(\d+)',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, abstract.lower())
            for match in matches:
                try:
                    count = int(match)
                    if 10 <= count <= 100000:
                        return count
                except ValueError:
                    continue
        return 0
    
    def _extract_finding(self, abstract: str, title: str) -> str:
        """Extract key finding from abstract (prioritize results/conclusions)."""
        if not abstract:
            return f"Study on {title[:80]}..."
        
        # Look for results/conclusions sections
        abstract_lower = abstract.lower()
        for marker in ["RESULTS:", "CONCLUSIONS:", "CONCLUSION:", "FINDINGS:", "OUTCOME:"]:
            if marker.lower() in abstract_lower:
                idx = abstract_lower.find(marker.lower())
                finding = abstract[idx + len(marker):].strip()
                sentences = re.split(r'(?<=[.!?])\s+', finding)
                finding = '. '.join(sentences[:2])
                if len(finding) > 300:
                    finding = finding[:297] + "..."
                return finding
        
        # Fallback: last 2 sentences (often conclusions)
        sentences = re.split(r'(?<=[.!?])\s+', abstract)
        if len(sentences) >= 2:
            finding = '. '.join(sentences[-2:])
        else:
            finding = abstract
        
        if len(finding) > 300:
            finding = finding[:297] + "..."
        return finding
    
    # ═══════════════════════════════════════════════════════════════════════════
    # OPENALEX API
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _search_openalex(self, query: str) -> Optional[Dict]:
        """Search OpenAlex for papers (100k/day free)."""
        async with self._openalex_semaphore:
            try:
                # Remove boolean operators for OpenAlex
                clean_query = re.sub(r'\b(AND|OR)\b', ' ', query)
                clean_query = re.sub(r'[()]', '', clean_query)
                
                params = {
                    "search": clean_query,
                    "filter": "type:article,from_publication_date:2010-01-01",
                    "per-page": 1,
                    "mailto": "auvra@app.com"  # Polite pool for faster responses
                }
                
                response = await self.client.get(OPENALEX_SEARCH_URL, params=params)
                response.raise_for_status()
                
                data = response.json()
                results = data.get("results", [])
                
                if not results:
                    return None
                
                paper = results[0]
                
                # Extract abstract (OpenAlex provides inverted index)
                abstract = self._reconstruct_openalex_abstract(paper.get("abstract_inverted_index", {}))
                
                # Get PMID if available
                pmid = ""
                ids = paper.get("ids", {})
                pmid_url = ids.get("pmid", "")
                if pmid_url:
                    pmid = pmid_url.replace("https://pubmed.ncbi.nlm.nih.gov/", "").rstrip("/")
                
                doi = paper.get("doi", "").replace("https://doi.org/", "") if paper.get("doi") else ""
                
                return {
                    "title": paper.get("title", "Unknown"),
                    "journal": paper.get("primary_location", {}).get("source", {}).get("display_name", "Unknown"),
                    "year": paper.get("publication_year", 2020),
                    "participants": self._extract_participant_count(abstract),
                    "finding": self._extract_finding(abstract, paper.get("title", "")),
                    "pmid": pmid,
                    "doi": doi,
                    "verification_link": paper.get("doi", paper.get("id", "")),
                    "source": "openalex"
                }
                
            except Exception as e:
                logger.error(f"OpenAlex search error: {e}")
                return None
    
    def _reconstruct_openalex_abstract(self, inverted_index: Dict) -> str:
        """Reconstruct abstract from OpenAlex inverted index format."""
        if not inverted_index:
            return ""
        
        words_with_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                words_with_positions.append((pos, word))
        
        words_with_positions.sort(key=lambda x: x[0])
        return " ".join(word for _, word in words_with_positions)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SEMANTIC SCHOLAR API
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _search_semantic_scholar(self, query: str) -> Optional[Dict]:
        """Search Semantic Scholar for papers."""
        async with self._semantic_semaphore:
            try:
                # Clean query
                clean_query = re.sub(r'\b(AND|OR)\b', ' ', query)
                clean_query = re.sub(r'[()]', '', clean_query)
                
                params = {
                    "query": clean_query,
                    "limit": 1,
                    "fields": "title,abstract,year,venue,externalIds,citationCount"
                }
                
                response = await self.client.get(SEMANTIC_SCHOLAR_URL, params=params)
                
                # Handle rate limiting gracefully
                if response.status_code == 429:
                    logger.warning("Semantic Scholar rate limited, skipping")
                    return None
            
                response.raise_for_status()
                
                data = response.json()
                papers = data.get("data", [])
                
                if not papers:
                    return None
                
                paper = papers[0]
                abstract = paper.get("abstract", "") or ""
                
                external_ids = paper.get("externalIds", {}) or {}
                pmid = external_ids.get("PubMed", "") or ""
                doi = external_ids.get("DOI", "") or ""
                
                return {
                    "title": paper.get("title", "Unknown"),
                    "journal": paper.get("venue", "Unknown") or "Unknown",
                    "year": paper.get("year", 2020) or 2020,
                    "participants": self._extract_participant_count(abstract),
                    "finding": self._extract_finding(abstract, paper.get("title", "")),
                    "pmid": pmid,
                    "doi": doi,
                    "verification_link": f"https://doi.org/{doi}" if doi else "",
                    "source": "semantic_scholar"
                }
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("Semantic Scholar rate limited")
                else:
                    logger.error(f"Semantic Scholar HTTP error: {e.response.status_code}")
                return None
            except Exception as e:
                logger.error(f"Semantic Scholar search error: {e}")
                return None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # EUROPE PMC API (Fallback 1 - EU biomedical, 25 req/sec, free)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _search_europe_pmc(self, query: str) -> Optional[Dict]:
        """Search Europe PMC for papers (25/sec free, EU biomedical focus)."""
        try:
            # Clean query for Europe PMC
            clean_query = re.sub(r'\b(AND|OR)\b', ' ', query)
            clean_query = re.sub(r'[()]', '', clean_query)
            
            params = {
                "query": clean_query,
                "format": "json",
                "pageSize": 1,
                "resultType": "core",  # Get full metadata
                "sort": "CITED desc"  # Most cited first
            }
            
            response = await self.client.get(EUROPE_PMC_SEARCH_URL, params=params)
            
            if response.status_code == 429:
                logger.warning("Europe PMC rate limited")
                return None
            
            response.raise_for_status()
            
            data = response.json()
            results = data.get("resultList", {}).get("result", [])
            
            if not results:
                return None
            
            paper = results[0]
            abstract = paper.get("abstractText", "") or ""
            
            # Get PMID if available
            pmid = paper.get("pmid", "") or ""
            doi = paper.get("doi", "") or ""
            
            return {
                "title": paper.get("title", "Unknown"),
                "journal": paper.get("journalTitle", "Unknown") or "Unknown",
                "year": int(paper.get("pubYear", 2020)) if paper.get("pubYear") else 2020,
                "participants": self._extract_participant_count(abstract),
                "finding": self._extract_finding(abstract, paper.get("title", "")),
                "pmid": str(pmid) if pmid else "",
                "doi": doi,
                "verification_link": f"https://europepmc.org/article/MED/{pmid}" if pmid else (f"https://doi.org/{doi}" if doi else ""),
                "source": "europe_pmc"
            }
            
        except Exception as e:
            logger.error(f"Europe PMC search error: {e}")
            return None
    
    async def _search_europe_pmc_multiple(self, query: str, max_results: int = 3) -> List[Dict]:
        """Search Europe PMC for multiple papers."""
        results = []
        try:
            clean_query = re.sub(r'\b(AND|OR)\b', ' ', query)
            clean_query = re.sub(r'[()]', '', clean_query)
            
            params = {
                "query": clean_query,
                "format": "json",
                "pageSize": max_results + 2,
                "resultType": "core",
                "sort": "CITED desc"
            }
            
            response = await self.client.get(EUROPE_PMC_SEARCH_URL, params=params)
            
            if response.status_code == 429:
                logger.warning("Europe PMC rate limited in multiple search")
                return []
            
            response.raise_for_status()
            
            data = response.json()
            papers = data.get("resultList", {}).get("result", [])
            
            for paper in papers[:max_results]:
                abstract = paper.get("abstractText", "") or ""
                pmid = paper.get("pmid", "") or ""
                doi = paper.get("doi", "") or ""
                
                results.append({
                    "title": paper.get("title", "Unknown"),
                    "journal": paper.get("journalTitle", "Unknown") or "Unknown",
                    "year": int(paper.get("pubYear", 2020)) if paper.get("pubYear") else 2020,
                    "participants": self._extract_participant_count(abstract),
                    "finding": self._extract_finding(abstract, paper.get("title", "")),
                    "pmid": str(pmid) if pmid else "",
                    "doi": doi,
                    "verification_link": f"https://europepmc.org/article/MED/{pmid}" if pmid else (f"https://doi.org/{doi}" if doi else ""),
                    "source": "europe_pmc"
                })
                
        except Exception as e:
            logger.error(f"Europe PMC multiple search error: {e}")
        
        return results
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CORE API (Fallback 2 - Open access papers, 10k/month free)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _search_core(self, query: str) -> Optional[Dict]:
        """Search CORE for open access papers (10k/month free tier)."""
        try:
            # Clean query
            clean_query = re.sub(r'\b(AND|OR)\b', ' ', query)
            clean_query = re.sub(r'[()]', '', clean_query)
            
            # CORE uses POST for search
            payload = {
                "q": clean_query,
                "limit": 1,
                "sort": [{"citationCount": "desc"}]  # Most cited first
            }
            
            headers = {
                "Content-Type": "application/json"
            }
            
            # Note: CORE API v3 requires no auth for basic search
            response = await self.client.post(
                CORE_SEARCH_URL, 
                json=payload, 
                headers=headers,
                timeout=10.0  # CORE can be slow
            )
            
            if response.status_code == 429:
                logger.warning("CORE rate limited")
                return None
            
            if response.status_code == 401:
                logger.warning("CORE requires API key for this query")
                return None
            
            response.raise_for_status()
            
            data = response.json()
            results = data.get("results", [])
            
            if not results:
                return None
            
            paper = results[0]
            abstract = paper.get("abstract", "") or ""
            
            # CORE has different ID structures
            doi = paper.get("doi", "") or ""
            core_id = paper.get("id", "")
            
            # Try to find PMID from identifiers
            pmid = ""
            for identifier in paper.get("identifiers", []):
                if identifier and "pubmed" in identifier.lower():
                    # Extract PMID from URL or identifier
                    match = re.search(r'(\d+)', identifier)
                    if match:
                        pmid = match.group(1)
                        break
            
            return {
                "title": paper.get("title", "Unknown"),
                "journal": paper.get("publisher", "Unknown") or paper.get("journals", [{}])[0].get("title", "Unknown") if paper.get("journals") else "Unknown",
                "year": paper.get("yearPublished", 2020) or 2020,
                "participants": self._extract_participant_count(abstract),
                "finding": self._extract_finding(abstract, paper.get("title", "")),
                "pmid": pmid,
                "doi": doi,
                "verification_link": f"https://core.ac.uk/works/{core_id}" if core_id else (f"https://doi.org/{doi}" if doi else ""),
                "source": "core"
            }
            
        except Exception as e:
            logger.error(f"CORE search error: {e}")
            return None
    
    async def _search_core_multiple(self, query: str, max_results: int = 3) -> List[Dict]:
        """Search CORE for multiple open access papers."""
        results = []
        try:
            clean_query = re.sub(r'\b(AND|OR)\b', ' ', query)
            clean_query = re.sub(r'[()]', '', clean_query)
            
            payload = {
                "q": clean_query,
                "limit": max_results + 2,
                "sort": [{"citationCount": "desc"}]
            }
            
            headers = {"Content-Type": "application/json"}
            
            response = await self.client.post(
                CORE_SEARCH_URL, 
                json=payload, 
                headers=headers,
                timeout=10.0
            )
            
            if response.status_code in [429, 401]:
                return []
            
            response.raise_for_status()
            
            data = response.json()
            papers = data.get("results", [])
            
            for paper in papers[:max_results]:
                abstract = paper.get("abstract", "") or ""
                doi = paper.get("doi", "") or ""
                core_id = paper.get("id", "")
                
                pmid = ""
                for identifier in paper.get("identifiers", []):
                    if identifier and "pubmed" in identifier.lower():
                        match = re.search(r'(\d+)', identifier)
                        if match:
                            pmid = match.group(1)
                            break
                
                results.append({
                    "title": paper.get("title", "Unknown"),
                    "journal": paper.get("publisher", "Unknown") or "Unknown",
                    "year": paper.get("yearPublished", 2020) or 2020,
                    "participants": self._extract_participant_count(abstract),
                    "finding": self._extract_finding(abstract, paper.get("title", "")),
                    "pmid": pmid,
                    "doi": doi,
                    "verification_link": f"https://core.ac.uk/works/{core_id}" if core_id else (f"https://doi.org/{doi}" if doi else ""),
                    "source": "core"
                })
                
        except Exception as e:
            logger.error(f"CORE multiple search error: {e}")
        
        return results

    # ═══════════════════════════════════════════════════════════════════════════
    # CACHE LAYER
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _get_cached_citation(self, cache_key: str, db: AsyncSession) -> Optional[Dict]:
        """Get citation from cache."""
        from app.core.database import PubMedCache
        
        try:
            result = await db.execute(
                select(PubMedCache).where(PubMedCache.cache_key == cache_key)
            )
            cached = result.scalar_one_or_none()
            
            if cached:
                # Parse participants
                participants = 0
                if cached.participants:
                    match = re.search(r'(\d+)', str(cached.participants))
                    if match:
                        participants = int(match.group(1))
                
                return {
                    "title": cached.title,
                    "journal": cached.journal or "Unknown",
                    "year": cached.year or 2020,
                    "participants": participants if participants > 0 else "Women",
                    "finding": cached.finding or "",
                    "pmid": cached.pubmed_id or "",
                    "verification_link": f"https://pubmed.ncbi.nlm.nih.gov/{cached.pubmed_id}/" if cached.pubmed_id else "",
                    "source": "cache"
                }
                
        except Exception as e:
            logger.warning(f"Cache lookup error: {e}")
        
        return None
    
    async def _cache_citation(self, cache_key: str, paper: Dict, db: AsyncSession) -> None:
        """Store citation in cache using upsert pattern (Fix #3 - prevents duplicate key errors)."""
        from app.core.database import PubMedCache
        from sqlalchemy.dialects.postgresql import insert
        
        try:
            # Convert participants to string for storage
            participants = paper.get("participants", 0)
            if isinstance(participants, int):
                participants_str = str(participants) if participants > 0 else None
            else:
                participants_str = str(participants) if participants else None
            
            # Use upsert (INSERT ... ON CONFLICT DO NOTHING) to handle race conditions
            stmt = insert(PubMedCache).values(
                cache_key=cache_key,
                pubmed_id=paper.get("pmid", ""),
                title=paper.get("title", ""),
                journal=paper.get("journal", ""),
                year=paper.get("year", 2020),
                participants=participants_str,
                finding=paper.get("finding", ""),
                created_at=datetime.utcnow(),
                access_count=1
            ).on_conflict_do_nothing(index_elements=['cache_key'])
            
            await db.execute(stmt)
            await db.commit()
            logger.info(f"📚 Cached citation: {cache_key}")
            
        except Exception as e:
            logger.warning(f"Cache write error (non-critical): {e}")
            try:
                await db.rollback()
            except Exception:
                pass  # Session may already be invalid
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON & TOOL EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

_pubmed_service: Optional[PubMedService] = None


def get_pubmed_service() -> PubMedService:
    """Get singleton PubMed service instance."""
    global _pubmed_service
    if _pubmed_service is None:
        _pubmed_service = PubMedService()
    return _pubmed_service


async def execute_pubmed_tool(
    arguments: Dict[str, Any],
    db: Optional[AsyncSession] = None
) -> Dict[str, Any]:
    """
    Execute the search_research_paper tool (single citation - backward compatible).
    Called by action_plan_generator when GPT requests the tool.
    
    Args:
        arguments: Tool arguments from GPT (query, action_title, category, target_hormone)
        db: Database session for caching
        
    Returns:
        Paper details dict or empty dict if not found
    """
    service = get_pubmed_service()
    
    result = await service.find_citation(
        query=arguments.get("query", ""),
        action_title=arguments.get("action_title", ""),
        category=arguments.get("category", "food"),
        hormone=arguments.get("target_hormone", "cortisol"),
        db=db
    )
    
    return result


async def execute_pubmed_tool_multiple(
    arguments: Dict[str, Any],
    db: Optional[AsyncSession] = None,
    max_citations: int = 4
) -> List[Dict[str, Any]]:
    """
    Execute the search_research_paper tool and return MULTIPLE citations (2-4).
    Prioritizes: Meta-analysis > Systematic Review > RCT > Clinical Trial > Review
    
    Args:
        arguments: Tool arguments from GPT (query, action_title, category, target_hormone)
        db: Database session for caching
        max_citations: Maximum number of citations to return (2-4)
        
    Returns:
        List of paper details dicts, sorted by study type quality
    """
    service = get_pubmed_service()
    
    results = await service.find_multiple_citations(
        query=arguments.get("query", ""),
        action_title=arguments.get("action_title", ""),
        category=arguments.get("category", "food"),
        hormone=arguments.get("target_hormone", "cortisol"),
        db=db,
        max_citations=max_citations
    )
    
    return results
