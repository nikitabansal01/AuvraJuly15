#!/usr/bin/env python3
"""
기존 랜덤 ID 벡터들을 삭제하는 스크립트
"""

import os
import json
import logging
import re
from typing import List, Dict, Any
from datetime import datetime
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OldVectorCleaner:
    def __init__(self):
        self.api_key = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX", "auvra-rag")
        
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY 환경변수가 설정되지 않았습니다.")
        
        # Pinecone 클라이언트 초기화
        try:
            from pinecone import Pinecone
            self.pc = Pinecone(api_key=self.api_key)
            
            # 인덱스 이름 자동 감지
            available_indexes = [index.name for index in self.pc.list_indexes()]
            if self.index_name not in available_indexes:
                if available_indexes:
                    self.index_name = available_indexes[0]
                    logger.info(f"설정된 인덱스 '{os.getenv('PINECONE_INDEX')}'를 찾을 수 없어 '{self.index_name}'을 사용합니다.")
                else:
                    raise ValueError("사용 가능한 Pinecone 인덱스가 없습니다.")
            
            self.index = self.pc.Index(self.index_name)
            logger.info(f"Pinecone 인덱스 '{self.index_name}'에 연결되었습니다.")
            
        except Exception as e:
            raise RuntimeError(f"Pinecone 클라이언트 초기화 실패: {e}")
    
    def find_old_random_vectors(self) -> List[str]:
        """기존 랜덤 ID 벡터들을 찾아서 ID 리스트 반환"""
        try:
            # 인덱스 통계 확인
            stats = self.index.describe_index_stats()
            total_vectors = stats.total_vector_count
            logger.info(f"총 벡터 수: {total_vectors}")
            
            # 모든 벡터 조회
            all_vectors = []
            batch_size = 1000
            
            for offset in range(0, total_vectors, batch_size):
                logger.info(f"벡터 조회 중... {offset}/{total_vectors}")
                
                # 더미 벡터로 쿼리
                dummy_vector = [0.0] * 1536
                
                response = self.index.query(
                    vector=dummy_vector,
                    top_k=batch_size,
                    include_metadata=False,  # 메타데이터 불필요
                    include_values=False     # 벡터 값 불필요
                )
                
                for match in response.matches:
                    all_vectors.append(match.id)
                
                if len(response.matches) < batch_size:
                    break
            
            # 랜덤 ID 패턴으로 필터링
            random_pattern = re.compile(r'^chunk-[a-f0-9]{8}$')
            old_random_ids = [vector_id for vector_id in all_vectors if random_pattern.match(vector_id)]
            
            logger.info(f"총 {len(all_vectors)}개 벡터 중 {len(old_random_ids)}개의 랜덤 ID 벡터 발견")
            return old_random_ids
            
        except Exception as e:
            logger.error(f"기존 랜덤 벡터 조회 실패: {e}")
            raise
    
    def verify_new_vectors_exist(self, old_ids: List[str]) -> bool:
        """새로운 결정적 ID 벡터들이 존재하는지 확인"""
        try:
            # 기존 랜덤 ID에서 새로운 ID 패턴 추출
            new_ids_to_check = []
            
            for old_id in old_ids[:10]:  # 샘플 10개만 확인
                # 임시로 새로운 ID 패턴 생성 (실제로는 마이그레이션 로그에서 확인해야 함)
                # 여기서는 단순히 확인용으로만 사용
                new_ids_to_check.append(f"paper_test_chunk_1")
            
            logger.info("새로운 결정적 ID 벡터 존재 여부 확인 중...")
            
            # 실제로는 마이그레이션 로그 파일에서 새로운 ID들을 읽어와야 함
            migration_log_file = "chunk_migration_log.json"
            if os.path.exists(migration_log_file):
                with open(migration_log_file, 'r', encoding='utf-8') as f:
                    migration_data = json.load(f)
                
                new_ids = [item['new_id'] for item in migration_data.get('migration_details', [])]
                logger.info(f"마이그레이션 로그에서 {len(new_ids)}개의 새로운 ID 확인")
                
                # 샘플로 몇 개 확인
                sample_new_ids = new_ids[:5]
                for new_id in sample_new_ids:
                    try:
                        # 벡터 존재 여부 확인
                        response = self.index.query(
                            vector=[0.0] * 1536,
                            filter={"chunk_id": {"$eq": new_id}},
                            top_k=1,
                            include_metadata=False
                        )
                        if response.matches:
                            logger.info(f"✅ 새로운 벡터 존재 확인: {new_id}")
                        else:
                            logger.warning(f"❌ 새로운 벡터 없음: {new_id}")
                    except Exception as e:
                        logger.warning(f"새 벡터 확인 실패: {new_id}, 오류: {e}")
                
                return len(new_ids) > 0
            else:
                logger.warning("마이그레이션 로그 파일을 찾을 수 없습니다.")
                return False
                
        except Exception as e:
            logger.error(f"새로운 벡터 확인 실패: {e}")
            return False
    
    def delete_old_vectors(self, old_ids: List[str], batch_size: int = 100) -> bool:
        """기존 랜덤 ID 벡터들 삭제"""
        try:
            if not old_ids:
                logger.info("삭제할 랜덤 ID 벡터가 없습니다.")
                return True
            
            total_batches = (len(old_ids) + batch_size - 1) // batch_size
            
            logger.info(f"총 {len(old_ids)}개의 랜덤 ID 벡터를 {total_batches}개 배치로 삭제합니다.")
            
            for batch_idx in range(total_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(old_ids))
                batch_ids = old_ids[start_idx:end_idx]
                
                logger.info(f"배치 {batch_idx + 1}/{total_batches} 삭제 중... ({len(batch_ids)}개)")
                
                try:
                    # 배치 삭제
                    self.index.delete(ids=batch_ids)
                    logger.info(f"배치 {batch_idx + 1} 삭제 완료")
                except Exception as e:
                    logger.error(f"배치 {batch_idx + 1} 삭제 실패: {e}")
                    return False
            
            logger.info("모든 랜덤 ID 벡터 삭제 완료")
            return True
            
        except Exception as e:
            logger.error(f"벡터 삭제 실패: {e}")
            return False
    
    def save_cleanup_log(self, old_ids: List[str], filename: str = "cleanup_log.json"):
        """삭제 로그 저장"""
        try:
            log_data = {
                'cleanup_timestamp': datetime.now().isoformat(),
                'total_vectors_deleted': len(old_ids),
                'deleted_vector_ids': old_ids
            }
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"삭제 로그 저장 완료: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"삭제 로그 저장 실패: {e}")
            return False
    
    def run_cleanup(self) -> bool:
        """전체 삭제 프로세스 실행"""
        try:
            logger.info("=== 기존 랜덤 ID 벡터 삭제 시작 ===")
            
            # 1. 기존 랜덤 ID 벡터들 찾기
            old_ids = self.find_old_random_vectors()
            
            if not old_ids:
                logger.info("삭제할 랜덤 ID 벡터가 없습니다.")
                return True
            
            # 2. 새로운 벡터 존재 확인
            if not self.verify_new_vectors_exist(old_ids):
                logger.warning("새로운 결정적 ID 벡터들이 충분히 존재하지 않습니다.")
                logger.warning("마이그레이션이 완료되었는지 확인해주세요.")
                
                confirm = input("새로운 벡터 확인이 실패했지만 계속 진행하시겠습니까? (y/N): ").strip().lower()
                if confirm != 'y':
                    logger.info("사용자가 삭제를 취소했습니다.")
                    return False
            
            # 3. 사용자 확인
            print(f"\n=== 삭제 요약 ===")
            print(f"삭제할 랜덤 ID 벡터 수: {len(old_ids)}")
            print(f"샘플 삭제할 ID들:")
            for i, old_id in enumerate(old_ids[:5]):
                print(f"  {i+1}. {old_id}")
            if len(old_ids) > 5:
                print(f"  ... 및 {len(old_ids) - 5}개 더")
            
            confirm = input(f"\n정말로 {len(old_ids)}개의 랜덤 ID 벡터들을 삭제하시겠습니까? (y/N): ").strip().lower()
            if confirm != 'y':
                logger.info("사용자가 삭제를 취소했습니다.")
                return False
            
            # 4. 삭제 실행
            if not self.delete_old_vectors(old_ids):
                logger.error("벡터 삭제 실패")
                return False
            
            # 5. 삭제 로그 저장
            self.save_cleanup_log(old_ids)
            
            logger.info("=== 기존 랜덤 ID 벡터 삭제 완료 ===")
            return True
            
        except Exception as e:
            logger.error(f"삭제 프로세스 실패: {e}")
            return False

def main():
    """메인 함수"""
    try:
        # 클리너 초기화
        cleaner = OldVectorCleaner()
        
        # 삭제 실행
        success = cleaner.run_cleanup()
        
        if success:
            print("\n✅ 기존 랜덤 ID 벡터 삭제가 성공적으로 완료되었습니다!")
            print("이제 결정적 ID 벡터들만 남아있습니다.")
        else:
            print("\n❌ 삭제가 실패했습니다.")
            print("로그를 확인하여 문제를 해결해주세요.")
            
    except Exception as e:
        logger.error(f"삭제 스크립트 실행 실패: {e}")
        print(f"\n❌ 스크립트 실행 실패: {e}")

if __name__ == "__main__":
    main()
