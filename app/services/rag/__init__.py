"""
RAG (Retrieval-Augmented Generation) Module for AUVRA
Contains all RAG-related services for research paper retrieval and processing
"""

import logging
logger = logging.getLogger(__name__)

# Safe imports - don't let one module failure break everything
try:
    from app.services.rag.paper_fetcher import (
        CompletePaperFetcher,
        fetch_papers_for_rag,
        PAPER_QUERIES,
        TIER_SUMMARY
    )
except ImportError as e:
    logger.warning(f"⚠️ RAG: paper_fetcher import failed: {e}")
    CompletePaperFetcher = None
    fetch_papers_for_rag = None
    PAPER_QUERIES = {}
    TIER_SUMMARY = {}

try:
    from app.services.rag.rag_retriever import (
        get_retriever,
        HybridRetriever
    )
except ImportError as e:
    logger.warning(f"⚠️ RAG: rag_retriever import failed: {e}")
    get_retriever = None
    HybridRetriever = None

try:
    from app.services.rag.rag_context_compiler import (
        get_context_compiler,
        ContextCompiler
    )
except ImportError as e:
    logger.warning(f"⚠️ RAG: rag_context_compiler import failed: {e}")
    get_context_compiler = None
    ContextCompiler = None

try:
    from app.services.rag.rag_citation_validator import (
        get_citation_validator,
        CitationValidator
    )
except ImportError as e:
    logger.warning(f"⚠️ RAG: rag_citation_validator import failed: {e}")
    get_citation_validator = None
    CitationValidator = None

try:
    from app.services.rag.rag_orchestrator import (
        get_rag_orchestrator,
        RAGOrchestrator,
        generate_rag_recommendations
    )
except ImportError as e:
    logger.error(f"❌ RAG: rag_orchestrator import failed: {e}")
    import traceback
    logger.error(f"Traceback: {traceback.format_exc()}")
    get_rag_orchestrator = None
    RAGOrchestrator = None
    generate_rag_recommendations = None

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
