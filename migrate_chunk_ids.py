#!/usr/bin/env python3
"""
기존 랜덤 청크 ID를 새로운 결정적 형식으로 변경하는 마이그레이션 스크립트
"""

import os
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ChunkIDMigrator:
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
    
    def get_all_vectors(self) -> List[Dict[str, Any]]:
        """모든 벡터를 조회"""
        try:
            # 인덱스 통계 확인
            stats = self.index.describe_index_stats()
            total_vectors = stats.total_vector_count
            logger.info(f"총 벡터 수: {total_vectors}")
            
            # 실제 차원 확인
            dimension = 1536
            if hasattr(stats, 'dimension'):
                dimension = stats.dimension
            
            # 이전 성공한 방식으로 조회
            logger.info("이전 성공한 방식으로 벡터 조회 시도...")
            dummy_vector = [0.0] * dimension
            
            # 여러 네임스페이스 시도
            namespaces_to_try = ["pcos-rag", "", None]  # 일반적인 네임스페이스들
            
            for namespace in namespaces_to_try:
                try:
                    logger.info(f"네임스페이스 '{namespace}'로 시도...")
                    
                    response = self.index.query(
                        vector=dummy_vector,
                        top_k=10000,  # 최대 10000개 (이전 성공 방식)
                        include_metadata=True,
                        include_values=True,
                        namespace=namespace if namespace else None
                    )
                    
                    logger.info(f"네임스페이스 '{namespace}' 결과: {len(response.matches)}개 벡터")
                    
                    if len(response.matches) > 0:
                        all_vectors = []
                        for match in response.matches:
                            all_vectors.append({
                                'id': match.id,
                                'metadata': match.metadata,
                                'values': match.values
                            })
                        
                        logger.info(f"성공! 총 {len(all_vectors)}개의 벡터를 조회했습니다.")
                        return all_vectors
                        
                except Exception as e:
                    logger.warning(f"네임스페이스 '{namespace}' 실패: {e}")
                    continue
            
            # 모든 네임스페이스 시도 실패
            logger.error("모든 네임스페이스에서 벡터 조회 실패")
            logger.error("가능한 원인:")
            logger.error("1. 인덱스가 비어있음")
            logger.error("2. 네임스페이스 문제")
            logger.error("3. 쿼리 방식 문제")
            raise RuntimeError("벡터 조회 실패")
            
        except Exception as e:
            logger.error(f"벡터 조회 실패: {e}")
            raise
    
    def group_vectors_by_paper(self, vectors: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """논문별로 벡터들을 그룹화하고 start_idx로 정렬"""
        papers = {}
        
        for vector in vectors:
            metadata = vector['metadata']
            pmid = metadata.get('pmid')
            
            if not pmid:
                # PMID가 없으면 제목으로 대체
                title = metadata.get('title', 'unknown')
                pmid = title.replace(' ', '_')[:20]
            
            if pmid not in papers:
                papers[pmid] = []
            
            # start_idx 추출
            start_idx = metadata.get('start_idx', 0)
            
            papers[pmid].append({
                'vector_id': vector['id'],
                'metadata': metadata,
                'values': vector['values'],
                'start_idx': start_idx
            })
        
        # 각 논문의 벡터들을 start_idx로 정렬
        for pmid in papers:
            papers[pmid].sort(key=lambda x: x['start_idx'])
        
        logger.info(f"논문별 그룹화 완료: {len(papers)}개 논문")
        return papers
    
    def generate_new_chunk_id(self, pmid: str, chunk_index: int) -> str:
        """새로운 결정적 청크 ID 생성"""
        return f"paper_{pmid}_chunk_{chunk_index + 1}"
    
    def migrate_chunk_ids(self, papers: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """청크 ID 마이그레이션 수행"""
        migration_data = []
        
        for pmid, vectors in papers.items():
            logger.info(f"논문 {pmid} 처리 중... ({len(vectors)}개 청크)")
            
            for i, vector in enumerate(vectors):
                old_id = vector['vector_id']
                new_id = self.generate_new_chunk_id(pmid, i)
                
                # 메타데이터 업데이트
                updated_metadata = vector['metadata'].copy()
                updated_metadata['chunk_id'] = new_id  # 새로운 청크 ID 추가
                
                migration_data.append({
                    'old_id': old_id,
                    'new_id': new_id,
                    'pmid': pmid,
                    'chunk_index': i + 1,
                    'start_idx': vector['start_idx'],
                    'metadata': updated_metadata,
                    'values': vector['values']
                })
                
                logger.debug(f"  청크 {i+1}: {old_id} → {new_id}")
        
        logger.info(f"총 {len(migration_data)}개의 청크 ID 마이그레이션 준비 완료")
        return migration_data
    
    def update_pinecone_vectors(self, migration_data: List[Dict[str, Any]], batch_size: int = 100) -> bool:
        """Pinecone 벡터 업데이트"""
        try:
            total_batches = (len(migration_data) + batch_size - 1) // batch_size
            
            for batch_idx in range(total_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(migration_data))
                batch = migration_data[start_idx:end_idx]
                
                logger.info(f"배치 {batch_idx + 1}/{total_batches} 처리 중... ({len(batch)}개 벡터)")
                
                # 배치 업데이트 준비
                vectors_to_upsert = []
                for item in batch:
                    vectors_to_upsert.append({
                        'id': item['new_id'],  # 새로운 ID 사용
                        'values': item['values'],
                        'metadata': item['metadata']
                    })
                
                # Pinecone에 업서트
                self.index.upsert(vectors=vectors_to_upsert)
                
                logger.info(f"배치 {batch_idx + 1} 업데이트 완료")
            
            logger.info("모든 벡터 업데이트 완료")
            return True
            
        except Exception as e:
            logger.error(f"Pinecone 업데이트 실패: {e}")
            return False
    

    
    def save_migration_log(self, migration_data: List[Dict[str, Any]], filename: str = "chunk_migration_log.json"):
        """마이그레이션 로그 저장"""
        try:
            log_data = {
                'migration_timestamp': datetime.now().isoformat(),
                'total_chunks_migrated': len(migration_data),
                'papers_processed': len(set(item['pmid'] for item in migration_data)),
                'migration_details': migration_data
            }
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"마이그레이션 로그 저장 완료: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"마이그레이션 로그 저장 실패: {e}")
            return False
    
    def run_migration(self) -> bool:
        """전체 마이그레이션 프로세스 실행"""
        try:
            logger.info("=== 청크 ID 마이그레이션 시작 ===")
            
            # 1. 모든 벡터 조회
            vectors = self.get_all_vectors()
            
            # 2. 논문별 그룹화 및 정렬
            papers = self.group_vectors_by_paper(vectors)
            
            # 3. 마이그레이션 데이터 생성
            migration_data = self.migrate_chunk_ids(papers)
            
            # 4. 사용자 확인
            print(f"\n=== 마이그레이션 요약 ===")
            print(f"총 논문 수: {len(papers)}")
            print(f"총 청크 수: {len(migration_data)}")
            print(f"기존 벡터는 유지됩니다. (삭제는 별도 스크립트로 진행)")
            
            confirm = input("\n마이그레이션을 진행하시겠습니까? (y/N): ").strip().lower()
            if confirm != 'y':
                logger.info("사용자가 마이그레이션을 취소했습니다.")
                return False
            
            # 5. Pinecone 업데이트
            if not self.update_pinecone_vectors(migration_data):
                logger.error("Pinecone 업데이트 실패")
                return False
            
            # 6. 마이그레이션 로그 저장
            self.save_migration_log(migration_data)
            
            logger.info("=== 청크 ID 마이그레이션 완료 ===")
            logger.info("기존 랜덤 ID 벡터 삭제는 'python cleanup_old_vectors.py'로 진행하세요.")
            return True
            
        except Exception as e:
            logger.error(f"마이그레이션 실패: {e}")
            return False

def main():
    """메인 함수"""
    try:
        # 마이그레이터 초기화
        migrator = ChunkIDMigrator()
        
        # 마이그레이션 실행
        success = migrator.run_migration()
        
        if success:
            print("\n✅ 마이그레이션이 성공적으로 완료되었습니다!")
            print("이제 새로운 결정적 청크 ID 형식이 적용되었습니다.")
            print("\n기존 랜덤 ID 벡터 삭제는 다음 명령어로 진행하세요:")
            print("python cleanup_old_vectors.py")
        else:
            print("\n❌ 마이그레이션이 실패했습니다.")
            print("로그를 확인하여 문제를 해결해주세요.")
            
    except Exception as e:
        logger.error(f"마이그레이션 스크립트 실행 실패: {e}")
        print(f"\n❌ 스크립트 실행 실패: {e}")

if __name__ == "__main__":
    main()
