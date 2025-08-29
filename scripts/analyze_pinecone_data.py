#!/usr/bin/env python3
"""
Pinecone index data analysis script by namespace
"""

import asyncio
import sys
import os
import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag_service import RAGService

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PineconeAnalyzer:
    def __init__(self):
        self.index = RAGService.get_pinecone_client()
        
    def get_all_namespaces(self) -> List[str]:
        """Return all namespace list"""
        try:
            stats = self.index.describe_index_stats()
            if hasattr(stats, 'namespaces') and stats.namespaces:
                return list(stats.namespaces.keys())
            else:
                return ["", "pcos-rag"]  # Default namespaces
        except Exception as e:
            logger.error(f"Failed to retrieve namespace list: {e}")
            return []
    
    def analyze_namespace(self, namespace: str = "") -> Dict[str, Any]:
        """Analyze specific namespace"""
        try:
            logger.info(f"Analyzing namespace '{namespace}'...")
            
            # Basic statistics
            stats = self.index.describe_index_stats()
            namespace_stats = stats.namespaces.get(namespace) if hasattr(stats, 'namespaces') and stats.namespaces else None
            
            # Retrieve all vectors
            sample_vectors = self.get_sample_vectors(namespace, limit=-1)
            
            # Metadata analysis
            metadata_analysis = self.analyze_metadata(sample_vectors)
            
            # Model version analysis
            model_analysis = self.analyze_model_versions(sample_vectors)
            
            # Paper chunk analysis
            paper_analysis = self.analyze_papers(sample_vectors)
            
            result = {
                "namespace": namespace,
                "total_vectors": namespace_stats.vector_count if namespace_stats else 0,
                "actual_vectors_retrieved": len(sample_vectors),
                "metadata_analysis": metadata_analysis,
                "model_analysis": model_analysis,
                "paper_analysis": paper_analysis,
                "sample_vectors": sample_vectors[:5]  # Include only top 5
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to analyze namespace '{namespace}': {e}")
            return {
                "namespace": namespace,
                "error": str(e),
                "total_vectors": 0
            }
    
    def get_sample_vectors(self, namespace: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieve sample vectors"""
        try:
            # Query with dummy vector (1536 dimensions)
            dummy_vector = [0.0] * 1536
            
            # Use large value to retrieve all vectors
            if limit == -1:
                # Pinecone's maximum query limit (10,000)
                query_limit = 10000
            else:
                query_limit = limit
            
            response = self.index.query(
                vector=dummy_vector,
                namespace=namespace,
                top_k=query_limit,
                include_metadata=True,
                include_values=False
            )
            
            vectors = []
            for match in response.matches:
                vectors.append({
                    "id": match.id,
                    "score": match.score,
                    "metadata": match.metadata
                })
            
            return vectors
            
        except Exception as e:
            logger.error(f"Failed to retrieve sample vectors: {e}")
            return []
    
    def analyze_metadata(self, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Metadata analysis"""
        if not vectors:
            return {"error": "No vectors to analyze"}
        
        metadata_keys = set()
        chunk_sections = {}
        model_versions = set()
        tagging_timestamps = []
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            
            # Collect metadata keys
            metadata_keys.update(metadata.keys())
            
            # Section type analysis
            section = metadata.get("chunk_section_type", "unknown")
            chunk_sections[section] = chunk_sections.get(section, 0) + 1
            
            # Model version analysis
            model_version = metadata.get("model_version", "unknown")
            model_versions.add(model_version)
            
            # Tagging timestamp analysis
            timestamp = metadata.get("tagging_timestamp")
            if timestamp:
                tagging_timestamps.append(timestamp)
        
        return {
            "metadata_keys": list(metadata_keys),
            "chunk_sections": chunk_sections,
            "model_versions": list(model_versions),
            "tagging_timestamps_count": len(tagging_timestamps),
            "earliest_timestamp": min(tagging_timestamps) if tagging_timestamps else None,
            "latest_timestamp": max(tagging_timestamps) if tagging_timestamps else None
        }
    
    def analyze_model_versions(self, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Model version analysis"""
        model_counts = {}
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            model_version = metadata.get("model_version", "unknown")
            model_counts[model_version] = model_counts.get(model_version, 0) + 1
        
        return {
            "model_distribution": model_counts,
            "total_models": len(model_counts)
        }
    
    def analyze_papers(self, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Paper analysis"""
        paper_chunks = {}
        
        for vector in vectors:
            metadata = vector.get("metadata", {})
            pmid = metadata.get("pmid", "unknown")
            
            if pmid not in paper_chunks:
                paper_chunks[pmid] = {
                    "chunk_count": 0,
                    "sections": set(),
                    "title": metadata.get("paper_title", "Unknown"),
                    "authors": metadata.get("authors", []),
                    "journal": metadata.get("journal", "Unknown"),
                    "publication_year": metadata.get("publication_year"),
                    "doi": metadata.get("doi", ""),
                    "pmcid": metadata.get("pmcid", ""),
                    "chunk_ids": []
                }
            
            paper_chunks[pmid]["chunk_count"] += 1
            section = metadata.get("chunk_section_type", "unknown")
            paper_chunks[pmid]["sections"].add(section)
            paper_chunks[pmid]["chunk_ids"].append(vector["id"])
        
        # Convert set to list
        for pmid in paper_chunks:
            paper_chunks[pmid]["sections"] = list(paper_chunks[pmid]["sections"])
        
        return {
            "total_papers": len(paper_chunks),
            "paper_details": paper_chunks,
            "avg_chunks_per_paper": sum(p["chunk_count"] for p in paper_chunks.values()) / len(paper_chunks) if paper_chunks else 0,
            "research_list": self.get_research_list(paper_chunks)
        }
    
    def get_research_list(self, paper_chunks: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate research list"""
        research_list = []
        
        for pmid, paper_info in paper_chunks.items():
            research_item = {
                "pmid": pmid,
                "title": paper_info["title"],
                "authors": paper_info["authors"],
                "journal": paper_info["journal"],
                "publication_year": paper_info["publication_year"],
                "doi": paper_info["doi"],
                "pmcid": paper_info["pmcid"],
                "chunk_count": paper_info["chunk_count"],
                "sections": paper_info["sections"],
                "chunk_ids": paper_info["chunk_ids"]
            }
            research_list.append(research_item)
        
        # Sort by publication year (newest first)
        research_list.sort(key=lambda x: x["publication_year"] or 0, reverse=True)
        
        return research_list
    
    def analyze_all_namespaces(self) -> Dict[str, Any]:
        """Analyze all namespaces"""
        namespaces = self.get_all_namespaces()
        results = {}
        
        for namespace in namespaces:
            results[namespace] = self.analyze_namespace(namespace)
        
        return results
    
    def save_analysis_report(self, analysis_results: Dict[str, Any], filename: str = None):
        """Save analysis results to JSON file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"pinecone_analysis_{timestamp}.json"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(analysis_results, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"Analysis results saved to {filename}.")
            return filename
            
        except Exception as e:
            logger.error(f"Failed to save analysis results: {e}")
            return None

async def main():
    """Main function"""
    analyzer = PineconeAnalyzer()
    
    print("=== Pinecone Data Analysis Tool ===")
    print("1. Analyze all namespaces")
    print("2. Analyze specific namespace")
    print("3. Check namespace list only")
    print("4. Check research list only")
    
    choice = input("\nSelect (1-4): ").strip()
    
    if choice == "1":
        print("\nAnalyzing all namespaces...")
        results = analyzer.analyze_all_namespaces()
        
        # Output results
        for namespace, result in results.items():
            print(f"\n=== Namespace: {namespace} ===")
            if "error" in result:
                print(f"Error: {result['error']}")
            else:
                print(f"Total vectors: {result['total_vectors']}")
                print(f"Actually retrieved vectors: {result['actual_vectors_retrieved']}")
                
                if result['model_analysis']:
                    print(f"Model versions: {result['model_analysis']['model_distribution']}")
                
                if result['paper_analysis']:
                    print(f"Paper count: {result['paper_analysis']['total_papers']}")
                    print(f"Average chunks per paper: {result['paper_analysis']['avg_chunks_per_paper']:.1f}")
                    
                    # Output research list
                    if result['paper_analysis']['research_list']:
                        print(f"\n=== Research List (Newest First) ===")
                        for i, research in enumerate(result['paper_analysis']['research_list'][:10], 1):  # Top 10 only
                            print(f"{i}. PMID: {research['pmid']}")
                            print(f"   Title: {research['title'][:80]}...")
                            print(f"   Journal: {research['journal']} ({research['publication_year']})")
                            print(f"   Chunk count: {research['chunk_count']}")
                            print(f"   Sections: {', '.join(research['sections'])}")
                            if research['doi']:
                                print(f"   DOI: {research['doi']}")
                            print()
        
        # Save file
        save = input("\nSave analysis results to file? (y/n): ").strip().lower()
        if save == 'y':
            filename = analyzer.save_analysis_report(results)
            if filename:
                print(f"Saved: {filename}")
    
    elif choice == "2":
        namespaces = analyzer.get_all_namespaces()
        print(f"\nAvailable namespaces: {namespaces}")
        
        namespace = input("Enter namespace to analyze: ").strip()
        if namespace == "":
            namespace = ""  # Default namespace
        
        result = analyzer.analyze_namespace(namespace)
        
        print(f"\n=== Namespace: {namespace} Analysis Results ===")
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Total vectors: {result['total_vectors']}")
            print(f"Actually retrieved vectors: {result['actual_vectors_retrieved']}")
            
            if result['model_analysis']:
                print(f"Model versions: {result['model_analysis']['model_distribution']}")
            
            if result['paper_analysis']:
                print(f"Paper count: {result['paper_analysis']['total_papers']}")
                print(f"Average chunks per paper: {result['paper_analysis']['avg_chunks_per_paper']:.1f}")
                
                # Output research list
                if result['paper_analysis']['research_list']:
                    print(f"\n=== Research List (Newest First) ===")
                    for i, research in enumerate(result['paper_analysis']['research_list'][:10], 1):  # Top 10 only
                        print(f"{i}. PMID: {research['pmid']}")
                        print(f"   Title: {research['title'][:80]}...")
                        print(f"   Journal: {research['journal']} ({research['publication_year']})")
                        print(f"   Chunk count: {research['chunk_count']}")
                        print(f"   Sections: {', '.join(research['sections'])}")
                        if research['doi']:
                            print(f"   DOI: {research['doi']}")
                        print()
            
            # Output sample vectors
            if result['sample_vectors']:
                print(f"\n=== Sample Vectors (Top 5) ===")
                for i, vector in enumerate(result['sample_vectors'], 1):
                    print(f"{i}. ID: {vector['id']}")
                    print(f"   Score: {vector['score']:.4f}")
                    print(f"   PMID: {vector['metadata'].get('pmid', 'N/A')}")
                    print(f"   Section: {vector['metadata'].get('chunk_section_type', 'N/A')}")
                    print()
    
    elif choice == "3":
        namespaces = analyzer.get_all_namespaces()
        print(f"\nAvailable namespaces:")
        for i, namespace in enumerate(namespaces, 1):
            print(f"{i}. '{namespace}'")
    
    elif choice == "4":
        namespaces = analyzer.get_all_namespaces()
        print(f"\nAvailable namespaces: {namespaces}")
        
        namespace = input("Enter namespace to check: ").strip()
        if namespace == "":
            namespace = ""  # Default namespace
        
        result = analyzer.analyze_namespace(namespace)
        
        if "error" in result:
            print(f"Error: {result['error']}")
        elif result['paper_analysis'] and result['paper_analysis']['research_list']:
            print(f"\n=== Namespace '{namespace}' Research List ===")
            print(f"Total {len(result['paper_analysis']['research_list'])} papers")
            print()
            
            for i, research in enumerate(result['paper_analysis']['research_list'], 1):
                print(f"{i:2d}. PMID: {research['pmid']}")
                print(f"    Title: {research['title']}")
                print(f"    Journal: {research['journal']} ({research['publication_year']})")
                print(f"    Chunk count: {research['chunk_count']}")
                print(f"    Sections: {', '.join(research['sections'])}")
                if research['doi']:
                    print(f"    DOI: {research['doi']}")
                if research['authors']:
                    print(f"    Authors: {', '.join(research['authors'][:3])}{'...' if len(research['authors']) > 3 else ''}")
                print()
        else:
            print("No research list available.")
    
    else:
        print("Invalid selection.")

if __name__ == "__main__":
    asyncio.run(main())
