#!/usr/bin/env python3
"""
RAG 모듈 환경변수 확인 스크립트
"""

import os
from dotenv import load_dotenv

load_dotenv()

def check_rag_environment():
    """RAG 모듈에 필요한 환경변수들을 확인한다."""
    
    print("🔍 RAG 모듈 환경변수 확인")
    print("=" * 50)
    
    # OpenAI 설정
    openai_key = os.getenv("OPENAI_API_KEY")
    print(f"✅ OPENAI_API_KEY: {'설정됨' if openai_key else '❌ 설정되지 않음'}")
    
    # Firecrawl 설정
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
    firecrawl_url = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev/v1/scrape")
    print(f"✅ FIRECRAWL_API_KEY: {'설정됨' if firecrawl_key else '❌ 설정되지 않음'}")
    print(f"✅ FIRECRAWL_BASE_URL: {firecrawl_url}")
    
    # Pinecone 설정
    pinecone_key = os.getenv("PINECONE_API_KEY")
    pinecone_env = os.getenv("PINECONE_ENVIRONMENT")
    pinecone_index = os.getenv("PINECONE_INDEX")
    
    print(f"✅ PINECONE_API_KEY: {'설정됨' if pinecone_key else '❌ 설정되지 않음'}")
    print(f"✅ PINECONE_ENVIRONMENT: {pinecone_env or '❌ 설정되지 않음'}")
    print(f"✅ PINECONE_INDEX: {pinecone_index or '❌ 설정되지 않음'}")
    
    print("\n📋 요약:")
    if all([openai_key, firecrawl_key, pinecone_key, pinecone_env, pinecone_index]):
        print("🎉 모든 환경변수가 설정되어 있습니다!")
    else:
        print("⚠️  일부 환경변수가 설정되지 않았습니다.")
        if not openai_key:
            print("  - OPENAI_API_KEY 필요")
        if not firecrawl_key:
            print("  - FIRECRAWL_API_KEY 필요")
        if not pinecone_key:
            print("  - PINECONE_API_KEY 필요")
        if not pinecone_env:
            print("  - PINECONE_ENVIRONMENT 필요")
        if not pinecone_index:
            print("  - PINECONE_INDEX 필요")

if __name__ == "__main__":
    check_rag_environment() 