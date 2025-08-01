#!/usr/bin/env python3
"""
Pinecone namespace check script
"""

import asyncio
import os
import sys
import logging
from typing import List, Dict, Any

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag_service import RAGService

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_pinecone_namespace():
    """Check Pinecone namespace"""
    try:
        logger.info("Pinecone namespace check started")
        
        # 1. Pinecone client connection
        index = RAGService.get_pinecone_client()
        logger.info("✓ Pinecone client connection successful")
        
        # 2. Index statistics check (all namespaces)
        stats = index.describe_index_stats()
        logger.info(f"Total index statistics: {stats}")
        
        # 3. Vector count per namespace
        if hasattr(stats, 'namespaces'):
            logger.info("Vectors per namespace:")
            for namespace, namespace_stats in stats.namespaces.items():
                vector_count = namespace_stats.get('vector_count', 0)
                logger.info(f"  - {namespace}: {vector_count} vectors")
        else:
            logger.warning("No namespaces information available.")
            
        # 4. Specific namespace check
        target_namespace = "pcos-rag"
        logger.info(f"\n'{target_namespace}' namespace check:")
        
        # Try to query vectors from the namespace
        try:
            # Search with dummy vector (query all vectors)
            dummy_vector = [0.0] * 1536
            results = index.query(
                vector=dummy_vector,
                top_k=10,
                namespace=target_namespace,
                include_metadata=True
            )
            
            logger.info(f"Found {len(results.matches)} vectors in '{target_namespace}' namespace")
            
            if results.matches:
                logger.info("First 3 vectors:")
                for i, match in enumerate(results.matches[:3]):
                    logger.info(f"  {i+1}. ID: {match.id}")
                    logger.info(f"     Title: {match.metadata.get('title', 'N/A')}")
                    logger.info(f"     Score: {match.score}")
            else:
                logger.warning(f"No vectors found in '{target_namespace}' namespace.")
                
        except Exception as e:
            logger.error(f"'{target_namespace}' namespace query failed: {e}")
            
        # 5. Default namespace check (namespace=None)
        logger.info(f"\nDefault namespace (namespace=None) check:")
        try:
            results = index.query(
                vector=dummy_vector,
                top_k=10,
                namespace=None,  # Default namespace
                include_metadata=True
            )
            
            logger.info(f"Found {len(results.matches)} vectors in default namespace")
            
            if results.matches:
                logger.info("First 3 vectors:")
                for i, match in enumerate(results.matches[:3]):
                    logger.info(f"  {i+1}. ID: {match.id}")
                    logger.info(f"     Title: {match.metadata.get('title', 'N/A')}")
                    logger.info(f"     Score: {match.score}")
            else:
                logger.warning("No vectors found in default namespace.")
                
        except Exception as e:
            logger.error(f"Default namespace query failed: {e}")
        
    except Exception as e:
        logger.error(f"Check failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(check_pinecone_namespace()) 