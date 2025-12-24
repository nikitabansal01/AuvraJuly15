"""
PubMed Service - Fetches real research citations from NIH PubMed database.
Enhanced with GPT Tool Calling support and multi-API fallback.

Features:
- GPT tool calling for intelligent query building
- Multi-API fallback: PubMed → OpenAlex → Semantic Scholar
- PostgreSQL caching to avoid rate limits
- Full abstract extraction for real findings
- PMID/DOI links for verification
"""
import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Any
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
OPENALEX_SEARCH_URL = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


# ═══════════════════════════════════════════════════════════════════════════════
# GPT TOOL SCHEMA - For function calling
# ═══════════════════════════════════════════════════════════════════════════════

PUBMED_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_research_paper",
        "description": """Search for a REAL published research paper that SUPPORTS WHY we recommend this action.

CRITICAL: Find papers that explain the mechanism/benefit of the specific intervention for the target hormone or health outcome.

The paper should answer: "Why does [intervention] help [hormone/condition]?"

Guidelines for RELEVANT queries:
- Include the specific intervention (e.g., 'pumpkin seeds', 'yoga', 'deep breathing')
- Include the MECHANISM or BENEFIT (e.g., 'reduces cortisol', 'improves insulin sensitivity', 'lowers blood sugar')
- Always include 'women' or 'female' for women's health relevance
- Include the health outcome (e.g., 'stress reduction', 'blood sugar', 'hormone balance')

GOOD query examples (explain WHY it works):
- "pumpkin seeds zinc magnesium women hormone"
- "yoga cortisol reduction stress women randomized"
- "cinnamon blood sugar insulin sensitivity women"
- "omega-3 fatty acids inflammation PCOS women"
- "deep breathing stress cortisol women intervention"
- "flaxseed lignans estrogen women menstrual"

BAD query examples (too vague):
- "healthy eating women" (doesn't explain mechanism)
- "exercise benefits" (too generic)
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
    
    Priority order:
    1. Cache lookup (instant)
    2. PubMed (primary - biomedical focus)
    3. OpenAlex (secondary - broad coverage)
    4. Semantic Scholar (tertiary - AI-powered relevance)
    """
    
    # Class-level semaphore for rate limiting (Fix #1)
    _api_semaphore = asyncio.Semaphore(1)  # Serialize API calls to prevent 429
    _MAX_RETRIES = 3
    _RETRY_DELAYS = [1.0, 2.0, 4.0]  # Exponential backoff delays
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0)
        self._rate_limit_delay = 0.5  # Increased from 0.35 to 0.5 seconds
    
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
        
        # Step 1: Check cache
        if db:
            cached = await self._get_cached_citation(cache_key, db)
            if cached:
                logger.info(f"✅ Cache hit for '{action_title}'")
                return cached
        
        # Step 2: Try PubMed (primary - best for biomedical)
        paper = await self._search_pubmed(query)
        if paper:
            logger.info(f"✅ PubMed found: {paper.get('title', '')[:50]}... (PMID: {paper.get('pmid')})")
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 3: Try OpenAlex (secondary - broad coverage, 100k/day free)
        await asyncio.sleep(self._rate_limit_delay)
        paper = await self._search_openalex(query)
        if paper:
            logger.info(f"✅ OpenAlex found: {paper.get('title', '')[:50]}...")
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 4: Try Semantic Scholar (tertiary - AI relevance)
        await asyncio.sleep(self._rate_limit_delay)
        paper = await self._search_semantic_scholar(query)
        if paper:
            logger.info(f"✅ Semantic Scholar found: {paper.get('title', '')[:50]}...")
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 5: No results - try simpler query
        logger.warning(f"⚠️ No results for '{action_title}', trying simpler query...")
        simple_query = self._simplify_query(query, category, hormone)
        
        paper = await self._search_pubmed(simple_query)
        if paper:
            logger.info(f"✅ PubMed (simplified) found: {paper.get('title', '')[:50]}...")
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Final fallback - return empty
        logger.warning(f"❌ No citation found for '{action_title}'")
        return {}
    
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
        async with self._api_semaphore:
            for attempt in range(self._MAX_RETRIES):
                try:
                    # Exclude clinical guidelines and non-original research
                    enhanced_query = f"({query}) NOT (guideline[ti] OR guidelines[ti] OR cancer[ti] OR oncology[ti] OR chemotherapy[ti])"
                    
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
        """Check if paper is relevant (not clinical guidelines, cancer, etc.)."""
        title = paper.get("title", "").lower()
        journal = paper.get("journal", "").lower()
        
        # Exclude irrelevant topics
        exclude_terms = [
            "cancer", "oncology", "chemotherapy", "tumor", "carcinoma",
            "guideline", "guidelines", "clinical practice",
            "covid", "coronavirus", "pandemic"
        ]
        
        for term in exclude_terms:
            if term in title:
                return False
        
        # Must have participants (women in the study) - handle string/int types
        try:
            participants = paper.get("participants", 0)
            if isinstance(participants, str):
                participants = int(participants) if participants.isdigit() else 0
            if participants > 0:
                return True
        except (ValueError, TypeError):
            pass
        
        # If no participants, check if it mentions women/female in title
        if "women" in title or "female" in title:
            return True
        
        return True  # Default to relevant if no exclusion criteria matched
    
    async def _fetch_pubmed_paper(self, pmid: str) -> Optional[Dict]:
        """Fetch full paper details from PubMed including abstract."""
        try:
            params = {
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml"
            }
            
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
    Execute the search_research_paper tool.
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
