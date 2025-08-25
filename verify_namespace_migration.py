#!/usr/bin/env python3
"""
네임스페이스 이동이 제대로 되었는지 확인하는 스크립트
"""

import os
import asyncio
import json
import logging
from typing import List, Dict, Any
from datetime import datetime
from pinecone import Pinecone
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NamespaceMigrationVerifier:
    def __init__(self):
        self.api_key = os.getenv("PINECONE_API_KEY")
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY 환경변수가 설정되지 않았습니다.")
        
        # Pinecone 클라이언트 초기화
        self.pc = Pinecone(api_key=self.api_key)
        
        # 인덱스 이름 자동 감지
        indexes = self.pc.list_indexes()
        if not indexes:
            raise ValueError("사용 가능한 Pinecone 인덱스가 없습니다.")
        
        self.index_name = indexes[0].name
        logger.info(f"사용할 인덱스: {self.index_name}")
        
        # 인덱스 객체 생성
        self.index = self.pc.Index(self.index_name)
        
        # 확인할 네임스페이스들
        self.source_namespace = ""  # default
        self.target_namespace = "pcos-rag-gpt4o"
        
        logger.info(f"소스 네임스페이스: '{self.source_namespace}' (default)")
        logger.info(f"타겟 네임스페이스: '{self.target_namespace}'")
    
    async def get_namespace_stats(self) -> Dict[str, Any]:
        """네임스페이스별 통계 조회"""
        try:
            logger.info("네임스페이스별 통계 조회 중...")
            
            stats = self.index.describe_index_stats()
            logger.info(f"전체 인덱스 통계: {stats}")
            
            source_count = stats.get("namespaces", {}).get(self.source_namespace, {}).get("vector_count", 0)
            target_count = stats.get("namespaces", {}).get(self.target_namespace, {}).get("vector_count", 0)
            
            result = {
                "source_namespace": self.source_namespace,
                "target_namespace": self.target_namespace,
                "source_count": source_count,
                "target_count": target_count,
                "total_vectors": stats.get("total_vector_count", 0)
            }
            
            logger.info(f"네임스페이스 통계: {result}")
            return result
            
        except Exception as e:
            logger.error(f"통계 조회 실패: {e}")
            return {}
    
    async def get_sample_vectors(self, namespace: str, count: int = 5) -> List[Dict[str, Any]]:
        """네임스페이스에서 샘플 벡터 조회"""
        try:
            logger.info(f"네임스페이스 '{namespace}'에서 샘플 벡터 조회 중...")
            
            # 인덱스 차원 확인
            stats = self.index.describe_index_stats()
            dimension = stats.get("dimension", 1536)
            
            response = self.index.query(
                vector=[0.0] * dimension,
                top_k=count,
                include_metadata=True,
                include_values=True,
                namespace=namespace
            )
            
            sample_vectors = []
            for match in response.matches:
                vector_data = {
                    "id": match.id,
                    "metadata": match.metadata or {},
                    "score": match.score,
                    "has_values": len(match.values) > 0 if match.values else False
                }
                sample_vectors.append(vector_data)
            
            logger.info(f"네임스페이스 '{namespace}'에서 {len(sample_vectors)}개 샘플 벡터 조회 완료")
            return sample_vectors
            
        except Exception as e:
            logger.error(f"샘플 벡터 조회 실패 (네임스페이스 '{namespace}'): {e}")
            return []
    
    async def compare_vectors(self) -> Dict[str, Any]:
        """소스와 타겟 네임스페이스의 벡터 비교"""
        try:
            logger.info("벡터 비교 분석 중...")
            
            # 샘플 벡터 조회
            source_samples = await self.get_sample_vectors(self.source_namespace, 10)
            target_samples = await self.get_sample_vectors(self.target_namespace, 10)
            
            # ID 패턴 분석
            source_ids = [v["id"] for v in source_samples]
            target_ids = [v["id"] for v in target_samples]
            
            # 메타데이터 분석
            source_metadata_keys = set()
            target_metadata_keys = set()
            
            for vector in source_samples:
                source_metadata_keys.update(vector["metadata"].keys())
            
            for vector in target_samples:
                target_metadata_keys.update(vector["metadata"].keys())
            
            comparison = {
                "source_samples_count": len(source_samples),
                "target_samples_count": len(target_samples),
                "source_id_patterns": source_ids,
                "target_id_patterns": target_ids,
                "source_metadata_keys": list(source_metadata_keys),
                "target_metadata_keys": list(target_metadata_keys),
                "common_metadata_keys": list(source_metadata_keys & target_metadata_keys),
                "source_only_metadata_keys": list(source_metadata_keys - target_metadata_keys),
                "target_only_metadata_keys": list(target_metadata_keys - source_metadata_keys)
            }
            
            logger.info(f"벡터 비교 결과: {comparison}")
            return comparison
            
        except Exception as e:
            logger.error(f"벡터 비교 실패: {e}")
            return {}
    
    async def verify_migration_integrity(self) -> Dict[str, Any]:
        """이동 무결성 확인"""
        try:
            logger.info("이동 무결성 확인 중...")
            
            # 1. 통계 확인
            stats = await self.get_namespace_stats()
            
            # 2. 벡터 비교
            comparison = await self.compare_vectors()
            
            # 3. 무결성 판단
            integrity_checks = {
                "target_has_vectors": stats.get("target_count", 0) > 0,
                "source_has_vectors": stats.get("source_count", 0) > 0,
                "vectors_have_values": all(v["has_values"] for v in comparison.get("target_samples", [])),
                "metadata_structure_preserved": len(comparison.get("common_metadata_keys", [])) > 0,
                "id_patterns_consistent": len(comparison.get("target_id_patterns", [])) > 0
            }
            
            # 4. 전체 무결성 점수
            integrity_score = sum(integrity_checks.values()) / len(integrity_checks) * 100
            
            integrity_result = {
                "integrity_checks": integrity_checks,
                "integrity_score": integrity_score,
                "migration_successful": integrity_score >= 80,  # 80% 이상이면 성공
                "recommendations": []
            }
            
            # 5. 권장사항 생성
            if not integrity_checks["target_has_vectors"]:
                integrity_result["recommendations"].append("타겟 네임스페이스에 벡터가 없습니다. 이동을 다시 확인하세요.")
            
            if not integrity_checks["vectors_have_values"]:
                integrity_result["recommendations"].append("일부 벡터에 값이 없습니다. 데이터 무결성을 확인하세요.")
            
            if not integrity_checks["metadata_structure_preserved"]:
                integrity_result["recommendations"].append("메타데이터 구조가 보존되지 않았습니다. 이동 과정을 확인하세요.")
            
            logger.info(f"무결성 확인 결과: {integrity_result}")
            return integrity_result
            
        except Exception as e:
            logger.error(f"무결성 확인 실패: {e}")
            return {}
    
    def save_verification_log(self, verification_data: Dict[str, Any]):
        """확인 로그 저장"""
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "verification_data": verification_data
        }
        
        with open("namespace_migration_verification_log.json", "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        logger.info("확인 로그가 namespace_migration_verification_log.json에 저장되었습니다.")

async def main():
    """메인 실행 함수"""
    try:
        logger.info("=== 네임스페이스 이동 확인 스크립트 시작 ===")
        
        # 확인기 초기화
        verifier = NamespaceMigrationVerifier()
        
        # 1. 네임스페이스 통계 확인
        stats = await verifier.get_namespace_stats()
        
        print(f"\n=== 네임스페이스 통계 ===")
        print(f"소스 네임스페이스 ('{stats.get('source_namespace', '')}'): {stats.get('source_count', 0)}개 벡터")
        print(f"타겟 네임스페이스 ('{stats.get('target_namespace', '')}'): {stats.get('target_count', 0)}개 벡터")
        print(f"전체 벡터 수: {stats.get('total_vectors', 0)}개")
        
        # 2. 벡터 비교 분석
        comparison = await verifier.compare_vectors()
        
        print(f"\n=== 벡터 비교 분석 ===")
        print(f"소스 샘플 벡터 수: {comparison.get('source_samples_count', 0)}개")
        print(f"타겟 샘플 벡터 수: {comparison.get('target_samples_count', 0)}개")
        print(f"공통 메타데이터 키: {comparison.get('common_metadata_keys', [])}")
        print(f"타겟 전용 메타데이터 키: {comparison.get('target_only_metadata_keys', [])}")
        
        # 3. 무결성 확인
        integrity = await verifier.verify_migration_integrity()
        
        print(f"\n=== 이동 무결성 확인 ===")
        print(f"무결성 점수: {integrity.get('integrity_score', 0):.1f}%")
        print(f"이동 성공 여부: {'성공' if integrity.get('migration_successful', False) else '실패'}")
        
        checks = integrity.get('integrity_checks', {})
        print(f"무결성 검사 결과:")
        for check_name, result in checks.items():
            status = "✅" if result else "❌"
            print(f"  {status} {check_name}: {result}")
        
        if integrity.get('recommendations'):
            print(f"\n권장사항:")
            for rec in integrity.get('recommendations', []):
                print(f"  - {rec}")
        
        # 4. 최종 판단
        if integrity.get('migration_successful', False):
            print(f"\n🎉 네임스페이스 이동이 성공적으로 완료되었습니다!")
        else:
            print(f"\n⚠️ 네임스페이스 이동에 문제가 있을 수 있습니다. 위의 권장사항을 확인하세요.")
        
        # 5. 로그 저장
        verification_data = {
            "stats": stats,
            "comparison": comparison,
            "integrity": integrity
        }
        verifier.save_verification_log(verification_data)
        
        logger.info("=== 네임스페이스 이동 확인 완료 ===")
        
    except Exception as e:
        logger.error(f"스크립트 실행 실패: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
