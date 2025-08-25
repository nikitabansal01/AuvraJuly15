#!/usr/bin/env python3
"""
Pinecone 기존 벡터들에 model_version 메타데이터 추가 스크립트
임시 스크립트 - 사용 후 삭제 가능
"""

import asyncio
import os
import logging
from typing import List, Dict, Any
import pinecone
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PineconeUpdater:
    def __init__(self):
        self.api_key = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "pcos-papers")
        self.namespace = "pcos-rag"
        
        # 사용 가능한 인덱스 목록 확인
        from pinecone import Pinecone
        self.pc = Pinecone(api_key=self.api_key)
        
        try:
            # 인덱스 목록 조회
            indexes = self.pc.list_indexes()
            print(f"사용 가능한 인덱스: {indexes.names()}")
            
            # 첫 번째 인덱스 사용 (또는 지정된 인덱스)
            if self.index_name not in indexes.names():
                if indexes.names():
                    self.index_name = indexes.names()[0]
                    print(f"인덱스를 '{self.index_name}'로 변경했습니다.")
                else:
                    raise ValueError("사용 가능한 인덱스가 없습니다.")
                    
        except Exception as e:
            print(f"인덱스 목록 조회 실패: {e}")
            raise
        
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY 환경변수가 설정되지 않았습니다.")
        
        # Pinecone 초기화 (새로운 API)
        from pinecone import Pinecone
        self.pc = Pinecone(api_key=self.api_key)
        self.index = self.pc.Index(self.index_name)
        
    async def get_all_vectors(self) -> List[Dict[str, Any]]:
        """모든 벡터 조회"""
        try:
            logger.info("모든 벡터 조회 중...")
            
            # 인덱스 정보 조회로 차원 확인
            index_stats = self.index.describe_index_stats()
            print(f"인덱스 통계: {index_stats}")
            
            # 실제 차원 확인 (기본값 1536)
            dimension = 1536
            if hasattr(index_stats, 'dimension'):
                dimension = index_stats.dimension
            
            # 더미 벡터로 전체 조회
            dummy_vector = [0.0] * dimension
            
            response = self.index.query(
                vector=dummy_vector,
                top_k=10000,  # 최대 10000개
                include_metadata=True,
                include_values=True,  # 벡터 값도 포함
                namespace=self.namespace
            )
            
            vectors = []
            for match in response.matches:
                vectors.append({
                    "id": match.id,
                    "values": match.values,
                    "metadata": match.metadata
                })
            
            logger.info(f"총 {len(vectors)}개 벡터 조회 완료")
            return vectors
            
        except Exception as e:
            logger.error(f"벡터 조회 실패: {e}")
            return []
    
    def filter_vectors_without_model_version(self, vectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """model_version이 없는 벡터들만 필터링"""
        filtered_vectors = []
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            if "model_version" not in metadata:
                filtered_vectors.append(vector)
        
        logger.info(f"model_version이 없는 벡터: {len(filtered_vectors)}개")
        return filtered_vectors
    
    async def update_vectors_with_model_version(self, vectors: List[Dict[str, Any]], model_version: str = "gpt-4o") -> int:
        """벡터들에 model_version 추가"""
        if not vectors:
            logger.info("업데이트할 벡터가 없습니다.")
            return 0
        
        try:
            logger.info(f"{len(vectors)}개 벡터 업데이트 시작...")
            
            # 배치 크기 설정 (Pinecone 권장사항)
            batch_size = 100
            total_updated = 0
            
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i + batch_size]
                
                # 배치 내 각 벡터에 model_version 추가
                updated_batch = []
                for vector in batch:
                    # 벡터 값이 있는지 확인
                    if not vector.get("values") or len(vector["values"]) == 0:
                        logger.warning(f"벡터 {vector['id']}에 값이 없습니다. 건너뜁니다.")
                        continue
                    
                    updated_metadata = vector["metadata"].copy()
                    updated_metadata["model_version"] = model_version
                    updated_metadata["tagging_timestamp"] = "2024-01-01T00:00:00Z"  # 기본값
                    
                    updated_batch.append({
                        "id": vector["id"],
                        "values": vector["values"],
                        "metadata": updated_metadata
                    })
                
                # 배치 업데이트
                try:
                    self.index.upsert(
                        vectors=updated_batch,
                        namespace=self.namespace
                    )
                    
                    total_updated += len(updated_batch)
                    logger.info(f"배치 업데이트 완료: {i+1}-{min(i+batch_size, len(vectors))}/{len(vectors)}")
                    
                    # API 제한 방지를 위한 잠시 대기
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"배치 업데이트 실패 (배치 {i//batch_size + 1}): {e}")
                    continue
            
            logger.info(f"총 {total_updated}개 벡터 업데이트 완료")
            return total_updated
            
        except Exception as e:
            logger.error(f"벡터 업데이트 실패: {e}")
            return 0
    
    async def verify_update(self) -> Dict[str, Any]:
        """업데이트 결과 확인"""
        try:
            logger.info("업데이트 결과 확인 중...")
            
            # 전체 벡터 조회
            all_vectors = await self.get_all_vectors()
            
            # model_version 통계
            with_model_version = 0
            without_model_version = 0
            
            for vector in all_vectors:
                metadata = vector.get("metadata", {})
                if "model_version" in metadata:
                    with_model_version += 1
                else:
                    without_model_version += 1
            
            # model_version별 통계
            model_versions = {}
            for vector in all_vectors:
                metadata = vector.get("metadata", {})
                model_version = metadata.get("model_version", "unknown")
                model_versions[model_version] = model_versions.get(model_version, 0) + 1
            
            result = {
                "total_vectors": len(all_vectors),
                "with_model_version": with_model_version,
                "without_model_version": without_model_version,
                "model_versions": model_versions
            }
            
            logger.info(f"확인 결과: {result}")
            return result
            
        except Exception as e:
            logger.error(f"확인 실패: {e}")
            return {}

async def main():
    """메인 실행 함수"""
    try:
        logger.info("=== Pinecone model_version 업데이트 스크립트 시작 ===")
        
        # 업데이터 초기화
        updater = PineconeUpdater()
        
        # 1. 모든 벡터 조회
        all_vectors = await updater.get_all_vectors()
        
        if not all_vectors:
            logger.warning("업데이트할 벡터가 없습니다.")
            return
        
        # 2. model_version이 없는 벡터들 필터링
        vectors_to_update = updater.filter_vectors_without_model_version(all_vectors)
        
        if not vectors_to_update:
            logger.info("모든 벡터에 이미 model_version이 있습니다.")
            return
        
        # 3. 사용자 확인
        print(f"\n총 {len(all_vectors)}개 벡터 중 {len(vectors_to_update)}개에 model_version을 추가합니다.")
        print("계속하시겠습니까? (y/N): ", end="")
        
        user_input = input().strip().lower()
        if user_input != 'y':
            logger.info("사용자가 취소했습니다.")
            return
        
        # 4. 벡터 업데이트 (사용자 확인)
        print(f"\n현재 설정된 모델명: gpt-4o")
        print("다른 모델명을 사용하시겠습니까? (예: gpt-3.5-turbo, llama-3.3-70b)")
        print("기본값을 사용하려면 Enter를 누르세요: ", end="")
        
        user_model = input().strip()
        if not user_model:
            user_model = "gpt-4o"
        
        print(f"선택된 모델명: {user_model}")
        updated_count = await updater.update_vectors_with_model_version(vectors_to_update, user_model)
        
        # 5. 결과 확인
        verification = await updater.verify_update()
        
        # 6. 결과 출력
        print("\n=== 업데이트 완료 ===")
        print(f"총 벡터 수: {verification.get('total_vectors', 0)}")
        print(f"model_version 있는 벡터: {verification.get('with_model_version', 0)}")
        print(f"model_version 없는 벡터: {verification.get('without_model_version', 0)}")
        print(f"모델별 분포: {verification.get('model_versions', {})}")
        
        if updated_count > 0:
            print(f"\n✅ 성공적으로 {updated_count}개 벡터를 업데이트했습니다!")
        else:
            print("\n⚠️ 업데이트된 벡터가 없습니다.")
        
    except Exception as e:
        logger.error(f"스크립트 실행 실패: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
