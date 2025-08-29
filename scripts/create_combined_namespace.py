#!/usr/bin/env python3
"""
Pinecone 통합 네임스페이스 생성 스크립트
HQ + LQ 벡터를 하나의 네임스페이스에 합쳐서 공정한 비교 환경 구축
"""
import os
import sys
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

# 프로젝트 루트를 Python path에 추가
sys.path.append(str(Path(__file__).parent.parent))

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv()

from app.services.rag_service import RAGService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CombinedNamespaceCreator:
    def __init__(self):
        self.index = None
        self.combined_namespace = "pcos-rag-combined"
        
    def connect_pinecone(self):
        """Pinecone 클라이언트 초기화"""
        try:
            self.index = RAGService.get_pinecone_client()
            logger.info("Pinecone 클라이언트 연결 성공")
            return True
        except Exception as e:
            logger.error(f"Pinecone 연결 실패: {e}")
            return False
    
    def get_all_vectors_from_namespace(self, namespace: str, batch_size: int = 100) -> List[Dict[str, Any]]:
        """특정 네임스페이스에서 모든 벡터 가져오기 (ID, 벡터, 메타데이터)"""
        logger.info(f"네임스페이스 '{namespace}'에서 벡터 추출 중...")
        
        try:
            # 네임스페이스 통계 확인
            stats = self.index.describe_index_stats()
            ns_info = stats.namespaces.get(namespace, {})
            total_count = ns_info.vector_count if hasattr(ns_info, 'vector_count') else 0
            
            logger.info(f"네임스페이스 '{namespace}': {total_count}개 벡터")
            
            if total_count == 0:
                logger.warning(f"네임스페이스 '{namespace}'에 벡터가 없습니다")
                return []
            
            # 더미 쿼리로 모든 벡터 가져오기
            max_fetch = min(total_count, 10000)
            
            response = self.index.query(
                vector=[0.0] * 1536,
                top_k=max_fetch,
                include_metadata=True,
                include_values=True,  # 벡터 값도 포함
                namespace=namespace
            )
            
            vectors = []
            for match in response.matches:
                vector_data = {
                    "id": match.id,
                    "values": match.values,
                    "metadata": match.metadata
                }
                vectors.append(vector_data)
            
            logger.info(f"네임스페이스 '{namespace}'에서 {len(vectors)}개 벡터 추출 완료")
            return vectors
            
        except Exception as e:
            logger.error(f"벡터 추출 실패: {namespace}, 오류: {e}")
            return []
    
    def modify_vector_ids(self, vectors: List[Dict], prefix: str) -> List[Dict]:
        """벡터 ID에 모델 prefix 추가 (충돌 방지)"""
        for vector in vectors:
            original_id = vector["id"]
            vector["id"] = f"{prefix}_{original_id}"
            
            # 메타데이터에 원본 ID 보존
            if "metadata" not in vector:
                vector["metadata"] = {}
            vector["metadata"]["original_id"] = original_id
            vector["metadata"]["model_prefix"] = prefix
        
        return vectors
    
    def create_combined_namespace(self, dry_run: bool = False) -> bool:
        """통합 네임스페이스 생성"""
        try:
            logger.info("=== 통합 네임스페이스 생성 시작 ===")
            
            # 1. HQ 벡터들 가져오기
            hq_namespace = "pcos-rag-gpt_4o"
            hq_vectors = self.get_all_vectors_from_namespace(hq_namespace)
            
            if not hq_vectors:
                logger.error(f"HQ 네임스페이스 '{hq_namespace}'에서 벡터를 가져올 수 없습니다")
                return False
            
            # 2. LQ 벡터들 가져오기
            lq_namespace = "pcos-rag-gpt_3.5_turbo"
            lq_vectors = self.get_all_vectors_from_namespace(lq_namespace)
            
            if not lq_vectors:
                logger.error(f"LQ 네임스페이스 '{lq_namespace}'에서 벡터를 가져올 수 없습니다")
                return False
            
            # 3. 벡터 ID 수정 (충돌 방지)
            hq_vectors = self.modify_vector_ids(hq_vectors, "hq")
            lq_vectors = self.modify_vector_ids(lq_vectors, "lq")
            
            # 4. 통합 벡터 리스트 생성
            all_vectors = hq_vectors + lq_vectors
            
            logger.info(f"통합할 벡터 수: HQ {len(hq_vectors)}개 + LQ {len(lq_vectors)}개 = 총 {len(all_vectors)}개")
            
            if dry_run:
                logger.info("DRY RUN 모드: 실제 업로드하지 않음")
                return True
            
            # 5. 기존 통합 네임스페이스 삭제 (있다면)
            try:
                logger.info(f"기존 네임스페이스 '{self.combined_namespace}' 정리 중...")
                self.index.delete(delete_all=True, namespace=self.combined_namespace)
                logger.info("기존 네임스페이스 삭제 완료")
            except Exception as e:
                logger.info(f"기존 네임스페이스가 없거나 삭제 실패 (정상): {e}")
            
            # 6. 배치별로 업로드
            batch_size = 100  # Pinecone 권장 배치 크기
            total_batches = (len(all_vectors) + batch_size - 1) // batch_size
            
            for i in range(0, len(all_vectors), batch_size):
                batch = all_vectors[i:i + batch_size]
                batch_num = i // batch_size + 1
                
                logger.info(f"배치 {batch_num}/{total_batches} 업로드 중... ({len(batch)}개 벡터)")
                
                # Pinecone upsert 형식으로 변환
                vectors_to_upsert = []
                for vector in batch:
                    vectors_to_upsert.append({
                        "id": vector["id"],
                        "values": vector["values"],
                        "metadata": vector["metadata"]
                    })
                
                self.index.upsert(vectors=vectors_to_upsert, namespace=self.combined_namespace)
                
                # 진행률 로깅
                if batch_num % 10 == 0:
                    progress = (batch_num / total_batches) * 100
                    logger.info(f"진행률: {progress:.1f}% ({batch_num}/{total_batches} 배치 완료)")
            
            logger.info("=== 통합 네임스페이스 생성 완료 ===")
            
            # 7. 결과 확인
            stats = self.index.describe_index_stats()
            combined_info = stats.namespaces.get(self.combined_namespace, {})
            combined_count = combined_info.vector_count if hasattr(combined_info, 'vector_count') else 0
            
            logger.info(f"통합 네임스페이스 '{self.combined_namespace}': {combined_count}개 벡터")
            logger.info(f"예상 벡터 수: {len(all_vectors)}개")
            
            if combined_count == len(all_vectors):
                logger.info("✅ 통합 네임스페이스 생성 성공!")
                return True
            else:
                logger.warning(f"⚠️ 벡터 수 불일치: 예상 {len(all_vectors)}개, 실제 {combined_count}개")
                return False
                
        except Exception as e:
            logger.error(f"통합 네임스페이스 생성 실패: {e}")
            return False
    
    def verify_combined_namespace(self) -> Dict[str, Any]:
        """통합 네임스페이스 검증"""
        try:
            logger.info("=== 통합 네임스페이스 검증 ===")
            
            # 통계 확인
            stats = self.index.describe_index_stats()
            combined_info = stats.namespaces.get(self.combined_namespace, {})
            combined_count = combined_info.vector_count if hasattr(combined_info, 'vector_count') else 0
            
            # 샘플 쿼리로 검증
            response = self.index.query(
                vector=[0.0] * 1536,
                top_k=20,
                include_metadata=True,
                namespace=self.combined_namespace
            )
            
            # 모델별 분포 확인
            model_distribution = {}
            for match in response.matches:
                model_version = match.metadata.get("model_version", "unknown")
                model_distribution[model_version] = model_distribution.get(model_version, 0) + 1
            
            verification_result = {
                "namespace": self.combined_namespace,
                "total_vectors": combined_count,
                "sample_size": len(response.matches),
                "model_distribution": model_distribution,
                "has_hq_vectors": any(match.id.startswith("hq_") for match in response.matches),
                "has_lq_vectors": any(match.id.startswith("lq_") for match in response.matches)
            }
            
            logger.info(f"통합 네임스페이스 검증 결과:")
            logger.info(f"  총 벡터 수: {combined_count}")
            logger.info(f"  모델 분포: {model_distribution}")
            logger.info(f"  HQ 벡터 존재: {verification_result['has_hq_vectors']}")
            logger.info(f"  LQ 벡터 존재: {verification_result['has_lq_vectors']}")
            
            return verification_result
            
        except Exception as e:
            logger.error(f"통합 네임스페이스 검증 실패: {e}")
            return {"error": str(e)}

def main():
    """메인 실행 함수"""
    creator = CombinedNamespaceCreator()
    
    # Pinecone 연결
    if not creator.connect_pinecone():
        logger.error("Pinecone 연결 실패, 종료")
        return
    
    # 명령행 인자 처리
    import argparse
    parser = argparse.ArgumentParser(description="Pinecone 통합 네임스페이스 생성")
    parser.add_argument("--dry-run", action="store_true", help="실제 업로드 없이 테스트만")
    parser.add_argument("--verify-only", action="store_true", help="기존 네임스페이스 검증만")
    
    args = parser.parse_args()
    
    if args.verify_only:
        # 검증만 실행
        result = creator.verify_combined_namespace()
        if "error" not in result:
            logger.info("검증 완료!")
        else:
            logger.error("검증 실패!")
    else:
        # 네임스페이스 생성
        success = creator.create_combined_namespace(dry_run=args.dry_run)
        
        if success and not args.dry_run:
            # 생성 후 검증
            creator.verify_combined_namespace()

if __name__ == "__main__":
    main()