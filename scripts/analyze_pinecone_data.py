#!/usr/bin/env python3
"""
Pinecone 인덱스의 데이터를 namespace별로 분석하는 스크립트
"""

import asyncio
import sys
import os
import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag_service import RAGService

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PineconeAnalyzer:
    def __init__(self):
        self.index = RAGService.get_pinecone_client()
        
    def get_all_namespaces(self) -> List[str]:
        """모든 네임스페이스 목록 반환"""
        try:
            stats = self.index.describe_index_stats()
            if hasattr(stats, 'namespaces') and stats.namespaces:
                return list(stats.namespaces.keys())
            else:
                return ["", "pcos-rag"]  # 기본 네임스페이스들
        except Exception as e:
            logger.error(f"네임스페이스 목록 조회 실패: {e}")
            return []
    
    def analyze_namespace(self, namespace: str = "") -> Dict[str, Any]:
        """특정 네임스페이스 분석"""
        try:
            logger.info(f"네임스페이스 '{namespace}' 분석 중...")
            
            # 기본 통계
            stats = self.index.describe_index_stats()
            namespace_stats = stats.namespaces.get(namespace) if hasattr(stats, 'namespaces') and stats.namespaces else None
            
            # 모든 벡터 조회
            sample_vectors = self.get_sample_vectors(namespace, limit=-1)
            
            # 메타데이터 분석
            metadata_analysis = self.analyze_metadata(sample_vectors)
            
            # 모델 버전 분석
            model_analysis = self.analyze_model_versions(sample_vectors)
            
            # 논문별 청크 분석
            paper_analysis = self.analyze_papers(sample_vectors)
            
            result = {
                "namespace": namespace,
                "total_vectors": namespace_stats.vector_count if namespace_stats else 0,
                "actual_vectors_retrieved": len(sample_vectors),
                "metadata_analysis": metadata_analysis,
                "model_analysis": model_analysis,
                "paper_analysis": paper_analysis,
                "sample_vectors": sample_vectors[:5]  # 상위 5개만 포함
            }
            
            return result
            
        except Exception as e:
            logger.error(f"네임스페이스 '{namespace}' 분석 실패: {e}")
            return {
                "namespace": namespace,
                "error": str(e),
                "total_vectors": 0
            }
    
    def get_sample_vectors(self, namespace: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        """샘플 벡터 조회"""
        try:
            # 더미 벡터로 쿼리 (1536 차원)
            dummy_vector = [0.0] * 1536
            
            # 모든 벡터를 조회하기 위해 큰 값 사용
            if limit == -1:
                # Pinecone의 최대 쿼리 한계 (10,000)
                query_limit = 10000
            else:
                query_limit = limit
            
            response = self.index.query(
                vector=dummy_vector,
                namespace=namespace,
                top_k=query_limit,
                include_metadata=True,
                include_values=False
            )
            
            vectors = []
            for match in response.matches:
                vectors.append({
                    "id": match.id,
                    "score": match.score,
                    "metadata": match.metadata
                })
            
            return vectors
            
        except Exception as e:
            logger.error(f"샘플 벡터 조회 실패: {e}")
            return []
    
    def analyze_metadata(self, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """메타데이터 분석"""
        if not vectors:
            return {"error": "분석할 벡터가 없습니다"}
        
        metadata_keys = set()
        chunk_sections = {}
        model_versions = set()
        tagging_timestamps = []
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            
            # 메타데이터 키 수집
            metadata_keys.update(metadata.keys())
            
            # 섹션 타입 분석
            section = metadata.get("chunk_section_type", "unknown")
            chunk_sections[section] = chunk_sections.get(section, 0) + 1
            
            # 모델 버전 분석
            model_version = metadata.get("model_version", "unknown")
            model_versions.add(model_version)
            
            # 태깅 타임스탬프 분석
            timestamp = metadata.get("tagging_timestamp")
            if timestamp:
                tagging_timestamps.append(timestamp)
        
        return {
            "metadata_keys": list(metadata_keys),
            "chunk_sections": chunk_sections,
            "model_versions": list(model_versions),
            "tagging_timestamps_count": len(tagging_timestamps),
            "earliest_timestamp": min(tagging_timestamps) if tagging_timestamps else None,
            "latest_timestamp": max(tagging_timestamps) if tagging_timestamps else None
        }
    
    def analyze_model_versions(self, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """모델 버전별 분석"""
        model_counts = {}
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            model_version = metadata.get("model_version", "unknown")
            model_counts[model_version] = model_counts.get(model_version, 0) + 1
        
        return {
            "model_distribution": model_counts,
            "total_models": len(model_counts)
        }
    
    def analyze_papers(self, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """논문별 분석"""
        paper_chunks = {}
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            pmid = metadata.get("pmid", "unknown")
            
            if pmid not in paper_chunks:
                paper_chunks[pmid] = {
                    "chunk_count": 0,
                    "sections": set(),
                    "title": metadata.get("paper_title", "Unknown"),
                    "authors": metadata.get("authors", []),
                    "journal": metadata.get("journal", "Unknown"),
                    "publication_year": metadata.get("publication_year"),
                    "doi": metadata.get("doi", ""),
                    "pmcid": metadata.get("pmcid", ""),
                    "chunk_ids": []
                }
            
            paper_chunks[pmid]["chunk_count"] += 1
            section = metadata.get("chunk_section_type", "unknown")
            paper_chunks[pmid]["sections"].add(section)
            paper_chunks[pmid]["chunk_ids"].append(vector["id"])
        
        # set을 list로 변환
        for pmid in paper_chunks:
            paper_chunks[pmid]["sections"] = list(paper_chunks[pmid]["sections"])
        
        return {
            "total_papers": len(paper_chunks),
            "paper_details": paper_chunks,
            "avg_chunks_per_paper": sum(p["chunk_count"] for p in paper_chunks.values()) / len(paper_chunks) if paper_chunks else 0,
            "research_list": self.get_research_list(paper_chunks)
        }
    
    def get_research_list(self, paper_chunks: Dict[str, Any]) -> List[Dict[str, Any]]:
        """리서치 리스트 생성"""
        research_list = []
        
        for pmid, paper_info in paper_chunks.items():
            research_item = {
                "pmid": pmid,
                "title": paper_info["title"],
                "authors": paper_info["authors"],
                "journal": paper_info["journal"],
                "publication_year": paper_info["publication_year"],
                "doi": paper_info["doi"],
                "pmcid": paper_info["pmcid"],
                "chunk_count": paper_info["chunk_count"],
                "sections": paper_info["sections"],
                "chunk_ids": paper_info["chunk_ids"]
            }
            research_list.append(research_item)
        
        # 출판년도별로 정렬 (최신순)
        research_list.sort(key=lambda x: x["publication_year"] or 0, reverse=True)
        
        return research_list
    
    def analyze_all_namespaces(self) -> Dict[str, Any]:
        """모든 네임스페이스 분석"""
        namespaces = self.get_all_namespaces()
        results = {}
        
        for namespace in namespaces:
            results[namespace] = self.analyze_namespace(namespace)
        
        return results
    
    def save_analysis_report(self, analysis_results: Dict[str, Any], filename: str = None):
        """분석 결과를 JSON 파일로 저장"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"pinecone_analysis_{timestamp}.json"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(analysis_results, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"분석 결과가 {filename}에 저장되었습니다.")
            return filename
            
        except Exception as e:
            logger.error(f"분석 결과 저장 실패: {e}")
            return None

async def main():
    """메인 함수"""
    analyzer = PineconeAnalyzer()
    
    print("=== Pinecone 데이터 분석 도구 ===")
    print("1. 모든 네임스페이스 분석")
    print("2. 특정 네임스페이스 분석")
    print("3. 네임스페이스 목록만 확인")
    print("4. 리서치 리스트만 확인")
    
    choice = input("\n선택하세요 (1-4): ").strip()
    
    if choice == "1":
        print("\n모든 네임스페이스를 분석합니다...")
        results = analyzer.analyze_all_namespaces()
        
        # 결과 출력
        for namespace, result in results.items():
            print(f"\n=== 네임스페이스: {namespace} ===")
            if "error" in result:
                print(f"오류: {result['error']}")
            else:
                print(f"총 벡터 수: {result['total_vectors']}")
                print(f"실제 조회된 벡터 수: {result['actual_vectors_retrieved']}")
                
                if result['model_analysis']:
                    print(f"모델 버전: {result['model_analysis']['model_distribution']}")
                
                if result['paper_analysis']:
                    print(f"논문 수: {result['paper_analysis']['total_papers']}")
                    print(f"논문당 평균 청크: {result['paper_analysis']['avg_chunks_per_paper']:.1f}")
                    
                    # 리서치 리스트 출력
                    if result['paper_analysis']['research_list']:
                        print(f"\n=== 리서치 리스트 (최신순) ===")
                        for i, research in enumerate(result['paper_analysis']['research_list'][:10], 1):  # 상위 10개만
                            print(f"{i}. PMID: {research['pmid']}")
                            print(f"   제목: {research['title'][:80]}...")
                            print(f"   저널: {research['journal']} ({research['publication_year']})")
                            print(f"   청크 수: {research['chunk_count']}개")
                            print(f"   섹션: {', '.join(research['sections'])}")
                            if research['doi']:
                                print(f"   DOI: {research['doi']}")
                            print()
        
        # 파일 저장
        save = input("\n분석 결과를 파일로 저장하시겠습니까? (y/n): ").strip().lower()
        if save == 'y':
            filename = analyzer.save_analysis_report(results)
            if filename:
                print(f"저장 완료: {filename}")
    
    elif choice == "2":
        namespaces = analyzer.get_all_namespaces()
        print(f"\n사용 가능한 네임스페이스: {namespaces}")
        
        namespace = input("분석할 네임스페이스를 입력하세요: ").strip()
        if namespace == "":
            namespace = ""  # 기본 네임스페이스
        
        result = analyzer.analyze_namespace(namespace)
        
        print(f"\n=== 네임스페이스: {namespace} 분석 결과 ===")
        if "error" in result:
            print(f"오류: {result['error']}")
        else:
            print(f"총 벡터 수: {result['total_vectors']}")
            print(f"실제 조회된 벡터 수: {result['actual_vectors_retrieved']}")
            
            if result['model_analysis']:
                print(f"모델 버전: {result['model_analysis']['model_distribution']}")
            
            if result['paper_analysis']:
                print(f"논문 수: {result['paper_analysis']['total_papers']}")
                print(f"논문당 평균 청크: {result['paper_analysis']['avg_chunks_per_paper']:.1f}")
                
                # 리서치 리스트 출력
                if result['paper_analysis']['research_list']:
                    print(f"\n=== 리서치 리스트 (최신순) ===")
                    for i, research in enumerate(result['paper_analysis']['research_list'][:10], 1):  # 상위 10개만
                        print(f"{i}. PMID: {research['pmid']}")
                        print(f"   제목: {research['title'][:80]}...")
                        print(f"   저널: {research['journal']} ({research['publication_year']})")
                        print(f"   청크 수: {research['chunk_count']}개")
                        print(f"   섹션: {', '.join(research['sections'])}")
                        if research['doi']:
                            print(f"   DOI: {research['doi']}")
                        print()
            
            # 샘플 벡터 출력
            if result['sample_vectors']:
                print(f"\n=== 샘플 벡터 (상위 5개) ===")
                for i, vector in enumerate(result['sample_vectors'], 1):
                    print(f"{i}. ID: {vector['id']}")
                    print(f"   점수: {vector['score']:.4f}")
                    print(f"   PMID: {vector['metadata'].get('pmid', 'N/A')}")
                    print(f"   섹션: {vector['metadata'].get('chunk_section_type', 'N/A')}")
                    print()
    
    elif choice == "3":
        namespaces = analyzer.get_all_namespaces()
        print(f"\n사용 가능한 네임스페이스:")
        for i, namespace in enumerate(namespaces, 1):
            print(f"{i}. '{namespace}'")
    
    elif choice == "4":
        namespaces = analyzer.get_all_namespaces()
        print(f"\n사용 가능한 네임스페이스: {namespaces}")
        
        namespace = input("확인할 네임스페이스를 입력하세요: ").strip()
        if namespace == "":
            namespace = ""  # 기본 네임스페이스
        
        result = analyzer.analyze_namespace(namespace)
        
        if "error" in result:
            print(f"오류: {result['error']}")
        elif result['paper_analysis'] and result['paper_analysis']['research_list']:
            print(f"\n=== 네임스페이스 '{namespace}' 리서치 리스트 ===")
            print(f"총 {len(result['paper_analysis']['research_list'])}개 논문")
            print()
            
            for i, research in enumerate(result['paper_analysis']['research_list'], 1):
                print(f"{i:2d}. PMID: {research['pmid']}")
                print(f"    제목: {research['title']}")
                print(f"    저널: {research['journal']} ({research['publication_year']})")
                print(f"    청크 수: {research['chunk_count']}개")
                print(f"    섹션: {', '.join(research['sections'])}")
                if research['doi']:
                    print(f"    DOI: {research['doi']}")
                if research['authors']:
                    print(f"    저자: {', '.join(research['authors'][:3])}{'...' if len(research['authors']) > 3 else ''}")
                print()
        else:
            print("리서치 리스트가 없습니다.")
    
    else:
        print("잘못된 선택입니다.")

if __name__ == "__main__":
    asyncio.run(main())
