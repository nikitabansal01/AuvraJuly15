from fastapi import APIRouter, HTTPException, status, Request
from app.models.rag_models import PaperMeta, RAGRequest
from app.services.rag_service import RAGService
from typing import List, Dict, Any
import logging
import time
from app.models.rag_models import RAGResponse

# RAG endpoint dedicated logger setup
logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/health")
async def health_check():
    """
    RAG service status check
    """
    return {"status": "healthy", "service": "RAG"}

@router.get("/pinecone-status")
async def pinecone_status_endpoint():
    """
    Pinecone index status check
    """
    try:
        index = RAGService.get_pinecone_client()
        stats = index.describe_index_stats()
        
        # Extract statistics
        total_vector_count = stats.total_vector_count
        namespaces = stats.namespaces
        
        # Statistics by namespace
        namespace_stats = {}
        for ns_name, ns_info in namespaces.items():
            namespace_stats[ns_name] = {
                "vector_count": ns_info.vector_count
            }
        
        # Domain statistics (URL based)
        all_vectors = index.query(
            vector=[0] * 1536,  # Dummy vector
            top_k=1000,
            include_metadata=True
        )
        
        unique_domains = set()
        for match in all_vectors.matches:
            url = match.metadata.get("url", "")
            if url:
                domain = url.split("/")[2] if len(url.split("/")) > 2 else url
                unique_domains.add(domain)
        
        return {
            "status": "connected",
            "total_vectors": total_vector_count,
            "namespaces": namespace_stats,
            "domains": list(unique_domains)[:20]  # Top 20 domains
        }
        
    except Exception as e:
        logger.error(f"Pinecone status check failed: {e}")
        return {"status": "error", "message": str(e)} 

@router.post("/search-rag")
async def search_rag_endpoint(request: Dict[str, Any]):
    """
    Personalized RAG search based on user query
    :param request: {"query": "user question", "user_profile": {...}, "top_k": 5}
    """
    query = request.get("query", "")
    user_profile = request.get("user_profile", None)
    top_k = request.get("top_k", 5)
    
    if not query:
        raise HTTPException(status_code=400, detail="Query is required.")
    
    logger.info(f"RAG search started - query: {query[:50]}..., user profile: {user_profile is not None}")
    
    try:
        # RAG search and priority calculation
        ranked_papers = await RAGService.search_and_rank_papers(query, user_profile, top_k)
        
        logger.info(f"RAG search completed - {len(ranked_papers)} papers found")
        
        return {
            "query": query,
            "user_profile": user_profile,
            "papers_found": len(ranked_papers),
            "ranked_papers": [
                {
                    "title": paper.get("title", ""),
                    "content": paper.get("content", "")[:200] + "...",  # Content preview
                    "url": paper.get("url", ""),
                    "priority_score": paper.get("priority_score", 0),
                    "study_type": paper.get("study_type", ""),
                    "intervention_type": paper.get("intervention_type", ""),
                    "participant_count": paper.get("participant_count", 0),
                    "published_year": paper.get("published_year", 0),
                    "similarity_score": paper.get("similarity_score", 0)
                }
                for paper in ranked_papers
            ]
        }
        
    except Exception as e:
        logger.error(f"RAG search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"RAG search failed: {str(e)}") 

@router.post("/fetch-and-process", response_model=RAGResponse)
async def fetch_and_process_endpoint(resume_from_checkpoint: bool = False):
    """
    Collect and process PCOS related papers and store in Pinecone
            Default settings: max 50 papers, after 2015, use LLM tagging
    :param resume_from_checkpoint: Whether to resume from checkpoint (default: False)
    """
    start_time = time.time()
    logger.info(f"PCOS paper collection and processing started (resume_from_checkpoint: {resume_from_checkpoint})")
    
    try:
        # 1. Collect papers from PubMed Central (default settings)
        all_papers = await RAGService.fetch_pcos_papers_from_pubmed_api(resume_from_checkpoint)
        logger.info(f"PubMed Central paper collection completed: {len(all_papers)} papers")
        
        if not all_papers:
            return RAGResponse(
                success=False,
                message="No papers found in PubMed Central.",
                papers_processed=0,
                papers_stored=0,
                processing_time=time.time() - start_time
            )
        
        # 2. Filter duplicate papers
        filtered_papers = await RAGService.filter_new_papers(all_papers)
        logger.info(f"After deduplication: {len(filtered_papers)} papers")
        
        if not filtered_papers:
            return RAGResponse(
                success=True,
                message="All papers are already stored.",
                papers_processed=len(all_papers),
                papers_stored=0,
                processing_time=time.time() - start_time
            )
        
        # 3. Process all papers (use LLM tagging)
        processed_count = 0
        stored_count = 0
        
        for paper in filtered_papers:
            try:
                # Create PaperMeta object
                paper_meta = PaperMeta(
                    title=paper["title"],
                    content=paper["content"],
                    url=paper["url"],
                    date=paper["date"],
                    source=paper.get("source", "pubmed-api"),
                    # Paper identifiers
                    pmid=paper.get("pmid"),
                    pmcid=paper.get("pmcid"),
                    doi=paper.get("doi"),
                    # Author information
                    authors=paper.get("authors", []),
                    # Journal information
                    journal=paper.get("journal"),
                    journal_issn=paper.get("journal_issn"),
                    # Publication year information
                    publication_year=paper.get("publication_year"),
                    # Additional metadata
                    mesh_terms=paper.get("mesh_terms", []),
                    abstract=paper.get("abstract"),
                    # Include both section tags and section information
                    source_paper={
                        "section_tags": paper.get("section_tags"),
                        "sections": paper.get("sections"),
                        "content": paper.get("content")
                    } if paper.get("section_tags") or paper.get("sections") else None
                )
                
                # Process paper (use LLM tagging)
                results = await RAGService.process_paper_pipeline_with_llm_option(paper_meta, use_llm=True)
                processed_count += 1
                stored_count += len(results)
                
                logger.info(f"Paper processing completed: {paper_meta.title[:50]}... ({len(results)} chunks saved)")
                
            except Exception as e:
                logger.error(f"Paper processing failed: {paper.get('title', 'Unknown')}, error: {e}")
                continue
        
        processing_time = time.time() - start_time
        
        return RAGResponse(
            success=True,
            message=f"PCOS paper collection and processing completed",
            papers_processed=processed_count,
            papers_stored=stored_count,
            processing_time=processing_time
        )
        
    except Exception as e:
        logger.error(f"PCOS paper collection and processing failed: {e}", exc_info=True)
        return RAGResponse(
            success=False,
            message=f"Processing failed: {str(e)}",
            papers_processed=0,
            papers_stored=0,
            processing_time=time.time() - start_time
        ) 

@router.get("/test-pubmed-search")
async def test_pubmed_search_endpoint():
    """
    Test API to show PubMed search results as raw XML
    """
    try:
        import httpx
        
        # PubMed search URL (same as currently used)
        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        mesh_query = "Polycystic+Ovary+Syndrome[Mesh]+OR+PCOS[Mesh]+OR+Stein-Leventhal+Syndrome[Mesh]"
        max_results = 100
        
        search_url = f"{base_url}esearch.fcgi?db=pubmed&term={mesh_query}&retmax={max_results}&retmode=xml&datetype=pdat&mindate=2015&maxdate=2025"
        
        logger.info(f"PubMed search URL: {search_url}")
        
        # Execute PubMed search
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            
            # Return XML response as is
            xml_content = response.text
            
            # Extract basic information (for logging)
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_content)
                id_list = root.findall(".//Id")
                id_count = len(id_list)
                logger.info(f"PubMed search results: {id_count} IDs found")
            except Exception as e:
                logger.warning(f"XML parsing failed: {e}")
                id_count = "Parsing failed"
            
        return {
                "status": "success",
                "search_url": search_url,
                "id_count": id_count,
                "raw_xml": xml_content,
                "content_length": len(xml_content)
            }
            
    except Exception as e:
        logger.error(f"PubMed search test failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "search_url": search_url if 'search_url' in locals() else "N/A"
        }

@router.get("/test-pmc-fetch/{pmcid}")
async def test_pmc_fetch_endpoint(pmcid: str):
    """
    Test API to fetch PMC XML by PMC ID
    """
    try:
        import httpx
        
        # Fetch content from PMC (same as currently used)
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
        
        logger.info(f"PMC fetch URL: {fetch_url}")
        
        # Fetch PMC XML
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(fetch_url)
            response.raise_for_status()
            
            # Return XML response as is
            xml_content = response.text
            
            # Extract basic information (for logging)
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_content)
                body_elements = root.findall(".//body")
                abstract_elements = root.findall(".//abstract")
                
                logger.info(f"PMC XML parsing: body={len(body_elements)} elements, abstract={len(abstract_elements)} elements")
            except Exception as e:
                logger.warning(f"PMC XML parsing failed: {e}")
            
            return {
                "status": "success",
                "pmcid": pmcid,
                "fetch_url": fetch_url,
                "raw_xml": xml_content,
                "content_length": len(xml_content)
            }
            
    except Exception as e:
        logger.error(f"PMC fetch test failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "pmcid": pmcid,
            "fetch_url": fetch_url if 'fetch_url' in locals() else "N/A"
        } 

@router.get("/test-pubmed-to-pmc")
async def test_pubmed_to_pmc_endpoint():
    """
    Test API to show the complete process of PubMed search → PMC XML fetch
    """
    try:
        import httpx
        import xml.etree.ElementTree as ET
        
        # Step 1: PubMed search
        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        mesh_query = "Polycystic+Ovary+Syndrome[Mesh]+OR+PCOS[Mesh]+OR+Stein-Leventhal+Syndrome[Mesh]"
        max_results = 10  # Only 10 for testing
        
        search_url = f"{base_url}esearch.fcgi?db=pubmed&term={mesh_query}&retmax={max_results}&retmode=xml&datetype=pdat&mindate=2015&maxdate=2025"
        
        logger.info(f"Step 1: PubMed search started - URL: {search_url}")
        
        # Execute PubMed search
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            pubmed_xml = response.text
            
            # Extract PubMed IDs
            root = ET.fromstring(pubmed_xml)
            pubmed_ids = [id_elem.text for id_elem in root.findall(".//Id")]
            logger.info(f"PubMed search completed: {len(pubmed_ids)} IDs found")
            
            # Step 2: Fetch PubMed detailed information
            fetch_url = f"{base_url}efetch.fcgi?db=pubmed&retmode=xml&id={','.join(pubmed_ids)}"
            logger.info(f"Step 2: Fetch PubMed detailed information - URL: {fetch_url}")
            
            response = await client.get(fetch_url)
            response.raise_for_status()
            pubmed_detail_xml = response.text
            
            # Extract PMC IDs
            root = ET.fromstring(pubmed_detail_xml)
            papers_with_pmcid = []
            
            for article in root.findall(".//PubmedArticle"):
                pmid = article.find(".//PMID")
                pmid_text = pmid.text if pmid is not None else ""
                
                # Find PMC ID
                pmcid = ""
                article_ids = article.findall(".//ArticleId")
                for article_id in article_ids:
                    if article_id.get("IdType") == "pmc":
                        pmcid = article_id.text
                        break
                
                if pmcid:
                    papers_with_pmcid.append({
                        "pmid": pmid_text,
                        "pmcid": pmcid
                    })
            
            logger.info(f"Papers with PMC ID: {len(papers_with_pmcid)}")
            
            # Step 3: Fetch PMC XML (first paper only)
            pmc_results = []
            if papers_with_pmcid:
                first_paper = papers_with_pmcid[0]
                pmcid = first_paper["pmcid"]
                
                pmc_fetch_url = f"{base_url}efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
                logger.info(f"Step 3: Fetch PMC XML - URL: {pmc_fetch_url}")
                
                response = await client.get(pmc_fetch_url)
                response.raise_for_status()
                pmc_xml = response.text
                
                pmc_results.append({
                    "pmid": first_paper["pmid"],
                    "pmcid": pmcid,
                    "pmc_xml": pmc_xml,
                    "content_length": len(pmc_xml)
                })
        
        return {
            "status": "success",
                "process": {
                    "step1_pubmed_search": {
                        "url": search_url,
                        "xml": pubmed_xml,
                        "id_count": len(pubmed_ids)
                    },
                    "step2_pubmed_detail": {
                        "url": fetch_url,
                        "xml": pubmed_detail_xml,
                        "papers_with_pmcid": papers_with_pmcid
                    },
                    "step3_pmc_fetch": {
                        "results": pmc_results
                    }
                }
        }
        
    except Exception as e:
        logger.error(f"PubMed → PMC test failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        } 