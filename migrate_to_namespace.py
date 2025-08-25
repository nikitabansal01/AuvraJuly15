#!/usr/bin/env python3
"""
기존 default 네임스페이스의 벡터들을 pcos-rag-gpt4o 네임스페이스로 이동하는 스크립트
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

class NamespaceMigrator:
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
        
        # 네임스페이스 설정
        self.source_namespace = "pcos-rag-gpt4o"  # 기존 네임스페이스
        self.target_namespace = "pcos-rag-gpt_4o"  # 새로운 네임스페이스 (언더스코어 포함)
        
        logger.info(f"소스 네임스페이스: '{self.source_namespace}'")
        logger.info(f"타겟 네임스페이스: '{self.target_namespace}'")
    
    async def get_all_vectors_from_source(self) -> List[Dict[str, Any]]:
        """소스 네임스페이스에서 모든 벡터 조회"""
        try:
            logger.info("소스 네임스페이스에서 벡터 조회 중...")
            
            # 인덱스 통계 확인
            stats = self.index.describe_index_stats()
            logger.info(f"인덱스 통계: {stats}")
            
            # 소스 네임스페이스의 벡터 수 확인
            source_count = stats.get("namespaces", {}).get(self.source_namespace, {}).get("vector_count", 0)
            logger.info(f"소스 네임스페이스 벡터 수: {source_count}")
            
            if source_count == 0:
                logger.warning("소스 네임스페이스에 벡터가 없습니다.")
                return []
            
            # 인덱스 차원 확인
            dimension = stats.get("dimension", 1536)
            logger.info(f"벡터 차원: {dimension}")
            
            # 모든 벡터 조회 (큰 값으로 설정)
            response = self.index.query(
                vector=[0.0] * dimension,  # 더미 벡터
                top_k=10000,
                include_metadata=True,
                include_values=True,
                namespace=self.source_namespace
            )
            
            vectors = []
            for match in response.matches:
                vector_data = {
                    "id": match.id,
                    "values": match.values,
                    "metadata": match.metadata or {}
                }
                vectors.append(vector_data)
            
            logger.info(f"총 {len(vectors)}개의 벡터를 조회했습니다.")
            return vectors
            
        except Exception as e:
            logger.error(f"벡터 조회 실패: {e}")
            return []
    
    async def migrate_vectors_to_target(self, vectors: List[Dict[str, Any]]) -> int:
        """벡터들을 타겟 네임스페이스로 이동"""
        if not vectors:
            logger.info("이동할 벡터가 없습니다.")
            return 0
        
        try:
            logger.info(f"{len(vectors)}개 벡터를 타겟 네임스페이스로 이동 중...")
            
            # 배치 크기 설정
            batch_size = 100
            total_migrated = 0
            
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i + batch_size]
                
                # 배치 준비
                batch_data = []
                for vector in batch:
                    # 벡터 값이 있는지 확인
                    if not vector.get("values") or len(vector["values"]) == 0:
                        logger.warning(f"벡터 {vector['id']}에 값이 없습니다. 건너뜁니다.")
                        continue
                    
                    # 메타데이터에 migration 정보 추가
                    metadata = vector["metadata"].copy()
                    metadata["migrated_at"] = datetime.now().isoformat()
                    metadata["original_namespace"] = self.source_namespace
                    
                    batch_data.append({
                        "id": vector["id"],
                        "values": vector["values"],
                        "metadata": metadata
                    })
                
                # 타겟 네임스페이스에 배치 저장
                try:
                    self.index.upsert(
                        vectors=batch_data,
                        namespace=self.target_namespace
                    )
                    
                    total_migrated += len(batch_data)
                    logger.info(f"배치 이동 완료: {i+1}-{min(i+batch_size, len(vectors))}/{len(vectors)}")
                    
                    # API 제한 방지를 위한 잠시 대기
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"배치 이동 실패 (배치 {i//batch_size + 1}): {e}")
                    continue
            
            logger.info(f"총 {total_migrated}개 벡터 이동 완료")
            return total_migrated
            
        except Exception as e:
            logger.error(f"벡터 이동 실패: {e}")
            return 0
    
    async def verify_migration(self) -> Dict[str, Any]:
        """이동 결과 확인"""
        try:
            logger.info("이동 결과 확인 중...")
            
            # 잠시 대기 (Pinecone API 지연 고려)
            await asyncio.sleep(2)
            
            # 타겟 네임스페이스 통계 확인
            stats = self.index.describe_index_stats()
            logger.info(f"전체 인덱스 통계: {stats}")
            
            target_count = stats.get("namespaces", {}).get(self.target_namespace, {}).get("vector_count", 0)
            source_count = stats.get("namespaces", {}).get(self.source_namespace, {}).get("vector_count", 0)
            
            logger.info(f"타겟 네임스페이스 '{self.target_namespace}' 벡터 수: {target_count}")
            logger.info(f"소스 네임스페이스 '{self.source_namespace}' 벡터 수: {source_count}")
            
            # 타겟 네임스페이스에서 샘플 벡터 조회
            try:
                sample_vectors = self.index.query(
                    vector=[0.0] * 1536,
                    top_k=5,
                    include_metadata=True,
                    namespace=self.target_namespace
                )
                
                sample_data = [
                    {
                        "id": match.id,
                        "metadata": match.metadata
                    }
                    for match in sample_vectors.matches
                ]
                logger.info(f"샘플 벡터 조회 성공: {len(sample_data)}개")
                
            except Exception as e:
                logger.error(f"샘플 벡터 조회 실패: {e}")
                sample_data = []
            
            result = {
                "source_namespace_count": source_count,
                "target_namespace_count": target_count,
                "migration_successful": target_count > 0,
                "sample_vectors": sample_data
            }
            
            logger.info(f"확인 결과: {result}")
            return result
            
        except Exception as e:
            logger.error(f"확인 실패: {e}")
            return {}
    
    async def delete_source_vectors(self, vectors: List[Dict[str, Any]]) -> int:
        """소스 네임스페이스의 벡터들 삭제"""
        if not vectors:
            logger.info("삭제할 벡터가 없습니다.")
            return 0
        
        try:
            logger.info(f"소스 네임스페이스에서 {len(vectors)}개 벡터 삭제 중...")
            
            # 벡터 ID 추출
            vector_ids = [vector["id"] for vector in vectors]
            
            # 배치 크기 설정
            batch_size = 100
            total_deleted = 0
            
            for i in range(0, len(vector_ids), batch_size):
                batch_ids = vector_ids[i:i + batch_size]
                
                try:
                    self.index.delete(
                        ids=batch_ids,
                        namespace=self.source_namespace
                    )
                    
                    total_deleted += len(batch_ids)
                    logger.info(f"배치 삭제 완료: {i+1}-{min(i+batch_size, len(vector_ids))}/{len(vector_ids)}")
                    
                    # API 제한 방지를 위한 잠시 대기
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"배치 삭제 실패 (배치 {i//batch_size + 1}): {e}")
                    continue
            
            logger.info(f"총 {total_deleted}개 벡터 삭제 완료")
            return total_deleted
            
        except Exception as e:
            logger.error(f"벡터 삭제 실패: {e}")
            return 0
    
    def save_migration_log(self, migration_data: Dict[str, Any]):
        """이동 로그 저장"""
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "source_namespace": self.source_namespace,
            "target_namespace": self.target_namespace,
            "migration_data": migration_data
        }
        
        with open("namespace_migration_log.json", "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        logger.info("이동 로그가 namespace_migration_log.json에 저장되었습니다.")

async def main():
    """메인 실행 함수"""
    try:
        logger.info("=== 네임스페이스 이동 스크립트 시작 ===")
        
        # 마이그레이터 초기화
        migrator = NamespaceMigrator()
        
        # 1. 소스 네임스페이스에서 모든 벡터 조회
        source_vectors = await migrator.get_all_vectors_from_source()
        
        if not source_vectors:
            logger.warning("이동할 벡터가 없습니다.")
            return
        
        # 2. 사용자 확인
        print(f"\n총 {len(source_vectors)}개 벡터를 '{migrator.source_namespace}'에서 '{migrator.target_namespace}'로 이동합니다.")
        print("이 작업은 되돌릴 수 없습니다!")
        
        confirm = input("\n계속하시겠습니까? (y/N): ").strip().lower()
        if confirm != 'y':
            logger.info("사용자가 작업을 취소했습니다.")
            return
        
        # 3. 벡터들을 타겟 네임스페이스로 이동
        migrated_count = await migrator.migrate_vectors_to_target(source_vectors)
        
        if migrated_count == 0:
            logger.error("벡터 이동에 실패했습니다.")
            return
        
        # 4. 이동 결과 확인
        verification = await migrator.verify_migration()
        
        if not verification.get("migration_successful"):
            logger.error("이동 확인에 실패했습니다.")
            return
        
        # 5. 소스 네임스페이스 벡터 삭제 여부 확인
        print(f"\n이동이 완료되었습니다!")
        print(f"이동된 벡터 수: {migrated_count}")
        print(f"타겟 네임스페이스 벡터 수: {verification.get('target_namespace_count', 0)}")
        
        delete_confirm = input("\n소스 네임스페이스의 벡터들을 삭제하시겠습니까? (y/N): ").strip().lower()
        
        if delete_confirm == 'y':
            deleted_count = await migrator.delete_source_vectors(source_vectors)
            logger.info(f"소스 네임스페이스에서 {deleted_count}개 벡터 삭제 완료")
        else:
            logger.info("소스 네임스페이스 벡터 삭제를 건너뜁니다.")
        
        # 6. 로그 저장
        migration_data = {
            "total_vectors": len(source_vectors),
            "migrated_count": migrated_count,
            "verification": verification
        }
        migrator.save_migration_log(migration_data)
        
        logger.info("=== 네임스페이스 이동 완료 ===")
        
    except Exception as e:
        logger.error(f"스크립트 실행 실패: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
