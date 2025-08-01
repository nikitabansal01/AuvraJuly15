#!/usr/bin/env python3
"""
Pinecone 인덱스에 저장된 벡터 수를 확인하는 스크립트
"""

import asyncio
import sys
import os
import logging

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag_service import RAGService

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_pinecone_count():
    """Pinecone 인덱스 상태 확인"""
    
    try:
        # Get Pinecone client
        index = RAGService.get_pinecone_client()
        
        # Check index statistics
        stats = index.describe_index_stats()
        
        print("=== Pinecone 인덱스 상태 ===")
        print(f"인덱스 이름: {stats.index_name}")
        print(f"차원: {stats.dimension}")
        print(f"메트릭: {stats.metric}")
        print(f"총 벡터 수: {stats.total_vector_count}")
        
        # Statistics per namespace
        if hasattr(stats, 'namespaces') and stats.namespaces:
            print("\n=== 네임스페이스별 통계 ===")
            for namespace, namespace_stats in stats.namespaces.items():
                print(f"네임스페이스 '{namespace}': {namespace_stats.vector_count}개 벡터")
        else:
            print("\n=== 네임스페이스별 통계 ===")
            print("네임스페이스 정보 없음")
        
        # Check actual vector samples (max 10)
        print("\n=== 저장된 벡터 샘플 ===")
        try:
            # Check actually stored vectors with dummy query
            query_response = index.query(
                vector=[0] * 1536,  # Dummy vector
                namespace="pcos-rag",
                top_k=10,
                include_metadata=True
            )
            
            if query_response.matches:
                print(f"쿼리 결과: {len(query_response.matches)}개 벡터 발견")
                for i, match in enumerate(query_response.matches[:5]):  # Show max 5 only
                    print(f"  {i+1}. ID: {match.id}")
                    print(f"     점수: {match.score}")
                    print(f"     메타데이터: {match.metadata}")
                    print()
            else:
                print("저장된 벡터가 없습니다.")
                
        except Exception as e:
            print(f"쿼리 실패: {e}")
        
    except Exception as e:
        logger.error(f"Pinecone status check failed: {e}")

if __name__ == "__main__":
    asyncio.run(check_pinecone_count()) 