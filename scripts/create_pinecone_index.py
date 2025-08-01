#!/usr/bin/env python3
"""
Pinecone 인덱스 생성 스크립트
"""

import os
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

load_dotenv()

def create_pinecone_index():
    """Pinecone 인덱스 생성"""
    
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_INDEX = os.getenv("PINECONE_INDEX", "auvra-rag")
    
    if not PINECONE_API_KEY:
        print("❌ PINECONE_API_KEY 환경변수가 필요합니다.")
        return
    
    try:
        # Pinecone v2 API initialization
        pc = Pinecone(api_key=PINECONE_API_KEY)
        
        # 기존 인덱스 확인
        existing_indexes = [index.name for index in pc.list_indexes()]
        print(f"기존 인덱스: {existing_indexes}")
        
        if PINECONE_INDEX in existing_indexes:
            print(f"✅ 인덱스 '{PINECONE_INDEX}'가 이미 존재합니다.")
            return
        
        # 새 인덱스 생성 (Serverless)
        pc.create_index(
            name=PINECONE_INDEX,
            dimension=1536,  # text-embedding-3-small 차원
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region="us-east-1"
            )
        )
        
        print(f"✅ 인덱스 '{PINECONE_INDEX}' 생성 완료!")
        print(f"  - 차원: 1536")
        print(f"  - 메트릭: cosine")
        print(f"  - 타입: Serverless")
        
    except Exception as e:
        print(f"❌ 인덱스 생성 실패: {e}")

if __name__ == "__main__":
    create_pinecone_index() 