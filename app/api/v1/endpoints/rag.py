from fastapi import APIRouter, HTTPException, status, Request
from app.models.rag_models import PaperMeta, RAGRequest
from app.services.rag_service import RAGService
from typing import List, Dict, Any
import logging
import time
from app.models.rag_models import RAGResponse

# RAG 엔드포인트 전용 로거 설정
logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/health")
async def health_check():
    """
    RAG 서비스 상태 확인
    """
    return {"status": "healthy", "service": "RAG"}

@router.get("/pinecone-status")
async def pinecone_status_endpoint():
    """
    Pinecone 인덱스 상태 확인
    """
    try:
        index = RAGService.get_pinecone_client()
        stats = index.describe_index_stats()
        
        # 통계 정보 추출
        total_vector_count = stats.total_vector_count
        namespaces = stats.namespaces
        
        # 네임스페이스별 통계
        namespace_stats = {}
        for ns_name, ns_info in namespaces.items():
            namespace_stats[ns_name] = {
                "vector_count": ns_info.vector_count
            }
        
        # 도메인 통계 (URL 기반)
        all_vectors = index.query(
            vector=[0] * 1536,  # 더미 벡터
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
            "domains": list(unique_domains)[:20]  # 상위 20개 도메인
        }
        
    except Exception as e:
        logger.error(f"Pinecone status check failed: {e}")
        return {"status": "error", "message": str(e)} 

@router.post("/search-rag")
async def search_rag_endpoint(request: Dict[str, Any]):
    """
    사용자 쿼리에 따른 개인화된 RAG 검색
    :param request: {"query": "사용자 질문", "user_profile": {...}, "top_k": 5}
    """
    query = request.get("query", "")
    user_profile = request.get("user_profile", None)
    top_k = request.get("top_k", 5)
    
    if not query:
        raise HTTPException(status_code=400, detail="쿼리가 필요합니다.")
    
    logger.info(f"RAG search started - query: {query[:50]}..., user profile: {user_profile is not None}")
    
    try:
        # RAG 검색 및 우선순위 계산
        ranked_papers = await RAGService.search_and_rank_papers(query, user_profile, top_k)
        
        logger.info(f"RAG search completed - {len(ranked_papers)} papers found")
        
        return {
            "query": query,
            "user_profile": user_profile,
            "papers_found": len(ranked_papers),
            "ranked_papers": [
                {
                    "title": paper.get("title", ""),
                    "content": paper.get("content", "")[:200] + "...",  # 내용 미리보기
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
        raise HTTPException(status_code=500, detail=f"RAG 검색 실패: {str(e)}") 

@router.post("/fetch-and-process", response_model=RAGResponse)
async def fetch_and_process_endpoint():
    """
    PCOS 관련 논문을 수집하고 처리하여 Pinecone에 저장
            기본 설정: 최대 50개 논문, 2015년 이후, LLM 태깅 사용
    """
    start_time = time.time()
    logger.info("PCOS paper collection and processing started")
    
    try:
        # 1. PubMed Central에서 논문 수집 (기본 설정)
        all_papers = await RAGService.fetch_pcos_papers_from_pubmed_api()
        logger.info(f"PubMed Central paper collection completed: {len(all_papers)} papers")
        
        if not all_papers:
            return RAGResponse(
                success=False,
                message="PubMed Central에서 논문을 찾지 못했습니다.",
                papers_processed=0,
                papers_stored=0,
                processing_time=time.time() - start_time
            )
        
        # 2. 중복 논문 필터링
        filtered_papers = await RAGService.filter_new_papers(all_papers)
        logger.info(f"After deduplication: {len(filtered_papers)} papers")
        
        if not filtered_papers:
            return RAGResponse(
                success=True,
                message="모든 논문이 이미 저장되어 있습니다.",
                papers_processed=len(all_papers),
                papers_stored=0,
                processing_time=time.time() - start_time
            )
        
        # 3. 전체 논문 처리 (LLM 태깅 사용)
        processed_count = 0
        stored_count = 0
        
        for paper in filtered_papers:
            try:
                # PaperMeta 객체 생성
                paper_meta = PaperMeta(
                    title=paper["title"],
                    content=paper["content"],
                    url=paper["url"],
                    date=paper["date"],
                    source=paper.get("source", "pubmed-api"),
                    # 논문 식별자
                    pmid=paper.get("pmid"),
                    pmcid=paper.get("pmcid"),
                    doi=paper.get("doi"),
                    # 저자 정보
                    authors=paper.get("authors", []),
                    # 저널 정보
                    journal=paper.get("journal"),
                    journal_issn=paper.get("journal_issn"),
                    # 출판년도 정보
                    publication_year=paper.get("publication_year"),
                    # 추가 메타데이터
                    mesh_terms=paper.get("mesh_terms", []),
                    abstract=paper.get("abstract"),
                    # 섹션 태그 정보와 섹션 정보를 모두 포함
                    source_paper={
                        "section_tags": paper.get("section_tags"),
                        "sections": paper.get("sections"),
                        "content": paper.get("content")
                    } if paper.get("section_tags") or paper.get("sections") else None
                )
                
                # 논문 처리 (LLM 태깅 사용)
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
            message=f"PCOS 논문 수집 및 처리 완료",
            papers_processed=processed_count,
            papers_stored=stored_count,
            processing_time=processing_time
        )
        
    except Exception as e:
        logger.error(f"PCOS paper collection and processing failed: {e}", exc_info=True)
        return RAGResponse(
            success=False,
            message=f"처리 실패: {str(e)}",
            papers_processed=0,
            papers_stored=0,
            processing_time=time.time() - start_time
        ) 

@router.get("/test-pubmed-search")
async def test_pubmed_search_endpoint():
    """
    PubMed 검색 결과를 XML 그대로 보여주는 테스트 API
    """
    try:
        import httpx
        
        # PubMed 검색 URL (현재 사용 중인 것과 동일)
        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        mesh_query = "Polycystic+Ovary+Syndrome[Mesh]+OR+PCOS[Mesh]+OR+Stein-Leventhal+Syndrome[Mesh]"
        max_results = 100
        
        search_url = f"{base_url}esearch.fcgi?db=pubmed&term={mesh_query}&retmax={max_results}&retmode=xml&datetype=pdat&mindate=2015&maxdate=2025"
        
        logger.info(f"PubMed 검색 URL: {search_url}")
        
        # PubMed 검색 실행
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            
            # XML 응답 그대로 반환
            xml_content = response.text
            
            # 기본 정보 추출 (로깅용)
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_content)
                id_list = root.findall(".//Id")
                id_count = len(id_list)
                logger.info(f"PubMed 검색 결과: {id_count}개 ID 발견")
            except Exception as e:
                logger.warning(f"XML 파싱 실패: {e}")
                id_count = "파싱 실패"
            
    return {
                "status": "success",
                "search_url": search_url,
                "id_count": id_count,
                "raw_xml": xml_content,
                "content_length": len(xml_content)
            }
            
    except Exception as e:
        logger.error(f"PubMed 검색 테스트 실패: {e}")
        return {
            "status": "error",
            "message": str(e),
            "search_url": search_url if 'search_url' in locals() else "N/A"
        }

@router.get("/test-pmc-fetch/{pmcid}")
async def test_pmc_fetch_endpoint(pmcid: str):
    """
    PMC ID로 PMC XML을 가져오는 테스트 API
    """
    try:
        import httpx
        
        # PMC에서 본문 가져오기 (현재 사용 중인 것과 동일)
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
        
        logger.info(f"PMC 가져오기 URL: {fetch_url}")
        
        # PMC XML 가져오기
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(fetch_url)
            response.raise_for_status()
            
            # XML 응답 그대로 반환
            xml_content = response.text
            
            # 기본 정보 추출 (로깅용)
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_content)
                body_elements = root.findall(".//body")
                abstract_elements = root.findall(".//abstract")
                
                logger.info(f"PMC XML 파싱: body={len(body_elements)}개, abstract={len(abstract_elements)}개")
            except Exception as e:
                logger.warning(f"PMC XML 파싱 실패: {e}")
            
            return {
                "status": "success",
                "pmcid": pmcid,
                "fetch_url": fetch_url,
                "raw_xml": xml_content,
                "content_length": len(xml_content)
            }
            
    except Exception as e:
        logger.error(f"PMC 가져오기 테스트 실패: {e}")
        return {
            "status": "error",
            "message": str(e),
            "pmcid": pmcid,
            "fetch_url": fetch_url if 'fetch_url' in locals() else "N/A"
        } 

@router.get("/test-pubmed-to-pmc")
async def test_pubmed_to_pmc_endpoint():
    """
    PubMed 검색 → PMC XML 가져오기 전체 과정을 보여주는 테스트 API
    """
    try:
        import httpx
        import xml.etree.ElementTree as ET
        
        # 1단계: PubMed 검색
        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        mesh_query = "Polycystic+Ovary+Syndrome[Mesh]+OR+PCOS[Mesh]+OR+Stein-Leventhal+Syndrome[Mesh]"
        max_results = 10  # 테스트용으로 10개만
        
        search_url = f"{base_url}esearch.fcgi?db=pubmed&term={mesh_query}&retmax={max_results}&retmode=xml&datetype=pdat&mindate=2015&maxdate=2025"
        
        logger.info(f"1단계: PubMed 검색 시작 - URL: {search_url}")
        
        # PubMed 검색 실행
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            pubmed_xml = response.text
            
            # PubMed ID 추출
            root = ET.fromstring(pubmed_xml)
            pubmed_ids = [id_elem.text for id_elem in root.findall(".//Id")]
            logger.info(f"PubMed 검색 완료: {len(pubmed_ids)}개 ID 발견")
            
            # 2단계: PubMed 상세 정보 가져오기
            fetch_url = f"{base_url}efetch.fcgi?db=pubmed&retmode=xml&id={','.join(pubmed_ids)}"
            logger.info(f"2단계: PubMed 상세 정보 가져오기 - URL: {fetch_url}")
            
            response = await client.get(fetch_url)
            response.raise_for_status()
            pubmed_detail_xml = response.text
            
            # PMC ID 추출
            root = ET.fromstring(pubmed_detail_xml)
            papers_with_pmcid = []
            
            for article in root.findall(".//PubmedArticle"):
                pmid = article.find(".//PMID")
                pmid_text = pmid.text if pmid is not None else ""
                
                # PMC ID 찾기
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
            
            logger.info(f"PMC ID가 있는 논문: {len(papers_with_pmcid)}개")
            
            # 3단계: PMC XML 가져오기 (첫 번째 논문만)
            pmc_results = []
            if papers_with_pmcid:
                first_paper = papers_with_pmcid[0]
                pmcid = first_paper["pmcid"]
                
                pmc_fetch_url = f"{base_url}efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
                logger.info(f"3단계: PMC XML 가져오기 - URL: {pmc_fetch_url}")
                
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
        logger.error(f"PubMed → PMC 테스트 실패: {e}")
        return {
            "status": "error",
            "message": str(e)
        } 