#!/usr/bin/env python3
"""
Pinecone index reset script (delete + recreate)
"""

import os
import time
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

load_dotenv()

def reset_pinecone_index():
    """Delete and recreate Pinecone index"""
    
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_INDEX = os.getenv("PINECONE_INDEX", "auvra-rag")
    
    if not PINECONE_API_KEY:
        print("❌ PINECONE_API_KEY environment variable is required.")
        return
    
    try:
        # Initialize Pinecone v2 API
        pc = Pinecone(api_key=PINECONE_API_KEY)
        
        # Step 1: Check and delete existing index
        existing_indexes = [index.name for index in pc.list_indexes()]
        print(f"Existing indexes: {existing_indexes}")
        
        if PINECONE_INDEX in existing_indexes:
            print(f"🗑️ Deleting index '{PINECONE_INDEX}'...")
            pc.delete_index(PINECONE_INDEX)
            print(f"✅ Index '{PINECONE_INDEX}' deleted!")
            
            # Wait for deletion to complete (index status update)
            print("⏳ Waiting for index deletion to complete...")
            time.sleep(10)
        else:
            print(f"ℹ️ Index '{PINECONE_INDEX}' does not exist.")
        
        # Step 2: Create new index
        print(f"🔨 Creating new index '{PINECONE_INDEX}'...")
        pc.create_index(
            name=PINECONE_INDEX,
            dimension=1536,  # text-embedding-3-small dimension
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region="us-east-1"
            )
        )
        
        print(f"✅ Index '{PINECONE_INDEX}' created!")
        print(f"  - Dimension: 1536")
        print(f"  - Metric: cosine")
        print(f"  - Type: Serverless")
        
        # Step 3: Check index status
        print("⏳ Waiting for index initialization...")
        time.sleep(30)  # Wait for index initialization
        
        # Check index status
        try:
            index = pc.Index(PINECONE_INDEX)
            stats = index.describe_index_stats()
            print(f"✅ Index connection successful!")
            print(f"  - Total vector count: {stats.total_vector_count}")
        except Exception as e:
            print(f"⚠️ Failed to check index status: {e}")
        
    except Exception as e:
        print(f"❌ Index reset failed: {e}")

if __name__ == "__main__":
    reset_pinecone_index() 