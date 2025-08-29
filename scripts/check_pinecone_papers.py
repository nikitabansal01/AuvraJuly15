#!/usr/bin/env python3
"""
Pinecone paper count verification script
"""

import os
import sys
from pinecone import Pinecone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def check_pinecone_papers():
    """Check paper count in Pinecone index"""
    
    print("🔍 Checking Pinecone paper count...")
    
    # Get Pinecone client
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    
    # Namespace configuration
    namespace = "pcos-rag"
    
    # Get index information
    index = pc.Index(os.getenv("PINECONE_INDEX", "auvra-rag"))
    index_name = os.getenv("PINECONE_INDEX", "auvra-rag")
    print(f"📊 Index: {index_name}")
    print(f"📁 Namespace: {namespace}")
    
    # Check total vector count
    index_stats = index.describe_index_stats()
    print(f"\n📈 Overall Index Statistics:")
    print(f"  - Total vector count: {index_stats.total_vector_count:,}")
    
    # Statistics by namespace
    namespace_stats = index_stats.namespaces.get(namespace, {})
    print(f"  - {namespace} namespace vector count: {namespace_stats.get('vector_count', 0):,}")
    
    # Query actual data (max 10,000)
    print(f"\n🔎 Analyzing actual data...")
    
    # Group papers by URL
    paper_urls = set()
    chunk_count = 0
    
    # Query data in batches
    batch_size = 1000
    offset = 0
    
    while True:
        try:
            # Query vectors
            query_response = index.query(
                vector=[0] * 1536,  # Dummy vector (for metadata retrieval, not actual search)
                namespace=namespace,
                top_k=batch_size,
                include_metadata=True,
                filter={}  # Query all vectors
            )
            
            if not query_response.matches:
                break
            
            # Extract URL from metadata
            for match in query_response.matches:
                chunk_count += 1
                if match.metadata and 'url' in match.metadata:
                    paper_urls.add(match.metadata['url'])
            
            print(f"  - Processed chunks: {chunk_count:,}")
            print(f"  - Found paper URLs: {len(paper_urls):,}")
            
            offset += batch_size
            
            # Stop if no more data
            if len(query_response.matches) < batch_size:
                break
                
        except Exception as e:
            print(f"  ⚠️ Error during batch query: {e}")
            break
    
    print(f"\n📊 Final Results:")
    print(f"  - Total chunk count: {chunk_count:,}")
    print(f"  - Paper count: {len(paper_urls):,}")
    print(f"  - Average chunks per paper: {chunk_count / len(paper_urls):.1f}" if paper_urls else "  - No papers found")
    
    # Print paper URL samples
    if paper_urls:
        print(f"\n📄 Paper URL Samples (first 5):")
        for i, url in enumerate(list(paper_urls)[:5]):
            print(f"  {i+1}. {url}")
    
    # Check paper count by DOI
    print(f"\n🔍 DOI Analysis:")
    doi_papers = set()
    
    try:
        # Query again to collect DOIs
        query_response = index.query(
            vector=[0] * 1536,
            namespace=namespace,
            top_k=10000,
            include_metadata=True,
            filter={}
        )
        
        for match in query_response.matches:
            if match.metadata:
                # Check for DOI in metadata
                if 'doi' in match.metadata and match.metadata['doi']:
                    doi_papers.add(match.metadata['doi'])
                elif 'paper_doi' in match.metadata and match.metadata['paper_doi']:
                    doi_papers.add(match.metadata['paper_doi'])
                elif 'research_doi' in match.metadata and match.metadata['research_doi']:
                    doi_papers.add(match.metadata['research_doi'])
                    
    except Exception as e:
        print(f"  ⚠️ Error during DOI analysis: {e}")
    
    print(f"  - Papers with DOI: {len(doi_papers):,}")
    
    # Check paper count by PMID
    print(f"\n🔍 PMID Analysis:")
    pmid_papers = set()
    
    try:
        # Query again to collect PMIDs
        query_response = index.query(
            vector=[0] * 1536,
            namespace=namespace,
            top_k=10000,
            include_metadata=True,
            filter={}
        )
        
        for match in query_response.matches:
            if match.metadata:
                # Check for PMID in metadata
                if 'pmid' in match.metadata and match.metadata['pmid']:
                    pmid_papers.add(match.metadata['pmid'])
                elif 'paper_pmid' in match.metadata and match.metadata['paper_pmid']:
                    pmid_papers.add(match.metadata['paper_pmid'])
                elif 'research_pmid' in match.metadata and match.metadata['research_pmid']:
                    pmid_papers.add(match.metadata['research_pmid'])
                    
    except Exception as e:
        print(f"  ⚠️ Error during PMID analysis: {e}")
    
    print(f"  - Papers with PMID: {len(pmid_papers):,}")
    
    # Check paper count by PMC ID
    print(f"\n🔍 PMC ID Analysis:")
    pmcid_papers = set()
    
    try:
        # Query again to collect PMC IDs
        query_response = index.query(
            vector=[0] * 1536,
            namespace=namespace,
            top_k=10000,
            include_metadata=True,
            filter={}
        )
        
        for match in query_response.matches:
            if match.metadata:
                # Check for PMC ID in metadata
                if 'pmcid' in match.metadata and match.metadata['pmcid']:
                    pmcid_papers.add(match.metadata['pmcid'])
                elif 'paper_pmcid' in match.metadata and match.metadata['paper_pmcid']:
                    pmcid_papers.add(match.metadata['paper_pmcid'])
                elif 'research_pmcid' in match.metadata and match.metadata['research_pmcid']:
                    pmcid_papers.add(match.metadata['research_pmcid'])
                    
    except Exception as e:
        print(f"  ⚠️ Error during PMC ID analysis: {e}")
    
    print(f"  - Papers with PMC ID: {len(pmcid_papers):,}")
    
    print(f"\n✅ Analysis completed!")

def check_namespaces():
    """Check namespaces in Pinecone index"""
    
    print("\n🔍 Checking Pinecone namespaces...")
    
    try:
        pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
        index = pc.Index(os.getenv("PINECONE_INDEX", "auvra-rag"))
        
        index_stats = index.describe_index_stats()
        
        print(f"📊 Overall Index Statistics:")
        print(f"  - Total vector count: {index_stats.total_vector_count:,}")
        
        if hasattr(index_stats, 'namespaces') and index_stats.namespaces:
            print(f"\n📁 Namespace Statistics:")
            for namespace, stats in index_stats.namespaces.items():
                print(f"  - {namespace}: {stats.get('vector_count', 0):,} vectors")
        else:
            print("  - No namespace information")
            
    except Exception as e:
        print(f"❌ Error checking namespaces: {e}")

def main():
    """Main function"""
    print("🚀 Pinecone Paper Count Verification Script")
    print("=" * 60)
    
    # Check environment variables
    required_vars = ["PINECONE_API_KEY", "PINECONE_INDEX"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {missing_vars}")
        print("Please set the environment variables and run again.")
        return
    
    # Check namespaces
    check_namespaces()
    
    # Check paper count
    check_pinecone_papers()

if __name__ == "__main__":
    main() 