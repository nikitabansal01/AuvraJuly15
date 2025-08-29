#!/usr/bin/env python3
"""
Pinecone index creation script
"""

import os
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

load_dotenv()

def create_pinecone_index():
    """Create Pinecone index"""
    
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_INDEX = os.getenv("PINECONE_INDEX", "auvra-rag")
    
    if not PINECONE_API_KEY:
        print("❌ PINECONE_API_KEY environment variable is required.")
        return
    
    try:
        # Pinecone v2 API initialization
        pc = Pinecone(api_key=PINECONE_API_KEY)
        
        # Check existing indexes
        existing_indexes = [index.name for index in pc.list_indexes()]
        print(f"Existing indexes: {existing_indexes}")
        
        if PINECONE_INDEX in existing_indexes:
            print(f"✅ Index '{PINECONE_INDEX}' already exists.")
            return
        
        # Create new index (Serverless)
        pc.create_index(
            name=PINECONE_INDEX,
            dimension=1536,  # text-embedding-3-small dimension
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region="us-east-1"
            )
        )
        
        print(f"✅ Index '{PINECONE_INDEX}' created successfully!")
        print(f"  - Dimension: 1536")
        print(f"  - Metric: cosine")
        print(f"  - Type: Serverless")
        
    except Exception as e:
        print(f"❌ Index creation failed: {e}")

if __name__ == "__main__":
    create_pinecone_index() 