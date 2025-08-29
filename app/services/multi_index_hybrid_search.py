"""
공정한 모델 비교를 위한 다중 인덱스 하이브리드 검색
HQ vs LQ 모델을 동일한 조건에서 비교
"""
from typing import Dict, List, Any
from app.services.hybrid_search_service import HybridSearchService
import logging

logger = logging.getLogger(__name__)

class MultiIndexHybridSearchService:
    def __init__(self, data_dir: str = "data/bm25"):
        self.data_dir = data_dir
        self.services = {}
        
    def initialize_all_indexes(self):
        """모든 인덱스 초기화 (HQ, LQ, Combined)"""
        
        # HQ 전용 서비스
        self.services["hq"] = HybridSearchService(self.data_dir)
        hq_success = self.services["hq"].initialize("hq_documents_latest.json")
        
        # LQ 전용 서비스  
        self.services["lq"] = HybridSearchService(self.data_dir)
        lq_success = self.services["lq"].initialize("lq_documents_latest.json")
        
        # Combined 서비스
        self.services["combined"] = HybridSearchService(self.data_dir) 
        combined_success = self.services["combined"].initialize("combined_documents_latest.json")
        
        return {
            "hq": hq_success,
            "lq": lq_success, 
            "combined": combined_success
        }
    
    async def fair_model_comparison(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """공정한 모델 비교 (같은 조건)"""
        
        results = {}
        
        # 1. 분리된 환경에서 비교
        results["separated"] = {
            "hq": await self.services["hq"].hybrid_search(
                query, top_k=top_k, namespace="pcos-rag-gpt_4o"
            ),
            "lq": await self.services["lq"].hybrid_search(
                query, top_k=top_k, namespace="pcos-rag-gpt_3.5_turbo"
            )
        }
        
        # 2. 통합된 환경에서 비교 (Combined BM25 + 각 네임스페이스)
        combined_hq = await self.services["combined"].hybrid_search(
            query, top_k=top_k, namespace="pcos-rag-gpt_4o"
        )
        combined_lq = await self.services["combined"].hybrid_search(
            query, top_k=top_k, namespace="pcos-rag-gpt_3.5_turbo"  
        )
        
        results["unified"] = {
            "hq": combined_hq,
            "lq": combined_lq
        }
        
        # 3. 비교 분석
        results["analysis"] = self.analyze_comparison(results)
        
        return results
    
    def analyze_comparison(self, results: Dict) -> Dict[str, Any]:
        """비교 결과 분석"""
        analysis = {}
        
        # 분리 vs 통합 환경 차이
        sep_hq = set(r["id"] for r in results["separated"]["hq"]["results"])
        uni_hq = set(r["id"] for r in results["unified"]["hq"]["results"]) 
        
        analysis["hq_overlap"] = len(sep_hq & uni_hq) / max(len(sep_hq), 1)
        analysis["hq_separated_only"] = len(sep_hq - uni_hq)
        analysis["hq_unified_only"] = len(uni_hq - sep_hq)
        
        # HQ vs LQ 차이 (통합 환경에서)
        hq_ids = set(r["id"] for r in results["unified"]["hq"]["results"])
        lq_ids = set(r["id"] for r in results["unified"]["lq"]["results"])
        
        analysis["model_overlap"] = len(hq_ids & lq_ids) / max(len(hq_ids | lq_ids), 1)
        analysis["hq_unique"] = len(hq_ids - lq_ids)  
        analysis["lq_unique"] = len(lq_ids - hq_ids)
        
        return analysis