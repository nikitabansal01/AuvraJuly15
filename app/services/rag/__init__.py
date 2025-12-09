"""
RAG (Retrieval-Augmented Generation) Module for AUVRA
Contains all RAG-related services for research paper retrieval and processing
"""

from app.services.rag.paper_fetcher import (
    CompletePaperFetcher,
    fetch_papers_for_rag,
    PAPER_QUERIES,
    TIER_SUMMARY
)
from app.services.rag.rag_retriever import (
    get_retriever,
    HybridRetriever
)
from app.services.rag.rag_context_compiler import (
    get_context_compiler,
    ContextCompiler
)
from app.services.rag.rag_citation_validator import (
    get_citation_validator,
    CitationValidator
)
from app.services.rag.rag_orchestrator import (
    get_rag_orchestrator,
    RAGOrchestrator,
    generate_rag_recommendations
)

__all__ = [
    'CompletePaperFetcher',
    'fetch_papers_for_rag',
    'PAPER_QUERIES',
    'TIER_SUMMARY',
    'get_retriever',
    'HybridRetriever',
    'get_context_compiler',
    'ContextCompiler',
    'get_citation_validator',
    'CitationValidator',
    'get_rag_orchestrator',
    'RAGOrchestrator',
    'generate_rag_recommendations',
]
