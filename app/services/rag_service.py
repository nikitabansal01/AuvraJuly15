import httpx
import logging
from typing import List, Dict, Any, Optional
from app.models.rag_models import PaperMeta, ChunkedPaper, TaggedChunk, EmbeddingResult, ChunkStudyArms, StudyArm
import uuid
from app.services.ai_service import AIService
import os
import json
from datetime import datetime
from app.core.database import get_db
from sqlalchemy import text

# RAG service logger configuration
logger = logging.getLogger(__name__)

class RAGService:
    # PCOS related keywords list
    PCOS_KEYWORDS = [
        # Basic PCOS keywords
        "PCOS", "Polycystic Ovarian syndrome", "PCOD", "Polycystic Ovary Syndrome",
        
        # PCOS types
        "Type of PCOS", "PCOS phenotypes", "PCOS classification",
        
        # Symptoms related (exactly matching user requirements)
        "Acne", "facial hair", "hirsutism", "weight gain", "bloating", 
        "irregular periods", "amenorrhea", "oligomenorrhea",
        
        # Menstrual cycle phases (exactly matching user requirements)
        "Menses phase", "follicular phase", "ovulation phase", "luteal phase",
        "menstrual cycle", "ovulation", "follicular development",
        
        # Hormone related (exactly matching user requirements)
        "Estrogen", "progesterone", "androgens", "cortisol", "insulin", 
        "thyroid hormone", "testosterone", "DHEA", "SHBG",
        
        # Hormonal imbalance (exactly matching user requirements)
        "hormonal imbalance", "hormones", "womens health", "endocrine disorders",
        
        # Intervention methods (exactly matching user requirements)
        "food intervention", "movement intervention", "stress reduction", 
        "mindfulness", "supplements", "diet", "exercise", "meditation",
        
        # Research related
        "clinical trial", "systematic review", "meta-analysis", "randomized controlled trial"
    ]
    
    # Priority criteria (exactly matching user requirements)
    PRIORITY_CRITERIA = {
        "level_1": {
            "description": "Medical Relevance",
            "criteria": [
                "Relevance to user's hormone results",
                "Relevance to user's reported symptoms", 
                "Relevance to diagnosis"
            ]
        },
        "level_2": {
            "description": "Filtering Criteria",
            "criteria": [
                "Intervention type (food/movement/mindfulness > theory)",
                "Number of participating women (higher is better)",
                "Study type (clinical trial > systematic review > research paper)"
            ]
        },
        "level_3": {
            "description": "Quality Criteria",
            "criteria": [
                "Research recency (last 10 years)",
                "Citation count (higher is better)",
                "Risk of bias"
            ]
        }
    }

    # Section weight definitions
    SECTION_WEIGHTS = {
        "methods": 1.2,
        "results": 1.5,
        "discussion": 1.3,
        "conclusion": 1.4,
        "introduction": 0.8,
        "abstract": 1.1,
        "unknown": 1.0
    }

    @staticmethod
    async def fetch_pcos_papers_from_pubmed_api(resume_from_checkpoint: bool = False) -> List[Dict[str, Any]]:
        """
        Collect PCOS-related papers from PubMed using MeSH-based search, 
        continue searching until 50 papers with PMC IDs are found
        :param resume_from_checkpoint: Whether to resume from checkpoint (default: False)
        :return: List of paper metadata
        """
        logger.info("PubMed paper collection started - searching for 50 papers with PMC IDs")
        
        target_papers = 100  # Target number of papers
        all_papers = []
        enriched_papers = []
        batch_size = 50  # Number of papers to search at once
        processed_pmids = set()  # Track already processed PMIDs
        webenv = None  # PubMed session management
        query_key = None  # PubMed query key
        
        # Load checkpoint
        checkpoint = None
        if resume_from_checkpoint:
            checkpoint = RAGService.load_checkpoint()
            if checkpoint:
                logger.info(f"Resuming from checkpoint: {checkpoint.get('papers_processed', 0)} papers processed")
                processed_pmids = set(checkpoint.get("processed_pmids", []))
                enriched_papers = checkpoint.get("enriched_papers", [])
                webenv = checkpoint.get("webenv")
                query_key = checkpoint.get("query_key")
        
        try:
            import requests
            import xml.etree.ElementTree as ET
            from urllib.parse import quote_plus
            import time
            
            # PubMed API search URL
            base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
            
            # MeSH-based search query (for PubMed)
            mesh_query = "Polycystic+Ovary+Syndrome[Mesh]+OR+PCOS[Mesh]+OR+Stein-Leventhal+Syndrome[Mesh]"
            
            while len(enriched_papers) < target_papers:
                # Initialize current_offset
                current_offset = 0
                consecutive_empty_count = 0  # Initialize consecutive empty result counter
                
                # First search or start new session
                if webenv is None or query_key is None:
                    # Initial search to get WebEnv and QueryKey
                    search_url = f"{base_url}esearch.fcgi?db=pubmed&term={mesh_query}&retmax={batch_size}&retmode=xml&datetype=pdat&mindate=2015&maxdate=2025&usehistory=y"
                    logger.info(f"PubMed API initial search (current PMC papers: {len(enriched_papers)}/{target_papers})")
                else:
                    # Use existing session for next batch search (increment retstart by actual offset)
                    current_offset = len(processed_pmids)  # Offset by number of actually processed PMIDs
                    search_url = f"{base_url}esearch.fcgi?db=pubmed&WebEnv={webenv}&query_key={query_key}&retstart={current_offset}&retmax={batch_size}&retmode=xml"
                    logger.info(f"PubMed API continuous search (offset: {current_offset}, processed PMIDs: {len(processed_pmids)}, current PMC papers: {len(enriched_papers)}/{target_papers})")
                
                # Execute PubMed search
                response = requests.get(search_url)
                response.raise_for_status()
                
                # XML parsing
                root = ET.fromstring(response.content)
                
                # Get WebEnv and QueryKey from first search
                if webenv is None:
                    webenv_elem = root.find(".//WebEnv")
                    query_key_elem = root.find(".//QueryKey")
                    if webenv_elem is not None and query_key_elem is not None:
                        webenv = webenv_elem.text
                        query_key = query_key_elem.text
                        logger.info(f"PubMed session created: WebEnv={webenv[:10]}..., QueryKey={query_key}")
                
                # Extract PMID list (remove duplicates)
                pmid_list = []
                for id_elem in root.findall(".//Id"):
                    pmid = id_elem.text
                    if pmid not in processed_pmids:
                        pmid_list.append(pmid)
                
                if not pmid_list:
                    logger.warning("No more papers to search.")
                    break
                
                logger.info(f"Found {len(pmid_list)} new paper IDs from PubMed (total processed PMIDs: {len(processed_pmids)})")
                
                # Fetch paper details from PubMed (batch processing)
                fetch_batch_size = 20  # PubMed API allows up to 20 at a time
                batch_papers = []
                
                for i in range(0, len(pmid_list), fetch_batch_size):
                    batch_pmids = pmid_list[i:i + fetch_batch_size]
                    pmid_string = ",".join(batch_pmids)
                    
                    fetch_url = f"{base_url}efetch.fcgi?db=pubmed&id={pmid_string}&retmode=xml"
                    logger.debug(f"PubMed API fetching detailed info: {len(batch_pmids)} papers")
                    
                    fetch_response = requests.get(fetch_url)
                    fetch_response.raise_for_status()
                    
                    # Parse PubMed paper details
                    papers_batch = RAGService.parse_pubmed_xml(fetch_response.content)
                    batch_papers.extend(papers_batch)
                    
                    # Respect API rate limit (max 3 per second)
                    time.sleep(0.4)
                
                logger.info(f"PubMed batch processing completed: {len(batch_papers)} papers")
                
                # Filter and process only papers with PMC ID
                for paper in batch_papers:
                    if len(enriched_papers) >= target_papers:
                        break
                    
                    # Add PMID to processed_pmids
                    if paper.get("pmid"):
                        processed_pmids.add(paper["pmid"])
                    
                    try:
                        # If PMC ID exists, get XML and content (without tagging)
                        if paper.get("pmcid"):
                            # Get PMC XML
                            pmc_xml = await RAGService.fetch_pmc_xml(paper["pmcid"])
                            if pmc_xml:
                                paper["pmc_xml"] = pmc_xml
                                
                                # Extract content from XML (for chunking)
                                pmc_content = RAGService.extract_content_from_pmc_xml(pmc_xml)
                                if pmc_content:
                                    paper["content"] = pmc_content
                                    logger.info(f"PMC content extraction successful: {paper['pmcid']} ({len(enriched_papers)+1}/{target_papers})")
                                    enriched_papers.append(paper)
                                    
                                                                         # Save checkpoint
                                    checkpoint_data = {
                                        "processed_pmids": list(processed_pmids),
                                        "enriched_papers": enriched_papers,
                                        "webenv": webenv,
                                        "query_key": query_key,
                                        "papers_processed": len(enriched_papers),
                                        "target_papers": target_papers
                                    }
                                    RAGService.save_checkpoint(checkpoint_data)
                                else:
                                    logger.warning(f"PMC XML content extraction failed: {paper['pmcid']}")
                            else:
                                logger.warning(f"PMC XML fetch failed: {paper['pmcid']}")
                        else:
                            logger.debug(f"No PMC ID, excluding paper: {paper.get('title', 'Unknown')}")
                            
                    except Exception as e:
                        logger.error(f"Paper processing failed: {paper.get('title', 'Unknown')}, error: {e}")
                        continue
                
                # Respect API rate limit
                time.sleep(0.4)
                
                # Prevent infinite loop (maximum 1000 papers)
                if len(processed_pmids) > 1000:
                    logger.warning("Reached maximum search range.")
                    break
                
                # Prevent duplicate search: stop if no new papers
                if len(pmid_list) == 0:
                    logger.warning("No new papers found, stopping search.")
                    break
                
                # Prevent infinite loop: stop if same papers keep repeating
                if len(batch_papers) == 0 and len(pmid_list) > 0:
                    logger.warning("XML parsing failed, preventing infinite loop - stopping search.")
                    break
                
                # Prevent infinite loop: stop if offset gets too large
                if current_offset > 10000:
                    logger.warning("Offset too large, stopping search.")
                    break
                
                # Prevent infinite loop: stop if 3 consecutive empty results
                if len(batch_papers) == 0:
                    consecutive_empty_count += 1
                    if consecutive_empty_count >= 3:
                        logger.warning("3 consecutive empty results, stopping search.")
                        break
                    else:
                        consecutive_empty_count = 0
            
            logger.info(f"PMC content processing completed: {len(enriched_papers)} papers (target: {target_papers})")
            
            # Complete flow for each document (1st tagging → chunking → 2nd tagging → embedding → saving)
            logger.info("Starting complete document-by-document flow for collected papers")
            processed_papers = []
            
                         # Skip already processed papers at checkpoint
            start_index = 0
            if checkpoint and "processed_papers" in checkpoint:
                processed_paper_pmids = set(checkpoint.get("processed_papers", []))
                for i, paper in enumerate(enriched_papers):
                    if paper.get("pmid") in processed_paper_pmids:
                        start_index = i + 1
                        processed_papers.append(paper)
                        logger.info(f"Skipping already processed paper: {paper.get('title', 'Unknown')[:50]}...")
                                         # else: break removed - process incomplete papers as well
            
            for i, paper in enumerate(enriched_papers[start_index:], start=start_index):
                try:
                    # Execute complete flow for each document
                    success = await RAGService.process_paper_complete_pipeline(paper, checkpoint)
                    
                    if success:
                        processed_papers.append(paper)
                        logger.info(f"Document complete processing finished: {paper.get('title', 'Unknown')[:50]}... ({len(processed_papers)}/{len(enriched_papers)})")
                        
                                                 # Update checkpoint on completion
                        if checkpoint:
                            checkpoint["processed_papers"] = [p.get("pmid") for p in processed_papers if p.get("pmid")]
                            checkpoint["papers_processed"] = len(processed_papers)
                            RAGService.save_checkpoint(checkpoint)
                    else:
                        logger.warning(f"Document processing failed: {paper.get('title', 'Unknown')}")
                    
                except Exception as e:
                    logger.error(f"Exception during document processing: {paper.get('title', 'Unknown')}, error: {e}")
                    continue
            
            logger.info(f"Document-by-document complete flow finished: {len(processed_papers)} papers processed")
            
            # Clean up checkpoint on completion
            if checkpoint:
                RAGService.clear_checkpoint()
                logger.info("All processing completed, checkpoint cleared")
            
            return processed_papers
        
        except Exception as e:
            logger.error(f"PubMed paper collection failed: {e}", exc_info=True)
            return enriched_papers  # Return partially collected papers

    @staticmethod
    async def fetch_pmc_content(pmcid: str) -> Optional[str]:
        """
        Fetch content from PMC using PMC ID.
        :param pmcid: PMC ID
        :return: Content text or None
        """
        try:
            import requests
            import xml.etree.ElementTree as ET
            
            # Fetch content from PMC
            fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
            
            response = requests.get(fetch_url)
            response.raise_for_status()
            
            # XML parsing
            root = ET.fromstring(response.content)
            
            # Extract body (according to PMC XML structure)
            body_elements = root.findall(".//body")
            if body_elements:
                # Extract text if body exists
                body_text = ""
                for body in body_elements:
                    # Extract all text nodes
                    for elem in body.iter():
                        if elem.text and elem.text.strip():
                            body_text += elem.text.strip() + " "
                
                if body_text.strip():
                    logger.info(f"PMC content extraction successful: {pmcid}")
                    return body_text.strip()
            
            # Extract abstract if body is not available
            abstract_elements = root.findall(".//abstract")
            if abstract_elements:
                abstract_text = ""
                for abstract in abstract_elements:
                    for elem in abstract.iter():
                        if elem.text and elem.text.strip():
                            abstract_text += elem.text.strip() + " "
                
                if abstract_text.strip():
                    logger.info(f"PMC abstract extraction successful: {pmcid}")
                    return abstract_text.strip()
            
            logger.warning(f"PMC content not found: {pmcid}")
            return None
            
        except Exception as e:
            logger.error(f"PMC content fetch failed: {pmcid}, error: {e}")
            return None

    @staticmethod
    async def fetch_pmc_xml(pmcid: str) -> Optional[str]:
        """
        Fetch PMC XML using PMC ID.
        """
        try:
            import requests
            
            # Fetch XML from PMC
            fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
            
            response = requests.get(fetch_url)
            response.raise_for_status()
            
            xml_content = response.text
            
            if xml_content.strip():
                logger.info(f"PMC XML extraction successful: {pmcid}")
                return xml_content
            else:
                logger.warning(f"PMC XML is empty: {pmcid}")
                return None
                        
        except Exception as e:
            logger.error(f"PMC XML extraction failed: {pmcid}, error: {e}")
            return None

    @staticmethod
    def extract_content_from_pmc_xml(xml_content: str) -> Optional[str]:
        """
        Extract main text from PMC XML.
        :param xml_content: PMC XML string
        :return: Main text or None
        """
        try:
            import xml.etree.ElementTree as ET
            
            # XML parsing
            root = ET.fromstring(xml_content)
            
            # Extract body (according to PMC XML structure)
            body_elements = root.findall(".//body")
            if body_elements:
                # Extract text if body exists
                body_text = ""
                for body in body_elements:
                    # Extract all text nodes
                    for elem in body.iter():
                        if elem.text and elem.text.strip():
                            body_text += elem.text.strip() + " "
                
                if body_text.strip():
                    logger.info(f"PMC content text extraction successful")
                    return body_text.strip()
            
            # Extract abstract if body is not available
            abstract_elements = root.findall(".//abstract")
            if abstract_elements:
                abstract_text = ""
                for abstract in abstract_elements:
                    for elem in abstract.iter():
                        if elem.text and elem.text.strip():
                            abstract_text += elem.text.strip() + " "
                
                if abstract_text.strip():
                    logger.info(f"PMC abstract text extraction successful")
                    return abstract_text.strip()
            
            logger.warning(f"PMC XML content not found")
            return None
            
        except Exception as e:
            logger.error(f"PMC XML content extraction failed: {e}")
            return None

    @staticmethod
    def parse_pubmed_xml(xml_content: bytes) -> List[Dict[str, Any]]:
        """
        Parse PubMed XML response to extract paper information (including authors, journal, publication date, MeSH, etc.)
        :param xml_content: PubMed XML response
        :return: List of paper information
        """
        import xml.etree.ElementTree as ET
        papers = []
        
        try:
            root = ET.fromstring(xml_content)
            
            # Process each paper
            for article in root.findall(".//PubmedArticle"):
                try:
                    # Extract basic information
                    pmid = article.find(".//PMID")
                    pmid_text = pmid.text if pmid is not None else ""
                    
                    # Extract title
                    title_elem = article.find(".//ArticleTitle")
                    title = title_elem.text if title_elem is not None else f"Paper {pmid_text}"
                    
                    # Extract abstract
                    abstract_elem = article.find(".//Abstract/AbstractText")
                    abstract = abstract_elem.text if abstract_elem is not None and abstract_elem.text else ""
                    
                    # Extract author information
                    authors = []
                    author_list = article.findall(".//AuthorList/Author")
                    for author_elem in author_list:
                        last_name_elem = author_elem.find("LastName")
                        first_name_elem = author_elem.find("ForeName")
                        affiliation_elem = author_elem.find("AffiliationInfo/Affiliation")
                        
                        last_name = last_name_elem.text if last_name_elem is not None else ""
                        first_name = first_name_elem.text if first_name_elem is not None else ""
                        affiliation = affiliation_elem.text if affiliation_elem is not None else ""
                        
                        if last_name or first_name:
                            authors.append({
                                "last_name": last_name,
                                "first_name": first_name,
                                "affiliation": affiliation
                            })
                    
                    # Extract journal information
                    journal = ""
                    journal_issn = ""
                    journal_elem = article.find(".//Journal/Title")
                    if journal_elem is not None:
                        journal = journal_elem.text
                    
                    # Extract ISSN
                    issn_elem = article.find(".//Journal/ISSN")
                    if issn_elem is not None:
                        journal_issn = issn_elem.text
                    
                    # Extract publication year (year only)
                    publication_year = 0
                    pub_date = article.find(".//PubDate")
                    if pub_date is not None:
                        year_elem = pub_date.find("Year")
                        if year_elem is not None:
                            try:
                                publication_year = int(year_elem.text)
                            except (ValueError, TypeError):
                                pass
                    
                    # Extract PMC ID
                    pmcid = ""
                    article_ids = article.findall(".//ArticleId")
                    for article_id in article_ids:
                        if article_id.get("IdType") == "pmc":
                            pmcid = article_id.text
                            break
                    
                    # Extract DOI
                    doi = ""
                    for article_id in article_ids:
                        id_type = article_id.get("IdType")
                        if id_type == "doi":
                            doi = article_id.text
                            break
                    
                    # Extract MeSH Terms
                    mesh_terms = []
                    mesh_headings = article.findall(".//MeshHeadingList/MeshHeading")
                    for mesh_heading in mesh_headings:
                        descriptor = mesh_heading.find("DescriptorName")
                        if descriptor is not None and descriptor.text:
                            mesh_terms.append(descriptor.text)
                    
                    # Generate URL
                    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid_text}/"
                    if pmcid:
                        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
                    
                    # Compose paper information
                    paper = {
                        "title": title,
                        "abstract": abstract,
                        "pmid": pmid_text,
                        "pmcid": pmcid,
                        "doi": doi,
                        "date": str(publication_year) if publication_year else "",
                        "url": url,
                        "mesh_terms": mesh_terms,
                        "content": abstract,  # Use abstract as default
                        # New metadata
                        "authors": authors,
                        "journal": journal,
                        "journal_issn": journal_issn,
                        "publication_year": publication_year
                    }
                    
                    # Check if paper is PCOS-related (relaxed filtering)
                    if abstract and RAGService.is_pcos_related_paper(abstract, title, url):
                        papers.append(paper)
                        logger.debug(f"PCOS related paper added (with abstract): {title}")
                    elif pmcid:
                        # Include if PMC ID exists (PCOS relevance to be checked later from full text)
                        papers.append(paper)
                        logger.info(f"PMC ID paper added (PCOS relevance to be checked later): {title}")
                    elif abstract:
                        # Abstract is present, check PCOS relevance
                        if RAGService.is_pcos_related_paper(abstract, title, url):
                            papers.append(paper)
                            logger.debug(f"PCOS related paper added (abstract only): {title}")
                    else:
                        logger.debug(f"Excluded due to no PCOS relevance: {title}")
                except Exception as e:
                    logger.error(f"Paper parsing failed: {e}")
                    continue
                    
            logger.info(f"PubMed XML parsing completed: {len(papers)} papers")
            return papers
        
        except Exception as e:
            logger.error(f"PubMed XML parsing failed: {e}")
            return []

    # Existing Firecrawl method commented out
    """
    @staticmethod
    async def fetch_pcos_papers_from_firecrawl(keywords: List[str], max_results: int = 100) -> List[Dict[str, Any]]:
        # Firecrawl method is currently disabled
        # Replaced with PubMed API method
        return await RAGService.fetch_pcos_papers_from_pubmed_api(keywords, max_results)
    """

    @staticmethod
    def is_pcos_related_paper(content: str, title: str, url: str) -> bool:
        """
        Check if paper is PCOS-related (PubMed-specific filtering)
        :param content: Paper content
        :param title: Paper title
        :param url: Paper URL
        :return: PCOS relevance
        """
        # Check if URL is from PubMed
        if "pubmed.ncbi.nlm.nih.gov" not in url:
            return False
        
        # Convert title and content to lowercase
        title_lower = title.lower()
        content_lower = content.lower()
        
        # PubMed-specific PCOS keywords (more accurate filtering)
        pcos_keywords = [
            # Basic PCOS terms
            "pcos", "polycystic ovary syndrome", "polycystic ovarian syndrome",
            "pcod", "stein-leventhal syndrome",
            
            # PCOS symptoms
            "hirsutism", "acne", "irregular periods", "amenorrhea", "oligomenorrhea",
            "weight gain", "obesity", "insulin resistance", "hyperandrogenism",
            
            # PCOS-related hormones
            "androgens", "testosterone", "insulin", "luteinizing hormone", "lh",
            "follicle stimulating hormone", "fsh", "estrogen", "progesterone",
            
            # PCOS complications
            "infertility", "anovulation", "diabetes", "metabolic syndrome",
            "cardiovascular disease", "endometrial cancer"
        ]
        
        # Include if any keyword is found
        for keyword in pcos_keywords:
            if keyword in title_lower or keyword in content_lower:
                logger.debug(f"PCOS keyword found: '{keyword}' in '{title[:50]}...'")
                return True
        
        return False

    @staticmethod
    def get_sample_papers() -> List[Dict[str, Any]]:
        """
        Return sample PCOS paper data for testing
        """
        return [
            {
                "title": "Polycystic Ovary Syndrome: A Comprehensive Review",
                "content": """
                Polycystic ovary syndrome (PCOS) is a common endocrine disorder affecting 5-10% of reproductive-aged women. 
                The condition is characterized by hyperandrogenism, ovulatory dysfunction, and polycystic ovarian morphology. 
                Insulin resistance plays a key role in the pathophysiology of PCOS, contributing to both metabolic and reproductive complications.
                
                Clinical manifestations include irregular menstrual cycles, hirsutism, acne, and weight gain. 
                Long-term health risks include type 2 diabetes, cardiovascular disease, and endometrial cancer.
                
                Treatment approaches focus on lifestyle modifications, including diet and exercise interventions. 
                Pharmacological options include metformin for insulin resistance and anti-androgen medications for hirsutism.
                
                DOI: 10.1000/sample-pcos-review-2024
                """,
                "url": "https://pubmed.ncbi.nlm.nih.gov/sample-pcos-review",
                "date": "2024",
                "source": "sample-data"
            },
            {
                "title": "Dietary Interventions in PCOS: Impact on Insulin Sensitivity",
                "content": """
                This study investigated the effects of dietary interventions on insulin sensitivity in women with PCOS.
                A randomized controlled trial was conducted with 150 participants over 12 weeks.
                
                Results showed significant improvements in insulin sensitivity following a low-glycemic index diet.
                Participants also experienced reductions in testosterone levels and improvements in menstrual regularity.
                
                The study demonstrates that dietary modifications can be an effective first-line treatment for PCOS.
                
                DOI: 10.1000/diet-pcos-insulin-2024
                """,
                "url": "https://pubmed.ncbi.nlm.nih.gov/sample-diet-study",
                "date": "2024",
                "source": "sample-data"
            },
            {
                "title": "Exercise and PCOS: Benefits of Physical Activity",
                "content": """
                Regular exercise has been shown to improve multiple aspects of PCOS.
                This systematic review analyzed 25 studies involving exercise interventions in PCOS patients.
                
                Findings indicate that both aerobic and resistance training improve insulin sensitivity.
                Exercise also reduces androgen levels and improves menstrual cycle regularity.
                
                Recommendations include 150 minutes of moderate exercise per week for PCOS management.
                
                DOI: 10.1000/exercise-pcos-review-2024
                """,
                "url": "https://pubmed.ncbi.nlm.nih.gov/sample-exercise-review",
                "date": "2024",
                "source": "sample-data"
            }
        ]

    @staticmethod
    def tokenize_text(text: str) -> List[str]:
        """
        Tokenize text using OpenAI tokens (using tiktoken)
        """
        try:
            import tiktoken
            # Use same tokenization as GPT-4
            encoding = tiktoken.get_encoding("cl100k_base")
            tokens = encoding.encode(text)
            return [encoding.decode([token]) for token in tokens]
        except ImportError:
            # Fallback to space-based if tiktoken is not available
            logger.warning("tiktoken not installed, using space-based tokenization")
            return text.split()

    @staticmethod
    def count_tokens(text: str) -> int:
        """
        Count OpenAI tokens in text
        """
        try:
            import tiktoken
            # Use same tokenization as GPT-4
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # Fallback to space-based if tiktoken is not available
            logger.warning("tiktoken not installed, using space-based token counting")
            return len(text.split())

    @staticmethod
    def split_into_paragraphs(text: str) -> List[str]:
        """
        Split text into paragraphs based on <p> tags
        """
        import re
        
        # Split paragraphs by <p> tags
        paragraph_pattern = r'<p[^>]*>(.*?)</p>'
        paragraphs = re.findall(paragraph_pattern, text, re.DOTALL)
        
        # Split by empty lines if no <p> tags
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        return paragraphs

    @staticmethod
    def split_into_sentences(paragraph: str) -> List[str]:
        """
        Split paragraph into sentences using spaCy
        """
        import spacy
        import re
        
        # Load spaCy model (English)
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            # Download model if not available
            import subprocess
            subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
            nlp = spacy.load("en_core_web_sm")
        
        # Remove HTML tags
        clean_text = re.sub(r'<[^>]+>', '', paragraph)
        
        # Split sentences using spaCy
        doc = nlp(clean_text)
        sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
        
        return sentences

    @staticmethod
    def get_overlap_sentences(previous_chunk: List[str], overlap_size: int) -> List[str]:
        """
        Extract sentences from previous chunk that match overlap size
        - Ensure at least 1 sentence is included
        - Include at least 1 sentence even if it's long
        """
        overlap_sentences = []
        overlap_tokens = 0
        
        # Check sentences from the end of previous chunk
        for sentence in reversed(previous_chunk):
            sentence_tokens = RAGService.count_tokens(sentence)
            
            # Include first sentence unconditionally (ensure minimum 1 sentence)
            if not overlap_sentences:
                overlap_sentences.insert(0, sentence)
                overlap_tokens += sentence_tokens
                continue
            
            # Add additional sentences only if they fit within the overlap size
            if overlap_tokens + sentence_tokens <= overlap_size:
                overlap_sentences.insert(0, sentence)
                overlap_tokens += sentence_tokens
            else:
                # If the sentence exceeds the overlap size but at least 1 sentence is already included
                break
        
        # Ensure at least 1 sentence is included
        if not overlap_sentences and previous_chunk:
            # Include the last sentence of the previous chunk
            last_sentence = previous_chunk[-1]
            overlap_sentences = [last_sentence]
            logger.warning(f"Long sentence exceeds overlap size: {RAGService.count_tokens(last_sentence)} tokens")
        
        return overlap_sentences

    @staticmethod
    def semantic_chunk_text(
        text: str,
        chunk_size_min: int = 200,
        chunk_size_max: int = 500,
        overlap_size: int = 75
    ) -> List[Dict[str, Any]]:
        """
        Semantic chunking with respect to overlap (improved overlap logic)
        """
        chunks = []
        
        # Step 1: Split text into paragraphs
        paragraphs = RAGService.split_into_paragraphs(text)
        
        # Step 2: Split each paragraph into sentences
        all_sentences = []
        for paragraph in paragraphs:
            sentences = RAGService.split_into_sentences(paragraph)
            all_sentences.extend(sentences)
        
        # Step 3: Combine sentences to form chunks
        current_chunk = []
        current_tokens = 0
        chunk_start_idx = 0
        
        for i, sentence in enumerate(all_sentences):
            sentence_tokens = RAGService.count_tokens(sentence)
            
            # Check if current chunk size is within limits
            if current_tokens + sentence_tokens > chunk_size_max:
                # If current chunk meets minimum size, save it
                if current_tokens >= chunk_size_min:
                    chunk_text = " ".join(current_chunk)
                    chunks.append({
                        'start_idx': chunk_start_idx,
                        'end_idx': chunk_start_idx + len(chunk_text),
                        'text': chunk_text,
                        'tokens': current_tokens,
                        'sentences': current_chunk.copy(),
                        'paragraphs': paragraphs
                    })
                
                # Start next chunk with improved overlap
                overlap_sentences = RAGService.get_overlap_sentences(current_chunk, overlap_size)
                current_chunk = overlap_sentences
                current_tokens = sum(RAGService.count_tokens(s) for s in overlap_sentences)
                
                # Calculate start index of the new chunk (start of overlap sentences)
                overlap_start_idx = 0
                for j in range(i - len(overlap_sentences), i):
                    if j >= 0:
                        overlap_start_idx += len(all_sentences[j]) + 1  # +1 for space
                chunk_start_idx = overlap_start_idx
            
            # Add sentence to current chunk
            current_chunk.append(sentence)
            current_tokens += sentence_tokens
        
        # Handle last chunk
        if current_chunk and current_tokens >= chunk_size_min:
            chunk_text = " ".join(current_chunk)
            chunks.append({
                'start_idx': chunk_start_idx,
                'end_idx': chunk_start_idx + len(chunk_text),
                'text': chunk_text,
                'tokens': current_tokens,
                'sentences': current_chunk.copy(),
                'paragraphs': paragraphs
            })
        
        return chunks

    @staticmethod
    def chunk_text(text: str, size: int = 300, overlap: int = 75) -> List[Dict[str, int]]:
        """
        Chunk text into chunks of size, with overlap applied (traditional method - maintaining compatibility)
        :return: List of start_idx and end_idx for each chunk
        """
        chunks = []
        i = 0
        while i < len(text):
            start = i
            end = min(i + size, len(text))
            chunks.append({'start_idx': start, 'end_idx': end})
            i += size - overlap
        return chunks

    @staticmethod
    def chunk_paper(paper: PaperMeta, size: int = 300, overlap: int = 75) -> List[ChunkedPaper]:
        """
        Convert PaperMeta for semantic chunking into a list of ChunkedPaper
        """
        # Use semantic chunking (token-based)
        semantic_chunks = RAGService.semantic_chunk_text(
            paper.content, 
            chunk_size_min=200,  # Set for operational environment
            chunk_size_max=500, 
            overlap_size=75
        )
        
        result = []
        for idx, chunk_info in enumerate(semantic_chunks):
            # Generate unique chunk ID (paper ID + chunk index)
            paper_id = getattr(paper, 'pmid', paper.title.replace(' ', '_')[:20])
            chunk_id = f"paper_{paper_id}_chunk_{idx+1}"
            chunk = ChunkedPaper(
                chunk_id=chunk_id,
                text=chunk_info['text'],
                source_url=paper.url,
                title=paper.title,
                start_idx=chunk_info['start_idx'],
                end_idx=chunk_info['end_idx'],
                # Paper identifier
                pmid=getattr(paper, 'pmid', None),
                pmcid=getattr(paper, 'pmcid', None),
                doi=getattr(paper, 'doi', None),
                # Author information
                authors=getattr(paper, 'authors', []),
                # Journal information
                journal=getattr(paper, 'journal', None),
                journal_issn=getattr(paper, 'journal_issn', None),
                # Publication year information
                publication_year=getattr(paper, 'publication_year', None),
                # Additional metadata
                mesh_terms=getattr(paper, 'mesh_terms', []),
                abstract=getattr(paper, 'abstract', None),
                # Section tag information (from original paper)
                source_paper=getattr(paper, 'source_paper', None) or {}
                # Remove priority score - calculated at search time
            )
            result.append(chunk)
        
        # If section information exists, map to chunks
        if hasattr(paper, 'source_paper') and paper.source_paper and 'sections' in paper.source_paper:
            sections = paper.source_paper['sections']
            logger.info(f"Section information found: {len(sections)} sections, paper: {paper.title}")
            # Calculate start/end positions based on entire text
            total_offset = 0
            for section in sections:
                section_content = section.get('content', '')
                section['start_idx'] = total_offset
                section['end_idx'] = total_offset + len(section_content)
                total_offset += len(section_content) + 1  # +1 for potential separator
            
            result = RAGService.map_sections_to_chunks(result, sections)
        elif hasattr(paper, 'source_paper') and paper.source_paper and 'section_tags' in paper.source_paper:
            # Section tags exist but no section information: fallback to tagging
            logger.info(f"Section tags exist but no section information: {paper.title}")
            # Proceed without section information (only perform tagging)
        else:
            # No source_paper or section information
            logger.info(f"No section information: {paper.title}")
            if hasattr(paper, 'source_paper') and paper.source_paper:
                logger.debug(f"source_paper keys: {list(paper.source_paper.keys())}")
            else:
                logger.debug("No source_paper")
        
        logger.info(f"Semantic chunking completed: {len(result)} chunks created (token-based)")
        return result

    @staticmethod
    def suggest_tagging_prompt(chunk: ChunkedPaper, section_tags: Optional[Dict[str, Any]] = None) -> str:
        """
        LLM tagging prompt generation - extract chunk-specific details with strict options
        """
        # Use document-level tagging results as context
        context_info = ""
        if section_tags and section_tags.get("document_level"):
            doc_tags = section_tags["document_level"]
            context_info = f"""
DOCUMENT-LEVEL CONTEXT (from document-level tagging):
- Study Type: {', '.join(doc_tags.get('study_type', []))}
- Conditions: {', '.join(doc_tags.get('condition_disease', []))}
- Target: {', '.join(doc_tags.get('target', []))}
- Participants: {doc_tags.get('num_of_participants', 0)}
- Duration: {doc_tags.get('study_duration', '')}
- Interventions: {', '.join(doc_tags.get('intervention_type', []))}
- Hormones: {', '.join(doc_tags.get('hormone_focus', []))}
- Symptoms: {', '.join(doc_tags.get('target_symptoms', []))}
- Risk of Bias: {doc_tags.get('risk_of_bias', '')}
- Summary: {doc_tags.get('summary', '')}

Note: Do NOT extract the above fields again. Focus only on chunk-specific information.
"""

        # Add PubMed API information if available
        pubmed_context = ""
        if hasattr(chunk, 'source_paper') and chunk.source_paper:
            paper_info = chunk.source_paper
            pubmed_context = f"""
PAPER CONTEXT (from PubMed API):
- Title: {paper_info.get('title', 'N/A')}
- Abstract: {paper_info.get('abstract', 'N/A')[:500]}...
- MeSH Terms: {', '.join(paper_info.get('mesh_terms', []))}
"""

        # Add chunk-specific section information
        chunk_section_context = ""
        if hasattr(chunk, 'overlapping_sections') and chunk.overlapping_sections:
            chunk_section_context = "CHUNK-SPECIFIC SECTION INFORMATION:\n"
            for i, section in enumerate(chunk.overlapping_sections):
                chunk_section_context += f"Section {i+1}: {section.get('section_title', 'Unknown')}\n"
                chunk_section_context += f"- Type: {section.get('section_type', 'unknown')}\n"
                chunk_section_context += f"- Overlap Ratio: {section.get('overlap_ratio', 0):.2f}\n"
                
                # Include section tags if available
                if section.get('section_tags'):
                    section_tags_info = section['section_tags']
                    chunk_section_context += f"- Section Summary: {section_tags_info.get('section_summary', '')}\n"
                    chunk_section_context += f"- Study Type: {section_tags_info.get('study_type', '')}\n"
                    
                    # Include study arms for this section
                    if section_tags_info.get('study_arms'):
                        chunk_section_context += f"- Study Arms:\n"
                        for j, arm in enumerate(section_tags_info['study_arms']):
                            chunk_section_context += f"  Arm {j+1}:\n"
                            chunk_section_context += f"    - Conditions: {', '.join(arm.get('condition_disease', []))}\n"
                            chunk_section_context += f"    - Target: {', '.join(arm.get('target', []))}\n"
                            chunk_section_context += f"    - Participants: {arm.get('num_of_participants', 0)}\n"
                            chunk_section_context += f"    - Duration: {arm.get('study_duration', '')}\n"
                            chunk_section_context += f"    - Interventions: {', '.join(arm.get('intervention_category', []))}\n"
                            chunk_section_context += f"    - Hormones: {', '.join(arm.get('hormone_biomarker_focus', []))}\n"
                            chunk_section_context += f"    - Symptoms: {', '.join(arm.get('target_symptoms', []))}\n"
                            chunk_section_context += f"    - Risk of Bias: {arm.get('risk_of_bias', '')}\n"
                chunk_section_context += "\n"

        return f'''
Given the following medical text chunk about PCOS, extract chunk-specific information with strict options.
Use the provided context and focus ONLY on chunk-specific details.
Respond ONLY with valid JSON format using the specified options.

{context_info}

{pubmed_context}

{chunk_section_context}

CHUNK TEXT: """{chunk.text}"""

REQUIRED FIELDS (with strict options):
- section_type: Options: method, abstract, introduction, method, results, discussion, conclusion, others
- condition_disease: Options: PCOD, PCOS, endometriosis, dysmenorrhea, amenorrhea, menorrhagia, metrorrhagia, PMS, cushing's syndrome, others
- target: Options: female, male, mixed, animal, not_specified
- target_age_distribution: Options: children, teenager, young_adult, adult, middle_aged, aged, perimenopause, postmenopausal, others
  Criteria: children (-12), teenager (13-18), young_adult (18-25), adult (26-44), middle_aged (45-64), aged (65+)
  Format: {{"teenager": 10, "adult": 20}}
- num_of_participants: Total number
- study_duration: Total study duration (not partial results duration)
- intervention_category: Options: food, movement, mindfulness, others
- hormone_focus: Options: androgens, progesterone, estrogen, thyroid, cortisol, insulin, others
- target_symptoms: Options: irregular periods, painful periods, light periods, spotting, heavy periods, bloating, hot flashes, nausea, difficulty losing weight, stubborn belly fat, weight gain, menstrual headaches, hirsutism, thinning of hair, adult acne, mood swings, stress, fatigue, others
- primary_outcome: Who did what for how long and what results were obtained
- chunk_summary: 2-3 sentence summary of this chunk

IMPORTANT RULES:
1. Use ONLY the specified options for each field
2. If information is not found or doesn't match options, use empty string "" for text fields, empty list [] for list fields, and 0 for numeric fields
3. Do NOT use null or None values
4. Do NOT extract document-level fields that are already provided in context

Respond with ONLY this JSON format (no additional text):
{{
  "section_type": "method",
  "chunk_summary": "This study evaluates the effects of a structured dietary intervention on women diagnosed with PCOS. Hormonal biomarkers and symptoms were assessed over a 12-week period.",
  "study_arms": [
    {{
      "condition_disease": ["PCOS"],
  "study_duration": "12 weeks",
      "target": ["female"],
      "target_age_distribution": {{
        "adult": 30
      }},
      "num_of_participants": 30,
      "intervention_type": ["food"],
      "hormone_focus": ["insulin"],
      "primary_outcome": ["Improved insulin sensitivity and reduced androgen levels after dietary changes"],
      "target_symptoms": ["irregular periods", "weight gain", "bloating"]
    }}
  ]
}}
'''

    @staticmethod
    async def tag_chunk_with_llm(chunk: ChunkedPaper, section_tags: Optional[Dict[str, Any]] = None) -> TaggedChunk:
        """
        LLM tagging - assign tagging information to chunk (using document-level results as context)
        """
        prompt = RAGService.suggest_tagging_prompt(chunk, section_tags)
        llm_response, actual_model = await AIService.call_ai_model(prompt)
        
        # Parse JSON from LLM response
        try:
            import json
            import re
            
            # Find JSON block (```json ... ``` or {...} format)
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            
            if json_match:
                json_str = json_match.group(1) if json_match.groups() else json_match.group(0)
                
                # Clean JSON string
                json_str = json_str.strip()
                
                # Debug: JSON string log
                logger.debug(f"[LLM Tagging] JSON string: {json_str[:200]}...")
                
                parsed = json.loads(json_str)
                
                # Process according to new structure
                section_type = parsed.get('section_type', '')
                chunk_summary = parsed.get('chunk_summary', '')
                study_arms = parsed.get('study_arms', [])
                
                # Extract fields from study_arms and remove duplicates
                all_intervention_types = []
                all_symptoms_focus = []
                all_hormone_focus = []
                
                for arm in study_arms:
                    if arm.get('intervention_type'):
                        all_intervention_types.extend(arm['intervention_type'])
                    if arm.get('target_symptoms'):
                        all_symptoms_focus.extend(arm['target_symptoms'])
                    if arm.get('hormone_focus'):
                        all_hormone_focus.extend(arm['hormone_focus'])
                
                # Remove duplicates
                all_intervention_types = list(set(all_intervention_types))
                all_symptoms_focus = list(set(all_symptoms_focus))
                all_hormone_focus = list(set(all_hormone_focus))
                
                # Create TaggedChunk object (keep study_arms as is)
                tagged_chunk = TaggedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    # New fields
                    section_type=section_type,
                    chunk_summary=chunk_summary,
                    study_arms=study_arms,
                    # Legacy fields (set to empty for compatibility)
                    condition_disease=[],
                    target=[],
                    target_age_distribution={},
                    num_of_participants=0,
                    study_duration="",
                    intervention_type=all_intervention_types,
                    hormone_focus=all_hormone_focus,
                    target_symptoms=all_symptoms_focus,
                    primary_outcome=[],
                    # Legacy fields (maintained for compatibility)
                    symptoms_focus=all_symptoms_focus,
                    relevance_score=0,  # Not used in new structure
                    primary_outcome_text="",
                    menstrual_phase="",
                    citation_count=0
                )
                
                logger.debug(f"[Chunk Tagging] Chunk '{chunk.chunk_id}' tagging completed")
                return tagged_chunk
                
            else:
                logger.warning(f"[Chunk Tagging] JSON not found: {chunk.chunk_id}")
                # Create TaggedChunk with default values
                return TaggedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    section_type="",
                    chunk_summary="",
                    study_arms=[],
                    condition_disease=[],
                    target=[],
                    target_age_distribution={},
                    num_of_participants=0,
                    study_duration="",
                    intervention_category=[],
                    hormone_focus=[],
                    target_symptoms=[],
                    primary_outcome=[],
                    intervention_type=[],
                    symptoms_focus=[],
                    relevance_score=0,
                    primary_outcome_text="",
                    menstrual_phase="",
                    citation_count=0
                )
                
        except Exception as e:
            logger.error(f"[Chunk Tagging] Chunk tagging failed: {chunk.chunk_id}, error: {e}")
            # Create TaggedChunk with default values on error
            return TaggedChunk(
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                section_type="",
                chunk_summary="",
                study_arms=[],
                condition_disease=[],
                target=[],
                target_age_distribution={},
                num_of_participants=0,
                study_duration="",
                intervention_category=[],
                hormone_focus=[],
                target_symptoms=[],
                primary_outcome=[],
                intervention_type=[],
                symptoms_focus=[],
                relevance_score=0,
                primary_outcome_text="",
                menstrual_phase="",
                citation_count=0
        )

    @staticmethod
    def get_current_model_version() -> str:
        """
        Return the currently used model version
        """
        # Check model settings from environment variables
        model_from_env = os.getenv("CURRENT_MODEL", "gpt-4o")
        return model_from_env
    
    @staticmethod
    def get_actual_model_version(used_model: str = None) -> str:
        """
        Return the actually used model version (considering fallback)
        """
        if used_model:
            return used_model
        else:
            # Check model settings from environment variables
            model_from_env = os.getenv("CURRENT_MODEL", "gpt-4o")
            return model_from_env
    
    @staticmethod
    async def embed_chunk(chunk: ChunkedPaper, actual_model: str = None) -> EmbeddingResult:
        """
        Convert chunk text to embedding vector using OpenAI Embedding API.
        :return: EmbeddingResult(id, values, metadata)
        """
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        
        try:
            # Call OpenAI Embedding API
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            body = {
                "input": chunk.text,
                "model": "text-embedding-3-small"
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers=headers,
                    json=body
                )
                response.raise_for_status()
                data = response.json()
                embedding_vector = data["data"][0]["embedding"]
                
                # Basic metadata
                metadata = {
                    "title": chunk.title[:500] if chunk.title else "",  # Size limit
                    "url": chunk.source_url[:200] if chunk.source_url else "",  # Size limit
                    "text": chunk.text[:1000] if chunk.text else "",  # Store original text (required for RAG, size limit)
                    "start_idx": chunk.start_idx,
                    "end_idx": chunk.end_idx,
                    # Add model information
                    "model_version": RAGService.get_actual_model_version(actual_model),  # Actually used model
                    "tagging_timestamp": datetime.now().isoformat()  # Tagging timestamp
                }
                
                # Add paper identifiers (store keys even if empty)
                metadata["pmid"] = getattr(chunk, 'pmid', None) or ""
                metadata["pmcid"] = getattr(chunk, 'pmcid', None) or ""
                metadata["doi"] = getattr(chunk, 'doi', None) or ""
                
                # Add author information
                authors = getattr(chunk, 'authors', [])
                if authors:
                    try:
                        # Access Pydantic Author object properties
                        metadata["authors"] = [f"{author.last_name} {author.first_name}".strip() for author in authors]
                    except Exception as e:
                        logger.warning(f"Author information processing failed: {e}")
                        metadata["authors"] = []
                else:
                    metadata["authors"] = []
                
                # Add journal information
                metadata["journal"] = getattr(chunk, 'journal', None) or ""
                metadata["journal_issn"] = getattr(chunk, 'journal_issn', None) or ""
                
                # Add publication year information
                metadata["publication_year"] = getattr(chunk, 'publication_year', None) or 0
                
                # Additional metadata (store keys even if empty)
                metadata["mesh_terms"] = getattr(chunk, 'mesh_terms', None) or []
                metadata["abstract"] = getattr(chunk, 'abstract', None) or ""
                
                # Add section information
                if hasattr(chunk, 'section_info') and chunk.section_info:
                    metadata["section_type"] = chunk.section_info.get("section_type", "")
                    metadata["section_title"] = chunk.section_info.get("section_title", "")
                    metadata["section_priority"] = chunk.section_info.get("section_priority", 0)
                    metadata["overlap_ratio"] = chunk.section_info.get("overlap_ratio", 0.0)
                else:
                    metadata["section_type"] = ""
                    metadata["section_title"] = ""
                    metadata["section_priority"] = 0
                    metadata["overlap_ratio"] = 0.0
                
                # Add overlapping section information (convert to string for Pinecone compatibility)
                if hasattr(chunk, 'overlapping_sections') and chunk.overlapping_sections:
                    overlapping_sections_str = []
                    for section in chunk.overlapping_sections:
                        section_str = f"{section.get('section_title', '')}|{section.get('section_type', '')}|{section.get('overlap_ratio', 0.0)}"
                        overlapping_sections_str.append(section_str)
                    metadata["overlapping_sections"] = overlapping_sections_str
                else:
                    metadata["overlapping_sections"] = []
                
                # Process new tagging structure
                try:
                    # Process document-level and chunk tags
                    if hasattr(chunk, 'source_paper') and chunk.source_paper and isinstance(chunk.source_paper, dict) and 'section_tags' in chunk.source_paper:
                        section_tags = chunk.source_paper['section_tags']
                        
                        # Add document-level tags
                        if section_tags.get("document_level"):
                            doc_tags = section_tags["document_level"]
                            # Convert target_age_distribution to array
                            age_distribution_dict = doc_tags.get("target_age_distribution", {})
                            age_distribution_array = RAGService.convert_age_distribution_to_array(age_distribution_dict)
                            
                            metadata.update({
                                # Document-level tags
                                "doc_study_type": doc_tags.get("study_type", []),
                                "doc_condition_disease": doc_tags.get("condition_disease", []),
                                "doc_target": doc_tags.get("target", []),
                                "doc_target_age_distribution": age_distribution_array,  # Convert to array
                                "doc_num_of_participants": doc_tags.get("num_of_participants", 0),
                                "doc_study_duration": doc_tags.get("study_duration", ""),
                                "doc_intervention_type": doc_tags.get("intervention_type", []),
                                "doc_hormone_focus": doc_tags.get("hormone_focus", []),
                                "doc_target_symptoms": doc_tags.get("target_symptoms", []),
                                "doc_risk_of_bias": doc_tags.get("risk_of_bias", ""),
                                "doc_summary": doc_tags.get("summary", "")
                            })
                        
                        # Perform chunk tagging (using document-level tags as context)
                        chunk_tagged = await RAGService.tag_chunk_with_llm(chunk, section_tags)
                        
                        # Convert study_arms to text
                        study_arms_text = ""
                        if chunk_tagged.study_arms:
                            study_arms_text = RAGService.convert_study_arms_to_text(chunk_tagged.study_arms)
                        
                        # Add chunk-level tags
                        metadata.update({
                            "chunk_section_type": chunk_tagged.section_type or "",
                            "chunk_summary": chunk_tagged.chunk_summary or "",
                            # Fields extracted from study_arms (duplicates removed)
                            "intervention_type": chunk_tagged.intervention_type or [],
                            "symptoms_focus": chunk_tagged.symptoms_focus or [],
                            "hormone_focus": chunk_tagged.hormone_focus or [],
                            # Store study_arms as text
                            "study_arms_text": study_arms_text
                        })
                        
                        # Log study_arms structure
                        if chunk_tagged.study_arms:
                            logger.debug(f"[Embedding] Study arms structure for {chunk.chunk_id}:")
                            for i, arm in enumerate(chunk_tagged.study_arms):
                                logger.debug(f"  Study Arm {i+1}: {arm}")
                        
                        logger.info(f"[Embedding] Document and chunk tags processed: {chunk.chunk_id}")
                        
                    else:
                        # If no document-level tags, perform chunk tagging only
                        logger.info(f"[Embedding] No document-level tags, performing chunk tagging only: {chunk.chunk_id}")
                        
                        # Perform chunk tagging only
                        chunk_tagged = await RAGService.tag_chunk_with_llm(chunk, None)
                        
                        # Convert study_arms to text
                        study_arms_text = ""
                        if chunk_tagged.study_arms:
                            study_arms_text = RAGService.convert_study_arms_to_text(chunk_tagged.study_arms)
                        
                        # Add chunk-level tags only
                        metadata.update({
                            "chunk_section_type": chunk_tagged.section_type or "",
                            "chunk_summary": chunk_tagged.chunk_summary or "",
                            # Fields extracted from study_arms (duplicates removed)
                            "intervention_type": chunk_tagged.intervention_type or [],
                            "symptoms_focus": chunk_tagged.symptoms_focus or [],
                            "hormone_focus": chunk_tagged.hormone_focus or [],
                            # Store study_arms as text
                            "study_arms_text": study_arms_text
                        })
                        
                        logger.info(f"[Embedding] Chunk tags only processed: {chunk.chunk_id}")
                    
                except Exception as e:
                    logger.warning(f"[Embedding] Tagging failed, using basic metadata only: {chunk.chunk_id}, error: {e}")
                
                logger.info(f"[OpenAI] Embedding generation successful: {chunk.chunk_id}, dimensions: {len(embedding_vector)}")
                # Remove chunk_id from metadata to prevent duplication
                if "chunk_id" in metadata:
                    del metadata["chunk_id"]
                return EmbeddingResult(id=chunk.chunk_id, values=embedding_vector, metadata=metadata)
                
        except Exception as e:
            raise RuntimeError(f"OpenAI embedding generation failed: {e}")

    @staticmethod
    def get_pinecone_client():
        """
        Create Pinecone client instance (environment variable based)
        """
        PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
        PINECONE_INDEX = os.getenv("PINECONE_INDEX")
        
        if not PINECONE_API_KEY or not PINECONE_INDEX:
            raise RuntimeError("Pinecone environment variables are not set. Please set PINECONE_API_KEY and PINECONE_INDEX.")
        
        try:
            # Use Pinecone v2 API
            from pinecone import Pinecone
            pc = Pinecone(api_key=PINECONE_API_KEY)
            return pc.Index(PINECONE_INDEX)
        except Exception as e:
            raise RuntimeError(f"Pinecone client initialization failed: {e}")

    @staticmethod
    async def check_paper_exists_in_pinecone(paper_url: str, namespace: str = None) -> bool:
        """
        Check if paper already exists in Pinecone
        :param paper_url: Paper URL
        :param namespace: Pinecone namespace (use model-specific namespace if None)
        :return: Existence status
        """
        try:
            # Use model-specific namespace if not specified
            if namespace is None:
                namespace = RAGService.get_model_namespace()
            
            index = RAGService.get_pinecone_client()
            
            # Search metadata by URL
            query_response = index.query(
                vector=[0] * 1536,  # Dummy vector (for metadata filtering, not actual search)
                filter={"url": {"$eq": paper_url}},
                namespace=namespace,
                top_k=1,
                include_metadata=True
            )
            
            return len(query_response.matches) > 0
            
        except Exception as e:
            logger.warning(f"Pinecone duplicate check failed: {e}")
            return False

    @staticmethod
    def extract_doi_from_text(text: str) -> Optional[str]:
        """
        Extract DOI from text
        :param text: Paper text
        :return: DOI or None
        """
        import re
        
        # DOI pattern matching
        doi_patterns = [
            r'doi:\s*([^\s]+)',  # doi: 10.xxxx/xxxx
            r'DOI:\s*([^\s]+)',  # DOI: 10.xxxx/xxxx
            r'https?://doi\.org/([^\s]+)',  # https://doi.org/10.xxxx/xxxx
            r'10\.\d{4,}/[^\s]+',  # 10.xxxx/xxxx format
        ]
        
        for pattern in doi_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                doi = match.group(1) if len(match.groups()) > 0 else match.group(0)
                logger.debug(f"DOI extracted: {doi}")
                return doi
        
        return None

    @staticmethod
    def extract_paper_id_from_url(url: str) -> Optional[str]:
        """
        Extract paper ID from URL
        :param url: Paper URL
        :return: Paper ID or None
        """
        import re
        from urllib.parse import urlparse
        
        try:
            parsed = urlparse(url)
            
            # Extract PubMed ID
            if 'pubmed' in parsed.netloc:
                pmid_match = re.search(r'(\d+)$', parsed.path)
                if pmid_match:
                    return f"PMID:{pmid_match.group(1)}"
            
            # Extract PMC ID
            elif 'pmc' in parsed.netloc:
                pmc_match = re.search(r'PMC(\d+)', parsed.path)
                if pmc_match:
                    return f"PMC:{pmc_match.group(1)}"
            
            # Other paper IDs
            else:
                # Use the last part of URL as ID
                path_parts = parsed.path.split('/')
                if path_parts and path_parts[-1]:
                    return f"URL_ID:{path_parts[-1]}"
        
        except Exception as e:
            logger.debug(f"Failed to extract ID from URL: {e}")
        
        return None

    @staticmethod
    async def get_existing_papers_from_pinecone(namespace: str = None) -> Dict[str, List[str]]:
        """
        Get existing paper information from Pinecone (URL, DOI, ID based)
        :param namespace: Pinecone namespace (use model-specific namespace if None)
        :return: Dictionary of stored paper information
        """
        try:
            # Use model-specific namespace if not specified
            if namespace is None:
                namespace = RAGService.get_model_namespace()
            
            index = RAGService.get_pinecone_client()
            
            # Fetch all vectors (maximum 10000)
            fetch_response = index.fetch(
                ids=[],  # Empty list to fetch all
                namespace=namespace
            )
            
            existing_urls = set()
            existing_dois = set()
            existing_ids = set()
            
            for vector_id, vector_data in fetch_response.vectors.items():
                if vector_data.metadata:
                    # Collect URLs
                    if "url" in vector_data.metadata:
                        existing_urls.add(vector_data.metadata["url"])
                    
                    # Collect DOIs
                    if "doi" in vector_data.metadata:
                        existing_dois.add(vector_data.metadata["doi"])
                    
                    # Collect paper IDs
                    if "paper_id" in vector_data.metadata:
                        existing_ids.add(vector_data.metadata["paper_id"])
            
            logger.info(f"Found {len(existing_urls)} URLs, {len(existing_dois)} DOIs, {len(existing_ids)} IDs in Pinecone")
            
            return {
                "urls": list(existing_urls),
                "dois": list(existing_dois),
                "ids": list(existing_ids)
            }
            
        except Exception as e:
            logger.warning(f"Failed to fetch existing papers from Pinecone: {e}")
            return {"urls": [], "dois": [], "ids": []}

    @staticmethod
    async def filter_new_papers(papers: List[Dict[str, Any]], namespace: str = None) -> List[Dict[str, Any]]:
        """
        Filter out papers already stored in Pinecone and return only new papers (URL, DOI, ID based)
        :param papers: List of original papers
        :param namespace: Pinecone namespace (None means model-specific namespace)
        :return: Filtered list of new papers
        """
        if not papers:
            return []
        
        # If namespace is not specified, use model-specific namespace
        if namespace is None:
            namespace = RAGService.get_model_namespace()
        
        # Retrieve existing paper information
            return []
        
        # Retrieve existing paper information
        existing_data = await RAGService.get_existing_papers_from_pinecone(namespace)
        existing_urls = set(existing_data["urls"])
        existing_dois = set(existing_data["dois"])
        existing_ids = set(existing_data["ids"])
        
        new_papers = []
        skipped_count = 0
        
        for paper in papers:
            paper_url = paper.get("url", "")
            paper_content = paper.get("content", "")
            
            # Extract DOI
            doi = RAGService.extract_doi_from_text(paper_content)
            
            # Extract paper ID
            paper_id = RAGService.extract_paper_id_from_url(paper_url)
            
            # Check for duplicates (any of URL, DOI, ID match)
            is_duplicate = False
            
            if paper_url and paper_url in existing_urls:
                is_duplicate = True
                logger.debug(f"URL duplicate: {paper_url}")
            
            if doi and doi in existing_dois:
                is_duplicate = True
                logger.debug(f"DOI duplicate: {doi}")
            
            if paper_id and paper_id in existing_ids:
                is_duplicate = True
                logger.debug(f"ID duplicate: {paper_id}")
            
            if is_duplicate:
                skipped_count += 1
                logger.debug(f"Skipping existing paper: {paper_url}")
            else:
                # Add DOI and ID to new paper
                paper["doi"] = doi
                paper["paper_id"] = paper_id
                new_papers.append(paper)
        
        logger.info(f"Filtering completed - {len(papers)} papers, {len(new_papers)} new papers, {skipped_count} skipped")
        return new_papers

    @staticmethod
    def get_model_namespace() -> str:
        """Return namespace based on current model"""
        model_name = os.getenv("CURRENT_MODEL", "gpt-4o")
        return f"pcos-rag-{model_name.replace('-', '_')}"
    
    @staticmethod
    async def save_embedding_to_pinecone(embedding: EmbeddingResult, namespace: str = None) -> bool:
        """
        Save embedding result to Pinecone
        :param embedding: EmbeddingResult
        :param namespace: Pinecone namespace (None means model-specific namespace)
        :return: Success status
        """
        try:
            # If namespace is not specified, use model-specific namespace
            if namespace is None:
                namespace = RAGService.get_model_namespace()
            
            index = RAGService.get_pinecone_client()
            
            # Check metadata size
            metadata_size = len(str(embedding.metadata))
            if metadata_size > 40000:  # Pinecone metadata limit
                logger.error(f"[Pinecone] Metadata too large ({metadata_size} chars), exceeding 40,000 char limit. Skipping vector: {embedding.id}")
                logger.error(f"[Pinecone] Metadata keys: {list(embedding.metadata.keys())}")
                return False
            
            # Save vector
            vector_data = {
                "id": embedding.id,
                "values": embedding.values,
                "metadata": embedding.metadata
            }
            
            logger.debug(f"[Pinecone] Attempting to save vector: {embedding.id}")
            logger.debug(f"[Pinecone] Vector dimension: {len(embedding.values)}")
            logger.debug(f"[Pinecone] Metadata keys: {list(embedding.metadata.keys())}")
            
            index.upsert(vectors=[vector_data], namespace=namespace)
            
            logger.info(f"[Pinecone] Embedding saved successfully: {embedding.id}")
            logger.debug(f"  - Vector dimension: {len(embedding.values)}")
            logger.debug(f"  - Metadata size: {len(str(embedding.metadata))} chars")
            return True
        except Exception as e:
            logger.error(f"[Pinecone] Failed to save embedding: {e}")
            logger.error(f"[Pinecone] Vector ID: {embedding.id}")
            logger.error(f"[Pinecone] Vector dimension: {len(embedding.values)}")
            logger.error(f"[Pinecone] Metadata keys: {list(embedding.metadata.keys()) if embedding.metadata else 'None'}")
            return False

    @staticmethod
    async def process_paper_pipeline(paper: PaperMeta) -> List[Dict[str, Any]]:
        """
        Run entire pipeline for one paper: chunking → tagging → embedding → saving to Pinecone
        :return: List of results for each step
        """
        results = []
        chunks = RAGService.chunk_paper(paper)
        for chunk in chunks:
            tagged = await RAGService.hybrid_tagging(chunk)
            embedding = await RAGService.embed_chunk(chunk, actual_model)
            pinecone_ok = await RAGService.save_embedding_to_pinecone(embedding)
            results.append({
                "chunk_id": chunk.chunk_id,
                "tagged": tagged,
                "embedding": embedding,
                "pinecone_saved": pinecone_ok
            })
        return results 

    @staticmethod
    async def process_paper_pipeline_with_llm_option(paper: PaperMeta, use_llm: bool = False) -> List[Dict[str, Any]]:
        """
        Run entire pipeline for one paper: chunking → tagging → embedding → saving to Pinecone
        :param paper: PaperMeta
        :param use_llm: Use LLM or not
        :return: List of results for each step
        """
        results = []
        chunks = RAGService.chunk_paper(paper)
        for chunk in chunks:
            tagged = await RAGService.hybrid_tagging(chunk, use_llm)
            embedding = await RAGService.embed_chunk(chunk)
            pinecone_ok = await RAGService.save_embedding_to_pinecone(embedding)
            results.append({
                "chunk_id": chunk.chunk_id,
                "tagged": tagged,
                "embedding": embedding,
                "pinecone_saved": pinecone_ok,
                "use_llm": use_llm
            })
        return results 

    @staticmethod
    def filter_and_rank_papers(papers: List[Dict[str, Any]], user_profile: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Filter papers based on priority criteria and sort them
        Apply 3-level priority criteria based on user requirements
        :param papers: List of original papers
        :param user_profile: User profile (optional)
        :return: Filtered and sorted list of papers
        """
        if not papers:
            return []
        
        scored_papers = []
        
        for paper in papers:
            score = 0
            content = paper.get("content", "").lower()
            
            # Level 1: Medical relevance (based on user's hormone results)
            if user_profile:
                # Relevance to user's hormone results
                user_hormones = user_profile.get("hormoneScores", {})
                if any(hormone in content for hormone in user_hormones.keys()):
                    score += 30
                
                # Relevance to user's symptoms
                user_symptoms = user_profile.get("symptoms", [])
                if any(symptom in content for symptom in user_symptoms):
                    score += 25
                
                # Relevance to diagnosis
                user_conditions = user_profile.get("conditions", [])
                if any(condition in content for condition in user_conditions):
                    score += 20
            
            # Level 2: Filtering criteria (Intervention type > Theory)
            # Intervention types
            intervention_keywords = ["diet", "nutrition", "food", "meal", "exercise", "workout", "training", "physical activity", "mindfulness", "meditation", "stress", "relaxation", "supplement", "vitamin", "mineral"]
            if any(keyword in content for keyword in intervention_keywords):
                score += 25
            elif "theory" in content or "review" in content:
                score += 5
            
            # Number of participating women (higher is better)
            import re
            participant_match = re.search(r'(\d+)\s*(women|participants|subjects)', content)
            if participant_match:
                participant_count = int(participant_match.group(1))
                if participant_count >= 100:
                    score += 20
                elif participant_count >= 50:
                    score += 15
                elif participant_count >= 20:
                    score += 10
            
            # Study type (Clinical trial > Systematic review > Research paper)
            if "clinical trial" in content or "randomized" in content:
                score += 20
            elif "systematic review" in content or "meta-analysis" in content:
                score += 15
            elif "research" in content:
                score += 5
            
            # Level 3: Quality criteria (Research recency, Citation count, Risk of bias)
            # Research recency (2020 and later)
            try:
                year = int(paper.get("date", "0")[:4])
                if 2020 <= year <= 2025:  # Papers from 2020 and later
                    score += 15
                elif year >= 2015:  # Papers from 2015 and later
                    score += 5
            except:
                pass
            
            # Source quality (PubMed Central > Medarxiv)
            source = paper.get("source", "")
            if "pubmed" in source or "ncbi" in source:
                score += 15
            elif "medarxiv" in source:
                score += 10
            
            # Number of citations (higher is better) - extracted from text
            citation_match = re.search(r'(\d+)\s*citations?', content)
            if citation_match:
                citation_count = int(citation_match.group(1))
                if citation_count >= 50:
                    score += 10
                elif citation_count >= 20:
                    score += 5
            
            # Risk of bias (lower is better)
            if "randomized" in content or "blinded" in content:
                score += 10  # Low bias risk
            elif "observational" in content:
                score += 5   # Medium bias risk
            
            scored_papers.append({
                **paper,
                "priority_score": score
            })
        
        # Sort by score (highest first)
        scored_papers.sort(key=lambda x: x["priority_score"], reverse=True)
        
        logger.info(f"[Filtering] Filtered {len(papers)} papers, {len(scored_papers)} papers remaining")
        for i, paper in enumerate(scored_papers[:10]):  # Log only top 10 for brevity
            logger.debug(f"  {i+1}. {paper['title'][:50]}... (Score: {paper['priority_score']})")
        
        return scored_papers  # Return entire list

    @staticmethod
    def rule_based_tagging(chunk: ChunkedPaper) -> TaggedChunk:
        """
        Rule-based tagging - extract basic metadata without LLM
        """
        text = chunk.text.lower()
        
        # Classify study type
        study_type = "research_paper"
        if "clinical trial" in text or "randomized" in text:
            study_type = "clinical_trial"
        elif "systematic review" in text or "meta-analysis" in text:
            study_type = "systematic_review"
        elif "case study" in text:
            study_type = "case_study"
        
        # Classify intervention types
        intervention_types = []
        if any(word in text for word in ["diet", "nutrition", "food", "meal"]):
            intervention_types.append("food")
        if any(word in text for word in ["exercise", "workout", "training", "physical activity", "movement"]):
            intervention_types.append("movement")
        if any(word in text for word in ["mindfulness", "meditation", "stress", "relaxation"]):
            intervention_types.append("mindfulness")
        if any(word in text for word in ["supplement", "vitamin", "mineral"]):
            intervention_types.append("supplement")
        if any(word in text for word in ["medication", "drug", "treatment"]):
            intervention_types.append("medication")
        
        if not intervention_types:
            intervention_types = ["none"]
        
        # Hormone-related keywords
        hormone_keywords = ["estrogen", "progesterone", "androgens", "cortisol", "insulin", "thyroid", "testosterone", "dhea", "shbg"]
        hormone_focus = [hormone for hormone in hormone_keywords if hormone in text]
        
        # Symptom-related keywords
        symptom_keywords = ["acne", "hirsutism", "weight gain", "irregular periods", "infertility", "insulin resistance"]
        symptoms_focus = [symptom for symptom in symptom_keywords if symptom in text]
        
        # Menstrual cycle phase
        menstrual_phase = ""
        if "follicular" in text:
            menstrual_phase = "follicular"
        elif "ovulation" in text:
            menstrual_phase = "ovulation"
        elif "luteal" in text:
            menstrual_phase = "luteal"
        elif "menses" in text or "menstrual" in text:
            menstrual_phase = "menses"
        
        # Extract year
        import re
        year_match = re.search(r'20[12]\d', text)
        published_year = int(year_match.group()) if year_match else 0
        
        # Extract number of participants
        participant_match = re.search(r'(\d+)\s*(women|participants|subjects)', text)
        participant_count = int(participant_match.group(1)) if participant_match else 0
        
        return TaggedChunk(
            chunk_id=chunk.chunk_id,
            study_type=study_type,
            is_human_study="women" in text or "participants" in text or "subjects" in text,
            published_year=published_year,
            participant_count=participant_count,
            hormone_focus=hormone_focus,
            symptoms_focus=symptoms_focus,
            intervention_type=intervention_types,
            menstrual_phase=menstrual_phase,
            title=chunk.title,
            url=chunk.source_url
        )

    @staticmethod
    async def hybrid_tagging(chunk: ChunkedPaper, use_llm: bool = False, section_tags: Optional[Dict[str, Any]] = None) -> TaggedChunk:
        """
        Use only OpenAI LLM for tagging (1st-level tagging results as context)
        :param use_llm: Use LLM or not (ignored, always True)
        :param section_tags: 1st-level tagging results (section-level tagging)
        """
        try:
            # Use only OpenAI LLM for tagging (1st-level tagging results as context)
            llm_tagged = await RAGService.tag_chunk_with_llm(chunk, section_tags)
            logger.info(f"OpenAI Tagging - LLM tagging completed: {chunk.chunk_id}")
            return llm_tagged
        except Exception as e:
            logger.error(f"OpenAI Tagging - LLM tagging failed: {chunk.chunk_id}, error: {e}")
            # Return default TaggedChunk if tagging fails
            return TaggedChunk(
                chunk_id=chunk.chunk_id,
                study_type="",
                is_human_study=False,
                published_year=0,
                participant_count=0,
                hormone_focus=[],
                symptoms_focus=[],
                intervention_type=[],
                menstrual_phase="",
                relevance_score=0,
                risk_of_bias="",
                citation_count=0,
                study_duration="",
                primary_outcome="",
                tags=[],
                title=chunk.title,
                url=chunk.source_url
            ) 

    @staticmethod
    async def search_and_rank_papers(query: str, user_profile: Optional[Dict] = None, top_k: int = 10, filter: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Search for papers using Pinecone and calculate priority
        :param query: Search query
        :param user_profile: User profile (optional)
        :param top_k: Number of results to return
        :param filter: Filter conditions (optional)
        :return: List of search results
        """
        try:
            # 1. Generate query embedding
            query_embedding = await RAGService.get_query_embedding(query)
            
            # 2. Search Pinecone
            index = RAGService.get_pinecone_client()
            search_results = index.query(
                vector=query_embedding,
                top_k=top_k * 2,  # Get more results for filtering
                include_metadata=True,
                namespace=RAGService.get_model_namespace()
            )
            
            # 3. Process search results
            papers = []
            for match in search_results.matches:
                paper = {
                    "id": match.id,
                    "score": match.score,
                    "metadata": match.metadata
                }
                
                # Get study arms information from Pinecone metadata
                paper["study_arms_text"] = match.metadata.get("study_arms_text", "")
                paper["section_type"] = match.metadata.get("chunk_section_type", "")
                paper["chunk_summary"] = match.metadata.get("chunk_summary", "")
                
                papers.append(paper)
            
            # 4. Calculate priority
            if user_profile:
                ranked_papers = RAGService.calculate_user_priority(papers, user_profile)
            else:
                ranked_papers = RAGService.calculate_general_priority(papers)
            
            # 5. Return top k results
            return ranked_papers[:top_k]
            
        except Exception as e:
            logger.error(f"Paper search and priority calculation failed: {e}")
            return []

    @staticmethod
    async def get_query_embedding(query: str) -> List[float]:
        """
        Convert user query to vector
        """
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        
        try:
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            body = {
                "input": query,
                "model": "text-embedding-3-small"
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers=headers,
                    json=body
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["embedding"]
                
        except Exception as e:
            raise RuntimeError(f"Query embedding generation failed: {e}")

    @staticmethod
    def calculate_user_priority(papers: List[Dict[str, Any]], user_profile: Dict) -> List[Dict[str, Any]]:
        """
        Calculate priority based on user profile
        """
        scored_papers = []
        
        for paper in papers:
            score = 0
            content = paper.get("content", "").lower()
            
            # Level 1: Medical relevance (based on user profile)
            user_hormones = user_profile.get("hormoneScores", {})
            if any(hormone in content for hormone in user_hormones.keys()):
                score += 30
            
            user_symptoms = user_profile.get("symptoms", [])
            if any(symptom in content for symptom in user_symptoms):
                score += 25
            
            user_conditions = user_profile.get("conditions", [])
            if any(condition in content for condition in user_conditions):
                score += 20
            
            # Level 2: Filtering criteria
            intervention_type = paper.get("intervention_type", "")
            if intervention_type in ["food", "movement", "mindfulness"]:
                score += 30
            elif intervention_type == "supplement":
                score += 20
            elif intervention_type == "medication":
                score += 10
            
            participant_count = paper.get("participant_count", 0)
            if participant_count >= 100:
                score += 20
            elif participant_count >= 50:
                score += 15
            elif participant_count >= 20:
                score += 10
            
            study_type = paper.get("study_type", "")
            if study_type == "systematic_review" or study_type == "meta_analysis":
                score += 20
            elif study_type == "clinical_trial":
                score += 15
            elif study_type == "research_paper":
                score += 5
            
            # Level 3: Quality criteria
            published_year = paper.get("published_year", 0)
            if 2015 <= published_year <= 2025:  # Papers after 2015
                score += 15
            elif published_year >= 2010:  # Papers after 2010
                score += 5
            
            citation_count = paper.get("citation_count", 0)
            if citation_count >= 50:
                score += 10
            elif citation_count >= 20:
                score += 5
            
            risk_of_bias = paper.get("risk_of_bias", "")
            if risk_of_bias == "low":
                score += 10
            elif risk_of_bias == "moderate":
                score += 5
            
            # Consider similarity score
            similarity_score = paper.get("similarity_score", 0)
            score += int(similarity_score * 50)  # Apply similarity score to priority
            
            scored_papers.append({
                **paper,
                "priority_score": score
            })
        
        # Sort by score
        scored_papers.sort(key=lambda x: x["priority_score"], reverse=True)
        return scored_papers

    @staticmethod
    def calculate_general_priority(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        General priority calculation (without user profile)
        """
        scored_papers = []
        
        for paper in papers:
            score = 0
            content = paper.get("content", "").lower()
            
            # General PCOS relevance
            pcos_keywords = ["pcos", "polycystic", "ovary", "syndrome"]
            if any(keyword in content for keyword in pcos_keywords):
                score += 20
            
            # Study type
            study_type = paper.get("study_type", "")
            if study_type == "systematic_review" or study_type == "meta_analysis":
                score += 20
            elif study_type == "clinical_trial":
                score += 15
            elif study_type == "research_paper":
                score += 5
            
            # Intervention type
            intervention_type = paper.get("intervention_type", "")
            if intervention_type in ["food", "movement", "mindfulness"]:
                score += 15
            elif intervention_type == "supplement":
                score += 10
            
            # Participant count
            participant_count = paper.get("participant_count", 0)
            if participant_count >= 100:
                score += 15
            elif participant_count >= 50:
                score += 10
            
            # Recency
            published_year = paper.get("published_year", 0)
            if 2015 <= published_year <= 2025:  # Papers after 2015
                score += 15
            elif published_year >= 2010:  # Papers after 2010
                score += 5
            
            # Similarity score
            similarity_score = paper.get("similarity_score", 0)
            score += int(similarity_score * 50)
            
            scored_papers.append({
                **paper,
                "priority_score": score
            })
        
        scored_papers.sort(key=lambda x: x["priority_score"], reverse=True)
        return scored_papers 

    @staticmethod
    def parse_pmc_sections(xml_content: str) -> List[Dict[str, Any]]:
        """
        Parse PMC XML by sections and convert to chunkable format
        """
        import xml.etree.ElementTree as ET
        
        sections = []
        
        try:
            # XML parsing
            root = ET.fromstring(xml_content)
            
            # Find <sec> tags
            sec_elements = root.findall(".//sec")
            
            logger.info(f"Found {len(sec_elements)} <sec> elements in PMC XML")
            
            for sec in sec_elements:
                section_info = {
                    "title": "",
                    "sec_type": "",
                    "content": "",
                    "confidence": 0.0,
                    "priority": 999  # Lower number means higher priority
                }
                
                # Extract title
                title_elem = sec.find("title")
                if title_elem is not None and title_elem.text:
                    section_info["title"] = title_elem.text.strip()
                
                # Check sec-type attribute
                sec_type = sec.get("sec-type", "").lower()
                section_info["sec_type"] = sec_type
                
                # Extract content (include all text nodes)
                content_parts = []
                
                # p tags
                for p in sec.findall(".//p"):
                    if p.text:
                        content_parts.append(p.text.strip())
                
                # list tags
                for list_elem in sec.findall(".//list"):
                    for item in list_elem.findall(".//list-item"):
                        if item.text:
                            content_parts.append(f"• {item.text.strip()}")
                
                # table tags (convert to simple text)
                for table in sec.findall(".//table"):
                    table_text = "Table: "
                    for row in table.findall(".//tr"):
                        row_text = []
                        for cell in row.findall(".//td"):
                            if cell.text:
                                row_text.append(cell.text.strip())
                        if row_text:
                            table_text += " | ".join(row_text) + " "
                    if table_text != "Table: ":
                        content_parts.append(table_text)
                
                # Other text nodes
                for elem in sec.iter():
                    if elem.text and elem.text.strip() and elem.tag not in ['p', 'list', 'table']:
                        content_parts.append(elem.text.strip())
                
                section_info["content"] = " ".join(content_parts)
                
                # Set priority based on section type
                priority_map = {
                    "methods": 1,
                    "method": 1,
                    "discussion": 2,
                    "abstract": 3,
                    "introduction": 4,
                    "results": 5,
                    "conclusion": 6,
                    "conclusions": 6
                }
                
                # Priority based on sec-type
                if sec_type in priority_map:
                    section_info["priority"] = priority_map[sec_type]
                    section_info["confidence"] = 0.9
                else:
                    # Priority based on title keywords
                    title_lower = section_info["title"].lower()
                    for keyword, priority in priority_map.items():
                        if keyword in title_lower:
                            section_info["priority"] = priority
                            section_info["confidence"] = 0.7
                            break
                
                if section_info["content"].strip():  # Only add sections with content
                    sections.append(section_info)
                    logger.debug(f"Section found: {section_info['title']} ({section_info['sec_type']}) - {len(section_info['content'])} chars")
                else:
                    logger.debug(f"Section skipped (no content): {section_info['title']} ({section_info['sec_type']})")
            
            # Sort by priority
            sections.sort(key=lambda x: x["priority"])
            
            logger.info(f"Successfully parsed {len(sections)} sections with content")
            return sections
            
        except Exception as e:
            logger.error(f"PMC section parsing failed: {e}")
            return []

    @staticmethod
    def create_section_tagging_prompt(section_title: str, section_content: str, section_type: str = "", paper_context: Dict[str, Any] = None) -> str:
        """
        Section-based LLM tagging prompt creation (with document context)
        """
        context_info = ""
        if paper_context:
            context_info = f"""
PAPER CONTEXT:
- Title: {paper_context.get('title', 'N/A')}
- Abstract: {paper_context.get('abstract', 'N/A')[:500]}...
- MeSH Terms: {', '.join(paper_context.get('mesh_terms', []))}

"""
        
        return f'''
Given the following research paper section, extract specific information. 
Respond ONLY with valid JSON format.

{context_info}SECTION INFO:
- Title: {section_title}
- Type: {section_type}
- Content: {section_content[:2000]}...

REQUIRED FIELDS:
- study_type: Options: clinical_trial, randomized controlled trial, systematic review, meta-analysis, review article, cohort study, case study, observational study
- section_type: Options: method, abstract, introduction, method, results, discussion, conclusion, others
- condition_disease: List of conditions/diseases mentioned ["PCOS", "PMS"]
- target: Main research target of the entire paper
- target_age_distribution: Age distribution of participants with counts
  Options: children, teenager, young_adult, adult, middle_aged, aged, perimenopause, postmenopausal
  Criteria: children (-12), teenager (13-18), young_adult (18-25), adult (26-44), middle_aged (45-64), aged (65+)
  Format: {{"teenager": 10, "adult": 20}}
- num_of_participants: Total number of participants
- study_duration: Total study duration (not partial results duration)
- intervention_type: List of main intervention/exposure categories covered in the entire paper
- hormone_focus: List of hormones mentioned
- target_symptoms: List of target symptoms mentioned
- primary_outcome: Who did what for how long and what results were obtained
- risk_of_bias: "low, reason" or "moderate, reason" or "high, reason"
- section_summary: 2-3 line summary of this section

IMPORTANT: If information is not available in this section, use empty string "" for text fields, empty list [] for list fields, and 0 for numeric fields. Do NOT use null or None values.

Respond with ONLY this JSON format (no additional text):
{{
  "section_type": "method",
  "section_summary": "This clinical study explores the comparative effects of physical activity and mindfulness techniques on obese female participants diagnosed with either PCOS or PMS. Over a 12-week observation period, changes in hormone levels and menstrual-related symptoms were tracked.",
  "study_type": "clinical_trial",
  "study_arms": [
    {{
      "condition_disease": ["Polycystic Ovary Syndrome"],
      "study_duration": "A continuous 12-week intervention period",
      "target": ["Overweight adolescent and adult women diagnosed with PCOS"],
      "target_age_distribution": {{
        "teenager": 10,
        "adult": 20
      }},
      "num_of_participants": 30,
      "intervention_type": ["Moderate-intensity exercise program involving regular movement routines"],
      "hormone_biomarker_focus": ["Fasting insulin", "Total androgen levels"],
      "section_primary_outcome": [],
      "target_symptoms": ["Unintended weight gain", "Irregular timing of menstrual cycles"],
      "risk_of_bias": "Assessment suggests minimal bias risk due to proper randomization and adherence tracking"
    }},
    {{
      "condition_disease": ["Premenstrual Syndrome"],
      "study_duration": "12 weeks, including baseline and post-intervention assessments",
      "target": ["Young adult females experiencing moderate PMS symptoms"],
      "target_age_distribution": {{
        "young_adult": 15
      }},
      "num_of_participants": 15,
      "intervention_type": ["Guided mindfulness sessions focused on stress reduction and emotional regulation"],
      "hormone_biomarker_focus": ["Salivary cortisol measured at morning and evening time points"],
      "section_primary_outcome": [],
      "target_symptoms": ["Fatigue during luteal phase", "Emotional instability and mood shifts"],
      "risk_of_bias": "Low, with clearly documented protocol and high participant retention"
    }}
  ]
}}
'''

    @staticmethod
    async def tag_section_with_llm(section_title: str, section_content: str, section_type: str = "", paper_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Section-based LLM tagging execution (with document context)
        """
        prompt = RAGService.create_section_tagging_prompt(section_title, section_content, section_type, paper_context)
        llm_response, actual_model = await AIService.call_ai_model(prompt)
        
        try:
            import json
            import re
            
            # JSON block search
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            
            if json_match:
                json_str = json_match.group(1) if json_match.groups() else json_match.group(0)
                json_str = json_str.strip()
                
                parsed = json.loads(json_str)
                
                # Result organization
                result = {
                    "section_type": parsed.get("section_type", ""),
                    "section_summary": parsed.get("section_summary", ""),
                    "study_type": parsed.get("study_type", ""),
                    "study_arms": parsed.get("study_arms", [])
                }
                
                logger.debug(f"[Section Tagging] Section '{section_title}' tagging completed")
                return result
                
            else:
                logger.warning(f"[Section Tagging] JSON not found: {section_title}")
                return {
                    "section_type": "",
                    "section_summary": "",
                    "study_type": "",
                    "study_arms": []
                }
                
        except Exception as e:
            logger.error(f"[Section Tagging] Section tagging failed: {section_title}, error: {e}")
            return {
                "section_type": "",
                "section_summary": "",
                "study_type": "",
                "study_arms": []
            }

    @staticmethod
    async def aggregate_section_tags(sections: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Aggregate section tags from all sections (no priority order)
        """
        if not sections:
            return {
                "section_type": "",
                "section_summary": "",
                "study_type": "",
                "study_arms": []
            }
        
        # Collect all study arms from all sections
        all_study_arms = []
        section_types = []
        section_summaries = []
        study_types = []
        
        for section in sections:
            if section.get("study_arms"):
                all_study_arms.extend(section["study_arms"])
            if section.get("section_type"):
                section_types.append(section["section_type"])
            if section.get("section_summary"):
                section_summaries.append(section["section_summary"])
            if section.get("study_type"):
                study_types.append(section["study_type"])
        
        # Aggregate results
        aggregated = {
            "section_type": ", ".join(set(section_types)) if section_types else "",
            "section_summary": " ".join(section_summaries) if section_summaries else "",
            "study_type": ", ".join(set(study_types)) if study_types else "",
            "study_arms": all_study_arms
        }
        
        logger.info(f"[Section Aggregation] Aggregated {len(sections)} sections with {len(all_study_arms)} study arms")
        return aggregated

    @staticmethod
    async def process_paper_with_section_tagging(paper: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process paper with section-based tagging (all sections, no priority) and create document-level tags
        """
        try:
            # Use PMC XML if available, otherwise use content
            pmc_xml = paper.get("pmc_xml", "")
            content = paper.get("content", "")
            
            if not pmc_xml and not content:
                logger.warning(f"No PMC XML or content available for section tagging: {paper.get('title', 'Unknown')}")
                return paper
            
            # Parse sections from XML if PMC XML is available, otherwise from content
            if pmc_xml:
                sections = RAGService.parse_pmc_sections(pmc_xml)
                logger.info(f"Parsing sections from PMC XML: {paper.get('title', 'Unknown')}")
            else:
                # If content is already extracted text, section parsing is difficult, so fallback
                logger.info(f"No PMC XML available, using fallback tagging: {paper.get('title', 'Unknown')}")
                return await RAGService.process_paper_with_fallback_tagging(paper)
            
            if sections:
                logger.info(f"Found {len(sections)} sections for tagging: {paper.get('title', 'Unknown')}")
                
                # Tag all sections (no priority order)
                tagged_sections = []
                for section in sections:
                    try:
                        section_tags = await RAGService.tag_section_with_llm(
                            section_title=section.get("title", ""),
                            section_content=section.get("content", ""),
                            section_type=section.get("sec_type", ""),  # Modified to use sec_type
                            paper_context={
                                "title": paper.get("title", ""),
                                "abstract": paper.get("abstract", ""),
                                "mesh_terms": paper.get("mesh_terms", [])
                            }
                        )
                        
                        tagged_sections.append({
                            "title": section.get("title", ""),
                            "type": section.get("sec_type", ""),  # Modified to use sec_type
                            "content": section.get("content", ""),
                            "tags": section_tags
                        })
                        
                        logger.debug(f"Section tagged: {section.get('title', 'Unknown')}")
                        
                    except Exception as e:
                        logger.error(f"Section tagging failed: {section.get('title', 'Unknown')}, error: {e}")
                        continue
                
                # Create document-level tags from all section tags
                if tagged_sections:
                    # Prepare paper context for document-level tagging
                    paper_context = {
                        "title": paper.get("title", ""),
                        "abstract": paper.get("abstract", ""),
                        "mesh_terms": paper.get("mesh_terms", [])
                    }
                    
                    # Create document-level tags
                    document_tags = await RAGService.create_document_level_tags(paper_context, tagged_sections)
                    
                    # Store both section tags and document-level tags
                    paper["section_tags"] = {
                        "sections": tagged_sections,
                        "document_level": document_tags
                    }
                    paper["sections"] = tagged_sections
                    logger.info(f"Section and document-level tagging completed: {paper.get('title', 'Unknown')}")
                else:
                    logger.warning(f"No sections successfully tagged: {paper.get('title', 'Unknown')}")
                    paper["section_tags"] = {
                        "sections": [],
                        "document_level": {}
                    }
                    paper["sections"] = []
            
            else:
                logger.info(f"No sections found, using fallback tagging: {paper.get('title', 'Unknown')}")
                # Fallback to chunk-based tagging
                paper = await RAGService.process_paper_with_fallback_tagging(paper)
            
            return paper
            
        except Exception as e:
            logger.error(f"Section tagging process failed: {paper.get('title', 'Unknown')}, error: {e}")
            return paper

    @staticmethod
    async def process_paper_with_fallback_tagging(paper: Dict[str, Any]) -> Dict[str, Any]:
        """
        Edge Case: When section separation fails, divide entire text into chunks for tagging
        - Case 1: No sections at all
        - Case 2: No priority sections (Methods, Discussion, etc.)
        """
        try:
            # Divide entire text into multiple chunks for tagging
            content = paper.get("content", "")
            if not content:
                logger.warning("No content available for tagging")
                return paper
            
            # Divide text into 8 chunks
            total_length = len(content)
            chunk_size = total_length // 8
            overlap = chunk_size // 4  # 25% overlap
            
            chunks = []
            for i in range(8):
                start_idx = i * (chunk_size - overlap)
                end_idx = min(start_idx + chunk_size, total_length)
                
                if start_idx < total_length:
                    chunks.append({
                        'start_idx': start_idx,
                        'end_idx': end_idx
                    })
            
            logger.info(f"Fallback tagging started: {len(chunks)} chunks")
            
            # Prepare paper context
            paper_context = {
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "mesh_terms": paper.get("mesh_terms", [])
            }
            
            all_tags = []
            for i, chunk in enumerate(chunks):
                chunk_text = content[chunk['start_idx']:chunk['end_idx']]
                tags = await RAGService.tag_section_with_llm(
                    f"Fallback Chunk {i+1}", 
                    chunk_text, 
                    "unknown",
                    paper_context
                )
                all_tags.append(tags)
                logger.debug(f"Chunk {i+1} tagging completed")
            
            # Aggregate same way as section tagging
            if all_tags:
                final_tags = await RAGService.aggregate_section_tags(all_tags)
                logger.debug(f"Fallback tagging aggregation: {len(final_tags.get('study_arms', []))} study arms extracted from {len(all_tags)} chunks")
            else:
                final_tags = {
                    "section_type": "",
                    "section_summary": "",
                    "study_type": "",
                    "study_arms": []
                }
            
            paper["section_tags"] = final_tags
            paper["fallback_tagging"] = True
            
            logger.info(f"Fallback tagging completed: {len(chunks)} chunks processed")
            return paper
            
        except Exception as e:
            logger.error(f"Fallback tagging failed: {e}")
            return paper

    @staticmethod
    async def process_paper_with_non_priority_sections(paper: Dict[str, Any], sections: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Case 2: No priority sections but <sec> tags exist
        Perform tagging using existing sections
        """
        try:
            logger.info(f"Non-priority section tagging started: {len(sections)} sections")
            
            # Prepare paper context
            paper_context = {
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "mesh_terms": paper.get("mesh_terms", [])
            }
            
            # Perform LLM tagging for each section
            for section in sections:
                section_tags = await RAGService.tag_section_with_llm(
                    section["title"], 
                    section["content"], 
                    section["sec_type"],
                    paper_context
                )
                section["tags"] = section_tags
                logger.debug(f"Section '{section['title']}' tagging completed")
            
            # Aggregate section tags (confidence-based)
            final_tags = await RAGService.aggregate_section_tags(sections, use_priority_order=False)
            
            # Add final tags to paper
            paper["section_tags"] = final_tags
            paper["sections"] = sections
            paper["non_priority_sections"] = True
            
            logger.info(f"Non-priority section tagging completed: {len(sections)} sections")
            return paper
            
        except Exception as e:
            logger.error(f"Non-priority section tagging failed: {e}")
            return await RAGService.process_paper_with_fallback_tagging(paper) 

    @staticmethod
    def get_section_type(chunk: ChunkedPaper) -> str:
        """Estimate section type of chunk"""
        text_lower = chunk.text.lower()
        
        if any(keyword in text_lower for keyword in ["method", "procedure", "protocol"]):
            return "methods"
        elif any(keyword in text_lower for keyword in ["result", "finding", "outcome"]):
            return "results"
        elif any(keyword in text_lower for keyword in ["discussion", "interpretation"]):
            return "discussion"
        elif any(keyword in text_lower for keyword in ["conclusion", "summary"]):
            return "conclusion"
        elif any(keyword in text_lower for keyword in ["introduction", "background"]):
            return "introduction"
        elif any(keyword in text_lower for keyword in ["abstract"]):
            return "abstract"
        else:
            return "unknown"
    
    @staticmethod
    async def embed_chunk_with_weight(chunk: ChunkedPaper) -> EmbeddingResult:
        """
        Generate embedding with section-specific weights applied
        """
        # Check section type
        section_type = RAGService.get_section_type(chunk)
        weight = RAGService.SECTION_WEIGHTS.get(section_type, 1.0)
        
        # Generate base embedding
        base_embedding = await RAGService.get_cached_embedding(chunk.text)
        
        # Apply weight
        weighted_embedding = [v * weight for v in base_embedding]
        
        # Generate metadata (same as existing)
        metadata = RAGService.create_chunk_metadata(chunk)
        metadata["section_type"] = section_type
        metadata["section_weight"] = weight
        
        return EmbeddingResult(
            id=chunk.chunk_id,
            values=weighted_embedding,
            metadata=metadata
        )
    
    @staticmethod
    async def get_cached_embedding(text: str) -> List[float]:
        """
        Generate cached embedding
        """
        # Simple hash-based caching
        text_hash = hash(text)
        
        if hasattr(RAGService, '_embedding_cache'):
            if text_hash in RAGService._embedding_cache:
                logger.debug(f"Using cached embedding: {text_hash}")
                return RAGService._embedding_cache[text_hash]
        else:
            RAGService._embedding_cache = {}
        
        # Generate new embedding
        embedding = await RAGService.get_embedding(text)
        RAGService._embedding_cache[text_hash] = embedding
        
        logger.debug(f"Generated and cached new embedding: {text_hash}")
        return embedding
    
    @staticmethod
    async def get_embedding(text: str) -> List[float]:
        """
        Basic embedding generation function
        """
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        
        try:
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            body = {
                "input": text,
                "model": "text-embedding-3-small"
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers=headers,
                    json=body
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["embedding"]
                
        except Exception as e:
            raise RuntimeError(f"Embedding generation failed: {e}")
    
    @staticmethod
    def create_chunk_metadata(chunk: ChunkedPaper) -> Dict[str, Any]:
        """
        Create chunk metadata (separated from existing embed_chunk)
        """
        metadata = {
            "title": chunk.title,
            "url": chunk.source_url,
            "text": chunk.text,
            "start_idx": chunk.start_idx,
            "end_idx": chunk.end_idx
        }
        
        # Paper identifiers
        metadata["pmid"] = getattr(chunk, 'pmid', None) or ""
        metadata["pmcid"] = getattr(chunk, 'pmcid', None) or ""
        metadata["doi"] = getattr(chunk, 'doi', None) or ""
        
        # Author information
        authors = getattr(chunk, 'authors', [])
        if authors:
            try:
                metadata["authors"] = [f"{author.last_name} {author.first_name}".strip() for author in authors]
            except Exception as e:
                logger.warning(f"Author information processing failed: {e}")
                metadata["authors"] = []
        else:
            metadata["authors"] = []
        
        # Journal information
        metadata["journal"] = getattr(chunk, 'journal', None) or ""
        metadata["journal_issn"] = getattr(chunk, 'journal_issn', None) or ""
        metadata["publication_year"] = getattr(chunk, 'publication_year', None) or 0
        
        # Additional metadata
        metadata["mesh_terms"] = getattr(chunk, 'mesh_terms', None) or []
        metadata["abstract"] = getattr(chunk, 'abstract', None) or ""
        
        return metadata

    @staticmethod
    def map_sections_to_chunks(chunks: List[ChunkedPaper], sections: List[Dict[str, Any]]) -> List[ChunkedPaper]:
        """
        Map section information to chunks (show all sections when chunk spans multiple sections)
        """
        for chunk in chunks:
            overlapping_sections = []
            primary_section = None
            max_overlap_ratio = 0
            
            for section in sections:
                # Section start and end positions (based on entire text)
                section_start = section.get("start_idx", 0)
                section_end = section.get("end_idx", len(section.get("content", "")))
                
                # Calculate overlap between chunk and section
                overlap_start = max(chunk.start_idx, section_start)
                overlap_end = min(chunk.end_idx, section_end)
                
                if overlap_start < overlap_end:  # There is overlap
                    overlap_length = overlap_end - overlap_start
                    chunk_length = chunk.end_idx - chunk.start_idx
                    overlap_ratio = overlap_length / chunk_length
                    
                    overlapping_sections.append({
                        "section_title": section.get("title", ""),
                        "section_type": section.get("sec_type", ""),
                        "section_priority": section.get("priority", 0),
                        "overlap_ratio": overlap_ratio,
                        "section_tags": section.get("tags", {}),
                        "start_idx": section_start,
                        "end_idx": section_end
                    })
                    
                    # Set the section with the most overlap as the primary section
                    if overlap_ratio > max_overlap_ratio:
                        max_overlap_ratio = overlap_ratio
                        primary_section = {
                            "section_title": section.get("title", ""),
                            "section_type": section.get("sec_type", ""),
                            "section_priority": section.get("priority", 0),
                            "overlap_ratio": overlap_ratio,
                            "section_tags": section.get("tags", {}),
                            "start_idx": section_start,
                            "end_idx": section_end
                        }
            
            # Set section information on chunk
            chunk.section_info = primary_section
            chunk.overlapping_sections = overlapping_sections
            
            # Logging
            if len(overlapping_sections) > 1:
                logger.info(f"Chunk {chunk.chunk_id} spans {len(overlapping_sections)} sections:")
                for section in overlapping_sections:
                    logger.info(f"  - {section['section_title']} ({section['section_type']}): {section['overlap_ratio']:.2f}")
        
        return chunks

    @staticmethod
    def normalize_intervention_type(intervention_types: List[str]) -> List[str]:
        """
        Normalize intervention types to standardized forms
        """
        normalized_types = []
        for intervention_type in intervention_types:
            if intervention_type.lower() in ["diet", "nutrition", "food"]:
                normalized_types.append("food")
            elif intervention_type.lower() in ["exercise", "workout", "training", "physical activity", "movement"]:
                normalized_types.append("movement")
            elif intervention_type.lower() in ["mindfulness", "meditation", "stress", "relaxation"]:
                normalized_types.append("mindfulness")
            elif intervention_type.lower() in ["supplement", "vitamin", "mineral"]:
                normalized_types.append("supplement")
            elif intervention_type.lower() in ["medication", "drug", "treatment"]:
                normalized_types.append("medication")
            else:
                normalized_types.append(intervention_type)
        return normalized_types

    @staticmethod
    async def process_paper_complete_pipeline(paper: Dict[str, Any], checkpoint: Dict[str, Any] = None) -> bool:
        """
        Execute complete flow for one document
        Primary tagging → Chunking → Secondary tagging → Embedding → Storage
        :param paper: Paper data to process
        :return: Success status
        """
        try:
            logger.info(f"Document complete flow started: {paper.get('title', 'Unknown')[:50]}...")
            
            # 1. Primary tagging (section-based)
            paper = await RAGService.process_paper_with_section_tagging(paper)
            
            # Store section tag information and section information in source_paper
            if "source_paper" not in paper:
                paper["source_paper"] = {}
            if "section_tags" in paper:
                paper["source_paper"]["section_tags"] = paper["section_tags"]
            if "sections" in paper:
                paper["source_paper"]["sections"] = paper["sections"]
            paper["source_paper"]["content"] = paper.get("content", "")
            
            # 2. Convert to PaperMeta
            paper_meta = PaperMeta(
                title=paper.get("title", ""),
                content=paper.get("content", ""),
                url=paper.get("url", ""),
                date=paper.get("date", ""),
                publication_year=paper.get("publication_year"),
                pmid=paper.get("pmid"),
                pmcid=paper.get("pmcid"),
                doi=paper.get("doi"),
                mesh_terms=paper.get("mesh_terms", []),
                abstract=paper.get("abstract", ""),
                authors=paper.get("authors", []),
                journal=paper.get("journal", ""),
                journal_issn=paper.get("journal_issn", ""),
                source_paper=paper.get("source_paper", {})
            )
            
            # 3. Chunking
            chunks = RAGService.chunk_paper(paper_meta)
            logger.info(f"Chunking completed: {len(chunks)} chunks")
            
            # 4. Secondary tagging and storage for each chunk
            saved_chunks = 0
            
            # Skip already processed chunks when resuming from checkpoint
            processed_chunks = set()
            paper_pmid = paper.get("pmid", "unknown")
            
            if checkpoint and "paper_progress" in checkpoint:
                paper_progress = checkpoint["paper_progress"].get(paper_pmid, {})
                processed_chunks = set(paper_progress.get("processed_chunks", []))
                total_chunks = paper_progress.get("total_chunks", len(chunks))
                logger.info(f"Skipping {len(processed_chunks)}/{total_chunks} already processed chunks for paper {paper_pmid}")
            
            for i, chunk in enumerate(chunks):
                # Skip already processed chunks
                if chunk.chunk_id in processed_chunks:
                    logger.info(f"Skipping already processed chunk: {chunk.chunk_id}")
                    saved_chunks += 1
                    continue
                try:
                    # Secondary tagging (using primary results as context)
                    tagged = await RAGService.hybrid_tagging(
                        chunk, 
                        section_tags=paper.get("section_tags")
                    )
                    
                    # Embedding
                    embedding = await RAGService.embed_chunk(chunk)
                    
                    # Save to Pinecone
                    success = await RAGService.save_embedding_to_pinecone(embedding)
                    
                    if success:
                        saved_chunks += 1
                        logger.info(f"Chunk saved successfully: {chunk.chunk_id} ({i+1}/{len(chunks)})")
                        
                        # Update checkpoint when chunk processing is complete
                        if checkpoint:
                            if "paper_progress" not in checkpoint:
                                checkpoint["paper_progress"] = {}
                            
                            paper_pmid = paper.get("pmid", "unknown")
                            if paper_pmid not in checkpoint["paper_progress"]:
                                checkpoint["paper_progress"][paper_pmid] = {
                                    "total_chunks": len(chunks),
                                    "processed_chunks": [],
                                    "paper_title": paper.get("title", "Unknown")
                                }
                            
                            checkpoint["paper_progress"][paper_pmid]["processed_chunks"].append(chunk.chunk_id)
                            RAGService.save_checkpoint(checkpoint)
                    else:
                        logger.error(f"Pinecone save failed: {chunk.chunk_id}")
                        
                except Exception as e:
                    logger.error(f"Chunk processing failed: {chunk.chunk_id}, error: {e}")
                    continue
            
            logger.info(f"Document complete processing successful: {paper.get('title', 'Unknown')[:50]}... (saved chunks: {saved_chunks}/{len(chunks)})")
            return True
            
        except Exception as e:
            logger.error(f"Document complete processing failed: {paper.get('title', 'Unknown')}, error: {e}")
            return False

    @staticmethod
    def create_document_level_tagging_prompt(paper_context: Dict[str, Any], section_tags: List[Dict[str, Any]]) -> str:
        """
        Create prompt for document-level tagging by aggregating section tagging results
        """
        # Prepare section tags context
        section_context = ""
        for i, section in enumerate(section_tags):
            section_context += f"Section {i+1}: {section.get('section_title', 'Unknown')}\n"
            section_context += f"- Type: {section.get('section_type', 'unknown')}\n"
            section_context += f"- Summary: {section.get('section_summary', '')}\n"
            section_context += f"- Study Type: {section.get('study_type', '')}\n"
            
            if section.get('study_arms'):
                section_context += f"- Study Arms:\n"
                for j, arm in enumerate(section['study_arms']):
                    section_context += f"  Arm {j+1}:\n"
                    section_context += f"    - Conditions: {', '.join(arm.get('condition_disease', []))}\n"
                    section_context += f"    - Target: {', '.join(arm.get('target', []))}\n"
                    section_context += f"    - Participants: {arm.get('num_of_participants', 0)}\n"
                    section_context += f"    - Duration: {arm.get('study_duration', '')}\n"
                    section_context += f"    - Interventions: {', '.join(arm.get('intervention_category', []))}\n"
                    section_context += f"    - Hormones: {', '.join(arm.get('hormone_biomarker_focus', []))}\n"
                    section_context += f"    - Symptoms: {', '.join(arm.get('target_symptoms', []))}\n"
                    section_context += f"    - Risk of Bias: {arm.get('risk_of_bias', '')}\n"
            section_context += "\n"
        
        return f'''
Given the following research paper and its section tagging results, create a comprehensive document-level summary.
Aggregate all section information, remove duplicates, and normalize according to the specified options.

PAPER CONTEXT:
- Title: {paper_context.get('title', 'N/A')}
- Abstract: {paper_context.get('abstract', 'N/A')[:500]}...
- MeSH Terms: {', '.join(paper_context.get('mesh_terms', []))}

SECTION TAGGING RESULTS:
{section_context}

REQUIRED FIELDS (with strict options):
- study_type: Options: clinical_trial, randomized controlled trial, systematic review, meta-analysis, review article, cohort study, case study, observational study
  (Can include multiple types, but focus on main types)
- condition_disease: Options: PCOD, PCOS, endometriosis, dysmenorrhea, amenorrhea, menorrhagia, metrorrhagia, PMS, cushing's syndrome, others
- target: Options: female, male, mixed, animal, not_specified
- target_age_distribution: Options: children, teenager, young_adult, adult, middle_aged, aged, perimenopause, postmenopausal
  Criteria: children (-12), teenager (13-18), young_adult (18-25), adult (26-44), middle_aged (45-64), aged (65+)
  Format: {{"teenager": 10, "adult": 20}}
- num_of_participants: Total number (only if study_type is NOT systematic review or meta-analysis)
- study_duration: Total study duration (only if study_type is NOT systematic review or meta-analysis)
- intervention_category: Options: food, movement, mindfulness, others
- hormone_focus: Options: androgens, progesterone, estrogen, thyroid, cortisol, insulin, others
- target_symptoms: Options: irregular periods, painful periods, light periods, spotting, heavy periods, bloating, hot flashes, nausea, difficulty losing weight, stubborn belly fat, weight gain, menstrual headaches, hirsutism, thinning of hair, adult acne, mood swings, stress, fatigue, others
- risk_of_bias: Options: low, medium, high (aggregate from all sections)
- summary: 4-5 line comprehensive summary

IMPORTANT RULES:
1. If study_type is systematic review or meta-analysis, do NOT include target_age_distribution, num_of_participants, study_duration
2. Aggregate all values from sections, remove duplicates, and normalize to specified options
3. For risk_of_bias, aggregate all section values and determine overall bias level
4. For summary, create a comprehensive 4-5 line summary covering all aspects

Respond with ONLY this JSON format (no additional text):
{{
  "study_type": ["clinical_trial"],
  "condition_disease": ["PCOS", "PMS"],
  "target": ["female"],
  "target_age_distribution": {{
    "teenager": 10,
    "adult": 20
  }},
  "num_of_participants": 30,
  "study_duration": "12 weeks",
  "intervention_type": ["movement", "mindfulness"],
  "hormone_focus": ["insulin", "androgens"],
  "target_symptoms": ["weight gain", "irregular periods"],
  "risk_of_bias": "low",
  "summary": "This clinical study explores the comparative effects of physical activity and mindfulness techniques on obese female participants diagnosed with either PCOS or PMS. Over a 12-week observation period, changes in hormone levels and menstrual-related symptoms were tracked."
}}
'''

    @staticmethod
    async def create_document_level_tags(paper_context: Dict[str, Any], section_tags: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create document-level tags by aggregating section tagging results
        """
        try:
            prompt = RAGService.create_document_level_tagging_prompt(paper_context, section_tags)
            llm_response, actual_model = await AIService.call_ai_model(prompt)
            
            import json
            import re
            
            # JSON block search
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            
            if json_match:
                json_str = json_match.group(1) if json_match.groups() else json_match.group(0)
                json_str = json_str.strip()
                
                parsed = json.loads(json_str)
                
                # Normalize and validate the response
                result = {
                    "study_type": parsed.get("study_type", []),
                    "condition_disease": parsed.get("condition_disease", []),
                    "target": parsed.get("target", []),
                    "target_age_distribution": parsed.get("target_age_distribution", {}),
                    "num_of_participants": parsed.get("num_of_participants", 0),
                    "study_duration": parsed.get("study_duration", ""),
                    "intervention_type": parsed.get("intervention_type", []),
                    "hormone_focus": parsed.get("hormone_focus", []),
                    "target_symptoms": parsed.get("target_symptoms", []),
                    "risk_of_bias": parsed.get("risk_of_bias", ""),
                    "summary": parsed.get("summary", "")
                }
                
                logger.info(f"Document-level tagging completed with {len(section_tags)} sections")
                return result
                
            else:
                logger.warning("Document-level tagging: JSON not found")
                return {
                    "study_type": [],
                    "condition_disease": [],
                    "target": [],
                    "target_age_distribution": {},
                    "num_of_participants": 0,
                    "study_duration": "",
                    "intervention_category": [],
                    "hormone_focus": [],
                    "target_symptoms": [],
                    "risk_of_bias": "",
                    "summary": ""
                }
                
        except Exception as e:
            logger.error(f"Document-level tagging failed: {e}")
            return {
                "study_type": [],
                "condition_disease": [],
                "target": [],
                "target_age_distribution": {},
                "num_of_participants": 0,
                "study_duration": "",
                "intervention_category": [],
                "hormone_focus": [],
                "target_symptoms": [],
                "risk_of_bias": "",
                "summary": ""
            }

    # @staticmethod
    # async def save_chunk_study_arms_to_db(chunk_id: str, paper_id: str, chunk_tagged: TaggedChunk) -> bool:
    #     """
    #     Save chunk study arms information to PostgreSQL (not used - stored as text in Pinecone)
    #     """
    #     # PostgreSQL save logic removed - store as study_arms_text in Pinecone
    #     return True

    # @staticmethod
    # async def get_chunk_study_arms_from_db(chunk_id: str) -> Optional[ChunkStudyArms]:
    #     """
    #     Query chunk study arms information from PostgreSQL (not used - query as text from Pinecone)
    #     """
    #     # PostgreSQL query logic removed - query as study_arms_text from Pinecone
    #     return None

    @staticmethod
    def convert_age_distribution_to_array(age_distribution: Dict[str, int]) -> List[str]:
        """
        Convert target_age_distribution dictionary to array
        :param age_distribution: Dictionary in format {"teenager": 50, "adult": 100}
        :return: Array in format ["teenager", "adult"]
        """
        if not age_distribution:
            return []
        
        # Convert only keys with values to array
        return [age_group for age_group, count in age_distribution.items() if count > 0]

    @staticmethod
    def convert_study_arms_to_text(study_arms: List[Dict[str, Any]]) -> str:
        """
        Convert study_arms list to text
        :param study_arms: study_arms list
        :return: study_arms information in text format
        """
        if not study_arms:
            return ""
        
        text_parts = []
        for i, arm in enumerate(study_arms, 1):
            arm_text = f"Study Arm {i}: "
            
            # Basic information
            if arm.get("arm_name"):
                arm_text += f"Name: {arm['arm_name']}, "
            
            if arm.get("intervention_type"):
                interventions = ", ".join(arm["intervention_type"])
                arm_text += f"Interventions: {interventions}, "
            
            if arm.get("target_symptoms"):
                symptoms = ", ".join(arm["target_symptoms"])
                arm_text += f"Target Symptoms: {symptoms}, "
            
            if arm.get("hormone_focus"):
                hormones = ", ".join(arm["hormone_focus"])
                arm_text += f"Hormone Focus: {hormones}, "
            
            if arm.get("participant_count"):
                arm_text += f"Participants: {arm['participant_count']}, "
            
            if arm.get("duration"):
                arm_text += f"Duration: {arm['duration']}, "
            
            if arm.get("description"):
                arm_text += f"Description: {arm['description']}"
            
            # Remove trailing comma
            if arm_text.endswith(", "):
                arm_text = arm_text[:-2]
            
            text_parts.append(arm_text)
        
        return " | ".join(text_parts)

    @staticmethod
    def save_checkpoint(checkpoint_data: Dict[str, any], checkpoint_file: str = "rag_checkpoint.json") -> bool:
        """
        Save checkpoint to file
        """
        try:
            checkpoint_data["timestamp"] = datetime.now().isoformat()
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Checkpoint saved successfully: {checkpoint_file}")
            return True
        except Exception as e:
            logger.error(f"Checkpoint save failed: {e}")
            return False
    
    @staticmethod
    def load_checkpoint(checkpoint_file: str = "rag_checkpoint.json") -> Optional[Dict[str, any]]:
        """
        Load checkpoint from file
        """
        try:
            if os.path.exists(checkpoint_file):
                with open(checkpoint_file, "r", encoding="utf-8") as f:
                    checkpoint = json.load(f)
                logger.info(f"Checkpoint loaded successfully: {checkpoint_file}")
                return checkpoint
            else:
                logger.info(f"Checkpoint file not found: {checkpoint_file}")
                return None
        except Exception as e:
            logger.error(f"Checkpoint load failed: {e}")
            return None
    
    @staticmethod
    def clear_checkpoint(checkpoint_file: str = "rag_checkpoint.json") -> bool:
        """
        Delete checkpoint file
        """
        try:
            if os.path.exists(checkpoint_file):
                os.remove(checkpoint_file)
                logger.info(f"Checkpoint deleted successfully: {checkpoint_file}")
            return True
        except Exception as e:
            logger.error(f"Checkpoint deletion failed: {e}")
            return False