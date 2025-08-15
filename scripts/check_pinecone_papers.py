#!/usr/bin/env python3
"""
Pinecone에서 논문 개수와 chunk 개수를 확인하는 스크립트
"""

import os
import asyncio
import sys
from typing import Dict, List, Set
import httpx
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag_service import RAGService
from app.core.config import settings

async def check_pinecone_papers():
    """
    Pinecone에서 논문 개수와 chunk 개수를 확인
    """
    print("🔍 Pinecone 논문 개수 확인 중...")
    
    try:
        # Pinecone 클라이언트 가져오기
        pinecone_client = RAGService.get_pinecone_client()
        
        # 네임스페이스 설정
        namespace = "pcos-rag"
        
        # 인덱스 정보 가져오기
        index_name = settings.PINECONE_INDEX
        print(f"📊 인덱스: {index_name}")
        print(f"📁 네임스페이스: {namespace}")
        
        # 전체 벡터 개수 확인
        index_stats = pinecone_client.describe_index_stats()
        print(f"\n📈 전체 인덱스 통계:")
        print(f"  - 총 벡터 개수: {index_stats.total_vector_count:,}")
        
        # 네임스페이스별 통계
        if 'namespaces' in index_stats:
            namespace_stats = index_stats.namespaces.get(namespace, {})
            print(f"  - {namespace} 네임스페이스 벡터 개수: {namespace_stats.get('vector_count', 0):,}")
        
        # 실제 데이터 조회 (최대 10,000개)
        print(f"\n🔎 실제 데이터 분석 중...")
        
        # URL별로 논문 그룹화
        paper_urls: Set[str] = set()
        chunk_count = 0
        
        # 배치로 데이터 조회
        batch_size = 1000
        offset = 0
        
        while True:
            try:
                # 벡터 조회
                query_response = pinecone_client.query(
                    vector=[0] * 1536,  # 더미 벡터 (실제 검색이 아닌 메타데이터 조회용)
                    top_k=batch_size,
                    include_metadata=True,
                    namespace=namespace,
                    filter={}  # 모든 벡터 조회
                )
                
                if not query_response.matches:
                    break
                
                # 메타데이터에서 URL 추출
                for match in query_response.matches:
                    chunk_count += 1
                    
                    if hasattr(match, 'metadata') and match.metadata:
                        url = match.metadata.get('url', '')
                        if url:
                            paper_urls.add(url)
                
                print(f"  - 처리된 chunk: {chunk_count:,}")
                print(f"  - 발견된 논문 URL: {len(paper_urls):,}")
                
                offset += batch_size
                
                # 더 이상 데이터가 없으면 중단
                if len(query_response.matches) < batch_size:
                    break
                    
            except Exception as e:
                print(f"  ⚠️ 배치 조회 중 오류: {e}")
                break
        
        print(f"\n📊 최종 결과:")
        print(f"  - 총 chunk 개수: {chunk_count:,}")
        print(f"  - 논문 개수: {len(paper_urls):,}")
        print(f"  - 논문당 평균 chunk: {chunk_count / len(paper_urls):.1f}" if paper_urls else "  - 논문 없음")
        
        # 논문 URL 샘플 출력
        if paper_urls:
            print(f"\n📄 논문 URL 샘플 (처음 5개):")
            for i, url in enumerate(list(paper_urls)[:5]):
                print(f"  {i+1}. {url}")
        
        # DOI별 논문 개수 확인
        print(f"\n🔍 DOI별 분석:")
        doi_papers: Set[str] = set()
        
        # 다시 조회하여 DOI 수집
        offset = 0
        while True:
            try:
                query_response = pinecone_client.query(
                    vector=[0] * 1536,
                    top_k=batch_size,
                    include_metadata=True,
                    namespace=namespace,
                    filter={}
                )
                
                if not query_response.matches:
                    break
                
                for match in query_response.matches:
                    if hasattr(match, 'metadata') and match.metadata:
                        doi = match.metadata.get('doi', '')
                        if doi:
                            doi_papers.add(doi)
                
                offset += batch_size
                
                if len(query_response.matches) < batch_size:
                    break
                    
            except Exception as e:
                print(f"  ⚠️ DOI 분석 중 오류: {e}")
                break
        
        print(f"  - DOI가 있는 논문: {len(doi_papers):,}")
        
        # PMID별 논문 개수 확인
        print(f"\n🔍 PMID별 분석:")
        pmid_papers: Set[str] = set()
        
        offset = 0
        while True:
            try:
                query_response = pinecone_client.query(
                    vector=[0] * 1536,
                    top_k=batch_size,
                    include_metadata=True,
                    namespace=namespace,
                    filter={}
                )
                
                if not query_response.matches:
                    break
                
                for match in query_response.matches:
                    if hasattr(match, 'metadata') and match.metadata:
                        pmid = match.metadata.get('pmid', '')
                        if pmid:
                            pmid_papers.add(pmid)
                
                offset += batch_size
                
                if len(query_response.matches) < batch_size:
                    break
                    
            except Exception as e:
                print(f"  ⚠️ PMID 분석 중 오류: {e}")
                break
        
        print(f"  - PMID가 있는 논문: {len(pmid_papers):,}")
        
        # PMC ID별 논문 개수 확인
        print(f"\n🔍 PMC ID별 분석:")
        pmcid_papers: Set[str] = set()
        
        offset = 0
        while True:
            try:
                query_response = pinecone_client.query(
                    vector=[0] * 1536,
                    top_k=batch_size,
                    include_metadata=True,
                    namespace=namespace,
                    filter={}
                )
                
                if not query_response.matches:
                    break
                
                for match in query_response.matches:
                    if hasattr(match, 'metadata') and match.metadata:
                        pmcid = match.metadata.get('pmcid', '')
                        if pmcid:
                            pmcid_papers.add(pmcid)
                
                offset += batch_size
                
                if len(query_response.matches) < batch_size:
                    break
                    
            except Exception as e:
                print(f"  ⚠️ PMC ID 분석 중 오류: {e}")
                break
        
        print(f"  - PMC ID가 있는 논문: {len(pmcid_papers):,}")
        
        print(f"\n✅ 분석 완료!")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

async def check_pinecone_namespaces():
    """
    Pinecone의 모든 네임스페이스 확인
    """
    print("\n🔍 Pinecone 네임스페이스 확인 중...")
    
    try:
        pinecone_client = RAGService.get_pinecone_client()
        index_stats = pinecone_client.describe_index_stats()
        
        print(f"📊 전체 인덱스 통계:")
        print(f"  - 총 벡터 개수: {index_stats.total_vector_count:,}")
        
        if 'namespaces' in index_stats:
            print(f"\n📁 네임스페이스별 통계:")
            for namespace, stats in index_stats.namespaces.items():
                print(f"  - {namespace}: {stats.get('vector_count', 0):,} 벡터")
        else:
            print("  - 네임스페이스 정보 없음")
            
    except Exception as e:
        print(f"❌ 네임스페이스 확인 중 오류: {e}")

async def main():
    """
    메인 함수
    """
    print("🚀 Pinecone 논문 개수 확인 스크립트")
    print("=" * 50)
    
    # 환경변수 확인
    required_env_vars = ['PINECONE_API_KEY', 'PINECONE_INDEX']
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ 필요한 환경변수가 없습니다: {missing_vars}")
        print("환경변수를 설정한 후 다시 실행하세요.")
        return
    
    # 네임스페이스 확인
    await check_pinecone_namespaces()
    
    # 논문 개수 확인
    await check_pinecone_papers()

if __name__ == "__main__":
    asyncio.run(main()) 