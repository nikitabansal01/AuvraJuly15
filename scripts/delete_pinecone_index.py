#!/usr/bin/env python3
"""
Pinecone index deletion script
"""

import os
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

def delete_pinecone_index():
    """Delete Pinecone index"""
    
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
        
        if PINECONE_INDEX not in existing_indexes:
            print(f"❌ Index '{PINECONE_INDEX}' does not exist.")
            return
        
        # Delete index
        pc.delete_index(PINECONE_INDEX)
        print(f"✅ Index '{PINECONE_INDEX}' deleted successfully!")
        
    except Exception as e:
        print(f"❌ Index deletion failed: {e}")

if __name__ == "__main__":
    delete_pinecone_index() 