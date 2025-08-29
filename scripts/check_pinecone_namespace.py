#!/usr/bin/env python3
"""
Pinecone namespace verification script
"""

import os
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

def check_pinecone_namespace():
    """Check namespaces in Pinecone index"""
    
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
        
        print(f"📊 Pinecone Namespace Check: '{PINECONE_INDEX}'")
        print("=" * 50)
        
        # Namespace information
        if hasattr(stats, 'namespaces') and stats.namespaces:
            print(f"Found {len(stats.namespaces)} namespaces:")
            for namespace, info in stats.namespaces.items():
                print(f"  - {namespace}: {info.vector_count} vectors")
        else:
            print("No namespaces found (using default namespace)")
        
        # Check specific namespaces
        target_namespaces = ["pcos-rag", "endometriosis-rag", "general-rag"]
        print(f"\n🔍 Checking target namespaces: {target_namespaces}")
        
        for namespace in target_namespaces:
            try:
                # Try to query each namespace
                query_response = index.query(
                    vector=[0] * 1536,  # Dummy vector
                    namespace=namespace,
                    top_k=1,
                    include_metadata=False
                )
                print(f"  ✅ {namespace}: {len(query_response.matches)} vectors found")
            except Exception as e:
                print(f"  ❌ {namespace}: {str(e)}")
        
    except Exception as e:
        print(f"❌ Failed to check Pinecone namespaces: {e}")

if __name__ == "__main__":
    check_pinecone_namespace()
