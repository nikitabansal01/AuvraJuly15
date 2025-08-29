#!/usr/bin/env python3
"""
Pinecone vector count verification script
"""

import os
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

def check_pinecone_count():
    """Check vector count in Pinecone index"""
    
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_INDEX = os.getenv("PINECONE_INDEX", "auvra-rag")
    
    if not PINECONE_API_KEY:
        print("❌ PINECONE_API_KEY environment variable is required.")
        return
    
    try:
        # Pinecone v2 API initialization
        pc = Pinecone(api_key=PINECONE_API_KEY)
        
        # Check if index exists
        existing_indexes = [index.name for index in pc.list_indexes()]
        if PINECONE_INDEX not in existing_indexes:
            print(f"❌ Index '{PINECONE_INDEX}' does not exist.")
            return
        
        # Connect to index
        index = pc.Index(PINECONE_INDEX)
        
        # Get index statistics
        stats = index.describe_index_stats()
        
        print(f"📊 Pinecone Index Statistics: '{PINECONE_INDEX}'")
        print("=" * 50)
        print(f"Total vector count: {stats.total_vector_count}")
        
        # Namespace statistics
        if hasattr(stats, 'namespaces') and stats.namespaces:
            print("\n📋 Namespace Statistics:")
            for namespace, info in stats.namespaces.items():
                print(f"  - {namespace}: {info.vector_count} vectors")
        else:
            print("\n📋 No namespaces found (using default namespace)")
        
    except Exception as e:
        print(f"❌ Failed to check Pinecone count: {e}")

if __name__ == "__main__":
    check_pinecone_count() 