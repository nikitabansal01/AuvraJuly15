#!/usr/bin/env python3
"""
default와 pcos-rag-gpt4o 네임스페이스에서 메타데이터의 chunk_id를 제거하는 스크립트
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

class ChunkIdRemover:
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
        
        # 처리할 네임스페이스들
        self.namespaces = ["", "pcos-rag-gpt4o"]  # default와 pcos-rag-gpt4o
        
        logger.info(f"처리할 네임스페이스: {self.namespaces}")
    
    async def get_vectors_with_chunk_id(self, namespace: str) -> List[Dict[str, Any]]:
        """chunk_id가 있는 벡터들 조회"""
        try:
            logger.info(f"네임스페이스 '{namespace}'에서 chunk_id가 있는 벡터 조회 중...")
            
            # 인덱스 통계 확인
            stats = self.index.describe_index_stats()
            namespace_count = stats.get("namespaces", {}).get(namespace, {}).get("vector_count", 0)
            logger.info(f"네임스페이스 '{namespace}' 벡터 수: {namespace_count}")
            
            if namespace_count == 0:
                logger.warning(f"네임스페이스 '{namespace}'에 벡터가 없습니다.")
                return []
            
            # 인덱스 차원 확인
            dimension = stats.get("dimension", 1536)
            
            # 모든 벡터 조회
            response = self.index.query(
                vector=[0.0] * dimension,
                top_k=10000,
                include_metadata=True,
                include_values=True,
                namespace=namespace
            )
            
            vectors_with_chunk_id = []
            for match in response.matches:
                metadata = match.metadata or {}
                if "chunk_id" in metadata:
                    vector_data = {
                        "id": match.id,
                        "values": match.values,
                        "metadata": metadata
                    }
                    vectors_with_chunk_id.append(vector_data)
            
            logger.info(f"네임스페이스 '{namespace}'에서 chunk_id가 있는 벡터: {len(vectors_with_chunk_id)}개")
            return vectors_with_chunk_id
            
        except Exception as e:
            logger.error(f"벡터 조회 실패 (네임스페이스 '{namespace}'): {e}")
            return []
    
    async def remove_chunk_id_from_vectors(self, vectors: List[Dict[str, Any]], namespace: str) -> int:
        """벡터들에서 chunk_id 제거"""
        if not vectors:
            logger.info(f"네임스페이스 '{namespace}'에서 제거할 벡터가 없습니다.")
            return 0
        
        try:
            logger.info(f"네임스페이스 '{namespace}'에서 {len(vectors)}개 벡터의 chunk_id 제거 중...")
            
            # 배치 크기 설정
            batch_size = 100
            total_updated = 0
            
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i + batch_size]
                
                # 배치 준비
                batch_data = []
                for vector in batch:
                    # 벡터 값이 있는지 확인
                    if not vector.get("values") or len(vector["values"]) == 0:
                        logger.warning(f"벡터 {vector['id']}에 값이 없습니다. 건너뜁니다.")
                        continue
                    
                    # chunk_id 제거
                    metadata = vector["metadata"].copy()
                    if "chunk_id" in metadata:
                        del metadata["chunk_id"]
                        logger.debug(f"chunk_id 제거: {vector['id']}")
                    
                    batch_data.append({
                        "id": vector["id"],
                        "values": vector["values"],
                        "metadata": metadata
                    })
                
                # 배치 업데이트
                try:
                    self.index.upsert(
                        vectors=batch_data,
                        namespace=namespace
                    )
                    
                    total_updated += len(batch_data)
                    logger.info(f"배치 업데이트 완료: {i+1}-{min(i+batch_size, len(vectors))}/{len(vectors)}")
                    
                    # API 제한 방지를 위한 잠시 대기
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"배치 업데이트 실패 (배치 {i//batch_size + 1}): {e}")
                    continue
            
            logger.info(f"네임스페이스 '{namespace}'에서 총 {total_updated}개 벡터 업데이트 완료")
            return total_updated
            
        except Exception as e:
            logger.error(f"chunk_id 제거 실패 (네임스페이스 '{namespace}'): {e}")
            return 0
    
    async def verify_removal(self, namespace: str) -> Dict[str, Any]:
        """chunk_id 제거 확인"""
        try:
            logger.info(f"네임스페이스 '{namespace}'에서 chunk_id 제거 확인 중...")
            
            # 잠시 대기
            await asyncio.sleep(2)
            
            # 벡터 조회
            vectors = await self.get_vectors_with_chunk_id(namespace)
            
            # 통계 확인
            stats = self.index.describe_index_stats()
            total_count = stats.get("namespaces", {}).get(namespace, {}).get("vector_count", 0)
            
            result = {
                "namespace": namespace,
                "total_vectors": total_count,
                "vectors_with_chunk_id": len(vectors),
                "removal_successful": len(vectors) == 0
            }
            
            logger.info(f"확인 결과 (네임스페이스 '{namespace}'): {result}")
            return result
            
        except Exception as e:
            logger.error(f"확인 실패 (네임스페이스 '{namespace}'): {e}")
            return {}
    
    def save_removal_log(self, removal_data: Dict[str, Any]):
        """제거 로그 저장"""
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "removal_data": removal_data
        }
        
        with open("chunk_id_removal_log.json", "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        logger.info("제거 로그가 chunk_id_removal_log.json에 저장되었습니다.")

async def main():
    """메인 실행 함수"""
    try:
        logger.info("=== chunk_id 제거 스크립트 시작 ===")
        
        # 제거기 초기화
        remover = ChunkIdRemover()
        
        total_removed = 0
        removal_results = {}
        
        # 각 네임스페이스 처리
        for namespace in remover.namespaces:
            logger.info(f"\n=== 네임스페이스 '{namespace}' 처리 중 ===")
            
            # 1. chunk_id가 있는 벡터 조회
            vectors = await remover.get_vectors_with_chunk_id(namespace)
            
            if not vectors:
                logger.info(f"네임스페이스 '{namespace}'에서 제거할 chunk_id가 없습니다.")
                removal_results[namespace] = {"removed_count": 0, "status": "no_chunk_id_found"}
                continue
            
            # 2. 사용자 확인
            print(f"\n네임스페이스 '{namespace}'에서 {len(vectors)}개 벡터의 chunk_id를 제거합니다.")
            
            confirm = input("계속하시겠습니까? (y/N): ").strip().lower()
            if confirm != 'y':
                logger.info(f"네임스페이스 '{namespace}' 처리를 건너뜁니다.")
                removal_results[namespace] = {"removed_count": 0, "status": "skipped"}
                continue
            
            # 3. chunk_id 제거
            removed_count = await remover.remove_chunk_id_from_vectors(vectors, namespace)
            
            # 4. 제거 확인
            verification = await remover.verify_removal(namespace)
            
            total_removed += removed_count
            removal_results[namespace] = {
                "removed_count": removed_count,
                "verification": verification,
                "status": "completed"
            }
        
        # 5. 최종 결과 출력
        print(f"\n=== chunk_id 제거 완료 ===")
        print(f"총 제거된 벡터 수: {total_removed}")
        
        for namespace, result in removal_results.items():
            print(f"네임스페이스 '{namespace}': {result['removed_count']}개 제거")
        
        # 6. 로그 저장
        remover.save_removal_log(removal_results)
        
        logger.info("=== chunk_id 제거 스크립트 완료 ===")
        
    except Exception as e:
        logger.error(f"스크립트 실행 실패: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
