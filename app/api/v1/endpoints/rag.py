from fastapi import APIRouter, HTTPException, status, Request, Query
from app.models.rag_models import PaperMeta, RAGRequest, Author
from app.services.rag_service import RAGService
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import logging
import time
from app.models.rag_models import RAGResponse

# RAG endpoint dedicated logger setup
logger = logging.getLogger(__name__)

router = APIRouter()


def convert_authors_to_objects(authors: List[Any]) -> List[Author]:
    """
    Convert author strings or dicts to Author objects.
    Handles formats like: "Smith J", "John Smith", {"name": "Smith J"}
    """
    result = []
    for author in authors:
        if author is None:
            continue
        
        # Already an Author object
        if isinstance(author, Author):
            result.append(author)
            continue
        
        # Dict format
        if isinstance(author, dict):
            if "name" in author:
                name = author["name"]
            elif "last_name" in author and "first_name" in author:
                result.append(Author(
                    last_name=author["last_name"],
                    first_name=author["first_name"],
                    affiliation=author.get("affiliation")
                ))
                continue
            else:
                continue
        else:
            # String format like "Smith J" or "John Smith"
            name = str(author)
        
        # Parse name string
        parts = name.strip().split()
        if len(parts) >= 2:
            # Try to detect format: "LastName FirstInitial" vs "First Last"
            if len(parts[-1]) <= 2:  # Likely "Smith J" format
                last_name = " ".join(parts[:-1])
                first_name = parts[-1]
            else:  # Likely "John Smith" format
                first_name = parts[0]
                last_name = " ".join(parts[1:])
        elif len(parts) == 1:
            last_name = parts[0]
            first_name = ""
        else:
            last_name = "Unknown"
            first_name = ""
        
        result.append(Author(
            last_name=last_name,
            first_name=first_name,
            affiliation=None
        ))
    
    return result

# Request model for indexing
class IndexRequest(BaseModel):
    tiers: Optional[List[str]] = None

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
                    authors=convert_authors_to_objects(paper.get("authors", [])),
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


@router.post("/index-expanded")
async def index_expanded_papers(request: IndexRequest = None):
    """
    Index papers using expanded Tier queries
    
    Args:
        request: JSON body with "tiers" list. Options: ["all"], ["essential"], or specific tiers
                Use "all" for all 205 queries (~$0.40)
                Use "essential" for tiers 1-5 (~$0.20)
    
    Example:
        curl -X POST "http://localhost:8000/api/v1/rag/index-expanded" \\
             -H "Content-Type: application/json" \\
             -d '{"tiers": ["all"]}'
    """
    start_time = time.time()
    
    # Handle None request or None tiers
    if request is None or request.tiers is None:
        tiers = ["tier_1_pcos"]
    else:
        tiers = request.tiers
    
    logger.info(f"📚 Starting expanded paper indexing for tiers: {tiers}")
    
    try:
        from app.services.rag.paper_fetcher import fetch_papers_for_rag
        from app.models.rag_models import PaperMeta
        
        # Step 1: Fetch papers from expanded queries
        papers = await fetch_papers_for_rag(tiers)
        logger.info(f"✅ Fetched {len(papers)} papers from PubMed")
        
        if not papers:
            return {
                "success": False,
                "message": "No papers found for specified tiers",
                "tiers": tiers,
                "papers_fetched": 0,
                "papers_indexed": 0,
                "processing_time": time.time() - start_time
            }
        
        # Step 2: Filter duplicates
        filtered_papers = await RAGService.filter_new_papers_by_pmid(papers)
        logger.info(f"✅ After deduplication: {len(filtered_papers)} new papers")
        
        # Step 3: Process and index each paper
        indexed_count = 0
        chunk_count = 0
        
        for paper in filtered_papers:
            try:
                # Create PaperMeta object
                paper_meta = PaperMeta(
                    title=paper.get("title", ""),
                    content=paper.get("full_text", paper.get("abstract", "")),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{paper.get('pmid', '')}",
                    date=str(paper.get("publication_year", "")),
                    source="pubmed-expanded",
                    pmid=paper.get("pmid"),
                    pmcid=paper.get("pmcid"),
                    doi=paper.get("doi"),
                    authors=convert_authors_to_objects(paper.get("authors", [])),
                    journal=paper.get("journal"),
                    publication_year=paper.get("publication_year"),
                    mesh_terms=paper.get("mesh_terms", []),
                    abstract=paper.get("abstract")
                )
                
                # Process with LLM tagging
                results = await RAGService.process_paper_pipeline_with_llm_option(paper_meta, use_llm=True)
                indexed_count += 1
                chunk_count += len(results)
                
                logger.info(f"✅ Indexed: {paper_meta.title[:50]}... ({len(results)} chunks)")
                
            except Exception as e:
                logger.error(f"❌ Failed to index paper: {paper.get('title', 'Unknown')}: {e}")
                continue
        
        processing_time = time.time() - start_time
        
        return {
            "success": True,
            "message": f"Indexed {indexed_count} papers with {chunk_count} chunks",
            "tiers": tiers,
            "papers_fetched": len(papers),
            "papers_indexed": indexed_count,
            "chunks_stored": chunk_count,
            "processing_time": round(processing_time, 2)
        }
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return {
            "success": False,
            "message": f"Module import failed: {str(e)}",
            "processing_time": time.time() - start_time
        }
    except Exception as e:
        logger.error(f"Expanded indexing failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "processing_time": time.time() - start_time
        }


@router.post("/index-from-cache")
async def index_papers_from_cache():
    """
    Index papers from the cached JSON file (no PubMed fetch needed).
    Use after fixing bugs to avoid re-fetching from PubMed.
    
    Example:
        curl -X POST "http://localhost:8000/api/v1/rag/index-from-cache"
    """
    start_time = time.time()
    logger.info("📦 Starting paper indexing from cache...")
    
    try:
        from app.services.rag.paper_fetcher import load_cached_papers
        from app.models.rag_models import PaperMeta
        
        # Step 1: Load papers from cache
        papers = load_cached_papers()
        logger.info(f"✅ Loaded {len(papers)} papers from cache")
        
        if not papers:
            return {
                "success": False,
                "message": "No papers found in cache",
                "papers_in_cache": 0,
                "papers_indexed": 0,
                "processing_time": time.time() - start_time
            }
        
        # Step 2: Filter duplicates against already indexed papers
        filtered_papers = await RAGService.filter_new_papers_by_pmid(papers)
        logger.info(f"✅ After deduplication: {len(filtered_papers)} new papers")
        
        # Step 3: Process and index each paper
        indexed_count = 0
        chunk_count = 0
        errors = []
        
        for paper in filtered_papers:
            try:
                # Skip papers without title or content
                title = paper.get("title") or ""
                content = paper.get("full_text") or paper.get("abstract") or ""
                
                if not title.strip():
                    logger.warning(f"Skipping paper with no title: PMID={paper.get('pmid')}")
                    continue
                
                if not content.strip():
                    logger.warning(f"Skipping paper with no content: {title[:50]}")
                    continue
                
                # Create PaperMeta object with author conversion
                paper_meta = PaperMeta(
                    title=title,
                    content=content,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{paper.get('pmid') or ''}",
                    date=str(paper.get("publication_year") or ""),
                    source="pubmed-cached",
                    pmid=paper.get("pmid"),
                    pmcid=paper.get("pmcid"),
                    doi=paper.get("doi"),
                    authors=convert_authors_to_objects(paper.get("authors") or []),
                    journal=paper.get("journal"),
                    publication_year=paper.get("publication_year"),
                    mesh_terms=paper.get("mesh_terms") or [],
                    abstract=paper.get("abstract")
                )
                
                # Process WITHOUT LLM tagging (embeddings only - faster & cheaper)
                results = await RAGService.process_paper_pipeline_with_llm_option(paper_meta, use_llm=False)
                indexed_count += 1
                chunk_count += len(results)
                
                if indexed_count % 50 == 0:
                    logger.info(f"📈 Progress: {indexed_count}/{len(filtered_papers)} papers indexed")
                
            except Exception as e:
                paper_title = (paper.get('title') or 'Unknown')[:50]
                error_msg = f"{paper_title}: {str(e)[:100]}"
                errors.append(error_msg)
                logger.error(f"❌ {error_msg}")
                continue
        
        processing_time = time.time() - start_time
        
        return {
            "success": True,
            "message": f"Indexed {indexed_count} papers with {chunk_count} chunks from cache",
            "papers_in_cache": len(papers),
            "papers_after_dedup": len(filtered_papers),
            "papers_indexed": indexed_count,
            "chunks_stored": chunk_count,
            "errors_count": len(errors),
            "processing_time": round(processing_time, 2)
        }
        
    except FileNotFoundError as e:
        return {
            "success": False,
            "message": f"Cache file not found: {str(e)}",
            "processing_time": time.time() - start_time
        }
    except Exception as e:
        logger.error(f"Cache indexing failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "processing_time": time.time() - start_time
        }


@router.post("/index-batch")
async def index_papers_batch():
    """
    FAST batch indexing - embeddings only, no LLM tagging.
    Uses OpenAI batch embedding API for speed (~3 min instead of 75 min).
    """
    import httpx
    import os
    from app.services.rag.paper_fetcher import load_cached_papers
    
    start_time = time.time()
    logger.info("🚀 Starting FAST batch indexing...")
    
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_INDEX = os.getenv("PINECONE_INDEX")
    
    try:
        # Step 1: Load papers
        papers = load_cached_papers()
        logger.info(f"📦 Loaded {len(papers)} papers from cache")
        
        if not papers:
            return {"success": False, "message": "No papers in cache", "processing_time": time.time() - start_time}
        
        # Step 2: Prepare all chunks
        all_chunks = []
        chunk_metadata = []
        
        for paper in papers:
            title = paper.get("title") or ""
            content = paper.get("full_text") or paper.get("abstract") or ""
            
            if not title.strip() or not content.strip():
                continue
            
            # Simple chunking - split into ~500 char chunks
            chunk_size = 500
            for i in range(0, len(content), chunk_size):
                chunk_text = content[i:i+chunk_size]
                if len(chunk_text) > 50:  # Skip tiny chunks
                    chunk_id = f"paper_{paper.get('pmid', 'unknown')}_{i//chunk_size}"
                    all_chunks.append(chunk_text)
                    chunk_metadata.append({
                        "chunk_id": chunk_id,
                        "title": title[:500],
                        "text": chunk_text[:1000],
                        "pmid": paper.get("pmid", ""),
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{paper.get('pmid', '')}",
                        "journal": paper.get("journal", ""),
                        "publication_year": paper.get("publication_year", 0),
                        "mesh_terms": paper.get("mesh_terms", [])[:10]
                    })
        
        logger.info(f"✂️ Created {len(all_chunks)} chunks from {len(papers)} papers")
        
        # Step 3: Batch embedding (max 2048 per batch)
        BATCH_SIZE = 2000
        all_embeddings = []
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            for batch_start in range(0, len(all_chunks), BATCH_SIZE):
                batch_end = min(batch_start + BATCH_SIZE, len(all_chunks))
                batch_texts = all_chunks[batch_start:batch_end]
                
                logger.info(f"🔄 Embedding batch {batch_start//BATCH_SIZE + 1}: {len(batch_texts)} chunks")
                
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={"input": batch_texts, "model": "text-embedding-3-small"}
                )
                response.raise_for_status()
                data = response.json()
                
                for item in data["data"]:
                    all_embeddings.append(item["embedding"])
                
                logger.info(f"✅ Batch complete: {len(all_embeddings)} embeddings total")
        
        # Step 4: Upload to Pinecone
        from pinecone import Pinecone
        pc = Pinecone(api_key=PINECONE_API_KEY)
        index = pc.Index(PINECONE_INDEX)
        
        # Get namespace
        namespace = RAGService.get_model_namespace()
        
        # Prepare vectors
        vectors = []
        for i, (embedding, metadata) in enumerate(zip(all_embeddings, chunk_metadata)):
            vectors.append({
                "id": metadata["chunk_id"],
                "values": embedding,
                "metadata": metadata
            })
        
        # Batch upsert (100 at a time)
        UPSERT_BATCH = 100
        for i in range(0, len(vectors), UPSERT_BATCH):
            batch = vectors[i:i+UPSERT_BATCH]
            index.upsert(vectors=batch, namespace=namespace)
            if (i // UPSERT_BATCH) % 10 == 0:
                logger.info(f"📤 Uploaded {i + len(batch)} / {len(vectors)} vectors")
        
        processing_time = time.time() - start_time
        
        return {
            "success": True,
            "message": f"Indexed {len(papers)} papers with {len(vectors)} chunks",
            "papers_processed": len(papers),
            "chunks_created": len(vectors),
            "processing_time": round(processing_time, 2)
        }
        
    except Exception as e:
        logger.error(f"Batch indexing failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "processing_time": time.time() - start_time
        }