"""
Pinecone Image Similarity Search Service for AUVRA
Production-ready implementation with CLIP embeddings

Usage:
    from app.services.pinecone_image_search import (
        PineconeConfig,
        PineconeFoodImageSearch,
        CLIPEmbedder
    )
    
    config = PineconeConfig(api_key="your-api-key")
    search = PineconeFoodImageSearch(config)
    embedder = CLIPEmbedder()
"""

import os
import time
import random
import logging
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# Pinecone imports
try:
    from pinecone import Pinecone, ServerlessSpec
    from pinecone.grpc import PineconeGRPC
    from pinecone.exceptions import PineconeException
    PINECONE_AVAILABLE = True
except ImportError:
    PINECONE_AVAILABLE = False
    PineconeException = Exception

# CLIP imports (optional - can use external embeddings)
try:
    import torch
    from PIL import Image
    from transformers import CLIPProcessor, CLIPModel
    CLIP_AVAILABLE = True
except ImportError:
    CLIP_AVAILABLE = False

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class PineconeConfig:
    """
    Pinecone configuration for AUVRA food images.
    
    Attributes:
        api_key: Pinecone API key (required)
        index_name: Name of the index
        dimension: Vector dimension (512 for CLIP ViT-B/32)
        metric: Similarity metric (cosine recommended for CLIP)
        cloud: Cloud provider (aws only for free tier)
        region: Cloud region (us-east-1 only for free tier)
        upsert_batch_size: Records per upsert batch
        query_batch_size: Queries per batch
        max_retries: Maximum retry attempts
        base_delay: Initial retry delay in seconds
        max_delay: Maximum retry delay in seconds
        cache_ttl: Query cache TTL in seconds
        cache_max_size: Maximum cache entries
    """
    api_key: str = field(default_factory=lambda: os.environ.get("PINECONE_API_KEY", ""))
    index_name: str = "auvra-food-images"
    dimension: int = 512
    metric: str = "cosine"
    cloud: str = "aws"
    region: str = "us-east-1"
    upsert_batch_size: int = 100
    query_batch_size: int = 10
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    cache_ttl: int = 300
    cache_max_size: int = 1000
    
    def __post_init__(self):
        if not self.api_key:
            raise ValueError(
                "Pinecone API key is required. "
                "Set PINECONE_API_KEY environment variable or pass api_key parameter."
            )


# =============================================================================
# CLIP Embedding Generator
# =============================================================================

class CLIPEmbedder:
    """
    Generate CLIP embeddings for images and text.
    
    Supports:
    - Single image embedding
    - Batch image embedding
    - Text embedding (for text-to-image search)
    
    Example:
        embedder = CLIPEmbedder()
        vector = embedder.embed_image(image)
        text_vector = embedder.embed_text("grilled chicken")
    """
    
    def __init__(
        self,
        model_name: str = "openai/clip-vit-base-patch32",
        device: Optional[str] = None
    ):
        """
        Initialize CLIP model.
        
        Args:
            model_name: HuggingFace model name
            device: Device to use ("cuda", "cpu", or None for auto-detect)
        """
        if not CLIP_AVAILABLE:
            raise ImportError(
                "CLIP dependencies not available. "
                "Install with: pip install torch transformers pillow"
            )
        
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        
        logger.info(f"Loading CLIP model '{model_name}' on {self.device}...")
        
        self.model = CLIPModel.from_pretrained(model_name).to(self.device)
        self.processor = CLIPProcessor.from_pretrained(model_name)
        self.model.eval()
        
        # Get embedding dimension
        with torch.no_grad():
            dummy_input = self.processor(
                images=Image.new('RGB', (224, 224)),
                return_tensors="pt"
            ).to(self.device)
            dummy_output = self.model.get_image_features(**dummy_input)
            self.dimension = dummy_output.shape[-1]
        
        logger.info(f"CLIP model loaded. Embedding dimension: {self.dimension}")
    
    def embed_image(self, image: "Image.Image") -> List[float]:
        """
        Generate normalized embedding for a single image.
        
        Args:
            image: PIL Image object
        
        Returns:
            List of floats (embedding vector)
        """
        with torch.no_grad():
            inputs = self.processor(images=image, return_tensors="pt").to(self.device)
            features = self.model.get_image_features(**inputs)
            # L2 normalize for cosine similarity
            features = features / features.norm(p=2, dim=-1, keepdim=True)
            return features.cpu().numpy().flatten().tolist()
    
    def embed_images_batch(
        self,
        images: List["Image.Image"],
        batch_size: int = 32
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple images.
        
        Args:
            images: List of PIL Image objects
            batch_size: Processing batch size
        
        Returns:
            List of embedding vectors
        """
        all_embeddings = []
        
        for i in range(0, len(images), batch_size):
            batch = images[i:i + batch_size]
            
            with torch.no_grad():
                inputs = self.processor(
                    images=batch,
                    return_tensors="pt",
                    padding=True
                ).to(self.device)
                features = self.model.get_image_features(**inputs)
                features = features / features.norm(p=2, dim=-1, keepdim=True)
                all_embeddings.extend(features.cpu().numpy().tolist())
        
        return all_embeddings
    
    def embed_text(self, text: str) -> List[float]:
        """
        Generate embedding for text query (for text-to-image search).
        
        Args:
            text: Query text
        
        Returns:
            Embedding vector
        """
        with torch.no_grad():
            inputs = self.processor(
                text=[text],
                return_tensors="pt",
                padding=True
            ).to(self.device)
            features = self.model.get_text_features(**inputs)
            features = features / features.norm(p=2, dim=-1, keepdim=True)
            return features.cpu().numpy().flatten().tolist()
    
    def embed_texts_batch(
        self,
        texts: List[str],
        batch_size: int = 32
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple text queries.
        
        Args:
            texts: List of query texts
            batch_size: Processing batch size
        
        Returns:
            List of embedding vectors
        """
        all_embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            with torch.no_grad():
                inputs = self.processor(
                    text=batch,
                    return_tensors="pt",
                    padding=True
                ).to(self.device)
                features = self.model.get_text_features(**inputs)
                features = features / features.norm(p=2, dim=-1, keepdim=True)
                all_embeddings.extend(features.cpu().numpy().tolist())
        
        return all_embeddings


# =============================================================================
# Retry Logic
# =============================================================================

def exponential_backoff_retry(
    func,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_status_codes: Tuple[int, ...] = (429, 500, 502, 503, 504)
):
    """
    Retry a function with exponential backoff and jitter.
    
    Args:
        func: Function to retry
        max_retries: Maximum retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap
        retryable_status_codes: HTTP codes that trigger retry
    
    Returns:
        Function result
    
    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return func()
        except PineconeException as e:
            last_exception = e
            status_code = getattr(e, 'status', None) or getattr(e, 'status_code', None)
            
            # Don't retry client errors (4xx except 429)
            if status_code and 400 <= status_code < 500 and status_code != 429:
                logger.error(f"Non-retryable error (status {status_code}): {e}")
                raise
            
            if attempt == max_retries - 1:
                logger.error(f"Max retries ({max_retries}) exceeded")
                raise
            
            # Exponential backoff with jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            wait_time = delay + jitter
            
            logger.warning(
                f"Retry {attempt + 1}/{max_retries} after {wait_time:.2f}s "
                f"(error: {type(e).__name__})"
            )
            time.sleep(wait_time)
            
        except Exception as e:
            last_exception = e
            
            if attempt == max_retries - 1:
                logger.error(f"Max retries exceeded: {e}")
                raise
            
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            wait_time = delay + jitter
            
            logger.warning(f"Retry {attempt + 1}/{max_retries} after {wait_time:.2f}s: {e}")
            time.sleep(wait_time)
    
    raise last_exception


# =============================================================================
# Query Cache
# =============================================================================

class QueryCache:
    """
    In-memory LRU cache for query results with TTL expiration.
    
    Features:
    - Hash-based cache keys
    - TTL expiration
    - LRU eviction when full
    """
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        """
        Initialize cache.
        
        Args:
            max_size: Maximum cached entries
            ttl_seconds: Time-to-live for entries
        """
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._hits = 0
        self._misses = 0
    
    def _hash_query(
        self,
        vector: List[float],
        top_k: int,
        namespace: str,
        filter_dict: Optional[Dict]
    ) -> str:
        """Generate cache key from query parameters."""
        # Use first/last 5 elements for faster hashing
        vector_sample = vector[:5] + vector[-5:] if len(vector) > 10 else vector
        key_data = f"{vector_sample}-{top_k}-{namespace}-{filter_dict}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def get(
        self,
        vector: List[float],
        top_k: int,
        namespace: str,
        filter_dict: Optional[Dict]
    ) -> Optional[Any]:
        """Get cached result if valid."""
        key = self._hash_query(vector, top_k, namespace, filter_dict)
        
        if key in self.cache:
            result, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl_seconds:
                self._hits += 1
                logger.debug(f"Cache hit (key: {key[:8]}...)")
                return result
            else:
                del self.cache[key]
        
        self._misses += 1
        return None
    
    def set(
        self,
        vector: List[float],
        top_k: int,
        namespace: str,
        filter_dict: Optional[Dict],
        result: Any
    ):
        """Cache a query result."""
        # Evict oldest if full
        if len(self.cache) >= self.max_size:
            oldest_keys = sorted(
                self.cache.keys(),
                key=lambda k: self.cache[k][1]
            )[:self.max_size // 4]
            for key in oldest_keys:
                del self.cache[key]
            logger.debug(f"Evicted {len(oldest_keys)} cache entries")
        
        key = self._hash_query(vector, top_k, namespace, filter_dict)
        self.cache[key] = (result, time.time())
    
    def clear(self):
        """Clear all cached entries."""
        self.cache.clear()
        self._hits = 0
        self._misses = 0
    
    @property
    def hit_rate(self) -> float:
        """Get cache hit rate."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self.hit_rate:.2%}"
        }


# =============================================================================
# Main Client
# =============================================================================

class PineconeFoodImageSearch:
    """
    Production-ready Pinecone client for AUVRA food image similarity search.
    
    Features:
    - Index creation and management
    - Batch upsert with retry logic
    - Cached queries with filtering
    - Metadata updates
    - Deletion by ID or filter
    
    Example:
        config = PineconeConfig(api_key="your-key")
        search = PineconeFoodImageSearch(config)
        
        # Upsert
        search.upsert_single(
            id="food_001",
            vector=[0.1, 0.2, ...],
            metadata={"food_name": "Apple"},
            namespace="fruits"
        )
        
        # Query
        results = search.query(
            vector=[0.1, 0.2, ...],
            top_k=10,
            namespace="fruits"
        )
    """
    
    def __init__(self, config: PineconeConfig):
        """
        Initialize Pinecone client.
        
        Args:
            config: PineconeConfig instance
        """
        if not PINECONE_AVAILABLE:
            raise ImportError(
                "Pinecone not available. "
                "Install with: pip install pinecone 'pinecone[grpc]'"
            )
        
        self.config = config
        self.cache = QueryCache(
            max_size=config.cache_max_size,
            ttl_seconds=config.cache_ttl
        )
        
        # Use gRPC client for better performance
        self.pc = PineconeGRPC(api_key=config.api_key)
        
        # Create index if needed
        self._ensure_index_exists()
        
        # Connect to index
        self.index = self.pc.Index(config.index_name)
        
        logger.info(f"Connected to Pinecone index: {config.index_name}")
    
    def _ensure_index_exists(self):
        """Create index if it doesn't exist."""
        if not self.pc.has_index(self.config.index_name):
            logger.info(f"Creating index: {self.config.index_name}")
            
            self.pc.create_index(
                name=self.config.index_name,
                vector_type="dense",
                dimension=self.config.dimension,
                metric=self.config.metric,
                spec=ServerlessSpec(
                    cloud=self.config.cloud,
                    region=self.config.region
                ),
                deletion_protection="disabled",
                tags={
                    "project": "auvra",
                    "type": "food-images",
                    "created": datetime.utcnow().isoformat()
                }
            )
            
            # Wait for ready
            logger.info("Waiting for index to be ready...")
            timeout = 60
            start = time.time()
            while not self.pc.describe_index(self.config.index_name).status.ready:
                if time.time() - start > timeout:
                    raise TimeoutError("Index creation timed out")
                time.sleep(1)
            
            logger.info("Index created and ready")
        else:
            logger.info(f"Index {self.config.index_name} already exists")
    
    # =========================================================================
    # Upsert Operations
    # =========================================================================
    
    def upsert_single(
        self,
        id: str,
        vector: List[float],
        metadata: Dict[str, Any],
        namespace: str = ""
    ) -> Dict:
        """
        Upsert a single vector with metadata.
        
        Args:
            id: Unique identifier
            vector: Embedding vector
            metadata: Metadata dictionary
            namespace: Target namespace
        
        Returns:
            Upsert response
        """
        def _upsert():
            return self.index.upsert(
                vectors=[{"id": id, "values": vector, "metadata": metadata}],
                namespace=namespace
            )
        
        return exponential_backoff_retry(
            _upsert,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
            max_delay=self.config.max_delay
        )
    
    def upsert_batch(
        self,
        records: List[Dict[str, Any]],
        namespace: str = ""
    ) -> Dict:
        """
        Upsert multiple vectors in batches.
        
        Args:
            records: List of {"id", "values", "metadata"} dicts
            namespace: Target namespace
        
        Returns:
            Combined response with total upserted count
        """
        total_upserted = 0
        batch_size = self.config.upsert_batch_size
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            def _upsert_batch(b=batch):
                return self.index.upsert(vectors=b, namespace=namespace)
            
            result = exponential_backoff_retry(
                _upsert_batch,
                max_retries=self.config.max_retries,
                base_delay=self.config.base_delay,
                max_delay=self.config.max_delay
            )
            
            total_upserted += result.upserted_count
            logger.info(f"Upserted batch {i // batch_size + 1}: {len(batch)} records")
        
        return {"upserted_count": total_upserted}
    
    def upsert_parallel(
        self,
        records: List[Dict[str, Any]],
        namespace: str = "",
        pool_threads: int = 10
    ) -> Dict:
        """
        Upsert vectors in parallel for maximum throughput.
        
        Args:
            records: List of records
            namespace: Target namespace
            pool_threads: Number of parallel threads
        
        Returns:
            Combined response
        """
        from pinecone import Pinecone as PineconeREST
        
        pc = PineconeREST(api_key=self.config.api_key, pool_threads=pool_threads)
        index = pc.Index(self.config.index_name)
        batch_size = self.config.upsert_batch_size
        
        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]
        
        async_results = [
            index.upsert(vectors=batch, namespace=namespace, async_req=True)
            for batch in chunks(records, batch_size)
        ]
        
        results = [ar.get() for ar in async_results]
        total_upserted = sum(r.upserted_count for r in results)
        
        logger.info(f"Parallel upsert complete: {total_upserted} records")
        return {"upserted_count": total_upserted}
    
    # =========================================================================
    # Query Operations
    # =========================================================================
    
    def query(
        self,
        vector: List[float],
        top_k: int = 10,
        namespace: str = "",
        filter: Optional[Dict] = None,
        include_metadata: bool = True,
        include_values: bool = False,
        use_cache: bool = True
    ) -> Dict:
        """
        Query for similar vectors.
        
        Args:
            vector: Query vector
            top_k: Number of results (max 10,000)
            namespace: Target namespace
            filter: Metadata filter expression
            include_metadata: Include metadata in results
            include_values: Include vectors in results
            use_cache: Use query cache
        
        Returns:
            Query results with matches
        """
        # Check cache
        if use_cache:
            cached = self.cache.get(vector, top_k, namespace, filter)
            if cached is not None:
                return cached
        
        def _query():
            return self.index.query(
                vector=vector,
                top_k=top_k,
                namespace=namespace,
                filter=filter,
                include_metadata=include_metadata,
                include_values=include_values
            )
        
        result = exponential_backoff_retry(
            _query,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
            max_delay=self.config.max_delay
        )
        
        # Cache result
        if use_cache:
            self.cache.set(vector, top_k, namespace, filter, result)
        
        return result
    
    def query_with_filters(
        self,
        vector: List[float],
        top_k: int = 10,
        namespace: str = "",
        category: Optional[str] = None,
        dietary_tags: Optional[List[str]] = None,
        max_calories: Optional[float] = None,
        min_protein: Optional[float] = None,
        verified_only: bool = False,
        use_cache: bool = True
    ) -> Dict:
        """
        Query with common food-specific filters.
        
        Args:
            vector: Query vector
            top_k: Number of results
            namespace: Target namespace
            category: Food category filter
            dietary_tags: Required dietary tags
            max_calories: Maximum calories per 100g
            min_protein: Minimum protein per 100g
            verified_only: Only verified images
            use_cache: Use query cache
        
        Returns:
            Filtered query results
        """
        conditions = []
        
        if category:
            conditions.append({"food_category": {"$eq": category}})
        
        if dietary_tags:
            for tag in dietary_tags:
                conditions.append({"dietary_tags": {"$eq": tag}})
        
        if max_calories is not None:
            conditions.append({"calories_per_100g": {"$lte": max_calories}})
        
        if min_protein is not None:
            conditions.append({"protein_g": {"$gte": min_protein}})
        
        if verified_only:
            conditions.append({"verified": {"$eq": True}})
        
        filter_dict = None
        if conditions:
            filter_dict = {"$and": conditions} if len(conditions) > 1 else conditions[0]
        
        return self.query(
            vector=vector,
            top_k=top_k,
            namespace=namespace,
            filter=filter_dict,
            use_cache=use_cache
        )
    
    # =========================================================================
    # Update Operations
    # =========================================================================
    
    def update_metadata(
        self,
        id: str,
        metadata: Dict[str, Any],
        namespace: str = ""
    ) -> Dict:
        """
        Update metadata for an existing record.
        
        Args:
            id: Record ID
            metadata: Metadata to update (merged with existing)
            namespace: Target namespace
        
        Returns:
            Update response
        """
        def _update():
            return self.index.update(
                id=id,
                set_metadata=metadata,
                namespace=namespace
            )
        
        return exponential_backoff_retry(
            _update,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
            max_delay=self.config.max_delay
        )
    
    def update_vector(
        self,
        id: str,
        vector: List[float],
        namespace: str = ""
    ) -> Dict:
        """
        Update vector for an existing record.
        
        Args:
            id: Record ID
            vector: New vector values
            namespace: Target namespace
        
        Returns:
            Update response
        """
        def _update():
            return self.index.update(
                id=id,
                values=vector,
                namespace=namespace
            )
        
        return exponential_backoff_retry(
            _update,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
            max_delay=self.config.max_delay
        )
    
    # =========================================================================
    # Delete Operations
    # =========================================================================
    
    def delete_by_ids(
        self,
        ids: List[str],
        namespace: str = ""
    ) -> Dict:
        """
        Delete records by IDs.
        
        Args:
            ids: List of record IDs (batched in chunks of 1000)
            namespace: Target namespace
        
        Returns:
            Delete response
        """
        deleted_count = 0
        
        for i in range(0, len(ids), 1000):
            batch = ids[i:i + 1000]
            
            def _delete(b=batch):
                return self.index.delete(ids=b, namespace=namespace)
            
            exponential_backoff_retry(
                _delete,
                max_retries=self.config.max_retries,
                base_delay=self.config.base_delay,
                max_delay=self.config.max_delay
            )
            deleted_count += len(batch)
        
        logger.info(f"Deleted {deleted_count} records")
        return {"deleted_count": deleted_count}
    
    def delete_by_metadata(
        self,
        filter: Dict,
        namespace: str = ""
    ) -> Dict:
        """
        Delete records matching a metadata filter.
        
        Args:
            filter: Metadata filter expression
            namespace: Target namespace
        
        Returns:
            Delete response
        """
        def _delete():
            return self.index.delete(filter=filter, namespace=namespace)
        
        return exponential_backoff_retry(
            _delete,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
            max_delay=self.config.max_delay
        )
    
    def delete_namespace(self, namespace: str) -> Dict:
        """
        Delete all records in a namespace.
        
        Args:
            namespace: Namespace to clear
        
        Returns:
            Delete response
        """
        def _delete():
            return self.index.delete(delete_all=True, namespace=namespace)
        
        return exponential_backoff_retry(
            _delete,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
            max_delay=self.config.max_delay
        )
    
    # =========================================================================
    # Utility Operations
    # =========================================================================
    
    def get_stats(self) -> Dict:
        """Get index statistics."""
        return self.index.describe_index_stats()
    
    def fetch_by_ids(
        self,
        ids: List[str],
        namespace: str = ""
    ) -> Dict:
        """
        Fetch records by IDs.
        
        Args:
            ids: List of record IDs (max 1000)
            namespace: Target namespace
        
        Returns:
            Fetched records
        """
        def _fetch():
            return self.index.fetch(ids=ids, namespace=namespace)
        
        return exponential_backoff_retry(
            _fetch,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
            max_delay=self.config.max_delay
        )
    
    def health_check(self) -> Dict:
        """
        Check Pinecone connectivity and index status.
        
        Returns:
            Health status dictionary
        """
        try:
            stats = self.get_stats()
            return {
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "index": {
                    "name": self.config.index_name,
                    "total_vectors": stats.total_vector_count,
                    "dimension": stats.dimension,
                    "metric": self.config.metric
                },
                "namespaces": {
                    ns: {"vector_count": ns_stats.vector_count}
                    for ns, ns_stats in stats.namespaces.items()
                },
                "cache": self.cache.stats
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }
    
    def clear_cache(self):
        """Clear the query cache."""
        self.cache.clear()
        logger.info("Query cache cleared")


# =============================================================================
# Factory Functions
# =============================================================================

def create_search_client(
    api_key: Optional[str] = None,
    index_name: str = "auvra-food-images"
) -> PineconeFoodImageSearch:
    """
    Factory function to create a search client.
    
    Args:
        api_key: Pinecone API key (uses env var if not provided)
        index_name: Index name
    
    Returns:
        Configured PineconeFoodImageSearch instance
    """
    config = PineconeConfig(
        api_key=api_key or os.environ.get("PINECONE_API_KEY", ""),
        index_name=index_name
    )
    return PineconeFoodImageSearch(config)


def create_embedder(
    model_name: str = "openai/clip-vit-base-patch32"
) -> CLIPEmbedder:
    """
    Factory function to create a CLIP embedder.
    
    Args:
        model_name: HuggingFace model name
    
    Returns:
        Configured CLIPEmbedder instance
    """
    return CLIPEmbedder(model_name=model_name)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Example usage
    print("Pinecone Image Search Module")
    print("=" * 50)
    print("\nUsage:")
    print("  from app.services.pinecone_image_search import (")
    print("      create_search_client, create_embedder")
    print("  )")
    print("")
    print("  search = create_search_client()")
    print("  embedder = create_embedder()")
    print("")
    print("  # Embed an image")
    print("  vector = embedder.embed_image(pil_image)")
    print("")
    print("  # Upsert to Pinecone")
    print("  search.upsert_single('id', vector, {'name': 'Apple'}, 'fruits')")
    print("")
    print("  # Query")
    print("  results = search.query(vector, top_k=10)")
