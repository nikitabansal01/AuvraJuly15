"""
하이브리드 검색 API 엔드포인트 (BM25 + Pinecone Vector)
HQ vs LQ 모델 성능 비교 및 A/B 테스트 지원
"""
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import logging
import asyncio
from datetime import datetime

from app.services.hybrid_search_service import get_hybrid_search_service
from app.services.multi_scenario_service import get_multi_scenario_service
from app.models.rag_models import RAGRequest

logger = logging.getLogger(__name__)

router = APIRouter()

class HybridSearchRequest(BaseModel):
    """하이브리드 검색 요청 모델"""
    query: str
    top_k: int = 20
    lexical_k: int = 50
    dense_k: int = 50
    namespace: Optional[str] = None
    model_filter: Optional[str] = None  # "hq", "lq", 또는 구체적인 모델명
    field_weights: Optional[Dict[str, float]] = None  # 커스텀 가중치

class SearchResult(BaseModel):
    """검색 결과 모델"""
    id: str
    rrf_score: float
    found_in: List[str]  # ["dense", "lexical"]
    title: str
    text_preview: str
    url: Optional[str] = None
    model_version: Optional[str] = None
    dense_rank: Optional[int] = None
    lexical_rank: Optional[int] = None
    bm25_score: Optional[float] = None
    dense_score: Optional[float] = None

class HybridSearchResponse(BaseModel):
    """하이브리드 검색 응답 모델"""
    query: str
    results: List[SearchResult]
    stats: Dict[str, Any]
    timestamp: str
    processing_time: Optional[float] = None

@router.get("/health")
async def health_check():
    """하이브리드 검색 서비스 상태 확인"""
    service = get_hybrid_search_service()
    return {
        "status": "healthy" if service.is_loaded else "not_initialized",
        "service": "HybridSearch",
        "bm25_indexes": len(service.bm25_indexes),
        "documents": len(service.documents)
    }

@router.post("/initialize")
async def initialize_service(
    json_file: Optional[str] = None,
    force_rebuild: bool = False
):
    """하이브리드 검색 서비스 초기화"""
    try:
        service = get_hybrid_search_service()
        
        logger.info(f"하이브리드 검색 서비스 초기화 시작 (force_rebuild: {force_rebuild})")
        
        # 백그라운드에서 초기화 실행 (시간이 오래 걸릴 수 있음)
        success = service.initialize(json_file, force_rebuild)
        
        if success:
            return {
                "status": "success",
                "message": "하이브리드 검색 서비스 초기화 완료",
                "documents": len(service.documents),
                "bm25_indexes": len(service.bm25_indexes),
                "field_weights": service.field_weights
            }
        else:
            raise HTTPException(status_code=500, detail="서비스 초기화 실패")
        
    except Exception as e:
        logger.error(f"하이브리드 검색 서비스 초기화 실패: {e}")
        raise HTTPException(status_code=500, detail=f"초기화 실패: {str(e)}")


# =============================================================================
# 🔍 DENSE SEARCH APIs (Vector Only)
# =============================================================================

@router.get("/dense-combined")
async def dense_combined_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수")
):
    """Combined Dense만 검색 (pcos-rag-combined 네임스페이스)"""
    try:
        service = get_hybrid_search_service()
        
        start_time = datetime.now()
        results = await service.dense_search(q, top_k, "pcos-rag-combined")
        processing_time = (datetime.now() - start_time).total_seconds()
        
        return {
            "query": q,
            "method": "dense_combined",
            "namespace": "pcos-rag-combined",
            "results": results,
            "count": len(results),
            "processing_time": processing_time
        }
        
    except Exception as e:
        logger.error(f"Combined Dense 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/dense-hq")
async def dense_hq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수")
):
    """HQ Dense만 검색 (pcos-rag-gpt_4o 네임스페이스)"""
    try:
        service = get_hybrid_search_service()
        
        start_time = datetime.now()
        results = await service.dense_search(q, top_k, "pcos-rag-gpt_4o")
        processing_time = (datetime.now() - start_time).total_seconds()
        
        return {
            "query": q,
            "method": "dense_hq",
            "namespace": "pcos-rag-gpt_4o",
            "results": results,
            "count": len(results),
            "processing_time": processing_time
        }
        
    except Exception as e:
        logger.error(f"HQ Dense 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/dense-lq")
async def dense_lq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수")
):
    """LQ Dense만 검색 (pcos-rag-gpt_3.5_turbo 네임스페이스)"""
    try:
        service = get_hybrid_search_service()
        
        start_time = datetime.now()
        results = await service.dense_search(q, top_k, "pcos-rag-gpt_3.5_turbo")
        processing_time = (datetime.now() - start_time).total_seconds()
        
        return {
            "query": q,
            "method": "dense_lq",
            "namespace": "pcos-rag-gpt_3.5_turbo",
            "results": results,
            "count": len(results),
            "processing_time": processing_time
        }
        
    except Exception as e:
        logger.error(f"LQ Dense 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

# =============================================================================
# 📝 LEXICAL SEARCH APIs (BM25 Only)
# =============================================================================

@router.get("/lexical-combined")
async def lexical_combined_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수")
):
    """Combined Lexical만 검색 (Combined BM25 인덱스)"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="서비스가 초기화되지 않음")
        
        start_time = datetime.now()
        results = service.lexical_search(q, top_k)
        processing_time = (datetime.now() - start_time).total_seconds()
        
        return {
            "query": q,
            "method": "lexical_combined",
            "bm25_index": "Combined (HQ+LQ)",
            "results": results,
            "count": len(results),
            "processing_time": processing_time
        }
        
    except Exception as e:
        logger.error(f"Combined Lexical 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/lexical-hq")
async def lexical_hq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수")
):
    """HQ Lexical만 검색 (HQ BM25 인덱스)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="시나리오 서비스가 초기화되지 않음")
        
        hq_service = multi_service.services["hq"]
        
        start_time = datetime.now()
        results = hq_service.lexical_search(q, top_k)
        processing_time = (datetime.now() - start_time).total_seconds()
        
        return {
            "query": q,
            "method": "lexical_hq",
            "bm25_index": "HQ Only",
            "results": results,
            "count": len(results),
            "processing_time": processing_time
        }
        
    except Exception as e:
        logger.error(f"HQ Lexical 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/lexical-lq")
async def lexical_lq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수")
):
    """LQ Lexical만 검색 (LQ BM25 인덱스)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="시나리오 서비스가 초기화되지 않음")
        
        lq_service = multi_service.services["lq"]
        
        start_time = datetime.now()
        results = lq_service.lexical_search(q, top_k)
        processing_time = (datetime.now() - start_time).total_seconds()
        
        return {
            "query": q,
            "method": "lexical_lq",
            "bm25_index": "LQ Only",
            "results": results,
            "count": len(results),
            "processing_time": processing_time
        }
        
    except Exception as e:
        logger.error(f"LQ Lexical 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

# =============================================================================
# 🚀 HYBRID SEARCH APIs (Lexical + Dense)
# =============================================================================

@router.get("/hybrid-combined")
async def hybrid_combined_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수"),
    lexical_k: int = Query(50, description="BM25 검색 결과 수"),
    dense_k: int = Query(50, description="벡터 검색 결과 수")
):
    """Combined Hybrid 검색 (Combined Lexical + Combined Dense)"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="서비스가 초기화되지 않음")
        
        result = await service.hybrid_search(
            query=q,
            top_k=top_k,
            lexical_k=lexical_k,
            dense_k=dense_k,
            namespace="pcos-rag-combined"
        )
        
        result["method"] = "hybrid_combined"
        result["environment"] = {
            "lexical": "Combined (HQ+LQ)",
            "dense": "Combined (HQ+LQ)",
            "namespace": "pcos-rag-combined"
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Combined Hybrid 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/hybrid-combined-hq")
async def hybrid_combined_hq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수"),
    lexical_k: int = Query(50, description="BM25 검색 결과 수"),
    dense_k: int = Query(50, description="벡터 검색 결과 수")
):
    """Combined Lexical + HQ Dense 하이브리드 검색"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="서비스가 초기화되지 않음")
        
        result = await service.hybrid_search(
            query=q,
            top_k=top_k,
            lexical_k=lexical_k,
            dense_k=dense_k,
            namespace="pcos-rag-gpt_4o"
        )
        
        result["method"] = "hybrid_combined_hq"
        result["environment"] = {
            "lexical": "Combined (HQ+LQ)",
            "dense": "HQ Only",
            "namespace": "pcos-rag-gpt_4o"
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Combined+HQ Hybrid 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/hybrid-combined-lq")
async def hybrid_combined_lq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수"),
    lexical_k: int = Query(50, description="BM25 검색 결과 수"),
    dense_k: int = Query(50, description="벡터 검색 결과 수")
):
    """Combined Lexical + LQ Dense 하이브리드 검색"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="서비스가 초기화되지 않음")
        
        result = await service.hybrid_search(
            query=q,
            top_k=top_k,
            lexical_k=lexical_k,
            dense_k=dense_k,
            namespace="pcos-rag-gpt_3.5_turbo"
        )
        
        result["method"] = "hybrid_combined_lq"
        result["environment"] = {
            "lexical": "Combined (HQ+LQ)",
            "dense": "LQ Only", 
            "namespace": "pcos-rag-gpt_3.5_turbo"
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Combined+LQ Hybrid 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/hybrid-hq")
async def hybrid_hq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수"),
    lexical_k: int = Query(50, description="BM25 검색 결과 수"), 
    dense_k: int = Query(50, description="벡터 검색 결과 수")
):
    """HQ Hybrid 검색 (HQ Lexical + HQ Dense)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="시나리오 서비스가 초기화되지 않음")
        
        hq_service = multi_service.services["hq"]
        
        result = await hq_service.hybrid_search(
            query=q,
            top_k=top_k,
            lexical_k=lexical_k,
            dense_k=dense_k,
            namespace="pcos-rag-gpt_4o"
        )
        
        result["method"] = "hybrid_hq"
        result["environment"] = {
            "lexical": "HQ Only",
            "dense": "HQ Only",
            "namespace": "pcos-rag-gpt_4o"
        }
        
        return result
        
    except Exception as e:
        logger.error(f"HQ Hybrid 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/hybrid-lq")
async def hybrid_lq_search(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, description="반환할 결과 수"),
    lexical_k: int = Query(50, description="BM25 검색 결과 수"),
    dense_k: int = Query(50, description="벡터 검색 결과 수")
):
    """LQ Hybrid 검색 (LQ Lexical + LQ Dense)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="시나리오 서비스가 초기화되지 않음")
        
        lq_service = multi_service.services["lq"]
        
        result = await lq_service.hybrid_search(
            query=q,
            top_k=top_k,
            lexical_k=lexical_k,
            dense_k=dense_k,
            namespace="pcos-rag-gpt_3.5_turbo"
        )
        
        result["method"] = "hybrid_lq"
        result["environment"] = {
            "lexical": "LQ Only",
            "dense": "LQ Only",
            "namespace": "pcos-rag-gpt_3.5_turbo"
        }
        
        return result
        
    except Exception as e:
        logger.error(f"LQ Hybrid 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

@router.get("/field-weights")
async def get_field_weights():
    """현재 필드별 가중치 조회"""
    service = get_hybrid_search_service()
    return {
        "field_weights": service.field_weights,
        "total_fields": len(service.field_weights)
    }

@router.post("/field-weights")
async def update_field_weights(weights: Dict[str, float]):
    """필드별 가중치 업데이트"""
    try:
        service = get_hybrid_search_service()
        
        # 유효한 필드만 업데이트
        updated_fields = []
        for field, weight in weights.items():
            if field in service.field_weights:
                service.field_weights[field] = weight
                updated_fields.append(field)
        
        return {
            "message": f"{len(updated_fields)}개 필드 가중치 업데이트",
            "updated_fields": updated_fields,
            "current_weights": service.field_weights
        }
        
    except Exception as e:
        logger.error(f"가중치 업데이트 실패: {e}")
        raise HTTPException(status_code=500, detail=f"업데이트 실패: {str(e)}")

@router.get("/stats")
async def get_service_stats():
    """서비스 통계 정보"""
    service = get_hybrid_search_service()
    
    if not service.is_loaded:
        return {"status": "not_initialized"}
    
    # 문서 통계
    model_versions = {}
    section_types = {}
    
    for doc in service.documents:
        model = doc.get("model_version", "unknown")
        model_versions[model] = model_versions.get(model, 0) + 1
        
        section = doc.get("chunk_section_type", "unknown")
        section_types[section] = section_types.get(section, 0) + 1
    
    return {
        "status": "initialized",
        "total_documents": len(service.documents),
        "bm25_indexes": len(service.bm25_indexes),
        "model_versions": model_versions,
        "section_types": section_types,
        "field_weights": service.field_weights
    }

@router.post("/compare-models")
async def compare_hq_lq_models(
    query: str,
    top_k: int = 10
):
    """HQ vs LQ 모델 성능 비교"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="서비스가 초기화되지 않음")
        
        # HQ 모델 (gpt-4o) 검색
        hq_results = await service.hybrid_search(
            query=query,
            top_k=top_k,
            namespace="pcos-rag-gpt_4o"
        )
        
        # LQ 모델 (gpt-3.5-turbo) 검색  
        lq_results = await service.hybrid_search(
            query=query,
            top_k=top_k,
            namespace="pcos-rag-gpt_3.5_turbo"
        )
        
        return {
            "query": query,
            "hq_model": {
                "namespace": "pcos-rag-gpt_4o",
                "results": hq_results["results"][:top_k],
                "stats": hq_results["stats"]
            },
            "lq_model": {
                "namespace": "pcos-rag-gpt_3.5_turbo", 
                "results": lq_results["results"][:top_k],
                "stats": lq_results["stats"]
            },
            "comparison": {
                "hq_count": len(hq_results["results"]),
                "lq_count": len(lq_results["results"]),
                "common_results": len(set(r["id"] for r in hq_results["results"]) & 
                                    set(r["id"] for r in lq_results["results"]))
            }
        }
        
    except Exception as e:
        logger.error(f"모델 비교 실패: {e}")
        raise HTTPException(status_code=500, detail=f"비교 실패: {str(e)}")

@router.post("/scenario-c-comparison")
async def scenario_c_fair_comparison(
    query: str,
    top_k: int = 10
):
    """
    Scenario C: 완전 통합 환경에서 공정한 모델 비교
    Combined BM25 + Combined Dense (pcos-rag-combined) → 모델별 필터링
    """
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="서비스가 초기화되지 않음")
        
        combined_namespace = "pcos-rag-combined"
        
        # 통합 환경에서 HQ 결과 (모델 필터링)
        hq_results = await service.hybrid_search(
            query=query,
            top_k=top_k * 2,  # 필터링 전에 더 많이 가져옴
            namespace=combined_namespace,
            model_filter="hq"
        )
        
        # 통합 환경에서 LQ 결과 (모델 필터링)
        lq_results = await service.hybrid_search(
            query=query,
            top_k=top_k * 2,
            namespace=combined_namespace,
            model_filter="lq"
        )
        
        # 무필터 전체 결과 (참고용)
        all_results = await service.hybrid_search(
            query=query,
            top_k=top_k * 2,
            namespace=combined_namespace
        )
        
        # 상위 결과만 자르기
        hq_final = hq_results["results"][:top_k]
        lq_final = lq_results["results"][:top_k]
        all_final = all_results["results"][:top_k]
        
        # 고급 분석
        hq_ids = set(r["id"] for r in hq_final)
        lq_ids = set(r["id"] for r in lq_final)
        all_ids = set(r["id"] for r in all_final)
        
        # 순위 분석 (전체 결과에서 각 모델의 순위)
        hq_ranks_in_all = []
        lq_ranks_in_all = []
        
        for i, result in enumerate(all_results["results"]):
            model_version = result.get("model_version", "")
            if "gpt-4o" in model_version:
                hq_ranks_in_all.append(i + 1)
            elif "gpt-3.5-turbo" in model_version:
                lq_ranks_in_all.append(i + 1)
        
        return {
            "query": query,
            "scenario": "C - 완전 통합 환경 비교",
            "namespace": combined_namespace,
            "hq_results": {
                "results": hq_final,
                "stats": hq_results["stats"],
                "avg_rank_in_all": sum(hq_ranks_in_all[:top_k]) / len(hq_ranks_in_all[:top_k]) if hq_ranks_in_all[:top_k] else 0
            },
            "lq_results": {
                "results": lq_final, 
                "stats": lq_results["stats"],
                "avg_rank_in_all": sum(lq_ranks_in_all[:top_k]) / len(lq_ranks_in_all[:top_k]) if lq_ranks_in_all[:top_k] else 0
            },
            "comparison_analysis": {
                "hq_count": len(hq_final),
                "lq_count": len(lq_final),
                "overlap": len(hq_ids & lq_ids),
                "hq_unique": len(hq_ids - lq_ids),
                "lq_unique": len(lq_ids - hq_ids),
                "total_unique_docs": len(hq_ids | lq_ids),
                # 순위 분석
                "hq_avg_rank": sum(hq_ranks_in_all[:top_k]) / len(hq_ranks_in_all[:top_k]) if hq_ranks_in_all[:top_k] else 0,
                "lq_avg_rank": sum(lq_ranks_in_all[:top_k]) / len(lq_ranks_in_all[:top_k]) if lq_ranks_in_all[:top_k] else 0,
                "hq_top_positions": len([r for r in hq_ranks_in_all if r <= top_k]),
                "lq_top_positions": len([r for r in lq_ranks_in_all if r <= top_k]),
            },
            "all_results_sample": all_final[:5]  # 참고용 상위 5개
        }
        
    except Exception as e:
        logger.error(f"Scenario C 비교 실패: {e}")
        raise HTTPException(status_code=500, detail=f"비교 실패: {str(e)}")

@router.post("/initialize-all-scenarios")
async def initialize_all_scenarios():
    """모든 시나리오용 서비스 초기화 (A, B, C)"""
    try:
        multi_service = get_multi_scenario_service()
        
        logger.info("모든 시나리오 서비스 초기화 시작...")
        results = multi_service.initialize_all_services()
        
        if all(results.values()):
            return {
                "status": "success",
                "message": "모든 시나리오 서비스 초기화 완료",
                "details": results,
                "scenarios_available": ["A", "B", "C"]
            }
        else:
            return {
                "status": "partial_success",
                "message": "일부 시나리오 서비스 초기화 실패",
                "details": results,
                "failed_services": [k for k, v in results.items() if not v]
            }
        
    except Exception as e:
        logger.error(f"시나리오 서비스 초기화 실패: {e}")
        raise HTTPException(status_code=500, detail=f"초기화 실패: {str(e)}")

@router.post("/scenario-a-comparison")
async def scenario_a_pure_comparison(
    query: str,
    top_k: int = 10
):
    """
    Scenario A: 순수 모델 성능 비교
    HQ 환경 (HQ BM25 + HQ Dense) vs LQ 환경 (LQ BM25 + LQ Dense)
    """
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(
                status_code=503, 
                detail="시나리오 서비스가 초기화되지 않음. /initialize-all-scenarios 를 먼저 호출하세요."
            )
        
        result = await multi_service.scenario_a_comparison(query, top_k)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scenario A 비교 실패: {e}")
        raise HTTPException(status_code=500, detail=f"비교 실패: {str(e)}")

@router.post("/scenario-b-comparison")
async def scenario_b_mixed_comparison(
    query: str,
    top_k: int = 10
):
    """
    Scenario B: 혼합 환경에서의 경쟁
    Combined BM25 + HQ Dense vs Combined BM25 + LQ Dense
    """
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="시나리오 서비스가 초기화되지 않음")
        
        result = await multi_service.scenario_b_comparison(query, top_k)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scenario B 비교 실패: {e}")
        raise HTTPException(status_code=500, detail=f"비교 실패: {str(e)}")

@router.post("/comprehensive-comparison")
async def comprehensive_abc_comparison(
    query: str,
    top_k: int = 10
):
    """
    종합 비교: Scenario A, B, C 모두 실행하여 비교
    모든 시나리오에서 HQ vs LQ 성능을 분석
    """
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="시나리오 서비스가 초기화되지 않음")
        
        logger.info(f"종합 비교 (A/B/C) 시작: '{query}'")
        result = await multi_service.comprehensive_comparison(query, top_k)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"종합 비교 실패: {e}")
        raise HTTPException(status_code=500, detail=f"비교 실패: {str(e)}")

@router.get("/scenarios-status")
async def get_scenarios_status():
    """모든 시나리오 서비스 상태 확인"""
    try:
        multi_service = get_multi_scenario_service()
        
        status = {
            "multi_scenario_initialized": multi_service.is_initialized,
            "services": {}
        }
        
        for service_name, service in multi_service.services.items():
            status["services"][service_name] = {
                "loaded": service.is_loaded,
                "documents": len(service.documents),
                "bm25_indexes": len(service.bm25_indexes),
                "service_type": service.service_type
            }
        
        # 단일 서비스 상태도 포함
        single_service = get_hybrid_search_service()
        status["single_service"] = {
            "loaded": single_service.is_loaded,
            "documents": len(single_service.documents),
            "bm25_indexes": len(single_service.bm25_indexes)
        }
        
        return status
        
    except Exception as e:
        logger.error(f"상태 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=f"상태 조회 실패: {str(e)}")

# =============================================================================
# 🔧 ADVANCED HYBRID SEARCH (POST with full options)
# =============================================================================

@router.post("/hybrid-advanced", response_model=HybridSearchResponse)
async def hybrid_advanced_search(request: HybridSearchRequest):
    """고급 하이브리드 검색 (모든 옵션 지원: 가중치, 필터링 등)"""
    start_time = datetime.now()
    
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="서비스가 초기화되지 않음. /initialize 를 먼저 호출하세요.")
        
        # 커스텀 가중치 적용
        original_weights = None
        if request.field_weights:
            original_weights = service.field_weights.copy()
            service.field_weights.update(request.field_weights)
            logger.info(f"커스텀 가중치 적용: {request.field_weights}")
        
        # 하이브리드 검색 실행
        result = await service.hybrid_search(
            query=request.query,
            top_k=request.top_k,
            lexical_k=request.lexical_k,
            dense_k=request.dense_k,
            namespace=request.namespace,
            model_filter=request.model_filter
        )
        
        # 가중치 복원
        if original_weights:
            service.field_weights = original_weights
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        # 응답 변환
        search_results = []
        for doc in result["results"]:
            search_result = SearchResult(
                id=doc["id"],
                rrf_score=doc.get("rrf_score", 0.0),
                found_in=doc.get("found_in", []),
                title=doc.get("title", "")[:100],
                text_preview=doc.get("text", "")[:300] + "..." if doc.get("text") else "",
                url=doc.get("url"),
                model_version=doc.get("model_version"),
                dense_rank=doc.get("dense_rank"),
                lexical_rank=doc.get("lexical_rank"),
                bm25_score=doc.get("bm25_score"),
                dense_score=doc.get("dense_score")
            )
            search_results.append(search_result)
        
        processing_time = (datetime.now() - start_time).total_seconds()
        
        response = HybridSearchResponse(
            query=result["query"],
            results=search_results,
            stats=result["stats"],
            timestamp=result["timestamp"],
            processing_time=processing_time
        )
        
        logger.info(f"고급 하이브리드 검색 완료: {len(search_results)}개 결과, {processing_time:.2f}초")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"고급 하이브리드 검색 실패: {e}")
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")

# =============================================================================
# ⚙️ LEGACY/COMPATIBILITY APIs (기존 호환성)
# =============================================================================

