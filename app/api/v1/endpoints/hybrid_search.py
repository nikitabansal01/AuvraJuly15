"""
Hybrid Search API Endpoints (BM25 + Pinecone Vector)
HQ vs LQ model performance comparison and A/B testing support
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
    """Hybrid search request model"""
    query: str
    top_k: int = 20
    lexical_k: int = 50
    dense_k: int = 50
    namespace: Optional[str] = None
    model_filter: Optional[str] = None  # "hq", "lq", or specific model name
    field_weights: Optional[Dict[str, float]] = None  # Custom weights

class SearchResult(BaseModel):
    """Search result model"""
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
    """Hybrid search response model"""
    query: str
    results: List[SearchResult]
    stats: Dict[str, Any]
    timestamp: str
    processing_time: Optional[float] = None

@router.get("/health")
async def health_check():
    """Hybrid search service status check"""
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
    """Initialize hybrid search service"""
    try:
        service = get_hybrid_search_service()
        
        logger.info(f"Hybrid search service initialization started (force_rebuild: {force_rebuild})")
        
        # Run initialization in background (may take long time)
        success = service.initialize(json_file, force_rebuild)
        
        if success:
            return {
                "status": "success",
                "message": "Hybrid search service initialization completed",
                "documents": len(service.documents),
                "bm25_indexes": len(service.bm25_indexes),
                "field_weights": service.field_weights
            }
        else:
            raise HTTPException(status_code=500, detail="Service initialization failed")
        
    except Exception as e:
        logger.error(f"Hybrid search service initialization failed: {e}")
        raise HTTPException(status_code=500, detail=f"Initialization failed: {str(e)}")


# =============================================================================
# 🔍 DENSE SEARCH APIs (Vector Only)
# =============================================================================

@router.get("/dense-combined")
async def dense_combined_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return")
):
    """Combined Dense search only (pcos-rag-combined namespace)"""
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
        logger.error(f"Combined Dense search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/dense-hq")
async def dense_hq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return")
):
    """HQ Dense search only (pcos-rag-gpt_4o namespace)"""
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
        logger.error(f"HQ Dense search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/dense-lq")
async def dense_lq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return")
):
    """LQ Dense search only (pcos-rag-gpt_3.5_turbo namespace)"""
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
        logger.error(f"LQ Dense search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

# =============================================================================
# 📝 LEXICAL SEARCH APIs (BM25 Only)
# =============================================================================

@router.get("/lexical-combined")
async def lexical_combined_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return")
):
    """Combined Lexical search only (Combined BM25 index)"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
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
        logger.error(f"Combined Lexical search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/lexical-hq")
async def lexical_hq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return")
):
    """HQ Lexical search only (HQ BM25 index)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="Scenario service not initialized")
        
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
        logger.error(f"HQ Lexical search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/lexical-lq")
async def lexical_lq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return")
):
    """LQ Lexical search only (LQ BM25 index)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="Scenario service not initialized")
        
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
        logger.error(f"LQ Lexical search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

# =============================================================================
# 🚀 HYBRID SEARCH APIs (Lexical + Dense)
# =============================================================================

@router.get("/hybrid-combined")
async def hybrid_combined_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return"),
    lexical_k: int = Query(50, description="Number of BM25 search results"),
    dense_k: int = Query(50, description="Number of vector search results")
):
    """Combined Hybrid search (Combined Lexical + Combined Dense)"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
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
        logger.error(f"Combined Hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/hybrid-combined-hq")
async def hybrid_combined_hq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return"),
    lexical_k: int = Query(50, description="Number of BM25 search results"),
    dense_k: int = Query(50, description="Number of vector search results")
):
    """Combined Lexical + HQ Dense hybrid search"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
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
        logger.error(f"Combined+HQ Hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/hybrid-combined-lq")
async def hybrid_combined_lq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return"),
    lexical_k: int = Query(50, description="Number of BM25 search results"),
    dense_k: int = Query(50, description="Number of vector search results")
):
    """Combined Lexical + LQ Dense hybrid search"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
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
        logger.error(f"Combined+LQ Hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/hybrid-hq")
async def hybrid_hq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return"),
    lexical_k: int = Query(50, description="Number of BM25 search results"), 
    dense_k: int = Query(50, description="Number of vector search results")
):
    """HQ Hybrid search (HQ Lexical + HQ Dense)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="Scenario service not initialized")
        
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
        logger.error(f"HQ Hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/hybrid-lq")
async def hybrid_lq_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(20, description="Number of results to return"),
    lexical_k: int = Query(50, description="Number of BM25 search results"),
    dense_k: int = Query(50, description="Number of vector search results")
):
    """LQ Hybrid search (LQ Lexical + LQ Dense)"""
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="Scenario service not initialized")
        
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
        logger.error(f"LQ Hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/field-weights")
async def get_field_weights():
    """Get current field weights"""
    service = get_hybrid_search_service()
    return {
        "field_weights": service.field_weights,
        "total_fields": len(service.field_weights)
    }

@router.post("/field-weights")
async def update_field_weights(weights: Dict[str, float]):
    """Update field weights"""
    try:
        service = get_hybrid_search_service()
        
        # Update only valid fields
        updated_fields = []
        for field, weight in weights.items():
            if field in service.field_weights:
                service.field_weights[field] = weight
                updated_fields.append(field)
        
        return {
            "message": f"{len(updated_fields)} field weights updated",
            "updated_fields": updated_fields,
            "current_weights": service.field_weights
        }
        
    except Exception as e:
        logger.error(f"Weight update failed: {e}")
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")

@router.get("/stats")
async def get_service_stats():
    """Get service statistics"""
    service = get_hybrid_search_service()
    
    if not service.is_loaded:
        return {"status": "not_initialized"}
    
    # Document statistics
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
    """Compare HQ vs LQ model performance"""
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        # HQ model (gpt-4o) search
        hq_results = await service.hybrid_search(
            query=query,
            top_k=top_k,
            namespace="pcos-rag-gpt_4o"
        )
        
        # LQ model (gpt-3.5-turbo) search  
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
        logger.error(f"Model comparison failed: {e}")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")

@router.post("/scenario-c-comparison")
async def scenario_c_fair_comparison(
    query: str,
    top_k: int = 10
):
    """
    Scenario C: Fair model comparison in fully integrated environment
    Combined BM25 + Combined Dense (pcos-rag-combined) → Model filtering
    """
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        combined_namespace = "pcos-rag-combined"
        
        # HQ results in integrated environment (model filtering)
        hq_results = await service.hybrid_search(
            query=query,
            top_k=top_k * 2,  # Get more before filtering
            namespace=combined_namespace,
            model_filter="hq"
        )
        
        # LQ results in integrated environment (model filtering)
        lq_results = await service.hybrid_search(
            query=query,
            top_k=top_k * 2,
            namespace=combined_namespace,
            model_filter="lq"
        )
        
        # Unfiltered all results (for reference)
        all_results = await service.hybrid_search(
            query=query,
            top_k=top_k * 2,
            namespace=combined_namespace
        )
        
        # Cut top results only
        hq_final = hq_results["results"][:top_k]
        lq_final = lq_results["results"][:top_k]
        all_final = all_results["results"][:top_k]
        
        # Advanced analysis
        hq_ids = set(r["id"] for r in hq_final)
        lq_ids = set(r["id"] for r in lq_final)
        all_ids = set(r["id"] for r in all_final)
        
        # Rank analysis (rank of each model in overall results)
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
            "scenario": "C - Fully Integrated Environment Comparison",
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
                # Rank analysis
                "hq_avg_rank": sum(hq_ranks_in_all[:top_k]) / len(hq_ranks_in_all[:top_k]) if hq_ranks_in_all[:top_k] else 0,
                "lq_avg_rank": sum(lq_ranks_in_all[:top_k]) / len(lq_ranks_in_all[:top_k]) if lq_ranks_in_all[:top_k] else 0,
                "hq_top_positions": len([r for r in hq_ranks_in_all if r <= top_k]),
                "lq_top_positions": len([r for r in lq_ranks_in_all if r <= top_k]),
            },
            "all_results_sample": all_final[:5]  # Top 5 for reference
        }
        
    except Exception as e:
        logger.error(f"Scenario C comparison failed: {e}")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")

@router.post("/initialize-all-scenarios")
async def initialize_all_scenarios():
    """Initialize all scenario services (A, B, C)"""
    try:
        multi_service = get_multi_scenario_service()
        
        logger.info("All scenario services initialization started...")
        results = multi_service.initialize_all_services()
        
        if all(results.values()):
            return {
                "status": "success",
                "message": "All scenario services initialization completed",
                "details": results,
                "scenarios_available": ["A", "B", "C"]
            }
        else:
            return {
                "status": "partial_success",
                "message": "Some scenario services initialization failed",
                "details": results,
                "failed_services": [k for k, v in results.items() if not v]
            }
        
    except Exception as e:
        logger.error(f"Scenario services initialization failed: {e}")
        raise HTTPException(status_code=500, detail=f"Initialization failed: {str(e)}")

@router.post("/scenario-a-comparison")
async def scenario_a_pure_comparison(
    query: str,
    top_k: int = 10
):
    """
    Scenario A: Pure model performance comparison
    HQ environment (HQ BM25 + HQ Dense) vs LQ environment (LQ BM25 + LQ Dense)
    """
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(
                status_code=503, 
                detail="Scenario service not initialized. Call /initialize-all-scenarios first."
            )
        
        result = await multi_service.scenario_a_comparison(query, top_k)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scenario A comparison failed: {e}")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")

@router.post("/scenario-b-comparison")
async def scenario_b_mixed_comparison(
    query: str,
    top_k: int = 10
):
    """
    Scenario B: Competition in mixed environment
    Combined BM25 + HQ Dense vs Combined BM25 + LQ Dense
    """
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="Scenario service not initialized")
        
        result = await multi_service.scenario_b_comparison(query, top_k)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scenario B comparison failed: {e}")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")

@router.post("/comprehensive-comparison")
async def comprehensive_abc_comparison(
    query: str,
    top_k: int = 10
):
    """
    Comprehensive comparison: Execute all scenarios A, B, C for comparison
    Analyze HQ vs LQ performance in all scenarios
    """
    try:
        multi_service = get_multi_scenario_service()
        
        if not multi_service.is_initialized:
            raise HTTPException(status_code=503, detail="Scenario service not initialized")
        
        logger.info(f"Comprehensive comparison (A/B/C) started: '{query}'")
        result = await multi_service.comprehensive_comparison(query, top_k)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comprehensive comparison failed: {e}")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")

@router.get("/scenarios-status")
async def get_scenarios_status():
    """Check status of all scenario services"""
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
        
        # Include single service status
        single_service = get_hybrid_search_service()
        status["single_service"] = {
            "loaded": single_service.is_loaded,
            "documents": len(single_service.documents),
            "bm25_indexes": len(single_service.bm25_indexes)
        }
        
        return status
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")

# =============================================================================
# 🔧 ADVANCED HYBRID SEARCH (POST with full options)
# =============================================================================

@router.post("/hybrid-advanced", response_model=HybridSearchResponse)
async def hybrid_advanced_search(request: HybridSearchRequest):
    """Advanced hybrid search (supports all options: weights, filtering, etc.)"""
    start_time = datetime.now()
    
    try:
        service = get_hybrid_search_service()
        
        if not service.is_loaded:
            raise HTTPException(status_code=503, detail="Service not initialized. Call /initialize first.")
        
        # Apply custom weights
        original_weights = None
        if request.field_weights:
            original_weights = service.field_weights.copy()
            service.field_weights.update(request.field_weights)
            logger.info(f"Custom weights applied: {request.field_weights}")
        
        # Execute hybrid search
        result = await service.hybrid_search(
            query=request.query,
            top_k=request.top_k,
            lexical_k=request.lexical_k,
            dense_k=request.dense_k,
            namespace=request.namespace,
            model_filter=request.model_filter
        )
        
        # Restore weights
        if original_weights:
            service.field_weights = original_weights
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        # Convert response
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
        
        logger.info(f"Advanced hybrid search completed: {len(search_results)} results, {processing_time:.2f} seconds")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Advanced hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

# =============================================================================
# ⚙️ LEGACY/COMPATIBILITY APIs (Legacy compatibility)
# =============================================================================

