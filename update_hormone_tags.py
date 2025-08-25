#!/usr/bin/env python3
"""
Pinecone에 저장된 호르몬 태그 이름 변경 스크립트
PROLACTIN → prolactin, Hunger hormone (Ghrelin) → ghrelin으로 변경
"""

import asyncio
import os
import logging
from typing import List, Dict, Any, Set
import pinecone
from dotenv import load_dotenv
import json
from datetime import datetime

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HormoneTagUpdater:
    def __init__(self):
        self.api_key = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "pcos-papers")
        
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY 환경변수가 설정되지 않았습니다.")
        
        # Pinecone 초기화
        from pinecone import Pinecone
        self.pc = Pinecone(api_key=self.api_key)
        
        # 사용 가능한 인덱스 확인
        try:
            indexes = self.pc.list_indexes()
            print(f"사용 가능한 인덱스: {indexes.names()}")
            
            if self.index_name not in indexes.names():
                if indexes.names():
                    self.index_name = indexes.names()[0]
                    print(f"인덱스를 '{self.index_name}'로 변경했습니다.")
                else:
                    raise ValueError("사용 가능한 인덱스가 없습니다.")
        except Exception as e:
            print(f"인덱스 목록 조회 실패: {e}")
            raise
        
        self.index = self.pc.Index(self.index_name)
        
        # 호르몬 태그 매핑
        self.hormone_mapping = {
            "PROLACTIN": "prolactin",
            "Hunger hormone (Ghrelin)": "ghrelin"
        }
        
        # 변경 로그
        self.change_log = {
            "timestamp": datetime.now().isoformat(),
            "total_vectors_processed": 0,
            "vectors_updated": 0,
            "changes": []
        }
    
    async def get_all_vectors(self, namespace: str = None) -> List[Dict[str, Any]]:
        """모든 벡터 조회"""
        try:
            logger.info("모든 벡터 조회 중...")
            
            # 인덱스 정보 조회
            index_stats = self.index.describe_index_stats()
            print(f"인덱스 통계: {index_stats}")
            
            # 차원 확인
            dimension = 1536
            if hasattr(index_stats, 'dimension'):
                dimension = index_stats.dimension
            
            # 더미 벡터로 전체 조회
            dummy_vector = [0.0] * dimension
            
            response = self.index.query(
                vector=dummy_vector,
                top_k=10000,  # 최대 10000개
                include_metadata=True,
                include_values=True,
                namespace=namespace
            )
            
            vectors = []
            for match in response.matches:
                vectors.append({
                    "id": match.id,
                    "values": match.values,
                    "metadata": match.metadata or {},
                    "namespace": namespace
                })
            
            logger.info(f"총 {len(vectors)}개 벡터 조회 완료")
            return vectors
            
        except Exception as e:
            logger.error(f"벡터 조회 실패: {e}")
            return []
    
    def find_vectors_with_old_hormone_tags(self, vectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """이전 호르몬 태그가 있는 벡터들 찾기"""
        vectors_to_update = []
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            needs_update = False
            changes = []
            
            # hormone_focus 필드 확인
            if "hormone_focus" in metadata:
                hormone_focus = metadata["hormone_focus"]
                if isinstance(hormone_focus, list):
                    new_hormone_focus = []
                    for hormone in hormone_focus:
                        if hormone in self.hormone_mapping:
                            new_hormone = self.hormone_mapping[hormone]
                            new_hormone_focus.append(new_hormone)
                            changes.append(f"hormone_focus: {hormone} → {new_hormone}")
                            needs_update = True
                        else:
                            new_hormone_focus.append(hormone)
                    
                    if needs_update:
                        vector["new_metadata"] = metadata.copy()
                        vector["new_metadata"]["hormone_focus"] = new_hormone_focus
                        vector["changes"] = changes
                        vectors_to_update.append(vector)
            
            # tags 필드 확인 (하위 호환성)
            if "tags" in metadata:
                tags = metadata["tags"]
                if isinstance(tags, list):
                    new_tags = []
                    for tag in tags:
                        if tag in self.hormone_mapping:
                            new_tag = self.hormone_mapping[tag]
                            new_tags.append(new_tag)
                            changes.append(f"tags: {tag} → {new_tag}")
                            needs_update = True
                        else:
                            new_tags.append(tag)
                    
                    if needs_update:
                        if "new_metadata" not in vector:
                            vector["new_metadata"] = metadata.copy()
                        vector["new_metadata"]["tags"] = new_tags
                        if "changes" not in vector:
                            vector["changes"] = []
                        vector["changes"].extend(changes)
                        if vector not in vectors_to_update:
                            vectors_to_update.append(vector)
        
        logger.info(f"업데이트가 필요한 벡터: {len(vectors_to_update)}개")
        return vectors_to_update
    
    async def update_vectors(self, vectors: List[Dict[str, Any]]) -> int:
        """벡터 업데이트"""
        if not vectors:
            logger.info("업데이트할 벡터가 없습니다.")
            return 0
        
        try:
            logger.info(f"{len(vectors)}개 벡터 업데이트 시작...")
            
            # 배치 크기 설정
            batch_size = 100
            total_updated = 0
            
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i + batch_size]
                
                # 배치 업데이트 준비
                updated_batch = []
                for vector in batch:
                    updated_batch.append({
                        "id": vector["id"],
                        "values": vector["values"],
                        "metadata": vector["new_metadata"]
                    })
                    
                    # 변경 로그에 추가
                    self.change_log["changes"].append({
                        "vector_id": vector["id"],
                        "changes": vector["changes"],
                        "namespace": vector.get("namespace", "unknown")
                    })
                
                # 배치 업데이트
                try:
                    self.index.upsert(vectors=updated_batch, namespace=vector.get("namespace"))
                    total_updated += len(batch)
                    logger.info(f"배치 {i//batch_size + 1} 업데이트 완료 ({len(batch)}개)")
                    
                except Exception as e:
                    logger.error(f"배치 {i//batch_size + 1} 업데이트 실패: {e}")
                    continue
            
            logger.info(f"총 {total_updated}개 벡터 업데이트 완료")
            return total_updated
            
        except Exception as e:
            logger.error(f"벡터 업데이트 실패: {e}")
            return 0
    
    def save_change_log(self, filename: str = "hormone_tag_update_log.json"):
        """변경 로그 저장"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(self.change_log, f, ensure_ascii=False, indent=2)
            logger.info(f"변경 로그 저장 완료: {filename}")
        except Exception as e:
            logger.error(f"변경 로그 저장 실패: {e}")
    
    async def verify_update(self, namespace: str = None) -> Dict[str, Any]:
        """업데이트 결과 확인"""
        try:
            logger.info("업데이트 결과 확인 중...")
            
            # 샘플 벡터 조회
            sample_vectors = await self.get_all_vectors(namespace)
            
            # 호르몬 태그 통계
            hormone_stats = {}
            old_tags_found = 0
            new_tags_found = 0
            
            for vector in sample_vectors:
                metadata = vector.get("metadata", {})
                
                # hormone_focus 확인
                if "hormone_focus" in metadata:
                    hormone_focus = metadata["hormone_focus"]
                    if isinstance(hormone_focus, list):
                        for hormone in hormone_focus:
                            hormone_stats[hormone] = hormone_stats.get(hormone, 0) + 1
                            if hormone in self.hormone_mapping.values():
                                new_tags_found += 1
                            elif hormone in self.hormone_mapping.keys():
                                old_tags_found += 1
                
                # tags 확인
                if "tags" in metadata:
                    tags = metadata["tags"]
                    if isinstance(tags, list):
                        for tag in tags:
                            hormone_stats[tag] = hormone_stats.get(tag, 0) + 1
                            if tag in self.hormone_mapping.values():
                                new_tags_found += 1
                            elif tag in self.hormone_mapping.keys():
                                old_tags_found += 1
            
            result = {
                "total_vectors_checked": len(sample_vectors),
                "old_tags_found": old_tags_found,
                "new_tags_found": new_tags_found,
                "hormone_stats": hormone_stats
            }
            
            logger.info(f"확인 결과: {result}")
            return result
            
        except Exception as e:
            logger.error(f"확인 실패: {e}")
            return {}

async def main():
    """메인 실행 함수"""
    try:
        logger.info("=== Pinecone 호르몬 태그 업데이트 스크립트 시작 ===")
        
        # 업데이터 초기화
        updater = HormoneTagUpdater()
        
        print(f"변경할 호르몬 태그:")
        for old_tag, new_tag in updater.hormone_mapping.items():
            print(f"  {old_tag} → {new_tag}")
        
        # 사용자 확인
        print(f"\n계속하시겠습니까? (y/N): ", end="")
        user_input = input().strip().lower()
        if user_input != 'y':
            logger.info("사용자가 취소했습니다.")
            return
        
        # 네임스페이스 목록 확인
        index_stats = updater.index.describe_index_stats()
        namespaces = list(index_stats.get("namespaces", {}).keys())
        
        if not namespaces:
            namespaces = [None]  # 기본 네임스페이스
        
        print(f"처리할 네임스페이스: {namespaces}")
        
        total_vectors_processed = 0
        total_vectors_updated = 0
        
        # 각 네임스페이스 처리
        for namespace in namespaces:
            logger.info(f"\n=== 네임스페이스 '{namespace}' 처리 중 ===")
            
            # 1. 모든 벡터 조회
            all_vectors = await updater.get_all_vectors(namespace)
            total_vectors_processed += len(all_vectors)
            
            if not all_vectors:
                logger.info(f"네임스페이스 '{namespace}'에 벡터가 없습니다.")
                continue
            
            # 2. 업데이트가 필요한 벡터들 찾기
            vectors_to_update = updater.find_vectors_with_old_hormone_tags(all_vectors)
            
            if not vectors_to_update:
                logger.info(f"네임스페이스 '{namespace}'에서 업데이트할 벡터가 없습니다.")
                continue
            
            # 3. 벡터 업데이트
            updated_count = await updater.update_vectors(vectors_to_update)
            total_vectors_updated += updated_count
            
            # 4. 결과 확인
            verification = await updater.verify_update(namespace)
            print(f"네임스페이스 '{namespace}' 결과:")
            print(f"  - 처리된 벡터: {len(all_vectors)}개")
            print(f"  - 업데이트된 벡터: {updated_count}개")
            print(f"  - 이전 태그 발견: {verification.get('old_tags_found', 0)}개")
            print(f"  - 새 태그 발견: {verification.get('new_tags_found', 0)}개")
        
        # 5. 전체 결과 출력
        print(f"\n=== 전체 업데이트 완료 ===")
        print(f"총 처리된 벡터: {total_vectors_processed}개")
        print(f"총 업데이트된 벡터: {total_vectors_updated}개")
        
        # 6. 변경 로그 저장
        updater.change_log["total_vectors_processed"] = total_vectors_processed
        updater.change_log["vectors_updated"] = total_vectors_updated
        updater.save_change_log()
        
        print(f"\n✅ 호르몬 태그 업데이트 완료!")
        
    except Exception as e:
        logger.error(f"스크립트 실행 실패: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
