"""
Complete Paper Fetcher for AUVRA RAG - FULL COVERAGE
200+ queries covering EVERY edge case from prompt analysis
"""

import httpx
import logging
import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Set, Tuple
import asyncio

logger = logging.getLogger(__name__)

# Import medical safety module for quality scoring
try:
    from app.services.safety.medical_safety import PaperQualityScorer
    QUALITY_SCORING_ENABLED = True
except ImportError:
    QUALITY_SCORING_ENABLED = False
    logger.warning("Medical safety module not available - quality scoring disabled")


# ============================================================================
# COMPLETE QUERY DEFINITIONS - 200+ queries for full coverage
# ============================================================================

PAPER_QUERIES = {
    # =========================================================================
    # TIER 1: PCOS CORE (7 queries) - Index First
    # =========================================================================
    "tier_1_pcos": [
        "PCOS[Mesh] AND diet AND insulin",
        "PCOS[Mesh] AND exercise AND weight",
        "PCOS[Mesh] AND (mindfulness OR stress OR meditation)",
        "PCOS[Mesh] AND androgens AND diet",
        "PCOS[Mesh] AND hirsutism AND treatment",
        "PCOS[Mesh] AND acne AND women",
        "PCOS[Mesh] AND weight loss AND intervention",
    ],
    
    # =========================================================================
    # TIER 2: ALL HORMONES × 3 CATEGORIES (24 queries)
    # =========================================================================
    "tier_2_hormones_food": [
        "androgens AND women AND (diet OR nutrition) AND reduction",
        "insulin sensitivity AND (diet OR nutrition) AND women",
        "estrogen dominance AND (diet OR food) AND women",
        "estrogen AND (phytoestrogen OR soy) AND women AND menopause",
        "progesterone AND (diet OR vitamin B6 OR supplement) AND women",
        "cortisol AND (diet OR adaptogen) AND stress AND women",
        "adrenal fatigue AND (diet OR nutrition) AND women",
        "thyroid AND (diet OR iodine OR selenium) AND hypothyroidism AND women",
    ],
    
    "tier_2_hormones_movement": [
        "exercise AND androgens AND women",
        "exercise AND insulin sensitivity AND women",
        "exercise AND estrogen AND women",
        "exercise AND menopause AND hot flashes",
        "exercise AND progesterone AND menstrual",
        "exercise AND cortisol reduction AND women",
        "exercise AND adrenal fatigue",
        "exercise AND thyroid AND metabolism AND women",
    ],
    
    "tier_2_hormones_mindfulness": [
        "(mindfulness OR meditation) AND androgens AND women",
        "mindfulness AND insulin AND glucose",
        "(meditation OR yoga) AND estrogen AND women",
        "yoga AND menopause AND hot flashes",
        "(relaxation OR stress reduction) AND progesterone",
        "mindfulness AND cortisol reduction AND women",
        "rest AND adrenal AND recovery",
        "yoga AND thyroid AND hypothyroidism",
    ],
    
    # =========================================================================
    # TIER 3: ALL CONDITIONS × 3 CATEGORIES (39 queries)
    # =========================================================================
    "tier_3_conditions_food": [
        "Endometriosis[Mesh] AND diet AND (inflammation OR estrogen)",
        "Dysmenorrhea[Mesh] AND (diet OR omega-3 OR magnesium)",
        "Amenorrhea[Mesh] AND (diet OR nutrition OR weight)",
        "Menorrhagia[Mesh] AND (diet OR iron OR vitamin K)",
        "Metrorrhagia AND diet AND women",
        "Cushing Syndrome[Mesh] AND diet AND cortisol",
        "Premenstrual Syndrome[Mesh] AND (diet OR calcium OR supplement)",
        "PMDD AND (diet OR supplement)",
        "Diabetes[Mesh] AND women AND diet AND insulin",
        "Hashimoto Disease[Mesh] AND diet AND (selenium OR gluten)",
        "Perimenopause[Mesh] AND diet AND hot flashes",
        "Menopause[Mesh] AND diet AND phytoestrogen",
        "Postmenopause[Mesh] AND diet AND women",
    ],
    
    "tier_3_conditions_movement": [
        "Endometriosis[Mesh] AND exercise AND pain",
        "Dysmenorrhea[Mesh] AND (exercise OR yoga)",
        "Amenorrhea[Mesh] AND exercise AND recovery",
        "Menorrhagia[Mesh] AND exercise",
        "Metrorrhagia AND exercise AND women",
        "Cushing Syndrome[Mesh] AND exercise",
        "Premenstrual Syndrome[Mesh] AND exercise",
        "PMDD AND exercise AND mood",
        "Diabetes[Mesh] AND exercise AND women AND glucose",
        "Hashimoto[Mesh] AND exercise AND fatigue",
        "Perimenopause[Mesh] AND exercise",
        "Menopause[Mesh] AND exercise AND bone",
        "Postmenopause[Mesh] AND exercise AND bone",
    ],
    
    "tier_3_conditions_mindfulness": [
        "Endometriosis[Mesh] AND (mindfulness OR pain management)",
        "Dysmenorrhea[Mesh] AND (relaxation OR mindfulness)",
        "Amenorrhea[Mesh] AND stress AND relaxation",
        "Menorrhagia[Mesh] AND stress management",
        "bleeding disorders AND stress AND women",
        "Cushing Syndrome[Mesh] AND stress management",
        "PMS[Mesh] AND (mindfulness OR CBT)",
        "PMDD AND (mindfulness OR CBT OR therapy)",
        "Diabetes[Mesh] AND mindfulness AND glucose",
        "Hashimoto[Mesh] AND stress AND immune",
        "Perimenopause[Mesh] AND (mindfulness OR yoga)",
        "Menopause[Mesh] AND meditation AND hot flashes",
        "Postmenopause[Mesh] AND mindfulness",
    ],
    
    # =========================================================================
    # TIER 4: ALL SYMPTOMS × 3 CATEGORIES (57 queries)
    # =========================================================================
    "tier_4_symptoms_food": [
        "irregular menstruation AND diet AND women",
        "dysmenorrhea AND (omega-3 OR magnesium) AND diet",
        "hypomenorrhea AND diet AND women",
        "menorrhagia AND (iron OR vitamin K) AND diet",
        "bloating AND diet AND women AND hormonal",
        "hot flashes AND (diet OR phytoestrogen)",
        "nausea AND hormonal AND diet AND women",
        "weight loss resistance AND diet AND women AND hormonal",
        "abdominal obesity AND diet AND insulin AND women",
        "weight gain AND hormonal AND diet AND women",
        "menstrual migraine AND (diet OR magnesium)",
        "hirsutism AND (diet OR spearmint) AND women",
        "female hair loss AND (diet OR biotin OR iron)",
        "acne AND diet AND hormonal AND women",
        "mood swings AND diet AND women AND hormonal",
        "stress AND (diet OR adaptogen) AND women",
        "fatigue AND (diet OR iron OR nutrition) AND women",
        "night sweats AND diet AND women",
        "food cravings AND hormonal AND women",
    ],
    
    "tier_4_symptoms_movement": [
        "irregular periods AND exercise AND women",
        "dysmenorrhea AND (yoga OR exercise)",
        "light periods AND exercise",
        "heavy periods AND exercise",
        "bloating AND exercise AND women",
        "hot flashes AND exercise AND women",
        "nausea AND gentle exercise",
        "weight loss AND exercise AND women AND hormonal",
        "abdominal fat AND exercise AND women",
        "weight gain AND exercise AND women",
        "migraine AND exercise AND women",
        "hirsutism AND exercise AND PCOS",
        "hair loss AND exercise AND women",
        "acne AND exercise AND women",
        "mood swings AND exercise AND women",
        "stress AND exercise AND cortisol AND women",
        "fatigue AND exercise AND energy AND women",
        "night sweats AND exercise",
        "cravings AND exercise AND women",
    ],
    
    "tier_4_symptoms_mindfulness": [
        "irregular menstruation AND stress AND women",
        "dysmenorrhea AND relaxation AND mindfulness",
        "spotting AND stress AND hormones",
        "menorrhagia AND stress management",
        "bloating AND (stress OR relaxation) AND women",
        "hot flashes AND (mindfulness OR yoga)",
        "nausea AND relaxation AND women",
        "weight AND mindfulness AND cortisol",
        "belly fat AND stress AND cortisol",
        "weight AND mindfulness AND women",
        "menstrual headache AND relaxation",
        "hirsutism AND stress AND androgens",
        "hair loss AND stress AND women",
        "acne AND stress AND hormonal",
        "mood swings AND (mindfulness OR meditation)",
        "stress reduction AND mindfulness AND women",
        "fatigue AND (rest OR relaxation) AND women",
        "night sweats AND relaxation",
        "cravings AND mindfulness AND women",
    ],
    
    # =========================================================================
    # TIER 5: LIFESTYLE FACTORS (12 queries)
    # =========================================================================
    "tier_5_lifestyle": [
        "sleep deprivation AND (cortisol OR insulin) AND women",
        "sleep quality AND hormonal AND women",
        "chronic stress AND cortisol AND women AND intervention",
        "sedentary AND insulin resistance AND women",
        "overtraining AND (cortisol OR amenorrhea) AND women",
        "oral contraceptive AND side effects AND women",
        "oral contraceptive AND nutrient depletion",
        "IUD AND hormonal AND women",
        "adolescent AND (PCOS OR menstrual) AND intervention",
        "perimenopause AND lifestyle AND intervention",
        "postpartum AND hormonal AND recovery",
        "shift work AND circadian AND hormones AND women",
    ],
    
    # =========================================================================
    # TIER 6: CYCLE PHASES × 3 CATEGORIES (15 queries) - NEW!
    # =========================================================================
    "tier_6_cycle_food": [
        "menstrual phase AND (diet OR iron OR nutrition) AND women",
        "follicular phase AND (diet OR estrogen) AND women",
        "ovulation AND (diet OR fertility) AND women",
        "luteal phase AND (diet OR progesterone OR PMS) AND women",
        "menstrual cycle AND nutrition AND timing",
    ],
    
    "tier_6_cycle_movement": [
        "menstrual phase AND exercise AND women",
        "follicular phase AND exercise AND performance",
        "ovulation AND exercise AND women",
        "luteal phase AND exercise AND women",
        "menstrual cycle AND exercise AND periodization",
    ],
    
    "tier_6_cycle_mindfulness": [
        "menstrual phase AND (rest OR relaxation) AND women",
        "follicular phase AND energy AND women",
        "ovulation AND mood AND women",
        "luteal phase AND (PMS OR mood) AND mindfulness",
        "menstrual cycle AND mental health AND women",
    ],
    
    # =========================================================================
    # TIER 7: SPECIFIC FOODS WITH EXACT AMOUNTS (20 queries) - CRITICAL!
    # =========================================================================
    "tier_7_specific_foods": [
        "cinnamon AND (dosage OR amount OR grams) AND insulin AND women",
        "spearmint tea AND (dosage OR cups) AND hirsutism AND PCOS",
        "flaxseed AND (dosage OR tablespoons) AND hormones AND women",
        "inositol AND (dosage OR milligrams) AND PCOS",
        "omega-3 AND (dosage OR grams) AND inflammation AND women",
        "magnesium AND (dosage OR milligrams) AND (PMS OR cramps)",
        "vitamin D AND (dosage OR IU) AND hormones AND women",
        "vitamin B6 AND (dosage OR milligrams) AND PMS",
        "calcium AND (dosage OR milligrams) AND PMS AND women",
        "chromium AND (dosage OR micrograms) AND insulin AND women",
        "zinc AND (dosage OR milligrams) AND hormones AND women",
        "iron AND (dosage OR milligrams) AND menstruation AND women",
        "selenium AND (dosage OR micrograms) AND thyroid",
        "turmeric AND (dosage OR grams) AND inflammation AND women",
        "green tea AND (cups OR amount) AND metabolism AND women",
        "soy AND (isoflavones OR grams) AND menopause",
        "maca AND (dosage OR grams) AND hormones AND women",
        "vitex AND (dosage OR milligrams) AND progesterone",
        "evening primrose oil AND (dosage OR milligrams) AND PMS",
        "DIM AND (dosage OR milligrams) AND estrogen AND women",
    ],
    
    # =========================================================================
    # TIER 8: SPECIFIC EXERCISES WITH EXACT PROTOCOLS (15 queries) - CRITICAL!
    # =========================================================================
    "tier_8_specific_exercises": [
        "yoga AND (duration OR minutes) AND (cortisol OR stress) AND women",
        "HIIT AND (duration OR protocol) AND insulin AND women",
        "resistance training AND (frequency OR protocol) AND PCOS",
        "walking AND (duration OR minutes) AND (weight OR health) AND women",
        "swimming AND (duration OR laps) AND women AND hormonal",
        "pilates AND (duration OR frequency) AND women",
        "strength training AND (protocol OR frequency) AND metabolism AND women",
        "aerobic exercise AND (duration OR intensity) AND menopause",
        "stretching AND (duration OR routine) AND menstrual",
        "cycling AND (duration OR intensity) AND women AND hormonal",
        "tai chi AND (duration OR frequency) AND menopause",
        "dance AND exercise AND hormonal AND women",
        "interval training AND (protocol OR duration) AND weight AND women",
        "pelvic floor AND exercise AND women",
        "low impact exercise AND (duration OR type) AND women",
    ],
    
    # =========================================================================
    # TIER 9: SPECIFIC MINDFULNESS TECHNIQUES (10 queries) - CRITICAL!
    # =========================================================================
    "tier_9_specific_mindfulness": [
        "meditation AND (duration OR minutes) AND cortisol AND women",
        "deep breathing AND (technique OR duration) AND stress AND women",
        "progressive muscle relaxation AND (protocol OR duration)",
        "body scan meditation AND (duration OR protocol)",
        "mindful eating AND (technique OR intervention) AND weight",
        "CBT AND (protocol OR sessions) AND (PMS OR PMDD)",
        "yoga nidra AND (duration OR protocol) AND sleep",
        "guided imagery AND (duration OR technique) AND pain",
        "box breathing AND (technique OR protocol) AND stress",
        "loving kindness meditation AND (duration OR protocol) AND women",
    ],
    
    # =========================================================================
    # TIER 10: BIRTH CONTROL SPECIFIC (6 queries) - NEW!
    # =========================================================================
    "tier_10_birth_control": [
        "oral contraceptive AND (nutrition OR diet) AND women",
        "oral contraceptive AND exercise AND women",
        "hormonal birth control AND (side effects OR management)",
        "IUD AND lifestyle AND women",
        "contraceptive AND (vitamin OR mineral) AND depletion",
        "birth control AND (mood OR weight) AND management",
    ],
}


class CompletePaperFetcher:
    """
    Fetches papers from PubMed with QUALITY SCORING.
    Papers are scored using EBM hierarchy before indexing.
    """
    
    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    MAX_PAPERS_PER_QUERY = 12
    
    def __init__(self, enable_quality_filter: bool = True):
        self.processed_pmids: Set[str] = set()
        self.enable_quality_filter = enable_quality_filter and QUALITY_SCORING_ENABLED
        
        # Track quality statistics
        self.quality_stats = {
            'total_fetched': 0,
            'indexed': 0,
            'needs_review': 0,
            'rejected': 0,
        }
    
    async def fetch_all_tiers(
        self, 
        tiers: List[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Fetch papers from specified tiers WITH QUALITY FILTERING.
        
        Returns:
            (papers_to_index, papers_for_review, rejected_papers)
        """
        if tiers is None:
            tiers = ["tier_1_pcos"]
        
        if "all" in tiers or "complete" in tiers:
            tiers = list(PAPER_QUERIES.keys())
        elif "essential" in tiers:
            tiers = [k for k in PAPER_QUERIES.keys() if k.startswith(("tier_1", "tier_2", "tier_3", "tier_4", "tier_5"))]
        
        all_papers = []
        
        for tier in tiers:
            if tier not in PAPER_QUERIES:
                continue
            
            queries = PAPER_QUERIES[tier]
            logger.info(f"📚 {tier}: {len(queries)} queries")
            
            for query in queries:
                try:
                    papers = await self.fetch_papers_for_query(query)
                    all_papers.extend(papers)
                    logger.info(f"  ✓ {len(papers)} papers")
                    await asyncio.sleep(0.35)
                except Exception as e:
                    logger.error(f"  ✗ {e}")
        
        self.quality_stats['total_fetched'] = len(all_papers)
        
        # ============================================================
        # CACHE PAPERS TO FILE BEFORE PROCESSING
        # This prevents data loss if quality filtering fails
        # ============================================================
        import json
        import os
        from datetime import datetime
        
        cache_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'paper_cache')
        os.makedirs(cache_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        cache_file = os.path.join(cache_dir, f'papers_{timestamp}.json')
        
        try:
            with open(cache_file, 'w') as f:
                json.dump({
                    'timestamp': timestamp,
                    'tiers': tiers,
                    'paper_count': len(all_papers),
                    'papers': all_papers
                }, f, indent=2, default=str)
            logger.info(f"✅ Cached {len(all_papers)} papers to {cache_file}")
        except Exception as e:
            logger.error(f"❌ Failed to cache papers: {e}")
        # ============================================================
        
        # Apply quality filtering if enabled
        if self.enable_quality_filter:
            to_index, for_review, rejected = PaperQualityScorer.filter_papers_by_quality(all_papers)
            self.quality_stats['indexed'] = len(to_index)
            self.quality_stats['needs_review'] = len(for_review)
            self.quality_stats['rejected'] = len(rejected)
            
            logger.info(f"📊 Quality filtering: {len(to_index)} index | {len(for_review)} review | {len(rejected)} rejected")
            return to_index, for_review, rejected
        else:
            # No filtering - return all papers as "to index"
            return all_papers, [], []
    
    async def fetch_papers_for_query(self, query: str, max_papers: int = None) -> List[Dict[str, Any]]:
        """Fetch papers for query"""
        if max_papers is None:
            max_papers = self.MAX_PAPERS_PER_QUERY
        
        search_url = (
            f"{self.BASE_URL}esearch.fcgi?db=pubmed"
            f"&term={query.replace(' ', '+')}"
            f"&retmax={max_papers}&retmode=xml"
            f"&datetype=pdat&mindate=2015&maxdate=2025"
        )
        
        response = requests.get(search_url, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        
        pmids = []
        for id_elem in root.findall(".//Id"):
            pmid = id_elem.text
            if pmid not in self.processed_pmids:
                pmids.append(pmid)
                self.processed_pmids.add(pmid)
        
        if not pmids:
            return []
        
        fetch_url = f"{self.BASE_URL}efetch.fcgi?db=pubmed&id={','.join(pmids)}&retmode=xml"
        response = requests.get(fetch_url, timeout=30)
        response.raise_for_status()
        
        papers = self._parse_pubmed_xml(response.content)
        
        enriched = []
        for paper in papers:
            if paper.get("pmcid"):
                content = await self._fetch_pmc_content(paper["pmcid"])
                paper["full_text"] = content if content else paper.get("abstract", "")
            else:
                paper["full_text"] = paper.get("abstract", "")
            if paper["full_text"]:
                enriched.append(paper)
        
        return enriched
    
    def _parse_pubmed_xml(self, xml_content: bytes) -> List[Dict[str, Any]]:
        """Parse PubMed XML"""
        papers = []
        root = ET.fromstring(xml_content)
        
        for article in root.findall(".//PubmedArticle"):
            try:
                paper = {}
                pmid = article.find(".//PMID")
                paper["pmid"] = pmid.text if pmid is not None else None
                
                title = article.find(".//ArticleTitle")
                paper["title"] = title.text if title is not None else ""
                
                abstract_parts = []
                for text in article.findall(".//AbstractText"):
                    label = text.get("Label", "")
                    content = text.text or ""
                    abstract_parts.append(f"{label}: {content}" if label else content)
                paper["abstract"] = " ".join(abstract_parts)
                
                authors = []
                for author in article.findall(".//Author"):
                    last = author.find("LastName")
                    init = author.find("Initials")
                    if last is not None:
                        authors.append(last.text + (f" {init.text}" if init is not None else ""))
                paper["authors"] = authors
                
                journal = article.find(".//Journal/Title")
                paper["journal"] = journal.text if journal is not None else ""
                
                year = article.find(".//PubDate/Year")
                paper["publication_year"] = int(year.text) if year is not None else 0
                
                for aid in article.findall(".//ArticleId"):
                    if aid.get("IdType") == "pmc":
                        paper["pmcid"] = aid.text
                    if aid.get("IdType") == "doi":
                        paper["doi"] = aid.text
                
                mesh = [m.text for m in article.findall(".//MeshHeading/DescriptorName") if m.text]
                paper["mesh_terms"] = mesh
                
                if paper["pmid"]:
                    papers.append(paper)
            except:
                continue
        
        return papers
    
    async def _fetch_pmc_content(self, pmcid: str) -> str:
        """Fetch PMC full text"""
        try:
            if not pmcid.startswith("PMC"):
                pmcid = f"PMC{pmcid}"
            
            url = f"https://www.ncbi.nlm.nih.gov/pmc/oai/oai.cgi?verb=GetRecord&identifier=oai:pubmedcentral.nih.gov:{pmcid.replace('PMC', '')}&metadataPrefix=pmc"
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    texts = [p.text for p in root.findall(".//{*}p") if p.text]
                    return " ".join(texts)[:50000]
        except:
            pass
        return ""


async def fetch_papers_for_rag(tiers: List[str] = None) -> List[Dict[str, Any]]:
    """
    Main entry point
    
    Examples:
        papers = await fetch_papers_for_rag(["tier_1_pcos"])
        papers = await fetch_papers_for_rag(["essential"])  # Tiers 1-5
        papers = await fetch_papers_for_rag(["all"])        # All 200+ queries
    
    Returns:
        List of papers that passed quality filters (ready to index)
    """
    fetcher = CompletePaperFetcher()
    papers_to_index, papers_for_review, rejected_papers = await fetcher.fetch_all_tiers(tiers)
    
    # Log quality filtering statistics
    logger.info(f"📊 Fetched papers: {len(papers_to_index)} to index, {len(papers_for_review)} for review, {len(rejected_papers)} rejected")
    
    # Return only the papers that passed quality filters
    return papers_to_index


def load_cached_papers(cache_file: str = None) -> List[Dict[str, Any]]:
    """
    Load papers from cache file instead of re-fetching from PubMed.
    
    Args:
        cache_file: Path to cache file. If None, uses the most recent cache.
        
    Returns:
        List of papers from cache
        
    Usage:
        papers = load_cached_papers()  # Load most recent
        papers = load_cached_papers("/path/to/papers_20241209_164005.json")
    """
    import json
    import os
    import glob
    
    cache_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'paper_cache')
    
    if cache_file is None:
        # Find most recent cache file
        pattern = os.path.join(cache_dir, 'papers_*.json')
        files = glob.glob(pattern)
        if not files:
            raise FileNotFoundError(f"No cache files found in {cache_dir}")
        cache_file = max(files, key=os.path.getmtime)
        logger.info(f"Using most recent cache: {cache_file}")
    
    with open(cache_file, 'r') as f:
        data = json.load(f)
    
    papers = data.get('papers', [])
    logger.info(f"✅ Loaded {len(papers)} papers from cache (timestamp: {data.get('timestamp')})")
    
    return papers


async def process_cached_papers(cache_file: str = None) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Load cached papers and apply quality filtering.
    Use this after fixing a bug to avoid re-fetching.
    
    Returns:
        (papers_to_index, papers_for_review, rejected_papers)
    """
    papers = load_cached_papers(cache_file)
    
    if QUALITY_SCORING_ENABLED:
        to_index, for_review, rejected = PaperQualityScorer.filter_papers_by_quality(papers)
        logger.info(f"📊 Quality filtering: {len(to_index)} index | {len(for_review)} review | {len(rejected)} rejected")
        return to_index, for_review, rejected
    else:
        return papers, [], []


# Summary
TIER_SUMMARY = {
    "tier_1_pcos": "PCOS core - 7 queries",
    "tier_2_hormones_food": "Hormones × food - 8 queries",
    "tier_2_hormones_movement": "Hormones × movement - 8 queries",
    "tier_2_hormones_mindfulness": "Hormones × mindfulness - 8 queries",
    "tier_3_conditions_food": "Conditions × food - 13 queries",
    "tier_3_conditions_movement": "Conditions × movement - 13 queries",
    "tier_3_conditions_mindfulness": "Conditions × mindfulness - 13 queries",
    "tier_4_symptoms_food": "Symptoms × food - 19 queries",
    "tier_4_symptoms_movement": "Symptoms × movement - 19 queries",
    "tier_4_symptoms_mindfulness": "Symptoms × mindfulness - 19 queries",
    "tier_5_lifestyle": "Lifestyle factors - 12 queries",
    "tier_6_cycle_food": "Cycle phases × food - 5 queries",
    "tier_6_cycle_movement": "Cycle phases × movement - 5 queries",
    "tier_6_cycle_mindfulness": "Cycle phases × mindfulness - 5 queries",
    "tier_7_specific_foods": "Specific foods + dosages - 20 queries",
    "tier_8_specific_exercises": "Specific exercises + protocols - 15 queries",
    "tier_9_specific_mindfulness": "Specific techniques + durations - 10 queries",
    "tier_10_birth_control": "Birth control specific - 6 queries",
}

TOTAL_QUERIES = sum(len(q) for q in PAPER_QUERIES.values())
# Total: 205 queries
