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
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
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
    
    Priority order:
    1. In-memory cache (instant - for common hormone+condition combos)
    2. Database cache lookup (fast)
    3. PubMed (primary - biomedical focus)
    4. OpenAlex (secondary - broad coverage)
    5. Semantic Scholar (tertiary - AI-powered relevance)
    """
    
    # Class-level semaphores for rate limiting per provider
    # PubMed: 3 req/s without key, 10 req/s with key. We'll set to 5 (or 10 if key present).
    _pubmed_semaphore = asyncio.Semaphore(3) 
    
    # OpenAlex: 10 req/s (polite pool).
    _openalex_semaphore = asyncio.Semaphore(8)
    
    # Semantic Scholar: 1 req/s (unauthenticated).
    _semantic_semaphore = asyncio.Semaphore(1)

    _MAX_RETRIES = 2 
    _RETRY_DELAYS = [0.5, 1.0]
    
    # IN-MEMORY CACHE
    _memory_cache: Dict[str, Tuple[Dict, float]] = {}
    _CACHE_TTL = 86400  # 24 hours
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        self._rate_limit_delay = 0.2
        
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
        
        # Step 4: Try Semantic Scholar (tertiary - AI relevance)
        await asyncio.sleep(self._rate_limit_delay)
        paper = await self._search_semantic_scholar(query)
        if paper:
            logger.info(f"✅ Semantic Scholar found: {paper.get('title', '')[:50]}...")
            self._memory_cache[cache_key] = (paper, time.time())
            if db:
                await self._cache_citation(cache_key, paper, db)
            return paper
        
        # Step 5: No results - try simpler query
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
