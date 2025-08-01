#!/usr/bin/env python3
"""
Pinecone 인덱스 삭제 스크립트
"""

import os
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

def delete_pinecone_index():
    """Pinecone 인덱스 삭제"""
    
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_INDEX = os.getenv("PINECONE_INDEX", "auvra-rag")
    
    if not PINECONE_API_KEY:
        print("❌ PINECONE_API_KEY 환경변수가 필요합니다.")
        return
    
    try:
        # Pinecone v2 API 초기화
        pc = Pinecone(api_key=PINECONE_API_KEY)
        
        # 기존 인덱스 확인
        existing_indexes = [index.name for index in pc.list_indexes()]
        print(f"기존 인덱스: {existing_indexes}")
        
        if PINECONE_INDEX not in existing_indexes:
            print(f"❌ 인덱스 '{PINECONE_INDEX}'가 존재하지 않습니다.")
            return
        
        # 인덱스 삭제
        print(f"🗑️ 인덱스 '{PINECONE_INDEX}' 삭제 중...")
        pc.delete_index(PINECONE_INDEX)
        
        print(f"✅ 인덱스 '{PINECONE_INDEX}' 삭제 완료!")
        
    except Exception as e:
        print(f"❌ 인덱스 삭제 실패: {e}")

if __name__ == "__main__":
    delete_pinecone_index() 