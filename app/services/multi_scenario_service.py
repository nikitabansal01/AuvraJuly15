"""
다중 시나리오 하이브리드 검색 서비스
Scenario A, B, C를 모두 지원하는 통합 서비스
"""
import logging
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

from app.services.hybrid_search_service import HybridSearchService

logger = logging.getLogger(__name__)

class MultiScenarioSearchService:
    def __init__(self, data_dir: str = "data/bm25"):
        self.data_dir = data_dir
        self.services: Dict[str, HybridSearchService] = {}
        self.is_initialized = False
        
    def initialize_all_services(self) -> Dict[str, bool]:
        """모든 시나리오용 서비스 초기화"""
        results = {}
        
        try:
            # Scenario A용: HQ 전용 서비스
            logger.info("=== Scenario A: HQ 전용 서비스 초기화 ===")
            self.services["hq"] = HybridSearchService(self.data_dir, "hq_only")
            hq_files = list(Path(self.data_dir).glob("hq_documents_*.json"))
            if hq_files:
                hq_file = str(sorted(hq_files)[-1])
                results["hq"] = self.services["hq"].initialize(hq_file)
                logger.info(f"HQ 서비스 초기화: {'성공' if results['hq'] else '실패'}")
            else:
                logger.error("HQ 데이터 파일을 찾을 수 없음")
                results["hq"] = False
            
            # Scenario A용: LQ 전용 서비스
            logger.info("=== Scenario A: LQ 전용 서비스 초기화 ===")
            self.services["lq"] = HybridSearchService(self.data_dir, "lq_only")
            lq_files = list(Path(self.data_dir).glob("lq_documents_*.json"))
            if lq_files:
                lq_file = str(sorted(lq_files)[-1])
                results["lq"] = self.services["lq"].initialize(lq_file)
                logger.info(f"LQ 서비스 초기화: {'성공' if results['lq'] else '실패'}")
            else:
                logger.error("LQ 데이터 파일을 찾을 수 없음")
                results["lq"] = False
            
            # Scenario B, C용: Combined 서비스
            logger.info("=== Scenario B/C: Combined 서비스 초기화 ===")
            self.services["combined"] = HybridSearchService(self.data_dir, "combined")
            combined_files = list(Path(self.data_dir).glob("combined_documents_*.json"))
            if combined_files:
                combined_file = str(sorted(combined_files)[-1])
                results["combined"] = self.services["combined"].initialize(combined_file)
                logger.info(f"Combined 서비스 초기화: {'성공' if results['combined'] else '실패'}")
            else:
                logger.error("Combined 데이터 파일을 찾을 수 없음")
                results["combined"] = False
            
            self.is_initialized = all(results.values())
            logger.info(f"전체 서비스 초기화: {'성공' if self.is_initialized else '실패'}")
            
            return results
            
        except Exception as e:
            logger.error(f"서비스 초기화 실패: {e}")
            return {"hq": False, "lq": False, "combined": False}
    
    async def scenario_a_comparison(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """
        Scenario A: 순수 모델 성능 비교
        HQ 환경 (HQ BM25 + HQ Dense) vs LQ 환경 (LQ BM25 + LQ Dense)
        """
        if not self.is_initialized:
            raise Exception("서비스가 초기화되지 않음")
        
        logger.info(f"Scenario A 비교 시작: '{query}'")
        
        # 병렬로 HQ/LQ 환경에서 검색
        hq_task = asyncio.create_task(
            self.services["hq"].hybrid_search(
                query=query,
                top_k=top_k,
                namespace="pcos-rag-gpt_4o"  # HQ Dense
            )
        )
        
        lq_task = asyncio.create_task(
            self.services["lq"].hybrid_search(
                query=query,
                top_k=top_k,
                namespace="pcos-rag-gpt_3.5_turbo"  # LQ Dense
            )
        )
        
        hq_results, lq_results = await asyncio.gather(hq_task, lq_task)
        
        # 분석
        hq_ids = set(r["id"] for r in hq_results["results"])
        lq_ids = set(r["id"] for r in lq_results["results"])
        
        return {
            "scenario": "A - 순수 모델 성능 비교",
            "query": query,
            "hq_environment": {
                "bm25": "HQ 전용",
                "dense": "pcos-rag-gpt_4o",
                "results": hq_results["results"],
                "stats": hq_results["stats"]
            },
            "lq_environment": {
                "bm25": "LQ 전용", 
                "dense": "pcos-rag-gpt_3.5_turbo",
                "results": lq_results["results"],
                "stats": lq_results["stats"]
            },
            "analysis": {
                "hq_count": len(hq_results["results"]),
                "lq_count": len(lq_results["results"]),
                "overlap": len(hq_ids & lq_ids),
                "hq_unique": len(hq_ids - lq_ids),
                "lq_unique": len(lq_ids - hq_ids),
                "total_unique": len(hq_ids | lq_ids)
            }
        }
    
    async def scenario_b_comparison(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """
        Scenario B: 혼합 환경에서의 경쟁
        Combined BM25 + HQ Dense vs Combined BM25 + LQ Dense
        """
        if not self.is_initialized:
            raise Exception("서비스가 초기화되지 않음")
        
        logger.info(f"Scenario B 비교 시작: '{query}'")
        
        # Combined BM25 + 각각 Dense 검색
        hq_task = asyncio.create_task(
            self.services["combined"].hybrid_search(
                query=query,
                top_k=top_k,
                namespace="pcos-rag-gpt_4o"  # HQ Dense
            )
        )
        
        lq_task = asyncio.create_task(
            self.services["combined"].hybrid_search(
                query=query,
                top_k=top_k,
                namespace="pcos-rag-gpt_3.5_turbo"  # LQ Dense
            )
        )
        
        hq_results, lq_results = await asyncio.gather(hq_task, lq_task)
        
        # 분석
        hq_ids = set(r["id"] for r in hq_results["results"])
        lq_ids = set(r["id"] for r in lq_results["results"])
        
        return {
            "scenario": "B - 혼합 환경에서의 경쟁",
            "query": query,
            "hq_mixed": {
                "bm25": "Combined (HQ+LQ)",
                "dense": "pcos-rag-gpt_4o",
                "results": hq_results["results"],
                "stats": hq_results["stats"]
            },
            "lq_mixed": {
                "bm25": "Combined (HQ+LQ)",
                "dense": "pcos-rag-gpt_3.5_turbo", 
                "results": lq_results["results"],
                "stats": lq_results["stats"]
            },
            "analysis": {
                "hq_count": len(hq_results["results"]),
                "lq_count": len(lq_results["results"]),
                "overlap": len(hq_ids & lq_ids),
                "hq_unique": len(hq_ids - lq_ids),
                "lq_unique": len(lq_ids - hq_ids),
                "total_unique": len(hq_ids | lq_ids)
            }
        }
    
    async def scenario_c_comparison(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """
        Scenario C: 완전 통합 비교
        Combined BM25 + Combined Dense → model_version 필터링
        """
        if not self.is_initialized:
            raise Exception("서비스가 초기화되지 않음")
        
        logger.info(f"Scenario C 비교 시작: '{query}'")
        
        combined_namespace = "pcos-rag-combined"
        
        # 통합 환경에서 모델별 필터링
        hq_task = asyncio.create_task(
            self.services["combined"].hybrid_search(
                query=query,
                top_k=top_k * 2,
                namespace=combined_namespace,
                model_filter="hq"
            )
        )
        
        lq_task = asyncio.create_task(
            self.services["combined"].hybrid_search(
                query=query,
                top_k=top_k * 2,
                namespace=combined_namespace,
                model_filter="lq"
            )
        )
        
        all_task = asyncio.create_task(
            self.services["combined"].hybrid_search(
                query=query,
                top_k=top_k * 2,
                namespace=combined_namespace
            )
        )
        
        hq_results, lq_results, all_results = await asyncio.gather(hq_task, lq_task, all_task)
        
        # 상위 결과만
        hq_final = hq_results["results"][:top_k]
        lq_final = lq_results["results"][:top_k]
        
        # 분석
        hq_ids = set(r["id"] for r in hq_final)
        lq_ids = set(r["id"] for r in lq_final)
        
        # 순위 분석
        hq_ranks = []
        lq_ranks = []
        
        for i, result in enumerate(all_results["results"]):
            model_version = result.get("model_version", "")
            if "gpt-4o" in model_version:
                hq_ranks.append(i + 1)
            elif "gpt-3.5-turbo" in model_version:
                lq_ranks.append(i + 1)
        
        return {
            "scenario": "C - 완전 통합 비교",
            "query": query,
            "namespace": combined_namespace,
            "hq_unified": {
                "bm25": "Combined (HQ+LQ)",
                "dense": "Combined (HQ+LQ)",
                "filter": "gpt-4o only",
                "results": hq_final,
                "stats": hq_results["stats"]
            },
            "lq_unified": {
                "bm25": "Combined (HQ+LQ)",
                "dense": "Combined (HQ+LQ)",
                "filter": "gpt-3.5-turbo only",
                "results": lq_final,
                "stats": lq_results["stats"]
            },
            "analysis": {
                "hq_count": len(hq_final),
                "lq_count": len(lq_final),
                "overlap": len(hq_ids & lq_ids),
                "hq_unique": len(hq_ids - lq_ids),
                "lq_unique": len(lq_ids - hq_ids),
                "total_unique": len(hq_ids | lq_ids),
                "hq_avg_rank": sum(hq_ranks[:top_k]) / len(hq_ranks[:top_k]) if hq_ranks[:top_k] else 0,
                "lq_avg_rank": sum(lq_ranks[:top_k]) / len(lq_ranks[:top_k]) if lq_ranks[:top_k] else 0,
                "hq_top_positions": len([r for r in hq_ranks if r <= top_k]),
                "lq_top_positions": len([r for r in lq_ranks if r <= top_k])
            }
        }
    
    async def comprehensive_comparison(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """모든 시나리오 (A, B, C) 통합 비교"""
        if not self.is_initialized:
            raise Exception("서비스가 초기화되지 않음")
        
        logger.info(f"종합 비교 시작: '{query}'")
        
        # 병렬로 모든 시나리오 실행
        scenario_a_task = asyncio.create_task(self.scenario_a_comparison(query, top_k))
        scenario_b_task = asyncio.create_task(self.scenario_b_comparison(query, top_k))
        scenario_c_task = asyncio.create_task(self.scenario_c_comparison(query, top_k))
        
        scenario_a, scenario_b, scenario_c = await asyncio.gather(
            scenario_a_task, scenario_b_task, scenario_c_task
        )
        
        # 시나리오 간 비교 분석
        scenarios_analysis = {
            "consistency": {
                "a_vs_b_hq_overlap": len(
                    set(r["id"] for r in scenario_a["hq_environment"]["results"]) &
                    set(r["id"] for r in scenario_b["hq_mixed"]["results"])
                ) / max(len(scenario_a["hq_environment"]["results"]), 1),
                
                "a_vs_c_hq_overlap": len(
                    set(r["id"] for r in scenario_a["hq_environment"]["results"]) &
                    set(r["id"] for r in scenario_c["hq_unified"]["results"])
                ) / max(len(scenario_a["hq_environment"]["results"]), 1),
                
                "b_vs_c_hq_overlap": len(
                    set(r["id"] for r in scenario_b["hq_mixed"]["results"]) &
                    set(r["id"] for r in scenario_c["hq_unified"]["results"])
                ) / max(len(scenario_b["hq_mixed"]["results"]), 1)
            }
        }
        
        return {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "scenarios": {
                "A": scenario_a,
                "B": scenario_b, 
                "C": scenario_c
            },
            "cross_scenario_analysis": scenarios_analysis,
            "summary": {
                "most_consistent_scenario": "분석 필요",
                "hq_performance": {
                    "scenario_a": len(scenario_a["hq_environment"]["results"]),
                    "scenario_b": len(scenario_b["hq_mixed"]["results"]),
                    "scenario_c": len(scenario_c["hq_unified"]["results"])
                },
                "lq_performance": {
                    "scenario_a": len(scenario_a["lq_environment"]["results"]),
                    "scenario_b": len(scenario_b["lq_mixed"]["results"]),
                    "scenario_c": len(scenario_c["lq_unified"]["results"])
                }
            }
        }

# 전역 인스턴스
_multi_scenario_service = None

def get_multi_scenario_service() -> MultiScenarioSearchService:
    """다중 시나리오 서비스 인스턴스 반환"""
    global _multi_scenario_service
    
    if _multi_scenario_service is None:
        _multi_scenario_service = MultiScenarioSearchService()
    
    return _multi_scenario_service