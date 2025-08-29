#!/usr/bin/env python3
"""
Pinecone data dump script for BM25 search JSON
Data extraction for HQ(gpt-4o) vs LQ(gpt-3.5-turbo) model performance comparison
Includes all metadata and tags for complex weight experiments
"""
import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

# Add project root to Python path
sys.path.append(str(Path(__file__).parent.parent))

# Load .env file (important!)
from dotenv import load_dotenv
load_dotenv()

from app.services.rag_service import RAGService
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PineconeDumper:
    def __init__(self):
        self.index = None
        
    def connect_pinecone(self):
        """Initialize Pinecone client"""
        try:
            self.index = RAGService.get_pinecone_client()
            logger.info("Pinecone client connection successful")
            return True
        except Exception as e:
            logger.error(f"Pinecone connection failed: {e}")
            return False
    
    def get_namespace_stats(self, namespace: str) -> Dict[str, Any]:
        """Get namespace statistics"""
        try:
            stats = self.index.describe_index_stats()
            ns_info = stats.namespaces.get(namespace, {})
            return {
                "namespace": namespace,
                "vector_count": ns_info.vector_count if hasattr(ns_info, 'vector_count') else 0,
                "total_vectors": stats.total_vector_count
            }
        except Exception as e:
            logger.error(f"Failed to get namespace statistics: {e}")
            return {"namespace": namespace, "vector_count": 0, "total_vectors": 0}
    
    def extract_all_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Safely extract all metadata"""
        doc = {}
        
        # Basic text fields (for BM25 search)
        text_fields = {
            "title": metadata.get("title", ""),
            "abstract": metadata.get("abstract", ""),
            "text": metadata.get("text", ""),  # Chunk content
            "section_title": metadata.get("section_title", ""),
            "chunk_summary": metadata.get("chunk_summary", ""),
            "study_arms_text": metadata.get("study_arms_text", ""),
        }
        doc.update(text_fields)
        
        # Identifiers and basic information
        identifiers = {
            "url": metadata.get("url", ""),
            "pmid": metadata.get("pmid", ""),
            "pmcid": metadata.get("pmcid", ""),
            "doi": metadata.get("doi", ""),
            "model_version": metadata.get("model_version", ""),
            "tagging_timestamp": metadata.get("tagging_timestamp", ""),
            "start_idx": metadata.get("start_idx", 0),
            "end_idx": metadata.get("end_idx", 0),
        }
        doc.update(identifiers)
        
        # Journal and publication information
        publication_info = {
            "journal": metadata.get("journal", ""),
            "journal_issn": metadata.get("journal_issn", ""),
            "publication_year": metadata.get("publication_year", 0),
            "authors": metadata.get("authors", []),
            "mesh_terms": metadata.get("mesh_terms", []),
        }
        doc.update(publication_info)
        
        # Section information (for weight calculation)
        section_info = {
            "section_type": metadata.get("section_type", ""),
            "chunk_section_type": metadata.get("chunk_section_type", ""),
            "section_priority": metadata.get("section_priority", 0),
            "overlap_ratio": metadata.get("overlap_ratio", 0.0),
            "overlapping_sections": metadata.get("overlapping_sections", []),
        }
        doc.update(section_info)
        
        # Document level tags (doc_*)
        doc_level_tags = {
            "doc_study_type": metadata.get("doc_study_type", []),
            "doc_condition_disease": metadata.get("doc_condition_disease", []),
            "doc_target": metadata.get("doc_target", []),
            "doc_target_age_distribution": metadata.get("doc_target_age_distribution", []),
            "doc_num_of_participants": metadata.get("doc_num_of_participants", 0),
            "doc_study_duration": metadata.get("doc_study_duration", ""),
            "doc_intervention_type": metadata.get("doc_intervention_type", []),
            "doc_hormone_focus": metadata.get("doc_hormone_focus", []),
            "doc_target_symptoms": metadata.get("doc_target_symptoms", []),
            "doc_risk_of_bias": metadata.get("doc_risk_of_bias", ""),
            "doc_summary": metadata.get("doc_summary", ""),
        }
        doc.update(doc_level_tags)
        
        # Chunk level tags
        chunk_level_tags = {
            "intervention_type": metadata.get("intervention_type", []),
            "symptoms_focus": metadata.get("symptoms_focus", []),
            "hormone_focus": metadata.get("hormone_focus", []),
        }
        doc.update(chunk_level_tags)
        
        # Additional fields (in case we missed any)
        additional_fields = {}
        for key, value in metadata.items():
            if key not in doc:
                # Safely handle values
                if isinstance(value, (str, int, float, bool, list)):
                    additional_fields[key] = value
                elif isinstance(value, dict):
                    additional_fields[key] = value
                else:
                    additional_fields[key] = str(value)
        
        doc.update(additional_fields)
        
        return doc
    
    def extract_vectors_from_namespace(self, namespace: str, batch_size: int = 100) -> List[Dict[str, Any]]:
        """Extract all vectors from specific namespace (including all metadata)"""
        logger.info(f"Starting vector extraction from namespace '{namespace}'...")
        
        # Check namespace statistics
        stats = self.get_namespace_stats(namespace)
        total_count = stats["vector_count"]
        logger.info(f"Expected {total_count} vectors")
        
        documents = []
        
        try:
            # Method to get all vectors using dummy query
            # Pinecone doesn't have scan functionality, so use large top_k query
            max_fetch = min(total_count if total_count > 0 else 10000, 10000)  # Pinecone limit
            
            logger.info(f"Requesting up to {max_fetch} vectors...")
            
            response = self.index.query(
                vector=[0.0] * 1536,  # Dummy vector (text-embedding-3-small dimension)
                top_k=max_fetch,
                include_metadata=True,
                namespace=namespace
            )
            
            logger.info(f"Actually retrieved vectors: {len(response.matches)}")
            
            # Collect metadata key statistics
            all_keys = set()
            
            for i, match in enumerate(response.matches):
                # Extract all metadata
                doc = self.extract_all_metadata(match.metadata)
                
                # Add ID and similarity score
                doc["id"] = match.id
                doc["similarity_score"] = float(match.score)
                
                documents.append(doc)
                all_keys.update(doc.keys())
                
                # Progress logging
                if (i + 1) % 100 == 0:
                    logger.info(f"Processing progress: {i + 1}/{len(response.matches)}")
            
            logger.info(f"Extracted {len(documents)} documents from namespace '{namespace}'")
            logger.info(f"Found metadata keys: {len(all_keys)}")
            logger.info(f"Metadata key list: {sorted(all_keys)}")
            
            return documents
            
        except Exception as e:
            logger.error(f"Error during vector extraction: {e}")
            return documents
    
    def analyze_document_stats(self, documents: List[Dict]) -> Dict[str, Any]:
        """Document statistics analysis"""
        if not documents:
            return {}
        
        stats = {
            "total_documents": len(documents),
            "model_versions": {},
            "section_types": {},
            "chunk_section_types": {},
            "intervention_types": {},
            "hormone_focus": {},
            "doc_study_types": {},
            "doc_condition_disease": {},
            "publication_years": {},
            "text_lengths": [],
            "all_keys": set()
        }
        
        for doc in documents:
            # Collect all keys
            stats["all_keys"].update(doc.keys())
            
            # Model versions
            model = doc.get("model_version", "unknown")
            stats["model_versions"][model] = stats["model_versions"].get(model, 0) + 1
            
            # Section types
            section = doc.get("section_type", "unknown")
            stats["section_types"][section] = stats["section_types"].get(section, 0) + 1
            
            chunk_section = doc.get("chunk_section_type", "unknown")
            stats["chunk_section_types"][chunk_section] = stats["chunk_section_types"].get(chunk_section, 0) + 1
            
            # Intervention types
            interventions = doc.get("intervention_type", [])
            if isinstance(interventions, list):
                for intervention in interventions:
                    stats["intervention_types"][intervention] = stats["intervention_types"].get(intervention, 0) + 1
            
            # Hormone focus
            hormones = doc.get("hormone_focus", [])
            if isinstance(hormones, list):
                for hormone in hormones:
                    stats["hormone_focus"][hormone] = stats["hormone_focus"].get(hormone, 0) + 1
            
            # Document study types
            study_types = doc.get("doc_study_type", [])
            if isinstance(study_types, list):
                for study_type in study_types:
                    stats["doc_study_types"][study_type] = stats["doc_study_types"].get(study_type, 0) + 1
            
            # Document diseases/conditions
            conditions = doc.get("doc_condition_disease", [])
            if isinstance(conditions, list):
                for condition in conditions:
                    stats["doc_condition_disease"][condition] = stats["doc_condition_disease"].get(condition, 0) + 1
            
            # Publication years
            year = doc.get("publication_year", 0)
            if year > 0:
                stats["publication_years"][year] = stats["publication_years"].get(year, 0) + 1
            
            # Text lengths
            text_len = len(doc.get("text", ""))
            stats["text_lengths"].append(text_len)
        
        # Convert list to sorted list
        stats["all_keys"] = sorted(stats["all_keys"])
        
        return stats
    
    def save_to_json(self, documents: List[Dict], output_file: str, stats: Dict[str, Any] = None):
        """Save to JSON file"""
        try:
            # Save metadata and documents together
            output_data = {
                "metadata": {
                    "timestamp": datetime.now().isoformat(),
                    "document_count": len(documents),
                    "stats": stats
                },
                "documents": documents
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Data saved to {output_file} ({len(documents)} documents)")
            
            # Output statistics
            if stats:
                logger.info("=== Dump Statistics ===")
                logger.info(f"Total documents: {stats['total_documents']}")
                logger.info(f"Found metadata keys: {len(stats['all_keys'])}")
                
                logger.info("Model version distribution:")
                for model, count in sorted(stats["model_versions"].items()):
                    logger.info(f"  {model}: {count}")
                
                logger.info("Chunk section type distribution:")
                for section, count in sorted(stats["chunk_section_types"].items()):
                    if count > 0:
                        logger.info(f"  {section}: {count}")
                
                logger.info("Intervention type distribution:")
                for intervention, count in sorted(stats["intervention_types"].items()):
                    if count > 0:
                        logger.info(f"  {intervention}: {count}")
                
                if stats["text_lengths"]:
                    avg_length = sum(stats["text_lengths"]) / len(stats["text_lengths"])
                    logger.info(f"Average text length: {avg_length:.1f} characters")
            
        except Exception as e:
            logger.error(f"Failed to save JSON: {e}")

def main():
    """Main execution function"""
    dumper = PineconeDumper()
    
    # Connect to Pinecone
    if not dumper.connect_pinecone():
        logger.error("Pinecone connection failed, exiting")
        return
    
    # Create output directory
    output_dir = Path("data/bm25")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # HQ model (gpt-4o) data dump
    hq_namespace = "pcos-rag-gpt_4o"
    logger.info(f"=== HQ Model Data Dump: {hq_namespace} ===")
    hq_docs = dumper.extract_vectors_from_namespace(hq_namespace)
    hq_stats = dumper.analyze_document_stats(hq_docs) if hq_docs else {}
    
    if hq_docs:
        hq_file = output_dir / f"hq_documents_{timestamp}.json"
        dumper.save_to_json(hq_docs, str(hq_file), hq_stats)
    
    # LQ model (gpt-3.5-turbo) data dump  
    lq_namespace = "pcos-rag-gpt_3_5_turbo"
    logger.info(f"=== LQ Model Data Dump: {lq_namespace} ===")
    lq_docs = dumper.extract_vectors_from_namespace(lq_namespace)
    lq_stats = dumper.analyze_document_stats(lq_docs) if lq_docs else {}
    
    if lq_docs:
        lq_file = output_dir / f"lq_documents_{timestamp}.json"
        dumper.save_to_json(lq_docs, str(lq_file), lq_stats)
    
    # Create combined dataset (for comparison testing)
    all_docs = hq_docs + lq_docs
    if all_docs:
        combined_stats = dumper.analyze_document_stats(all_docs)
        combined_file = output_dir / f"combined_documents_{timestamp}.json"
        dumper.save_to_json(all_docs, str(combined_file), combined_stats)
        
        logger.info("=== Overall Statistics ===")
        logger.info(f"HQ documents: {len(hq_docs)}")
        logger.info(f"LQ documents: {len(lq_docs)}") 
        logger.info(f"Total documents: {len(all_docs)}")
        logger.info(f"Total metadata keys: {len(combined_stats.get('all_keys', []))}")
        logger.info(f"Key list: {combined_stats.get('all_keys', [])}")
    
    logger.info("Data dump completed!")

if __name__ == "__main__":
    main()