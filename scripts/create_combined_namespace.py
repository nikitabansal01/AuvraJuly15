#!/usr/bin/env python3
"""
Pinecone combined namespace creation script
"""

import os
import sys
import argparse
from typing import List, Dict, Any
from pinecone import Pinecone
from dotenv import load_dotenv

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file
load_dotenv()

def initialize_pinecone():
    """Initialize Pinecone client"""
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX", "auvra-rag")
    
    if not api_key:
        raise ValueError("PINECONE_API_KEY environment variable is required")
    
    pc = Pinecone(api_key=api_key)
    index = pc.Index(index_name)
    return index

def get_all_vectors_from_namespace(index, namespace: str) -> List[Dict[str, Any]]:
    """Get all vectors from specific namespace (ID, vector, metadata)"""
    
    # Check namespace statistics
    stats = index.describe_index_stats()
    namespace_stats = stats.namespaces.get(namespace, {})
    vector_count = namespace_stats.get('vector_count', 0)
    
    if vector_count == 0:
        print(f"⚠️ No vectors found in namespace '{namespace}'")
        return []
    
    print(f"📊 Found {vector_count:,} vectors in namespace '{namespace}'")
    
    # Get all vectors using dummy query
    all_vectors = []
    batch_size = 1000
    
    while True:
        try:
            query_response = index.query(
                vector=[0] * 1536,  # Dummy vector
                namespace=namespace,
                top_k=batch_size,
                include_metadata=True,
                include_values=True,  # Include vector values
                filter={}
            )
            
            if not query_response.matches:
                break
            
            for match in query_response.matches:
                all_vectors.append({
                    'id': match.id,
                    'values': match.values,
                    'metadata': match.metadata
                })
            
            print(f"  - Retrieved {len(all_vectors):,} vectors...")
            
            # Stop if we got fewer results than batch size
            if len(query_response.matches) < batch_size:
                break
                
        except Exception as e:
            print(f"❌ Error retrieving vectors: {e}")
            break
    
    print(f"✅ Retrieved {len(all_vectors):,} vectors from '{namespace}'")
    return all_vectors

def add_model_prefix_to_id(vector_data: Dict[str, Any], model_prefix: str) -> Dict[str, Any]:
    """Add model prefix to vector ID (prevent conflicts)"""
    
    # Preserve original ID in metadata
    if 'metadata' not in vector_data:
        vector_data['metadata'] = {}
    
    vector_data['metadata']['original_id'] = vector_data['id']
    vector_data['id'] = f"{model_prefix}_{vector_data['id']}"
    
    return vector_data

def create_combined_namespace(index, hq_namespace: str = "pcos-rag", lq_namespace: str = "pcos-rag-lq"):
    """Create combined namespace"""
    
    print(f"🚀 Creating combined namespace from '{hq_namespace}' and '{lq_namespace}'")
    
    # 1. Get HQ vectors
    print(f"\n📥 Retrieving HQ vectors from '{hq_namespace}'...")
    hq_vectors = get_all_vectors_from_namespace(index, hq_namespace)
    
    # 2. Get LQ vectors
    print(f"\n📥 Retrieving LQ vectors from '{lq_namespace}'...")
    lq_vectors = get_all_vectors_from_namespace(index, lq_namespace)
    
    # 3. Modify vector IDs (prevent conflicts)
    print(f"\n🔧 Modifying vector IDs...")
    for vector in hq_vectors:
        vector = add_model_prefix_to_id(vector, "hq")
    
    for vector in lq_vectors:
        vector = add_model_prefix_to_id(vector, "lq")
    
    # 4. Create combined vector list
    combined_vectors = hq_vectors + lq_vectors
    print(f"📊 Combined vectors: {len(combined_vectors):,} total")
    print(f"  - HQ vectors: {len(hq_vectors):,}")
    print(f"  - LQ vectors: {len(lq_vectors):,}")
    
    if not combined_vectors:
        print("❌ No vectors to combine")
        return
    
    # 5. Delete existing combined namespace (if exists)
    combined_namespace = "pcos-rag-combined"
    print(f"\n🗑️ Checking for existing combined namespace '{combined_namespace}'...")
    
    try:
        # Try to delete existing vectors in combined namespace
        index.delete(namespace=combined_namespace, delete_all=True)
        print(f"✅ Deleted existing vectors in '{combined_namespace}'")
    except Exception as e:
        print(f"ℹ️ No existing vectors to delete: {e}")
    
    # 6. Upload in batches
    print(f"\n📤 Uploading combined vectors to '{combined_namespace}'...")
    batch_size = 100  # Pinecone recommended batch size
    
    for i in range(0, len(combined_vectors), batch_size):
        batch = combined_vectors[i:i + batch_size]
        
        # Convert to Pinecone upsert format
        upsert_data = []
        for vector in batch:
            upsert_data.append({
                'id': vector['id'],
                'values': vector['values'],
                'metadata': vector['metadata']
            })
        
        try:
            index.upsert(vectors=upsert_data, namespace=combined_namespace)
            
            # Progress logging
            progress = min(i + batch_size, len(combined_vectors))
            print(f"  - Uploaded {progress:,}/{len(combined_vectors):,} vectors...")
            
        except Exception as e:
            print(f"❌ Error uploading batch: {e}")
            break
    
    # 7. Verify results
    print(f"\n✅ Combined namespace creation completed!")
    verify_combined_namespace(index, combined_namespace)

def verify_combined_namespace(index, namespace: str):
    """Verify combined namespace"""
    
    print(f"\n🔍 Verifying combined namespace '{namespace}'...")
    
    # Check statistics
    stats = index.describe_index_stats()
    namespace_stats = stats.namespaces.get(namespace, {})
    vector_count = namespace_stats.get('vector_count', 0)
    
    print(f"📊 Combined namespace statistics:")
    print(f"  - Total vectors: {vector_count:,}")
    
    # Sample query verification
    try:
        query_response = index.query(
            vector=[0] * 1536,
            namespace=namespace,
            top_k=10,
            include_metadata=True
        )
        
        print(f"  - Sample query successful: {len(query_response.matches)} results")
        
        # Check model distribution
        hq_count = 0
        lq_count = 0
        
        for match in query_response.matches:
            if match.id.startswith("hq_"):
                hq_count += 1
            elif match.id.startswith("lq_"):
                lq_count += 1
        
        print(f"  - Model distribution in sample:")
        print(f"    * HQ vectors: {hq_count}")
        print(f"    * LQ vectors: {lq_count}")
        
    except Exception as e:
        print(f"❌ Verification failed: {e}")

def main():
    """Main execution function"""
    
    print("🚀 Pinecone Combined Namespace Creation Script")
    print("=" * 60)
    
    # Pinecone connection
    try:
        index = initialize_pinecone()
        print("✅ Pinecone connection established")
    except Exception as e:
        print(f"❌ Pinecone connection failed: {e}")
        return
    
    # Command line argument processing
    parser = argparse.ArgumentParser(description="Create combined Pinecone namespace")
    parser.add_argument("--verify-only", action="store_true", help="Only verify existing combined namespace")
    parser.add_argument("--hq-namespace", default="pcos-rag", help="High quality namespace")
    parser.add_argument("--lq-namespace", default="pcos-rag-lq", help="Low quality namespace")
    
    args = parser.parse_args()
    
    combined_namespace = "pcos-rag-combined"
    
    # Verify only execution
    if args.verify_only:
        print(f"🔍 Verification mode - checking '{combined_namespace}'")
        verify_combined_namespace(index, combined_namespace)
        return
    
    # Create namespace
    create_combined_namespace(index, args.hq_namespace, args.lq_namespace)
    
    # Verify after creation
    verify_combined_namespace(index, combined_namespace)

if __name__ == "__main__":
    main()