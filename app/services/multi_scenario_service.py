"""
Multi-scenario hybrid search service
Integrated service supporting Scenario A, B, C
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
        """Initialize services for all scenarios"""
        results = {}
        
        try:
            # Scenario A: HQ-only service
            logger.info("=== Scenario A: HQ-only service initialization ===")
            self.services["hq"] = HybridSearchService(self.data_dir, "hq_only")
            hq_files = list(Path(self.data_dir).glob("hq_documents_*.json"))
            if hq_files:
                hq_file = str(sorted(hq_files)[-1])
                results["hq"] = self.services["hq"].initialize(hq_file)
                logger.info(f"HQ service initialization: {'Success' if results['hq'] else 'Failed'}")
            else:
                logger.error("HQ data file not found")
                results["hq"] = False
            
            # Scenario A: LQ-only service
            logger.info("=== Scenario A: LQ-only service initialization ===")
            self.services["lq"] = HybridSearchService(self.data_dir, "lq_only")
            lq_files = list(Path(self.data_dir).glob("lq_documents_*.json"))
            if lq_files:
                lq_file = str(sorted(lq_files)[-1])
                results["lq"] = self.services["lq"].initialize(lq_file)
                logger.info(f"LQ service initialization: {'Success' if results['lq'] else 'Failed'}")
            else:
                logger.error("LQ data file not found")
                results["lq"] = False
            
            # Scenario B, C: Combined service
            logger.info("=== Scenario B/C: Combined service initialization ===")
            self.services["combined"] = HybridSearchService(self.data_dir, "combined")
            combined_files = list(Path(self.data_dir).glob("combined_documents_*.json"))
            if combined_files:
                combined_file = str(sorted(combined_files)[-1])
                results["combined"] = self.services["combined"].initialize(combined_file)
                logger.info(f"Combined service initialization: {'Success' if results['combined'] else 'Failed'}")
            else:
                logger.error("Combined data file not found")
                results["combined"] = False
            
            self.is_initialized = all(results.values())
            logger.info(f"Overall service initialization: {'Success' if self.is_initialized else 'Failed'}")
            
            return results
            
        except Exception as e:
            logger.error(f"Service initialization failed: {e}")
            return {"hq": False, "lq": False, "combined": False}
    
    async def scenario_a_comparison(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """
        Scenario A: Pure model performance comparison
        HQ environment (HQ BM25 + HQ Dense) vs LQ environment (LQ BM25 + LQ Dense)
        """
        if not self.is_initialized:
            raise Exception("Service not initialized")
        
        logger.info(f"Scenario A comparison started: '{query}'")
        
        # Parallel search in HQ/LQ environments
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
        
        # Analysis
        hq_ids = set(r["id"] for r in hq_results["results"])
        lq_ids = set(r["id"] for r in lq_results["results"])
        
        return {
            "scenario": "A - Pure model performance comparison",
            "query": query,
            "hq_environment": {
                "bm25": "HQ-only",
                "dense": "pcos-rag-gpt_4o",
                "results": hq_results["results"],
                "stats": hq_results["stats"]
            },
            "lq_environment": {
                "bm25": "LQ-only", 
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
        Scenario B: Competition in mixed environment
        Combined BM25 + HQ Dense vs Combined BM25 + LQ Dense
        """
        if not self.is_initialized:
            raise Exception("Service not initialized")
        
        logger.info(f"Scenario B comparison started: '{query}'")
        
        # Combined BM25 + each Dense search
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
        
        # Analysis
        hq_ids = set(r["id"] for r in hq_results["results"])
        lq_ids = set(r["id"] for r in lq_results["results"])
        
        return {
            "scenario": "B - Competition in mixed environment",
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
        Scenario C: Complete integration comparison
        Combined BM25 + Combined Dense → model_version filtering
        """
        if not self.is_initialized:
            raise Exception("Service not initialized")
        
        logger.info(f"Scenario C comparison started: '{query}'")
        
        combined_namespace = "pcos-rag-combined"
        
        # Model-specific filtering in unified environment
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
        
        # Top results only
        hq_final = hq_results["results"][:top_k]
        lq_final = lq_results["results"][:top_k]
        
        # Analysis
        hq_ids = set(r["id"] for r in hq_final)
        lq_ids = set(r["id"] for r in lq_final)
        
        # Rank analysis
        hq_ranks = []
        lq_ranks = []
        
        for i, result in enumerate(all_results["results"]):
            model_version = result.get("model_version", "")
            if "gpt-4o" in model_version:
                hq_ranks.append(i + 1)
            elif "gpt-3.5-turbo" in model_version:
                lq_ranks.append(i + 1)
        
        return {
            "scenario": "C - Complete integration comparison",
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
        """Comprehensive comparison of all scenarios (A, B, C)"""
        if not self.is_initialized:
            raise Exception("Service not initialized")
        
        logger.info(f"Comprehensive comparison started: '{query}'")
        
        # Execute all scenarios in parallel
        scenario_a_task = asyncio.create_task(self.scenario_a_comparison(query, top_k))
        scenario_b_task = asyncio.create_task(self.scenario_b_comparison(query, top_k))
        scenario_c_task = asyncio.create_task(self.scenario_c_comparison(query, top_k))
        
        scenario_a, scenario_b, scenario_c = await asyncio.gather(
            scenario_a_task, scenario_b_task, scenario_c_task
        )
        
        # Cross-scenario comparison analysis
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
                "most_consistent_scenario": "Analysis required",
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

# Global instance
_multi_scenario_service = None

def get_multi_scenario_service() -> MultiScenarioSearchService:
    """Return multi-scenario service instance"""
    global _multi_scenario_service
    
    if _multi_scenario_service is None:
        _multi_scenario_service = MultiScenarioSearchService()
    
    return _multi_scenario_service