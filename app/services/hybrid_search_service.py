"""
BM25 + Pinecone Vector hybrid search service
HQ vs LQ model performance comparison and complex weighting system support
"""
import os
import json
import logging
import nltk
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from rank_bm25 import BM25Okapi
from datetime import datetime
import pickle
import asyncio
from functools import lru_cache

from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

# NLTK tokenizer download (once only)
try:
    nltk.download('punkt', quiet=True)
    from nltk.tokenize import word_tokenize
except:
    logger.warning("NLTK tokenizer unavailable, using basic split")
    def word_tokenize(text):
        return text.lower().split()

class HybridSearchService:
    def __init__(self, data_dir: str = "data/bm25", service_type: str = "combined"):
        self.data_dir = Path(data_dir)
        self.service_type = service_type  # "combined", "hq_only", "lq_only"
        self.documents: List[Dict[str, Any]] = []
        self.bm25_indexes: Dict[str, BM25Okapi] = {}
        self.document_map: Dict[str, int] = {}  # id -> index mapping
        self.is_loaded = False
        
        # Field-specific weight settings (configurable for experiments)
        self.field_weights = {
            # Basic text fields
            "title": 4.0,
            "abstract": 3.0,
            "text": 1.0,  # main content
            "section_title": 2.0,
            "chunk_summary": 2.5,
            "study_arms_text": 2.0,
            
            # Tag-based fields
            "doc_summary": 2.0,
            "intervention_type_text": 3.0,  # convert list to text
            "hormone_focus_text": 3.0,
            "symptoms_focus_text": 3.0,
            "doc_study_type_text": 2.0,
            "doc_condition_disease_text": 3.5,
            "doc_target_symptoms_text": 3.0,
            "mesh_terms_text": 1.5,
            
            # Section-specific weights (methods, results, discussion, etc.)
            "methods_text": 0.5,
            "results_text": 1.5,
            "discussion_text": 1.3,
            "conclusion_text": 1.4,
            "introduction_text": 0.8,
        }
    
    def tokenize_text(self, text: str) -> List[str]:
        """Text tokenization (considering Korean-English mixed text)"""
        if not text:
            return []
        
        try:
            # Use NLTK tokenizer
            tokens = word_tokenize(text.lower())
            # Keep only Korean, English, and numbers
            tokens = [token for token in tokens if any(c.isalnum() for c in token)]
            return tokens
        except:
            # Fallback tokenizer
            return [word.strip().lower() for word in text.split() if word.strip()]
    
    def load_data_from_json(self, json_file: str) -> bool:
        """Load data from JSON file"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Support new format (metadata + documents) or old format (documents array)
            if isinstance(data, dict) and "documents" in data:
                self.documents = data["documents"]
                logger.info(f"Loaded with metadata format: {data.get('metadata', {}).get('document_count', 0)} documents")
            elif isinstance(data, list):
                self.documents = data
                logger.info(f"Loaded with basic format: {len(data)} documents")
            else:
                logger.error(f"Unsupported JSON format: {json_file}")
                return False
            
            # Create document ID mapping
            self.document_map = {doc["id"]: i for i, doc in enumerate(self.documents)}
            
            logger.info(f"Data loading completed: {len(self.documents)} documents")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load JSON file: {json_file}, error: {e}")
            return False
    
    def prepare_corpus_for_field(self, field_name: str) -> List[List[str]]:
        """Prepare corpus for specific field"""
        corpus = []
        
        for doc in self.documents:
            # Basic text fields
            if field_name in ["title", "abstract", "text", "section_title", "chunk_summary", "study_arms_text", "doc_summary"]:
                text = doc.get(field_name, "")
            
            # Convert list tags to text
            elif field_name == "intervention_type_text":
                interventions = doc.get("intervention_type", []) + doc.get("doc_intervention_type", [])
                text = " ".join(interventions) if interventions else ""
                
            elif field_name == "hormone_focus_text":
                hormones = doc.get("hormone_focus", []) + doc.get("doc_hormone_focus", [])
                text = " ".join(hormones) if hormones else ""
                
            elif field_name == "symptoms_focus_text":
                symptoms = doc.get("symptoms_focus", []) + doc.get("doc_target_symptoms", [])
                text = " ".join(symptoms) if symptoms else ""
                
            elif field_name == "doc_study_type_text":
                study_types = doc.get("doc_study_type", [])
                text = " ".join(study_types) if study_types else ""
                
            elif field_name == "doc_condition_disease_text":
                conditions = doc.get("doc_condition_disease", [])
                text = " ".join(conditions) if conditions else ""
                
            elif field_name == "doc_target_symptoms_text":
                target_symptoms = doc.get("doc_target_symptoms", [])
                text = " ".join(target_symptoms) if target_symptoms else ""
                
            elif field_name == "mesh_terms_text":
                mesh_terms = doc.get("mesh_terms", [])
                text = " ".join(mesh_terms) if mesh_terms else ""
            
            # Section-specific text (classified by section type)
            elif field_name.endswith("_text"):
                section_type = field_name.replace("_text", "")
                doc_section_type = doc.get("chunk_section_type", "").lower()
                if doc_section_type == section_type:
                    text = doc.get("text", "")
                else:
                    text = ""
            else:
                text = ""
            
            # Tokenization
            tokens = self.tokenize_text(str(text))
            corpus.append(tokens)
        
        return corpus
    
    def build_bm25_indexes(self):
        """Build BM25 indexes for all fields"""
        logger.info("Building BM25 indexes...")
        
        for field_name in self.field_weights.keys():
            logger.info(f"Building index for field '{field_name}'...")
            
            # Prepare corpus
            corpus = self.prepare_corpus_for_field(field_name)
            
            # Check if there are non-empty documents
            non_empty_count = sum(1 for tokens in corpus if tokens)
            logger.info(f"Field '{field_name}': {non_empty_count}/{len(corpus)} documents have content")
            
            if non_empty_count > 0:
                # Build BM25 index
                bm25 = BM25Okapi(corpus)
                self.bm25_indexes[field_name] = bm25
                logger.info(f"Field '{field_name}' index built successfully")
            else:
                logger.warning(f"Field '{field_name}': skipping index due to no content")
        
        logger.info(f"BM25 index building completed: {len(self.bm25_indexes)} fields")
    
    def save_indexes(self, cache_file: str):
        """Save BM25 indexes to file (prevent cold start)"""
        try:
            cache_data = {
                "indexes": self.bm25_indexes,
                "documents": self.documents,
                "document_map": self.document_map,
                "field_weights": self.field_weights,
                "timestamp": datetime.now().isoformat()
            }
            
            with open(cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
            
            logger.info(f"BM25 index cache saved: {cache_file}")
            
        except Exception as e:
            logger.error(f"Failed to save BM25 index cache: {e}")
    
    def load_indexes(self, cache_file: str) -> bool:
        """Load cached BM25 indexes"""
        try:
            if not os.path.exists(cache_file):
                return False
            
            with open(cache_file, 'rb') as f:
                cache_data = pickle.load(f)
            
            self.bm25_indexes = cache_data["indexes"]
            self.documents = cache_data["documents"]
            self.document_map = cache_data["document_map"]
            self.field_weights = cache_data.get("field_weights", self.field_weights)
            
            logger.info(f"BM25 index cache loaded: {len(self.bm25_indexes)} fields, {len(self.documents)} documents")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load BM25 index cache: {e}")
            return False
    
    def initialize(self, json_file: str = None, force_rebuild: bool = False):
        """Initialize (load data + build indexes)"""
        if self.is_loaded and not force_rebuild:
            return True
        
        # Auto-select JSON file
        if json_file is None:
            json_files = list(self.data_dir.glob("combined_documents_*.json"))
            if json_files:
                json_file = str(sorted(json_files)[-1])  # Latest file
                logger.info(f"Auto-selected latest data file: {json_file}")
            else:
                logger.error(f"Data file not found: {self.data_dir}")
                return False
        
        cache_file = str(self.data_dir / "bm25_indexes.pkl")
        
        # Try to load cache
        if not force_rebuild and self.load_indexes(cache_file):
            self.is_loaded = True
            return True
        
        # Load data
        if not self.load_data_from_json(json_file):
            return False
        
        # Build BM25 indexes
        self.build_bm25_indexes()
        
        # Save cache
        self.save_indexes(cache_file)
        
        self.is_loaded = True
        logger.info("Hybrid search service initialization completed")
        return True
    
    def lexical_search(self, query: str, top_k: int = 50) -> List[Dict[str, Any]]:
        """BM25-based lexical search"""
        if not self.is_loaded:
            logger.error("Service not initialized")
            return []
        
        query_tokens = self.tokenize_text(query)
        if not query_tokens:
            return []
        
        # Calculate scores by field
        field_scores = {}
        for field_name, bm25_index in self.bm25_indexes.items():
            try:
                scores = bm25_index.get_scores(query_tokens)
                field_scores[field_name] = scores
            except Exception as e:
                logger.warning(f"Failed to calculate scores for field '{field_name}': {e}")
                field_scores[field_name] = [0.0] * len(self.documents)
        
        # Calculate weighted sum
        final_scores = [0.0] * len(self.documents)
        for field_name, scores in field_scores.items():
            weight = self.field_weights.get(field_name, 1.0)
            for i, score in enumerate(scores):
                final_scores[i] += weight * score
        
        # Top top_k results
        doc_scores = [(i, score) for i, score in enumerate(final_scores)]
        doc_scores.sort(key=lambda x: x[1], reverse=True)
        
        results = []
        for i, score in doc_scores[:top_k]:
            if score > 0:  # Exclude 0 scores
                doc = self.documents[i].copy()
                doc["bm25_score"] = float(score)
                results.append(doc)
        
        logger.info(f"BM25 search completed: {len(results)} results (query: '{query[:50]}...')")
        return results
    
    async def dense_search(self, query: str, top_k: int = 50, namespace: str = None) -> List[Dict[str, Any]]:
        """Pinecone-based dense search"""
        try:
            # Use default namespace if none provided
            if namespace is None:
                namespace = RAGService.get_model_namespace()
            
            # Generate query embedding
            query_embedding = await RAGService.get_query_embedding(query)
            
            # Pinecone search
            index = RAGService.get_pinecone_client()
            search_results = index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace
            )
            
            # Transform results
            dense_results = []
            for match in search_results.matches:
                doc = {
                    "id": match.id,
                    "dense_score": float(match.score),
                    # Extract only necessary metadata
                    "title": match.metadata.get("title", ""),
                    "text": match.metadata.get("text", ""),
                    "url": match.metadata.get("url", ""),
                    "model_version": match.metadata.get("model_version", ""),
                }
                dense_results.append(doc)
            
            logger.info(f"Dense search completed: {len(dense_results)} results")
            return dense_results
            
        except Exception as e:
            logger.error(f"Dense search failed: {e}")
            return []
    
    def rrf_fusion(self, dense_results: List[Dict], lexical_results: List[Dict], k: int = 60, top_k: int = 20) -> List[Dict[str, Any]]:
        """Result fusion using Reciprocal Rank Fusion (RRF)"""
        
        # ID to rank mapping
        dense_rank = {doc["id"]: i for i, doc in enumerate(dense_results)}
        lexical_rank = {doc["id"]: i for i, doc in enumerate(lexical_results)}
        
        # Collect all document IDs
        all_ids = set(dense_rank.keys()) | set(lexical_rank.keys())
        
        # Calculate RRF scores
        fused_results = []
        for doc_id in all_ids:
            rrf_score = 0.0
            
            # Dense search contribution
            if doc_id in dense_rank:
                rrf_score += 1.0 / (k + dense_rank[doc_id] + 1)
            
            # Lexical search contribution
            if doc_id in lexical_rank:
                rrf_score += 1.0 / (k + lexical_rank[doc_id] + 1)
            
            # Get original document information
            doc_info = None
            if doc_id in self.document_map:
                doc_info = self.documents[self.document_map[doc_id]].copy()
            else:
                # Get from dense search results
                for d_doc in dense_results:
                    if d_doc["id"] == doc_id:
                        doc_info = d_doc.copy()
                        break
            
            if doc_info:
                doc_info["rrf_score"] = rrf_score
                doc_info["found_in"] = []
                
                if doc_id in dense_rank:
                    doc_info["found_in"].append("dense")
                    doc_info["dense_rank"] = dense_rank[doc_id]
                
                if doc_id in lexical_rank:
                    doc_info["found_in"].append("lexical")
                    doc_info["lexical_rank"] = lexical_rank[doc_id]
                
                fused_results.append(doc_info)
        
        # Sort by RRF score
        fused_results.sort(key=lambda x: x["rrf_score"], reverse=True)
        
        logger.info(f"RRF fusion completed: {len(fused_results)} results")
        return fused_results[:top_k]
    
    def filter_results_by_model(self, results: List[Dict[str, Any]], model_filter: str = None) -> List[Dict[str, Any]]:
        """Filter results by model version"""
        if not model_filter:
            return results
        
        filtered_results = []
        for result in results:
            model_version = result.get("model_version", "")
            
            # Model filter matching
            if model_filter == "hq" and "gpt-4o" in model_version:
                filtered_results.append(result)
            elif model_filter == "lq" and "gpt-3.5-turbo" in model_version:
                filtered_results.append(result)
            elif model_filter == model_version:
                filtered_results.append(result)
        
        return filtered_results

    async def hybrid_search(self, query: str, top_k: int = 20, lexical_k: int = 50, dense_k: int = 50, namespace: str = None, model_filter: str = None) -> Dict[str, Any]:
        """Hybrid search (BM25 + Pinecone + RRF)"""
        
        if not self.is_loaded:
            logger.error("Service not initialized")
            return {"error": "Service not initialized"}
        
        logger.info(f"Hybrid search started: '{query}' (namespace: {namespace}, model_filter: {model_filter})")
        
        # Execute parallel searches
        lexical_task = asyncio.create_task(
            asyncio.to_thread(self.lexical_search, query, lexical_k)
        )
        dense_task = asyncio.create_task(
            self.dense_search, query, dense_k, namespace
        )
        
        lexical_results, dense_results = await asyncio.gather(lexical_task, dense_task)
        
        # Apply model filtering
        if model_filter:
            lexical_results = self.filter_results_by_model(lexical_results, model_filter)
            dense_results = self.filter_results_by_model(dense_results, model_filter)
            logger.info(f"Model filtering applied: lexical={len(lexical_results)}, dense={len(dense_results)}")
        
        # RRF fusion
        fused_results = self.rrf_fusion(dense_results, lexical_results, k=60, top_k=top_k)
        
        # Result statistics
        result_stats = {
            "total_results": len(fused_results),
            "lexical_only": len([r for r in fused_results if r["found_in"] == ["lexical"]]),
            "dense_only": len([r for r in fused_results if r["found_in"] == ["dense"]]),
            "both": len([r for r in fused_results if len(r["found_in"]) == 2]),
            "lexical_total": len(lexical_results),
            "dense_total": len(dense_results),
            "model_filter": model_filter,
        }
        
        return {
            "query": query,
            "results": fused_results,
            "stats": result_stats,
            "timestamp": datetime.now().isoformat()
        }

# Global service instance (singleton)
_hybrid_search_service = None

def get_hybrid_search_service() -> HybridSearchService:
    """Return hybrid search service instance (singleton)"""
    global _hybrid_search_service
    
    if _hybrid_search_service is None:
        _hybrid_search_service = HybridSearchService()
        # Initialization must be called separately
    
    return _hybrid_search_service