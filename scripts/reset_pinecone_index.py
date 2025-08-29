#!/usr/bin/env python3
"""
Pinecone index reset script
"""

import os
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

def reset_pinecone_index():
    """Reset Pinecone index by deleting and recreating it"""
    
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
        
        # Delete existing index if it exists
        if PINECONE_INDEX in existing_indexes:
            print(f"🗑️ Deleting existing index '{PINECONE_INDEX}'...")
            pc.delete_index(PINECONE_INDEX)
            print(f"✅ Index '{PINECONE_INDEX}' deleted successfully!")
        else:
            print(f"ℹ️ Index '{PINECONE_INDEX}' does not exist.")
        
        # Create new index
        print(f"🔨 Creating new index '{PINECONE_INDEX}'...")
        pc.create_index(
            name=PINECONE_INDEX,
            dimension=1536,
            metric="cosine"
        )
        
        print(f"✅ Index '{PINECONE_INDEX}' reset completed!")
        
    except Exception as e:
        print(f"❌ Index reset failed: {e}")

if __name__ == "__main__":
    reset_pinecone_index() 