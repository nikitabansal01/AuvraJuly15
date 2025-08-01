import httpx
import logging
from typing import List, Dict, Any, Optional
from app.models.rag_models import PaperMeta, ChunkedPaper, TaggedChunk, EmbeddingResult
import uuid
from app.services.ai_service import AIService
import os

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
    async def fetch_pcos_papers_from_pubmed_api() -> List[Dict[str, Any]]:
        """
        Collect PCOS-related papers from PubMed using MeSH-based search, 
        continue searching until 50 papers with PMC IDs are found
        :return: List of paper metadata
        """
        logger.info("PubMed paper collection started - searching for 50 papers with PMC IDs")
        
        target_papers = 50  # Target number of papers
        all_papers = []
        enriched_papers = []
        batch_size = 50  # Number of papers to search at once
        processed_pmids = set()  # Track already processed PMIDs
        webenv = None  # PubMed session management
        query_key = None  # PubMed query key
        
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
            
            for i, paper in enumerate(enriched_papers):
                try:
                    # Execute complete flow for each document
                    success = await RAGService.process_paper_complete_pipeline(paper)
                    
                    if success:
                        processed_papers.append(paper)
                        logger.info(f"Document complete processing finished: {paper.get('title', 'Unknown')[:50]}... ({len(processed_papers)}/{len(enriched_papers)})")
                    else:
                        logger.warning(f"Document processing failed: {paper.get('title', 'Unknown')}")
                    
                except Exception as e:
                    logger.error(f"Exception during document processing: {paper.get('title', 'Unknown')}, error: {e}")
                    continue
            
            logger.info(f"Document-by-document complete flow finished: {len(processed_papers)} papers processed")
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
            
            # PMC에서 본문 가져오기
            fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
            
            response = requests.get(fetch_url)
            response.raise_for_status()
            
            # XML 파싱
            root = ET.fromstring(response.content)
            
            # 본문 추출 (PMC XML 구조에 따라)
            body_elements = root.findall(".//body")
            if body_elements:
                # 본문이 있으면 텍스트 추출
                body_text = ""
                for body in body_elements:
                    # 모든 텍스트 노드 추출
                    for elem in body.iter():
                        if elem.text and elem.text.strip():
                            body_text += elem.text.strip() + " "
                
                if body_text.strip():
                    logger.info(f"PMC content extraction successful: {pmcid}")
                    return body_text.strip()
            
            # 본문이 없으면 abstract 추출
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
            
            # 본문이 없으면 abstract 추출
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
        PubMed XML 응답을 파싱하여 논문 정보 추출 (저자, 저널, 출판일자, MeSH 등 포함)
        :param xml_content: PubMed XML 응답
        :return: 논문 정보 리스트
        """
        import xml.etree.ElementTree as ET
        papers = []
        
        try:
            root = ET.fromstring(xml_content)
            
            # 각 논문 처리
            for article in root.findall(".//PubmedArticle"):
                try:
                    # 기본 정보 추출
                    pmid = article.find(".//PMID")
                    pmid_text = pmid.text if pmid is not None else ""
                    
                    # 제목 추출
                    title_elem = article.find(".//ArticleTitle")
                    title = title_elem.text if title_elem is not None else f"Paper {pmid_text}"
                    
                    # Abstract 추출
                    abstract_elem = article.find(".//Abstract/AbstractText")
                    abstract = abstract_elem.text if abstract_elem is not None and abstract_elem.text else ""
                    
                    # 저자 정보 추출
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
                    
                    # 저널 정보 추출
                    journal = ""
                    journal_issn = ""
                    journal_elem = article.find(".//Journal/Title")
                    if journal_elem is not None:
                        journal = journal_elem.text
                    
                    # ISSN 추출
                    issn_elem = article.find(".//Journal/ISSN")
                    if issn_elem is not None:
                        journal_issn = issn_elem.text
                    
                    # 출판년도 추출 (년도만)
                    publication_year = 0
                    pub_date = article.find(".//PubDate")
                    if pub_date is not None:
                        year_elem = pub_date.find("Year")
                        if year_elem is not None:
                            try:
                                publication_year = int(year_elem.text)
                            except (ValueError, TypeError):
                                pass
                    
                    # PMC ID 추출
                    pmcid = ""
                    article_ids = article.findall(".//ArticleId")
                    for article_id in article_ids:
                        if article_id.get("IdType") == "pmc":
                            pmcid = article_id.text
                            break
                    
                    # DOI 추출
                    doi = ""
                    for article_id in article_ids:
                        id_type = article_id.get("IdType")
                        if id_type == "doi":
                            doi = article_id.text
                            break
                    
                    # MeSH Terms 추출
                    mesh_terms = []
                    mesh_headings = article.findall(".//MeshHeadingList/MeshHeading")
                    for mesh_heading in mesh_headings:
                        descriptor = mesh_heading.find("DescriptorName")
                        if descriptor is not None and descriptor.text:
                            mesh_terms.append(descriptor.text)
                    
                    # URL 생성
                    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid_text}/"
                    if pmcid:
                        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
                    
                    # 논문 정보 구성
                    paper = {
                        "title": title,
                        "abstract": abstract,
                        "pmid": pmid_text,
                        "pmcid": pmcid,
                        "doi": doi,
                        "date": str(publication_year) if publication_year else "",
                        "url": url,
                        "mesh_terms": mesh_terms,
                        "content": abstract,  # 기본값으로 abstract 사용
                        # 새로운 메타데이터
                        "authors": authors,
                        "journal": journal,
                        "journal_issn": journal_issn,
                        "publication_year": publication_year
                    }
                    
                    # PCOS 관련 논문인지 확인 (필터링 완화)
                    if abstract and RAGService.is_pcos_related_paper(abstract, title, url):
                        papers.append(paper)
                        logger.debug(f"PCOS related paper added (with abstract): {title}")
                    elif pmcid:
                        # PMC ID가 있으면 무조건 포함 (나중에 본문에서 PCOS 관련성 확인)
                        papers.append(paper)
                        logger.info(f"PMC ID paper added (PCOS relevance to be checked later): {title}")
                    elif abstract:
                        # Abstract가 있으면 PCOS 관련성 확인 후 포함
                        if RAGService.is_pcos_related_paper(abstract, title, url):
                            papers.append(paper)
                            logger.debug(f"PCOS related paper added (abstract only): {title}")
                        else:
                            logger.debug(f"Excluded due to no PCOS relevance: {title}")
                      else:
                          # Exclude if neither abstract nor PMC ID exists
                          logger.warning(f"Excluded due to no abstract and PMC ID: {title}")
                        
                except Exception as e:
                    logger.error(f"Paper parsing failed: {e}")
                    continue
                    
            logger.info(f"PubMed XML parsing completed: {len(papers)} papers")
            return papers
            
        except Exception as e:
            logger.error(f"PubMed XML parsing failed: {e}")
            return []

    # 기존 Firecrawl 방식 주석처리
    """
    @staticmethod
    async def fetch_pcos_papers_from_firecrawl(keywords: List[str], max_results: int = 100) -> List[Dict[str, Any]]:
        # Firecrawl 방식은 현재 비활성화됨
        # PubMed API 방식으로 대체
        return await RAGService.fetch_pcos_papers_from_pubmed_api(keywords, max_results)
    """

    @staticmethod
    def is_pcos_related_paper(content: str, title: str, url: str) -> bool:
        """
        논문이 PCOS 관련인지 확인 (PubMed 특화 필터링)
        :param content: Paper content
        :param title: Paper title
        :param url: Paper URL
        :return: PCOS relevance
        """
        # URL이 PubMed인지 확인
        if "pubmed.ncbi.nlm.nih.gov" not in url:
            return False
        
        # 제목과 내용을 소문자로 변환
        title_lower = title.lower()
        content_lower = content.lower()
        
        # PubMed 특화 PCOS 키워드 (더 정확한 필터링)
        pcos_keywords = [
            # 기본 PCOS 용어
            "pcos", "polycystic ovary syndrome", "polycystic ovarian syndrome",
            "pcod", "stein-leventhal syndrome",
            
            # PCOS 증상
            "hirsutism", "acne", "irregular periods", "amenorrhea", "oligomenorrhea",
            "weight gain", "obesity", "insulin resistance", "hyperandrogenism",
            
            # PCOS 관련 호르몬
            "androgens", "testosterone", "insulin", "luteinizing hormone", "lh",
            "follicle stimulating hormone", "fsh", "estrogen", "progesterone",
            
            # PCOS 합병증
            "infertility", "anovulation", "diabetes", "metabolic syndrome",
            "cardiovascular disease", "endometrial cancer"
        ]
        
        # 키워드 중 하나라도 포함되면 PCOS 관련
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
        텍스트를 OpenAI 토큰으로 분할 (tiktoken 사용)
        """
        try:
            import tiktoken
            # GPT-4와 동일한 토큰화 사용
            encoding = tiktoken.get_encoding("cl100k_base")
            tokens = encoding.encode(text)
            return [encoding.decode([token]) for token in tokens]
        except ImportError:
            # tiktoken이 없으면 공백 기반으로 fallback
            logger.warning("tiktoken not installed, using space-based tokenization")
            return text.split()

    @staticmethod
    def count_tokens(text: str) -> int:
        """
        텍스트의 OpenAI 토큰 수 계산
        """
        try:
            import tiktoken
            # GPT-4와 동일한 토큰화 사용
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # tiktoken이 없으면 공백 기반으로 fallback
            logger.warning("tiktoken not installed, using space-based token counting")
            return len(text.split())

    @staticmethod
    def split_into_paragraphs(text: str) -> List[str]:
        """
        텍스트를 <p> 태그 기반으로 문단 분할
        """
        import re
        
        # <p> 태그로 문단 분할
        paragraph_pattern = r'<p[^>]*>(.*?)</p>'
        paragraphs = re.findall(paragraph_pattern, text, re.DOTALL)
        
        # <p> 태그가 없으면 빈 줄로 분할
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        return paragraphs

    @staticmethod
    def split_into_sentences(paragraph: str) -> List[str]:
        """
        문단을 spaCy로 문장 분할
        """
        import spacy
        import re
        
        # spaCy 모델 로드 (영어)
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            # 모델이 없으면 다운로드
            import subprocess
            subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
            nlp = spacy.load("en_core_web_sm")
        
        # HTML 태그 제거
        clean_text = re.sub(r'<[^>]+>', '', paragraph)
        
        # spaCy로 문장 분할
        doc = nlp(clean_text)
        sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
        
        return sentences

    @staticmethod
    def get_overlap_sentences(previous_chunk: List[str], overlap_size: int) -> List[str]:
        """
        이전 청크에서 오버랩 크기에 맞는 문장들을 추출
        - 최소 1문장은 반드시 포함
        - 긴 문장이어도 최소 1문장은 포함
        """
        overlap_sentences = []
        overlap_tokens = 0
        
        # 이전 청크의 문장들을 뒤에서부터 확인
        for sentence in reversed(previous_chunk):
            sentence_tokens = RAGService.count_tokens(sentence)
            
            # 첫 번째 문장이면 무조건 포함 (최소 1문장 보장)
            if not overlap_sentences:
                overlap_sentences.insert(0, sentence)
                overlap_tokens += sentence_tokens
                continue
            
            # 추가 문장은 오버랩 크기 내에서만 포함
            if overlap_tokens + sentence_tokens <= overlap_size:
                overlap_sentences.insert(0, sentence)
                overlap_tokens += sentence_tokens
            else:
                # 오버랩 크기를 초과하지만, 최소 1문장은 이미 포함됨
                break
        
        # 최소 1문장 보장 확인
        if not overlap_sentences and previous_chunk:
            # 이전 청크의 마지막 문장을 무조건 포함
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
        의미론적 경계를 존중하는 청킹 (개선된 오버랩 로직)
        """
        chunks = []
        
        # 단계 1: 문단 분할
        paragraphs = RAGService.split_into_paragraphs(text)
        
        # 단계 2: 각 문단을 문장으로 분할
        all_sentences = []
        for paragraph in paragraphs:
            sentences = RAGService.split_into_sentences(paragraph)
            all_sentences.extend(sentences)
        
        # 단계 3: 문장들을 조합하여 청크 생성
        current_chunk = []
        current_tokens = 0
        chunk_start_idx = 0
        
        for i, sentence in enumerate(all_sentences):
            sentence_tokens = RAGService.count_tokens(sentence)
            
            # 청크 크기 제한 확인
            if current_tokens + sentence_tokens > chunk_size_max:
                # 현재 청크가 최소 크기를 만족하면 저장
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
                
                # 개선된 오버랩으로 다음 청크 시작
                overlap_sentences = RAGService.get_overlap_sentences(current_chunk, overlap_size)
                current_chunk = overlap_sentences
                current_tokens = sum(RAGService.count_tokens(s) for s in overlap_sentences)
                
                # 시작 인덱스 계산 (오버랩 문장들의 시작점)
                overlap_start_idx = 0
                for j in range(i - len(overlap_sentences), i):
                    if j >= 0:
                        overlap_start_idx += len(all_sentences[j]) + 1  # +1 for space
                chunk_start_idx = overlap_start_idx
            
            # 문장 추가
            current_chunk.append(sentence)
            current_tokens += sentence_tokens
        
        # 마지막 청크 처리
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
        본문 텍스트를 size 단위로 chunking, overlap 적용 (기존 방식 - 호환성 유지)
        :return: 각 chunk의 start_idx, end_idx 리스트
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
        논문(PaperMeta) 1개를 의미론적 청킹하여 ChunkedPaper 리스트로 변환
        """
        # 의미론적 청킹 사용 (토큰 기반)
        semantic_chunks = RAGService.semantic_chunk_text(
            paper.content, 
            chunk_size_min=200,  # 운영 환경용 설정
            chunk_size_max=500, 
            overlap_size=75
        )
        
        result = []
        for idx, chunk_info in enumerate(semantic_chunks):
            chunk_id = f"chunk-{uuid.uuid4().hex[:8]}"
            chunk = ChunkedPaper(
                chunk_id=chunk_id,
                text=chunk_info['text'],
                source_url=paper.url,
                title=paper.title,
                start_idx=chunk_info['start_idx'],
                end_idx=chunk_info['end_idx'],
                # 논문 식별자
                pmid=getattr(paper, 'pmid', None),
                pmcid=getattr(paper, 'pmcid', None),
                doi=getattr(paper, 'doi', None),
                # 저자 정보
                authors=getattr(paper, 'authors', []),
                # 저널 정보
                journal=getattr(paper, 'journal', None),
                journal_issn=getattr(paper, 'journal_issn', None),
                # 출판년도 정보
                publication_year=getattr(paper, 'publication_year', None),
                # 추가 메타데이터
                mesh_terms=getattr(paper, 'mesh_terms', []),
                abstract=getattr(paper, 'abstract', None),
                # 섹션 태그 정보 (원본 논문에서 전달)
                source_paper=getattr(paper, 'source_paper', None) or {}
                # 우선순위 점수 제거 - 검색 시점에 계산
            )
            result.append(chunk)
        
        # 섹션 정보가 있으면 청크에 매핑
        if hasattr(paper, 'source_paper') and paper.source_paper and 'sections' in paper.source_paper:
            sections = paper.source_paper['sections']
            logger.info(f"Section information found: {len(sections)} sections, paper: {paper.title}")
            # 섹션의 시작/끝 위치를 전체 텍스트 기준으로 계산
            total_offset = 0
            for section in sections:
                section_content = section.get('content', '')
                section['start_idx'] = total_offset
                section['end_idx'] = total_offset + len(section_content)
                total_offset += len(section_content) + 1  # +1 for potential separator
            
            result = RAGService.map_sections_to_chunks(result, sections)
        elif hasattr(paper, 'source_paper') and paper.source_paper and 'section_tags' in paper.source_paper:
            # 섹션 태그는 있지만 섹션 정보가 없는 경우 (fallback 태깅)
            logger.info(f"Section tags exist but no section information: {paper.title}")
            # 섹션 정보 없이 진행 (청킹 태깅만 수행)
        else:
            # source_paper가 없거나 섹션 정보가 없는 경우
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
        LLM 태깅 프롬프트 생성 - 청크별 세부 정보만 추출 (1차 태깅 결과를 컨텍스트로 활용)
        """
        # 1차 태깅 결과가 있으면 컨텍스트로 활용
        context_info = ""
        if section_tags:
            context_info = f"""
DOCUMENT-LEVEL CONTEXT (from section tagging):
- Study Type: {section_tags.get('study_type', {}).get('value', 'unknown')}
- Human Study: {section_tags.get('is_human_study', {}).get('value', False)}
- Study Duration: {section_tags.get('study_duration', {}).get('value', 'unknown')}
- Risk of Bias: {section_tags.get('risk_of_bias', {}).get('value', 'unknown')}
- Participant Count: {section_tags.get('participant_count', {}).get('value', 0)}

Note: Do NOT extract the above fields again. Focus only on chunk-specific information.
"""

        return f'''
Given the following medical text chunk about PCOS, extract chunk-specific information. 
Use the document-level context provided and focus ONLY on chunk-specific details.
Respond ONLY with valid JSON format.

{context_info}

CHUNK TEXT: """{chunk.text}"""

REQUIRED FIELDS (chunk-specific only):
- intervention_type: list of intervention types mentioned in this chunk [food, movement, mindfulness]
  IMPORTANT: You MUST use ONLY these exact terms: "food", "movement", "mindfulness". 
  Do NOT use "diet", "exercise", "workout", "training", "physical activity", "meditation", "stress", "relaxation".
  If you find diet/nutrition related content, use "food".
  If you find exercise/workout/training related content, use "movement".
  If you find meditation/stress/relaxation related content, use "mindfulness".
  If you find supplement/medication content or no intervention content, use empty list [].
- hormone_focus: list of hormones mentioned in this chunk [estrogen, progesterone, androgens, cortisol, insulin, thyroid, testosterone, DHEA, SHBG]
- symptoms_focus: list of PCOS symptoms mentioned in this chunk [acne, hair_loss, hair_thinning, bloating, breast_tenderness]
- relevance_score: 1-10 (10 = highly relevant to PCOS treatment for this specific chunk)
- primary_outcome: main result or finding mentioned in this chunk

OPTIONAL FIELDS (chunk-specific):
- menstrual_phase: if mentioned in this chunk [follicular, ovulation, luteal, menses]
- citation_count: if mentioned in this chunk

IMPORTANT: Do NOT extract study_type, is_human_study, study_duration, or risk_of_bias as these are already determined at document level.

Respond with ONLY this JSON format (no additional text):
{{
  "intervention_type": ["food", "movement"],
  "hormone_focus": ["insulin", "androgens"],
                  "symptoms_focus": ["acne", "hair_loss"],
  "relevance_score": 8,
  "primary_outcome": "Improved insulin sensitivity",
  "menstrual_phase": "follicular",
  "citation_count": 45
}}

IMPORTANT: If a field is not found in the text, use empty string "" for text fields, empty list [] for list fields, and 0 for numeric fields. Do NOT use null or None values.
'''

    @staticmethod
    async def tag_chunk_with_llm(chunk: ChunkedPaper, section_tags: Optional[Dict[str, Any]] = None) -> TaggedChunk:
        """
        LLM을 이용해 chunk에 태깅 정보를 부여한다. (1차 태깅 결과를 컨텍스트로 활용)
        """
        prompt = RAGService.suggest_tagging_prompt(chunk, section_tags)
        llm_response = await AIService.call_openai(prompt)
        
        # LLM 응답에서 JSON 파싱
        try:
            import json
            import re
            
            # JSON 블록 찾기 (```json ... ``` 또는 {...} 형태)
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            
            if json_match:
                json_str = json_match.group(1) if json_match.groups() else json_match.group(0)
                
                # JSON 문자열 정리
                json_str = json_str.strip()
                
                # 디버깅: JSON 문자열 로그
                logger.debug(f"[LLM Tagging] JSON string: {json_str[:200]}...")
                
                parsed = json.loads(json_str)
                
                # 청크별 정보만 처리 (1차 태깅에서 이미 추출된 필드들은 제외)
                parsed = {
                    # 1차 태깅에서 이미 추출된 필드들은 제외
                    # "study_type": parsed.get('study_type') or "",
                    # "is_human_study": parsed.get('is_human_study') or False,
                    # "participant_count": parsed.get('participant_count') or 0,
                    # "risk_of_bias": parsed.get('risk_of_bias') or "",
                    # "study_duration": parsed.get('study_duration') or "",
                    
                    # 청크별 정보만 추출
                    "intervention_type": RAGService.normalize_intervention_type(parsed.get('intervention_type') or []),
                    "hormone_focus": parsed.get('hormone_focus') or [],
                    "symptoms_focus": parsed.get('symptoms_focus') or [],
                    "relevance_score": parsed.get('relevance_score') or 0,
                    "menstrual_phase": parsed.get('menstrual_phase') or "",
                    "citation_count": parsed.get('citation_count') or 0,
                    "primary_outcome": parsed.get('primary_outcome') or ""
                }
                
                logger.info(f"[LLM Tagging] Parsing successful: {chunk.chunk_id}")
                logger.debug(f"  - study_type: {parsed.get('study_type')}")
                logger.debug(f"  - is_human_study: {parsed.get('is_human_study')}")
                logger.debug(f"  - published_year: {parsed.get('published_year')}")
                logger.debug(f"  - participant_count: {parsed.get('participant_count')}")
                logger.debug(f"  - intervention_type: {parsed.get('intervention_type')}")
                logger.debug(f"  - relevance_score: {parsed.get('relevance_score')}")
            else:
                logger.warning(f"[LLM Tagging] JSON not found: {chunk.chunk_id}")
                logger.debug(f"  - Original response: {llm_response[:300]}...")
                parsed = {}
                
        except json.JSONDecodeError as e:
            logger.error(f"[LLM Tagging] JSON parsing failed: {chunk.chunk_id}, error: {e}")
            logger.debug(f"  - Original response: {llm_response[:300]}...")
            parsed = {}
        except Exception as e:
            logger.error(f"[LLM Tagging] Unexpected error: {chunk.chunk_id}, error: {e}")
            parsed = {}
        
        return TaggedChunk(
            chunk_id=chunk.chunk_id,
            # 기본 정보
            study_type=parsed.get("study_type"),
            is_human_study=parsed.get("is_human_study"),
            # published_year=parsed.get("published_year"),  # PubMed API 출판년도 우선 사용
            participant_count=parsed.get("participant_count"),
            
            # 우선순위 기준 Level 1 (의학적 관련성)
            hormone_focus=parsed.get("hormone_focus", []),
            symptoms_focus=parsed.get("symptoms_focus", []),
            relevance_score=parsed.get("relevance_score"),
            
            # 우선순위 기준 Level 2 (필터링)
            intervention_type=parsed.get("intervention_type"),
            
            # 우선순위 기준 Level 3 (품질)
            risk_of_bias=parsed.get("risk_of_bias"),
            citation_count=parsed.get("citation_count"),
            
            # 추가 정보
            menstrual_phase=parsed.get("menstrual_phase"),
            study_duration=parsed.get("study_duration"),
            primary_outcome=parsed.get("primary_outcome"),
            
            # 기존 필드 (하위 호환성)
            tags=parsed.get("tags", []),
            title=chunk.title,
            url=chunk.source_url
        )

    @staticmethod
    async def embed_chunk(chunk: ChunkedPaper) -> EmbeddingResult:
        """
        OpenAI Embedding API를 이용해 chunk 텍스트를 임베딩 벡터로 변환한다.
        :return: EmbeddingResult(id, values, metadata)
        """
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        
        try:
            # OpenAI Embedding API 호출
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
                
                # 기본 메타데이터
                metadata = {
                    "title": chunk.title,
                    "url": chunk.source_url,
                    "text": chunk.text,  # 원문 텍스트 저장 (RAG 필수)
                    "start_idx": chunk.start_idx,
                    "end_idx": chunk.end_idx
                }
                
                # 논문 식별자 추가 (비어있어도 키는 저장)
                metadata["pmid"] = getattr(chunk, 'pmid', None) or ""
                metadata["pmcid"] = getattr(chunk, 'pmcid', None) or ""
                metadata["doi"] = getattr(chunk, 'doi', None) or ""
                
                # 저자 정보 추가
                authors = getattr(chunk, 'authors', [])
                if authors:
                    try:
                        # Pydantic Author 객체의 속성에 접근
                        metadata["authors"] = [f"{author.last_name} {author.first_name}".strip() for author in authors]
                    except Exception as e:
                        logger.warning(f"Author information processing failed: {e}")
                        metadata["authors"] = []
                else:
                    metadata["authors"] = []
                
                # 저널 정보 추가
                metadata["journal"] = getattr(chunk, 'journal', None) or ""
                metadata["journal_issn"] = getattr(chunk, 'journal_issn', None) or ""
                
                # 출판년도 정보 추가
                metadata["publication_year"] = getattr(chunk, 'publication_year', None) or 0
                
                # 추가 메타데이터 (비어있어도 키는 저장)
                metadata["mesh_terms"] = getattr(chunk, 'mesh_terms', None) or []
                metadata["abstract"] = getattr(chunk, 'abstract', None) or ""
                
                # 섹션 정보 추가
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
                
                # 겹치는 섹션 정보 추가 (Pinecone 호환성을 위해 문자열로 변환)
                if hasattr(chunk, 'overlapping_sections') and chunk.overlapping_sections:
                    overlapping_sections_str = []
                    for section in chunk.overlapping_sections:
                        section_str = f"{section.get('section_title', '')}|{section.get('section_type', '')}|{section.get('overlap_ratio', 0.0)}"
                        overlapping_sections_str.append(section_str)
                    metadata["overlapping_sections"] = overlapping_sections_str
                else:
                    metadata["overlapping_sections"] = []
                
                # 섹션 태그 또는 청킹 태그 추가
                try:
                    # 섹션 태그가 있으면 우선 사용
                    if hasattr(chunk, 'source_paper') and chunk.source_paper and isinstance(chunk.source_paper, dict) and 'section_tags' in chunk.source_paper:
                        section_tags = chunk.source_paper['section_tags']
                        
                        tagging_metadata = {
                            "study_type": section_tags.get("study_type", {}).get("value", ""),
                            "is_human_study": section_tags.get("is_human_study", {}).get("value", False),
                            "study_duration": section_tags.get("study_duration", {}).get("value", ""),
                            "risk_of_bias": section_tags.get("risk_of_bias", {}).get("value", ""),
                            "hybrid": section_tags.get("hybrid", False),
                            # 청킹 태그는 여전히 수행
                            "participant_count": 0,
                            "intervention_type": [],
                            "hormone_focus": [],
                            "symptoms_focus": [],
                            "menstrual_phase": "",
                            "relevance_score": 0,
                            "citation_count": 0,
                            "primary_outcome": ""
                        }
                        
                        # 청킹 태그도 수행 (섹션 태그를 컨텍스트로 활용)
                        chunk_tagged = await RAGService.hybrid_tagging(chunk, use_llm=True, section_tags=section_tags)
                        tagging_metadata.update({
                            "participant_count": chunk_tagged.participant_count or 0,
                            "intervention_type": chunk_tagged.intervention_type or [],
                            "hormone_focus": chunk_tagged.hormone_focus or [],
                            "symptoms_focus": chunk_tagged.symptoms_focus or [],
                            "menstrual_phase": chunk_tagged.menstrual_phase or "",
                            "relevance_score": chunk_tagged.relevance_score or 0,
                            "citation_count": chunk_tagged.citation_count or 0,
                            "primary_outcome": chunk_tagged.primary_outcome or ""
                        })
                        
                        logger.info(f"[Section Tagging] Using section tags: {chunk.chunk_id}")
                        logger.debug(f"  - study_type: {tagging_metadata['study_type']}")
                        logger.debug(f"  - is_human_study: {tagging_metadata['is_human_study']}")
                        logger.debug(f"  - hybrid: {tagging_metadata['hybrid']}")
                        
                    else:
                        # 섹션 태그가 없으면 청킹 태깅만 수행 (fallback 태깅은 1차에서 이미 수행됨)
                                logger.info(f"[Chunk Tagging] No section tags, performing chunk tagging only: {chunk.chunk_id}")
        logger.debug(f"source_paper status: {getattr(chunk, 'source_paper', 'None')}")
        if hasattr(chunk, 'source_paper') and chunk.source_paper:
            logger.debug(f"source_paper keys: {list(chunk.source_paper.keys()) if isinstance(chunk.source_paper, dict) else 'Not a dict'}")
                        
                        # 청킹 태깅만 수행 (섹션 태그 없이)
                        chunk_tagged = await RAGService.hybrid_tagging(chunk, use_llm=True, section_tags=None)
                        
                        tagging_metadata = {
                            # 섹션 태그는 없음 (1차에서 이미 처리됨)
                            "study_type": "",
                            "is_human_study": False,
                            "study_duration": "",
                            "risk_of_bias": "",
                            "hybrid": False,
                            # 청킹 태그 결과만 사용
                            "participant_count": chunk_tagged.participant_count or 0,
                            "intervention_type": chunk_tagged.intervention_type or [],
                            "hormone_focus": chunk_tagged.hormone_focus or [],
                            "symptoms_focus": chunk_tagged.symptoms_focus or [],
                            "menstrual_phase": chunk_tagged.menstrual_phase or "",
                            "relevance_score": chunk_tagged.relevance_score or 0,
                            "citation_count": chunk_tagged.citation_count or 0,
                            "primary_outcome": chunk_tagged.primary_outcome or ""
                        }
                        
                        logger.info(f"[Chunk Tagging] 청킹 태그만 사용: {chunk.chunk_id}")
                        logger.debug(f"  - intervention_type: {tagging_metadata['intervention_type']}")
                        logger.debug(f"  - hormone_focus: {tagging_metadata['hormone_focus']}")
                    
                    metadata.update(tagging_metadata)
                    
                except Exception as e:
                    logger.warning(f"[Tagging] 태깅 실패, 기본 메타데이터만 사용: {chunk.chunk_id}, 에러: {e}")
                
                logger.info(f"[OpenAI] 임베딩 생성 성공: {chunk.chunk_id}, 차원: {len(embedding_vector)}")
                return EmbeddingResult(id=chunk.chunk_id, values=embedding_vector, metadata=metadata)
                
        except Exception as e:
            raise RuntimeError(f"OpenAI 임베딩 생성 실패: {e}")

    @staticmethod
    def get_pinecone_client():
        """
        Pinecone 클라이언트 인스턴스 생성 (환경변수 기반)
        """
        PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
        PINECONE_INDEX = os.getenv("PINECONE_INDEX")
        
        if not PINECONE_API_KEY or not PINECONE_INDEX:
            raise RuntimeError("Pinecone 환경변수가 설정되지 않았습니다. PINECONE_API_KEY, PINECONE_INDEX를 설정해주세요.")
        
        try:
            # Pinecone v2 API 사용
            from pinecone import Pinecone
            pc = Pinecone(api_key=PINECONE_API_KEY)
            return pc.Index(PINECONE_INDEX)
        except Exception as e:
            raise RuntimeError(f"Pinecone 클라이언트 초기화 실패: {e}")

    @staticmethod
    async def check_paper_exists_in_pinecone(paper_url: str, namespace: str = "pcos-rag") -> bool:
        """
        논문이 이미 Pinecone에 저장되어 있는지 확인
        :param paper_url: 논문 URL
        :param namespace: Pinecone 네임스페이스
        :return: 존재 여부
        """
        try:
            index = RAGService.get_pinecone_client()
            
            # URL로 메타데이터 검색
            query_response = index.query(
                vector=[0] * 1536,  # 더미 벡터 (실제 검색이 아닌 메타데이터 필터링용)
                filter={"url": {"$eq": paper_url}},
                namespace=namespace,
                top_k=1,
                include_metadata=True
            )
            
            return len(query_response.matches) > 0
            
        except Exception as e:
            logger.warning(f"Pinecone 중복 확인 실패: {e}")
            return False

    @staticmethod
    def extract_doi_from_text(text: str) -> Optional[str]:
        """
        텍스트에서 DOI 추출
        :param text: 논문 텍스트
        :return: DOI 또는 None
        """
        import re
        
        # DOI 패턴 매칭
        doi_patterns = [
            r'doi:\s*([^\s]+)',  # doi: 10.xxxx/xxxx
            r'DOI:\s*([^\s]+)',  # DOI: 10.xxxx/xxxx
            r'https?://doi\.org/([^\s]+)',  # https://doi.org/10.xxxx/xxxx
            r'10\.\d{4,}/[^\s]+',  # 10.xxxx/xxxx 형태
        ]
        
        for pattern in doi_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                doi = match.group(1) if len(match.groups()) > 0 else match.group(0)
                logger.debug(f"DOI 추출: {doi}")
                return doi
        
        return None

    @staticmethod
    def extract_paper_id_from_url(url: str) -> Optional[str]:
        """
        URL에서 논문 ID 추출
        :param url: 논문 URL
        :return: 논문 ID 또는 None
        """
        import re
        from urllib.parse import urlparse
        
        try:
            parsed = urlparse(url)
            
            # PubMed ID 추출
            if 'pubmed' in parsed.netloc:
                pmid_match = re.search(r'(\d+)$', parsed.path)
                if pmid_match:
                    return f"PMID:{pmid_match.group(1)}"
            
            # PMC ID 추출
            elif 'pmc' in parsed.netloc:
                pmc_match = re.search(r'PMC(\d+)', parsed.path)
                if pmc_match:
                    return f"PMC:{pmc_match.group(1)}"
            
            # 기타 논문 ID
            else:
                # URL의 마지막 부분을 ID로 사용
                path_parts = parsed.path.split('/')
                if path_parts and path_parts[-1]:
                    return f"URL_ID:{path_parts[-1]}"
        
        except Exception as e:
            logger.debug(f"URL에서 ID 추출 실패: {e}")
        
        return None

    @staticmethod
    async def get_existing_papers_from_pinecone(namespace: str = "pcos-rag") -> Dict[str, List[str]]:
        """
        Pinecone에 이미 저장된 논문 정보 가져오기 (URL, DOI, ID 기반)
        :param namespace: Pinecone 네임스페이스
        :return: 저장된 논문 정보 딕셔너리
        """
        try:
            index = RAGService.get_pinecone_client()
            
            # 전체 벡터 조회 (최대 10000개)
            fetch_response = index.fetch(
                ids=[],  # 빈 리스트로 전체 조회
                namespace=namespace
            )
            
            existing_urls = set()
            existing_dois = set()
            existing_ids = set()
            
            for vector_id, vector_data in fetch_response.vectors.items():
                if vector_data.metadata:
                    # URL 수집
                    if "url" in vector_data.metadata:
                        existing_urls.add(vector_data.metadata["url"])
                    
                    # DOI 수집
                    if "doi" in vector_data.metadata:
                        existing_dois.add(vector_data.metadata["doi"])
                    
                    # 논문 ID 수집
                    if "paper_id" in vector_data.metadata:
                        existing_ids.add(vector_data.metadata["paper_id"])
            
            logger.info(f"Pinecone에서 {len(existing_urls)}개 URL, {len(existing_dois)}개 DOI, {len(existing_ids)}개 ID 발견")
            
            return {
                "urls": list(existing_urls),
                "dois": list(existing_dois),
                "ids": list(existing_ids)
            }
            
        except Exception as e:
            logger.warning(f"Pinecone 기존 논문 조회 실패: {e}")
            return {"urls": [], "dois": [], "ids": []}

    @staticmethod
    async def filter_new_papers(papers: List[Dict[str, Any]], namespace: str = "pcos-rag") -> List[Dict[str, Any]]:
        """
        이미 Pinecone에 저장된 논문을 필터링하여 새로운 논문만 반환 (URL, DOI, ID 기반)
        :param papers: 원본 논문 리스트
        :param namespace: Pinecone 네임스페이스
        :return: 새로운 논문만 필터링된 리스트
        """
        if not papers:
            return []
        
        # 기존 논문 정보 가져오기
        existing_data = await RAGService.get_existing_papers_from_pinecone(namespace)
        existing_urls = set(existing_data["urls"])
        existing_dois = set(existing_data["dois"])
        existing_ids = set(existing_data["ids"])
        
        new_papers = []
        skipped_count = 0
        
        for paper in papers:
            paper_url = paper.get("url", "")
            paper_content = paper.get("content", "")
            
            # DOI 추출
            doi = RAGService.extract_doi_from_text(paper_content)
            
            # 논문 ID 추출
            paper_id = RAGService.extract_paper_id_from_url(paper_url)
            
            # 중복 확인 (URL, DOI, ID 중 하나라도 일치하면 중복)
            is_duplicate = False
            
            if paper_url and paper_url in existing_urls:
                is_duplicate = True
                logger.debug(f"URL 중복: {paper_url}")
            
            if doi and doi in existing_dois:
                is_duplicate = True
                logger.debug(f"DOI 중복: {doi}")
            
            if paper_id and paper_id in existing_ids:
                is_duplicate = True
                logger.debug(f"ID 중복: {paper_id}")
            
            if is_duplicate:
                skipped_count += 1
                logger.debug(f"기존 논문 건너뛰기: {paper_url}")
            else:
                # 새 논문에 DOI와 ID 정보 추가
                paper["doi"] = doi
                paper["paper_id"] = paper_id
                new_papers.append(paper)
        
        logger.info(f"논문 필터링 완료 - 총 {len(papers)}개 중 {len(new_papers)}개 새 논문, {skipped_count}개 건너뛰기")
        return new_papers

    @staticmethod
    async def save_embedding_to_pinecone(embedding: EmbeddingResult, namespace: str = "pcos-rag") -> bool:
        """
        Pinecone에 임베딩 결과를 저장한다.
        :param embedding: EmbeddingResult
        :param namespace: Pinecone 네임스페이스
        :return: 성공 여부
        """
        try:
            index = RAGService.get_pinecone_client()
            
            # 벡터 저장
            vector_data = {
                "id": embedding.id,
                "values": embedding.values,
                "metadata": embedding.metadata
            }
            index.upsert(vectors=[vector_data], namespace=namespace)
            
            logger.info(f"[Pinecone] 임베딩 저장 성공: {embedding.id}")
            logger.debug(f"  - 벡터 차원: {len(embedding.values)}")
            logger.debug(f"  - 메타데이터: {embedding.metadata}")
            return True
        except Exception as e:
            logger.error(f"[Pinecone] 임베딩 저장 실패: {e}")
            return False

    @staticmethod
    async def process_paper_pipeline(paper: PaperMeta) -> List[Dict[str, Any]]:
        """
        논문 1개에 대해 chunking → 태깅 → 임베딩 → Pinecone 저장까지 전체 파이프라인 실행
        :return: 각 단계별 결과 리스트
        """
        results = []
        chunks = RAGService.chunk_paper(paper)
        for chunk in chunks:
            tagged = await RAGService.hybrid_tagging(chunk)
            embedding = await RAGService.embed_chunk(chunk)
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
        논문 1개에 대해 chunking → 태깅 → 임베딩 → Pinecone 저장까지 전체 파이프라인 실행
        :param paper: PaperMeta
        :param use_llm: LLM 사용 여부
        :return: 각 단계별 결과 리스트
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
        우선순위 기준에 따라 논문을 필터링하고 정렬한다.
        사용자 요구사항에 맞춘 3단계 우선순위 적용
        :param papers: 원본 논문 리스트
        :param user_profile: 사용자 프로필 (선택사항)
        :return: 필터링 및 정렬된 논문 리스트
        """
        if not papers:
            return []
        
        scored_papers = []
        
        for paper in papers:
            score = 0
            content = paper.get("content", "").lower()
            
            # Level 1: 의학적 관련성 (Medical Relevance)
            if user_profile:
                # 사용자 호르몬 결과와의 관련성
                user_hormones = user_profile.get("hormoneScores", {})
                if any(hormone in content for hormone in user_hormones.keys()):
                    score += 30
                
                # 사용자 증상과의 관련성
                user_symptoms = user_profile.get("symptoms", [])
                if any(symptom in content for symptom in user_symptoms):
                    score += 25
                
                # 진단과의 관련성
                user_conditions = user_profile.get("conditions", [])
                if any(condition in content for condition in user_conditions):
                    score += 20
            
            # Level 2: 필터링 기준 (Filtering Criteria)
            # 개입 유형 (식이/운동/마음챙김 > 이론)
            intervention_keywords = ["diet", "nutrition", "food", "meal", "exercise", "workout", "training", "physical activity", "mindfulness", "meditation", "stress", "relaxation", "supplement", "vitamin", "mineral"]
            if any(keyword in content for keyword in intervention_keywords):
                score += 25
            elif "theory" in content or "review" in content:
                score += 5
            
            # 참여 여성 수 (높을수록 좋음)
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
            
            # 연구 유형 (임상시험 > 체계적 리뷰 > 연구논문)
            if "clinical trial" in content or "randomized" in content:
                score += 20
            elif "systematic review" in content or "meta-analysis" in content:
                score += 15
            elif "research" in content:
                score += 5
            
            # Level 3: 품질 기준 (Quality Criteria)
            # 연구 최신성 (2020년 이후)
            try:
                year = int(paper.get("date", "0")[:4])
                if 2020 <= year <= 2025:  # 2020년 이후 논문
                    score += 15
                elif year >= 2015:  # 2015년 이후 논문
                    score += 5
            except:
                pass
            
            # 출처 품질 (PubMed Central > Medarxiv)
            source = paper.get("source", "")
            if "pubmed" in source or "ncbi" in source:
                score += 15
            elif "medarxiv" in source:
                score += 10
            
            # 인용 수 (높을수록 좋음) - 텍스트에서 추출
            citation_match = re.search(r'(\d+)\s*citations?', content)
            if citation_match:
                citation_count = int(citation_match.group(1))
                if citation_count >= 50:
                    score += 10
                elif citation_count >= 20:
                    score += 5
            
            # 편향 위험도 (낮을수록 좋음)
            if "randomized" in content or "blinded" in content:
                score += 10  # 낮은 편향 위험도
            elif "observational" in content:
                score += 5   # 중간 편향 위험도
            
            scored_papers.append({
                **paper,
                "priority_score": score
            })
        
        # 점수 기준으로 정렬 (높은 점수 우선)
        scored_papers.sort(key=lambda x: x["priority_score"], reverse=True)
        
        logger.info(f"[Filtering] 총 {len(papers)}개 논문 중 {len(scored_papers)}개 필터링 완료")
        for i, paper in enumerate(scored_papers[:10]):  # 로그에서는 상위 10개만 표시
            logger.debug(f"  {i+1}. {paper['title'][:50]}... (점수: {paper['priority_score']})")
        
        return scored_papers  # 전체 반환

    @staticmethod
    def rule_based_tagging(chunk: ChunkedPaper) -> TaggedChunk:
        """
        규칙 기반 태깅 - LLM 없이 기본 메타데이터 추출
        """
        text = chunk.text.lower()
        
        # 연구 유형 분류
        study_type = "research_paper"
        if "clinical trial" in text or "randomized" in text:
            study_type = "clinical_trial"
        elif "systematic review" in text or "meta-analysis" in text:
            study_type = "systematic_review"
        elif "case study" in text:
            study_type = "case_study"
        
        # 개입 유형 분류
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
        
        # 호르몬 관련 키워드
        hormone_keywords = ["estrogen", "progesterone", "androgens", "cortisol", "insulin", "thyroid", "testosterone", "dhea", "shbg"]
        hormone_focus = [hormone for hormone in hormone_keywords if hormone in text]
        
        # 증상 관련 키워드
        symptom_keywords = ["acne", "hirsutism", "weight gain", "irregular periods", "infertility", "insulin resistance"]
        symptoms_focus = [symptom for symptom in symptom_keywords if symptom in text]
        
        # 생리 주기 단계
        menstrual_phase = ""
        if "follicular" in text:
            menstrual_phase = "follicular"
        elif "ovulation" in text:
            menstrual_phase = "ovulation"
        elif "luteal" in text:
            menstrual_phase = "luteal"
        elif "menses" in text or "menstrual" in text:
            menstrual_phase = "menses"
        
        # 연도 추출
        import re
        year_match = re.search(r'20[12]\d', text)
        published_year = int(year_match.group()) if year_match else 0
        
        # 참여자 수 추출
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
        OpenAI LLM 태깅만 사용 (1차 태깅 결과를 컨텍스트로 활용)
        :param use_llm: LLM 사용 여부 (무시됨, 항상 True)
        :param section_tags: 1차 태깅 결과 (섹션별 태깅)
        """
        try:
            # OpenAI LLM 태깅만 사용 (1차 태깅 결과를 컨텍스트로 활용)
            llm_tagged = await RAGService.tag_chunk_with_llm(chunk, section_tags)
            logger.info(f"OpenAI Tagging - LLM 태깅 완료: {chunk.chunk_id}")
            return llm_tagged
        except Exception as e:
            logger.error(f"OpenAI Tagging - LLM 태깅 실패: {chunk.chunk_id}, 에러: {e}")
            # 실패시 기본값으로 빈 TaggedChunk 반환
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
        사용자 쿼리에 따라 Pinecone에서 논문을 검색하고 우선순위를 계산한다.
        :param query: 사용자 쿼리
        :param user_profile: 사용자 프로필
        :param top_k: 반환할 최대 논문 수
        :param filter: Pinecone 필터 조건
        :return: 우선순위가 계산된 논문 리스트
        """
        try:
            # 1. 쿼리를 벡터로 변환
            query_vector = await RAGService.get_query_embedding(query)
            
            # 2. Pinecone에서 유사한 벡터 검색
            index = RAGService.get_pinecone_client()
            search_kwargs = {
                "vector": query_vector,
                "top_k": top_k * 3,  # 더 많은 결과를 가져와서 필터링
                "namespace": "pcos-rag",
                "include_metadata": True
            }
            
            # 필터가 있으면 추가
            if filter:
                search_kwargs["filter"] = filter
            
            search_results = index.query(**search_kwargs)
            
            # 3. 검색 결과를 논문 형태로 변환
            papers = []
            for match in search_results.matches:
                paper = {
                    "title": match.metadata.get("title", ""),
                    "content": match.metadata.get("text", ""),  # 원문 텍스트 (RAG 필수)
                    "url": match.metadata.get("url", ""),
                    "date": match.metadata.get("date", ""),
                    "source": match.metadata.get("source", ""),
                    "study_type": match.metadata.get("study_type", ""),
                    "intervention_type": match.metadata.get("intervention_type", ""),
                    "hormone_focus": match.metadata.get("hormone_focus", []),
                    "symptoms_focus": match.metadata.get("symptoms_focus", []),
                    "participant_count": match.metadata.get("participant_count", 0),
                    "published_year": match.metadata.get("published_year", 0),
                    "citation_count": match.metadata.get("citation_count", 0),
                    "risk_of_bias": match.metadata.get("risk_of_bias", ""),
                    "similarity_score": match.score
                }
                papers.append(paper)
            
            # 4. 사용자 프로필 기반 우선순위 계산
            if user_profile:
                ranked_papers = RAGService.calculate_user_priority(papers, user_profile)
            else:
                ranked_papers = RAGService.calculate_general_priority(papers)
            
            # 5. 상위 결과만 반환
            return ranked_papers[:top_k]
        except Exception as e:
            logger.error(f"논문 검색 및 우선순위 계산 실패: {e}")
            return []

    @staticmethod
    async def get_query_embedding(query: str) -> List[float]:
        """
        사용자 쿼리를 벡터로 변환한다.
        """
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        
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
            raise RuntimeError(f"쿼리 임베딩 생성 실패: {e}")

    @staticmethod
    def calculate_user_priority(papers: List[Dict[str, Any]], user_profile: Dict) -> List[Dict[str, Any]]:
        """
        사용자 프로필 기반 우선순위 계산
        """
        scored_papers = []
        
        for paper in papers:
            score = 0
            content = paper.get("content", "").lower()
            
            # Level 1: 의학적 관련성 (사용자 프로필 기반)
            user_hormones = user_profile.get("hormoneScores", {})
            if any(hormone in content for hormone in user_hormones.keys()):
                score += 30
            
            user_symptoms = user_profile.get("symptoms", [])
            if any(symptom in content for symptom in user_symptoms):
                score += 25
            
            user_conditions = user_profile.get("conditions", [])
            if any(condition in content for condition in user_conditions):
                score += 20
            
            # Level 2: 필터링 기준
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
            
            # Level 3: 품질 기준
            published_year = paper.get("published_year", 0)
            if 2015 <= published_year <= 2025:  # 2015년 이후 논문
                score += 15
            elif published_year >= 2010:  # 2010년 이후 논문
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
            
            # 유사도 점수도 고려
            similarity_score = paper.get("similarity_score", 0)
            score += int(similarity_score * 50)  # 유사도 점수를 우선순위에 반영
            
            scored_papers.append({
                **paper,
                "priority_score": score
            })
        
        # 점수 기준으로 정렬
        scored_papers.sort(key=lambda x: x["priority_score"], reverse=True)
        return scored_papers

    @staticmethod
    def calculate_general_priority(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        일반적인 우선순위 계산 (사용자 프로필 없이)
        """
        scored_papers = []
        
        for paper in papers:
            score = 0
            content = paper.get("content", "").lower()
            
            # 일반적인 PCOS 관련성
            pcos_keywords = ["pcos", "polycystic", "ovary", "syndrome"]
            if any(keyword in content for keyword in pcos_keywords):
                score += 20
            
            # 연구 유형
            study_type = paper.get("study_type", "")
            if study_type == "systematic_review" or study_type == "meta_analysis":
                score += 20
            elif study_type == "clinical_trial":
                score += 15
            elif study_type == "research_paper":
                score += 5
            
            # 개입 유형
            intervention_type = paper.get("intervention_type", "")
            if intervention_type in ["food", "movement", "mindfulness"]:
                score += 15
            elif intervention_type == "supplement":
                score += 10
            
            # 참여자 수
            participant_count = paper.get("participant_count", 0)
            if participant_count >= 100:
                score += 15
            elif participant_count >= 50:
                score += 10
            
            # 최신성
            published_year = paper.get("published_year", 0)
            if 2015 <= published_year <= 2025:  # 2015년 이후 논문
                score += 15
            elif published_year >= 2010:  # 2010년 이후 논문
                score += 5
            
            # 유사도 점수
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
        PMC XML에서 섹션별로 파싱하여 청킹 가능한 형태로 변환
        """
        import xml.etree.ElementTree as ET
        
        sections = []
        
        try:
            # XML 파싱
            root = ET.fromstring(xml_content)
            
            # <sec> 태그들을 찾기
            sec_elements = root.findall(".//sec")
            
            for sec in sec_elements:
                section_info = {
                    "title": "",
                    "sec_type": "",
                    "content": "",
                    "confidence": 0.0,
                    "priority": 999  # 낮을수록 우선순위 높음
                }
                
                # 제목 추출
                title_elem = sec.find("title")
                if title_elem is not None and title_elem.text:
                    section_info["title"] = title_elem.text.strip()
                
                # sec-type 속성 확인
                sec_type = sec.get("sec-type", "").lower()
                section_info["sec_type"] = sec_type
                
                # 내용 추출 (p 태그들)
                paragraphs = []
                for p in sec.findall(".//p"):
                    if p.text:
                        paragraphs.append(p.text.strip())
                
                section_info["content"] = " ".join(paragraphs)
                
                # 섹션 타입에 따른 우선순위 설정
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
                
                # sec-type 기반 우선순위
                if sec_type in priority_map:
                    section_info["priority"] = priority_map[sec_type]
                    section_info["confidence"] = 0.9
                else:
                    # 제목 키워드 기반 우선순위
                    title_lower = section_info["title"].lower()
                    for keyword, priority in priority_map.items():
                        if keyword in title_lower:
                            section_info["priority"] = priority
                            section_info["confidence"] = 0.7
                            break
                
                if section_info["content"].strip():  # 내용이 있는 섹션만 추가
                    sections.append(section_info)
            
            # 우선순위로 정렬
            sections.sort(key=lambda x: x["priority"])
            
            return sections
            
        except Exception as e:
            logger.error(f"PMC 섹션 파싱 실패: {e}")
            return []

    @staticmethod
    def create_section_tagging_prompt(section_title: str, section_content: str, section_type: str = "", paper_context: Dict[str, Any] = None) -> str:
        """
        섹션별 LLM 태깅 프롬프트 생성 (문서 컨텍스트 포함)
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
Given the following research paper section, extract specific information with confidence scores (0-1). 
Respond ONLY with valid JSON format.

{context_info}SECTION INFO:
- Title: {section_title}
- Type: {section_type}
- Content: {section_content[:2000]}...

REQUIRED FIELDS (with confidence scores):
- study_type: [clinical_trial, systematic_review, meta_analysis, research_paper, case_study] with confidence (0-1)
- is_human_study: [true, false] with confidence (0-1)
- study_duration: total study duration as a single value (e.g., "12 weeks", "6 months", "2 years") with confidence (0-1)
- risk_of_bias: [low, moderate, high] with confidence (0-1)

If information is not available in this section, use empty string "" for value and 0.0 for confidence. Do NOT use null or None values.

Respond with ONLY this JSON format (no additional text):
{{
  "study_type": {{
    "value": "clinical_trial",
    "confidence": 0.8
  }},
  "is_human_study": {{
    "value": true,
    "confidence": 0.9
  }},
  "study_duration": {{
    "value": "12 weeks",
    "confidence": 0.7
  }},
  "risk_of_bias": {{
    "value": "low",
    "confidence": 0.6
  }}
}}
'''

    @staticmethod
    async def tag_section_with_llm(section_title: str, section_content: str, section_type: str = "", paper_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        섹션별 LLM 태깅 수행 (문서 컨텍스트 포함)
        """
        prompt = RAGService.create_section_tagging_prompt(section_title, section_content, section_type, paper_context)
        llm_response = await AIService.call_openai(prompt)
        
        try:
            import json
            import re
            
            # JSON 블록 찾기
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            
            if json_match:
                json_str = json_match.group(1) if json_match.groups() else json_match.group(0)
                json_str = json_str.strip()
                
                parsed = json.loads(json_str)
                
                # 결과 정리
                result = {
                    "study_type": {
                        "value": parsed.get("study_type", {}).get("value", ""),
                        "confidence": parsed.get("study_type", {}).get("confidence", 0.0)
                    },
                    "is_human_study": {
                        "value": parsed.get("is_human_study", {}).get("value", False),
                        "confidence": parsed.get("is_human_study", {}).get("confidence", 0.0)
                    },
                    "study_duration": {
                        "value": parsed.get("study_duration", {}).get("value", ""),
                        "confidence": parsed.get("study_duration", {}).get("confidence", 0.0)
                    },
                    "risk_of_bias": {
                        "value": parsed.get("risk_of_bias", {}).get("value", ""),
                        "confidence": parsed.get("risk_of_bias", {}).get("confidence", 0.0)
                    }
                }
                
                logger.debug(f"[Section Tagging] 섹션 '{section_title}' 태깅 완료")
                return result
                
            else:
                logger.warning(f"[Section Tagging] JSON을 찾을 수 없음: {section_title}")
                return {
                    "study_type": {"value": "", "confidence": 0.0},
                    "is_human_study": {"value": False, "confidence": 0.0},
                    "study_duration": {"value": "", "confidence": 0.0},
                    "risk_of_bias": {"value": "", "confidence": 0.0}
                }
                
        except Exception as e:
            logger.error(f"[Section Tagging] 섹션 태깅 실패: {section_title}, 에러: {e}")
            return {
                "study_type": {"value": "", "confidence": 0.0},
                "is_human_study": {"value": False, "confidence": 0.0},
                "study_duration": {"value": "", "confidence": 0.0},
                "risk_of_bias": {"value": "", "confidence": 0.0}
            }

    @staticmethod
    async def aggregate_section_tags(sections: List[Dict[str, Any]], use_priority_order: bool = True) -> Dict[str, Any]:
        """
        섹션별 태깅 결과를 종합하여 최종 태그 생성
        :param use_priority_order: True면 우선순위 순서로, False면 신뢰도 기반으로 처리
        """
        final_tags = {
            "study_type": {"value": "", "confidence": 0.0},
            "is_human_study": {"value": False, "confidence": 0.0},
            "study_duration": {"value": "", "confidence": 0.0},
            "risk_of_bias": {"value": "", "confidence": 0.0},
            "hybrid": False
        }
        
        if use_priority_order:
            # Case 1: 우선순위 섹션별로 먼저 채워지는 것을 사용
            for tag_name in ["study_type", "is_human_study", "study_duration"]:
                # 우선순위 순서대로 섹션을 확인하여 첫 번째로 채워진 값을 사용
                for section in sections:
                    if "tags" in section:
                        tag_data = section["tags"].get(tag_name, {})
                        if tag_data.get("value"):  # 값이 있으면 사용
                            final_tags[tag_name] = tag_data
                            logger.debug(f"우선순위 기반 {tag_name}: {tag_data['value']} (섹션: {section['title']})")
                            break  # 첫 번째로 찾은 값 사용
            
            # risk_of_bias는 모든 섹션의 결과를 종합
            risk_scores = []
            for section in sections:
                if "tags" in section:
                    risk_data = section["tags"].get("risk_of_bias", {})
                    if risk_data.get("value") and risk_data.get("confidence", 0.0) > 0:
                        risk_scores.append({
                            "value": risk_data["value"],
                            "confidence": risk_data["confidence"]
                        })
            
            if risk_scores:
                # 신뢰도 가중 평균으로 최종 risk_of_bias 결정
                total_weight = sum(score["confidence"] for score in risk_scores)
                if total_weight > 0:
                    # 간단한 종합 로직
                    high_count = sum(1 for score in risk_scores if score["value"] == "high")
                    moderate_count = sum(1 for score in risk_scores if score["value"] == "moderate")
                    low_count = sum(1 for score in risk_scores if score["value"] == "low")
                    
                    if high_count > moderate_count and high_count > low_count:
                        final_risk = "high"
                    elif moderate_count > low_count:
                        final_risk = "moderate"
                    else:
                        final_risk = "low"
                    
                    final_tags["risk_of_bias"] = {
                        "value": final_risk,
                        "confidence": total_weight / len(risk_scores)
                    }
        else:
            # Case 2 & 3: 신뢰도 기반으로 최고 값 선택
            for tag_name in ["study_type", "is_human_study", "study_duration"]:
                best_section = None
                best_confidence = 0.0
                
                for section in sections:
                    if "tags" in section:
                        tag_data = section["tags"].get(tag_name, {})
                        confidence = tag_data.get("confidence", 0.0)
                        
                        if confidence > best_confidence and tag_data.get("value"):
                            best_confidence = confidence
                            best_section = section
                
                if best_section:
                    final_tags[tag_name] = best_section["tags"][tag_name]
            
            # risk_of_bias 종합 (동일)
            risk_scores = []
            for section in sections:
                if "tags" in section:
                    risk_data = section["tags"].get("risk_of_bias", {})
                    if risk_data.get("value") and risk_data.get("confidence", 0.0) > 0:
                        risk_scores.append({
                            "value": risk_data["value"],
                            "confidence": risk_data["confidence"]
                        })
            
            if risk_scores:
                total_weight = sum(score["confidence"] for score in risk_scores)
                if total_weight > 0:
                    high_count = sum(1 for score in risk_scores if score["value"] == "high")
                    moderate_count = sum(1 for score in risk_scores if score["value"] == "moderate")
                    low_count = sum(1 for score in risk_scores if score["value"] == "low")
                    
                    if high_count > moderate_count and high_count > low_count:
                        final_risk = "high"
                    elif moderate_count > low_count:
                        final_risk = "moderate"
                    else:
                        final_risk = "low"
                    
                    final_tags["risk_of_bias"] = {
                        "value": final_risk,
                        "confidence": total_weight / len(risk_scores)
                    }
        
        # hybrid 체크 (인간과 동물 혼합 연구)
        human_sections = []
        animal_sections = []
        
        for section in sections:
            if "tags" in section:
                is_human = section["tags"].get("is_human_study", {}).get("value", False)
                if is_human:
                    human_sections.append(section)
                else:
                    animal_sections.append(section)
        
        if human_sections and animal_sections:
            final_tags["hybrid"] = True
            final_tags["is_human_study"]["value"] = True  # 혼합 연구는 human으로 분류
        
        return final_tags

    @staticmethod
    async def process_paper_with_section_tagging(paper: Dict[str, Any]) -> Dict[str, Any]:
        """
        PMC XML을 섹션별로 파싱하고 LLM 태깅을 수행
        """
        try:
            # PMC XML에서 섹션 파싱
            if "pmc_xml" in paper:
                sections = RAGService.parse_pmc_sections(paper["pmc_xml"])
                
                if sections:
                    # 우선순위 섹션들이 있는지 확인 (Methods, Discussion, Abstract, Introduction, Results, Conclusion)
                    priority_sections = ["methods", "discussion", "abstract", "introduction", "results", "conclusion"]
                    has_priority_sections = any(
                        section["sec_type"] in priority_sections or 
                        any(keyword in section["title"].lower() for keyword in priority_sections)
                        for section in sections
                    )
                    
                    if has_priority_sections:
                        # 우선순위 섹션들이 있으면 섹션별 태깅 수행
                        logger.info(f"우선순위 섹션 발견, 섹션별 태깅 수행: {len(sections)}개 섹션")
                        
                        # 논문 컨텍스트 준비
                        paper_context = {
                            "title": paper.get("title", ""),
                            "abstract": paper.get("abstract", ""),
                            "mesh_terms": paper.get("mesh_terms", [])
                        }
                        
                        for i, section in enumerate(sections):
                            logger.info(f"섹션 태깅 진행 중: {i+1}/{len(sections)} - {section['title'][:50]}...")
                            section_tags = await RAGService.tag_section_with_llm(
                                section["title"], 
                                section["content"], 
                                section["sec_type"],
                                paper_context
                            )
                            section["tags"] = section_tags
                            logger.info(f"섹션 태깅 완료: {i+1}/{len(sections)}")
                        
                        # 섹션 태그들을 종합 (우선순위 순서 사용)
                        final_tags = await RAGService.aggregate_section_tags(sections, use_priority_order=True)
                        
                        # 논문에 최종 태그 추가
                        paper["section_tags"] = final_tags
                        paper["sections"] = sections
                        
                        logger.info(f"섹션별 태깅 완료: {len(sections)}개 섹션")
                        return paper
                    else:
                        # 우선순위 섹션이 없으면 기존 섹션들을 사용하여 태깅
                        logger.warning("우선순위 섹션 없음, 기존 섹션들로 태깅")
                        return await RAGService.process_paper_with_non_priority_sections(paper, sections)
                else:
                    # 섹션이 전혀 없으면 Edge Case 처리
                    logger.warning("섹션 파싱 실패 (섹션 없음), 전체 텍스트로 태깅")
                    return await RAGService.process_paper_with_fallback_tagging(paper)
            else:
                logger.warning("PMC XML이 없음, 기존 방식 사용")
                return paper
                
        except Exception as e:
            logger.error(f"섹션별 태깅 실패: {e}")
            return await RAGService.process_paper_with_fallback_tagging(paper)

    @staticmethod
    async def process_paper_with_fallback_tagging(paper: Dict[str, Any]) -> Dict[str, Any]:
        """
        Edge Case: 섹션 구분 실패 시 전체 텍스트를 청크로 나누어 태깅
        - Case 1: 섹션이 전혀 없는 경우
        - Case 2: 우선순위 섹션(Methods, Discussion 등)이 없는 경우
        """
        try:
            # 전체 텍스트를 여러 청크로 나누어 태깅
            content = paper.get("content", "")
            if not content:
                logger.warning("본문이 없어서 태깅 불가")
                return paper
            
            # 텍스트를 8개 청크로 분할
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
            
            logger.info(f"Fallback 태깅 시작: {len(chunks)}개 청크")
            
            # 논문 컨텍스트 준비
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
                logger.debug(f"청크 {i+1} 태깅 완료")
            
            # 신뢰도 기반으로 최고 값 선택
            final_tags = {
                "study_type": {"value": "", "confidence": 0.0},
                "is_human_study": {"value": False, "confidence": 0.0},
                "study_duration": {"value": "", "confidence": 0.0},
                "risk_of_bias": {"value": "", "confidence": 0.0},
                "hybrid": False
            }
            
            # 각 태그별로 최고 신뢰도 값 선택
            for tag_name in ["study_type", "is_human_study", "study_duration"]:
                best_tag = None
                best_confidence = 0.0
                
                for tags in all_tags:
                    if tags[tag_name]["confidence"] > best_confidence and tags[tag_name]["value"]:
                        best_confidence = tags[tag_name]["confidence"]
                        best_tag = tags[tag_name]
                
                if best_tag:
                    final_tags[tag_name] = best_tag
                    logger.debug(f"최고 신뢰도 {tag_name}: {best_tag['value']} (신뢰도: {best_tag['confidence']:.2f})")
            
            # risk_of_bias 종합 (모든 청크의 결과를 종합)
            risk_scores = [tag["risk_of_bias"] for tag in all_tags if tag["risk_of_bias"]["value"]]
            if risk_scores:
                # 신뢰도 기반 종합
                high_count = sum(1 for score in risk_scores if score["value"] == "high")
                moderate_count = sum(1 for score in risk_scores if score["value"] == "moderate")
                low_count = sum(1 for score in risk_scores if score["value"] == "low")
                
                if high_count > moderate_count and high_count > low_count:
                    final_risk = "high"
                elif moderate_count > low_count:
                    final_risk = "moderate"
                else:
                    final_risk = "low"
                
                avg_confidence = sum(score["confidence"] for score in risk_scores) / len(risk_scores)
                final_tags["risk_of_bias"] = {"value": final_risk, "confidence": avg_confidence}
                
                logger.debug(f"Risk of bias 종합: {final_risk} (신뢰도: {avg_confidence:.2f})")
            
            paper["section_tags"] = final_tags
            paper["fallback_tagging"] = True
            
            logger.info(f"Fallback 태깅 완료: {len(chunks)}개 청크 처리")
            return paper
            
        except Exception as e:
            logger.error(f"Fallback 태깅 실패: {e}")
            return paper

    @staticmethod
    async def process_paper_with_non_priority_sections(paper: Dict[str, Any], sections: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Case 2: 우선순위 섹션이 없지만 <sec> 태그는 있는 경우
        기존 섹션들을 사용하여 태깅 수행
        """
        try:
            logger.info(f"비우선순위 섹션 태깅 시작: {len(sections)}개 섹션")
            
            # 논문 컨텍스트 준비
            paper_context = {
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "mesh_terms": paper.get("mesh_terms", [])
            }
            
            # 각 섹션에 대해 LLM 태깅 수행
            for section in sections:
                section_tags = await RAGService.tag_section_with_llm(
                    section["title"], 
                    section["content"], 
                    section["sec_type"],
                    paper_context
                )
                section["tags"] = section_tags
                logger.debug(f"섹션 '{section['title']}' 태깅 완료")
            
            # 섹션 태그들을 종합 (신뢰도 기반)
            final_tags = await RAGService.aggregate_section_tags(sections, use_priority_order=False)
            
            # 논문에 최종 태그 추가
            paper["section_tags"] = final_tags
            paper["sections"] = sections
            paper["non_priority_sections"] = True
            
            logger.info(f"비우선순위 섹션 태깅 완료: {len(sections)}개 섹션")
            return paper
            
        except Exception as e:
            logger.error(f"비우선순위 섹션 태깅 실패: {e}")
            return await RAGService.process_paper_with_fallback_tagging(paper) 

    @staticmethod
    def get_section_type(chunk: ChunkedPaper) -> str:
        """청크의 섹션 타입을 추정"""
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
        섹션별 가중치가 적용된 임베딩 생성
        """
        # 섹션 타입 확인
        section_type = RAGService.get_section_type(chunk)
        weight = RAGService.SECTION_WEIGHTS.get(section_type, 1.0)
        
        # 기본 임베딩 생성
        base_embedding = await RAGService.get_cached_embedding(chunk.text)
        
        # 가중치 적용
        weighted_embedding = [v * weight for v in base_embedding]
        
        # 메타데이터 생성 (기존과 동일)
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
        캐싱된 임베딩 생성
        """
        # 간단한 해시 기반 캐싱
        text_hash = hash(text)
        
        if hasattr(RAGService, '_embedding_cache'):
            if text_hash in RAGService._embedding_cache:
                logger.debug(f"캐시된 임베딩 사용: {text_hash}")
                return RAGService._embedding_cache[text_hash]
        else:
            RAGService._embedding_cache = {}
        
        # 새 임베딩 생성
        embedding = await RAGService.get_embedding(text)
        RAGService._embedding_cache[text_hash] = embedding
        
        logger.debug(f"새 임베딩 생성 및 캐싱: {text_hash}")
        return embedding
    
    @staticmethod
    async def get_embedding(text: str) -> List[float]:
        """
        기본 임베딩 생성 함수
        """
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        
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
            raise RuntimeError(f"임베딩 생성 실패: {e}")
    
    @staticmethod
    def create_chunk_metadata(chunk: ChunkedPaper) -> Dict[str, Any]:
        """
        청크 메타데이터 생성 (기존 embed_chunk에서 분리)
        """
        metadata = {
            "title": chunk.title,
            "url": chunk.source_url,
            "text": chunk.text,
            "start_idx": chunk.start_idx,
            "end_idx": chunk.end_idx
        }
        
        # 논문 식별자
        metadata["pmid"] = getattr(chunk, 'pmid', None) or ""
        metadata["pmcid"] = getattr(chunk, 'pmcid', None) or ""
        metadata["doi"] = getattr(chunk, 'doi', None) or ""
        
        # 저자 정보
        authors = getattr(chunk, 'authors', [])
        if authors:
            try:
                metadata["authors"] = [f"{author.last_name} {author.first_name}".strip() for author in authors]
            except Exception as e:
                logger.warning(f"저자 정보 처리 실패: {e}")
                metadata["authors"] = []
        else:
            metadata["authors"] = []
        
        # 저널 정보
        metadata["journal"] = getattr(chunk, 'journal', None) or ""
        metadata["journal_issn"] = getattr(chunk, 'journal_issn', None) or ""
        metadata["publication_year"] = getattr(chunk, 'publication_year', None) or 0
        
        # 추가 메타데이터
        metadata["mesh_terms"] = getattr(chunk, 'mesh_terms', None) or []
        metadata["abstract"] = getattr(chunk, 'abstract', None) or ""
        
        return metadata

    @staticmethod
    def map_sections_to_chunks(chunks: List[ChunkedPaper], sections: List[Dict[str, Any]]) -> List[ChunkedPaper]:
        """
        청크에 섹션 정보를 매핑 (청크가 여러 섹션에 걸쳐있을 때 모든 섹션 표기)
        """
        for chunk in chunks:
            overlapping_sections = []
            primary_section = None
            max_overlap_ratio = 0
            
            for section in sections:
                # 섹션의 시작과 끝 위치 (전체 텍스트 기준)
                section_start = section.get("start_idx", 0)
                section_end = section.get("end_idx", len(section.get("content", "")))
                
                # 청크와 섹션의 겹치는 부분 계산
                overlap_start = max(chunk.start_idx, section_start)
                overlap_end = min(chunk.end_idx, section_end)
                
                if overlap_start < overlap_end:  # 겹치는 부분이 있음
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
                    
                    # 가장 많이 겹치는 섹션을 주요 섹션으로 설정
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
            
            # 청크에 섹션 정보 설정
            chunk.section_info = primary_section
            chunk.overlapping_sections = overlapping_sections
            
            # 로깅
            if len(overlapping_sections) > 1:
                logger.info(f"청크 {chunk.chunk_id}가 {len(overlapping_sections)}개 섹션에 걸쳐있음:")
                for section in overlapping_sections:
                    logger.info(f"  - {section['section_title']} ({section['section_type']}): {section['overlap_ratio']:.2f}")
        
        return chunks

    @staticmethod
    def normalize_intervention_type(intervention_types: List[str]) -> List[str]:
        """
        Intervention types를 정규화하여 표준화된 형태로 변환
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
    async def process_paper_complete_pipeline(paper: Dict[str, Any]) -> bool:
        """
        문서 하나에 대해 완전한 플로우 실행
        1차 태깅 → 청킹 → 2차 태깅 → 임베딩 → 저장
        :param paper: 처리할 논문 데이터
        :return: 성공 여부
        """
        try:
            logger.info(f"문서 완전 플로우 시작: {paper.get('title', 'Unknown')[:50]}...")
            
            # 1. 1차 태깅 (섹션별)
            paper = await RAGService.process_paper_with_section_tagging(paper)
            
            # source_paper에 섹션 태그 정보와 섹션 정보 저장
            if "source_paper" not in paper:
                paper["source_paper"] = {}
            if "section_tags" in paper:
                paper["source_paper"]["section_tags"] = paper["section_tags"]
            if "sections" in paper:
                paper["source_paper"]["sections"] = paper["sections"]
            paper["source_paper"]["content"] = paper.get("content", "")
            
            # 2. PaperMeta로 변환
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
            
            # 3. 청킹
            chunks = RAGService.chunk_paper(paper_meta)
            logger.info(f"청킹 완료: {len(chunks)}개 청크")
            
            # 4. 각 청크별 2차 태깅 및 저장
            saved_chunks = 0
            for i, chunk in enumerate(chunks):
                try:
                    # 2차 태깅 (1차 결과를 컨텍스트로 사용)
                    tagged = await RAGService.hybrid_tagging(
                        chunk, 
                        section_tags=paper.get("section_tags")
                    )
                    
                    # 임베딩
                    embedding = await RAGService.embed_chunk(chunk)
                    
                    # Pinecone 저장
                    success = await RAGService.save_embedding_to_pinecone(embedding)
                    
                    if success:
                        saved_chunks += 1
                        logger.info(f"청크 저장 성공: {chunk.chunk_id} ({i+1}/{len(chunks)})")
                    else:
                        logger.error(f"Pinecone 저장 실패: {chunk.chunk_id}")
                        
                except Exception as e:
                    logger.error(f"청크 처리 실패: {chunk.chunk_id}, 에러: {e}")
                    continue
            
            logger.info(f"문서 완전 처리 성공: {paper.get('title', 'Unknown')[:50]}... (저장된 청크: {saved_chunks}/{len(chunks)})")
            return True
            
        except Exception as e:
            logger.error(f"문서 완전 처리 실패: {paper.get('title', 'Unknown')}, 에러: {e}")
            return False