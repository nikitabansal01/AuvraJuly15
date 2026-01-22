"""
AUVRA Image Library Service

Semantic image caching and generation using:
- RunPod FLUX.1 Schnell for ultra-fast image generation ($0.0006/image at 512x512)
- OpenAI ada-002 for embeddings ($0.0001/call)  
- Cloudinary for image hosting
- PostgreSQL for semantic matching

Features:
- FLUX.1 Schnell: optimized for 1-4 step sampling, ultra-low latency
- Semantic embedding matching (cosine similarity > 0.90)
- Cross-user image reuse (never same image for same user)
- All 16 images generated per day (4 actions × 4 variants)
- 512x512 resolution for speed and cost optimization
"""

import os
import base64
import hashlib
import logging
import time
import json
import asyncio
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert

logger = logging.getLogger(__name__)


class ImageLibraryService:
    """
    Image generation and semantic caching service.
    
    Uses RunPod FLUX.1 Schnell for ultra-fast image generation.
    Optimized for 1-4 step sampling with 512x512 resolution.
    Stores all images with embeddings for semantic reuse.
    """
    
    # SIMILARITY_THRESHOLD: Balance between preventing false matches and enabling reuse
    # - 0.95+ = Very strict, few cache hits but high precision
    # - 0.90 = Was the old setting - only ~2 cache hits per plan
    # - 0.85 = Better balance - ~10-12 cache hits per plan, saves ~80% of image generation time
    # Based on log analysis: top similarities often hit 0.85-0.89 range
    SIMILARITY_THRESHOLD = 0.85  # Lowered from 0.90 for better cache hit rate (saves ~2+ minutes)
    
    # RunPod FLUX.1 Schnell pricing: $0.0024 per megapixel
    # 512x512 = 0.262 megapixels = ~$0.0006 per image
    COST_PER_IMAGE = 0.0006
    
    # Embedding model
    EMBEDDING_MODEL = "text-embedding-ada-002"  # or "text-embedding-3-small"
    EMBEDDING_DIMENSION = 1536
    
    # Retry settings - FLUX.1 Schnell is very fast and reliable
    MAX_IMAGE_RETRIES = 2  # Fewer retries needed
    RETRY_DELAYS = [0.5, 1.0]  # Fast retries
    
    # RunPod timeout settings - Schnell is ultra-fast
    RUNPOD_SYNC_TIMEOUT = 30.0  # 30s timeout (Schnell is much faster)
    RUNPOD_POLL_TIMEOUT = 45  # 45 seconds max (rarely needed)
    RUNPOD_POLL_INTERVAL = 0.3  # Poll every 0.3s for faster response
    
    # Fallback images when generation fails - prevents empty image URLs in database
    # These are hosted on Cloudinary and match the app's visual style
    FALLBACK_IMAGE_URLS = {
        "food": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_food_fzjqkl.jpg",
        "movement": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_movement_k8zq3n.jpg",
        "mindfulness": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_mindfulness_pqwz9m.jpg",
    }
    
    def __init__(self):
        """Initialize the image library service."""
        # RunPod configuration - uses FLUX.1 Schnell for ultra-fast generation
        self.runpod_api_key = os.getenv("RUNPOD_API_KEY")
        # FLUX.1 Schnell PUBLIC MANAGED endpoint (Black Forest Labs official)
        # This is a serverless endpoint provided by RunPod, NOT a custom pod
        self.runpod_endpoint = "black-forest-labs-flux-1-schnell"
        
        # OpenAI for embeddings
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        
        # Cloudinary for image storage (preferred)
        self.cloudinary_cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
        self.cloudinary_api_key = os.getenv("CLOUDINARY_API_KEY")
        self.cloudinary_api_secret = os.getenv("CLOUDINARY_API_SECRET")
        
        # Supabase for image storage (fallback)
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
        self.storage_bucket = "action-plan-images"
        
        # HTTP clients
        # FIX: Increase max connections to avoid "Max client connections reached" during parallel generation
        self.client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
        )
        
        # In-memory cache for embeddings (avoid repeated API calls)
        self._embedding_cache: Dict[str, List[float]] = {}
        
        # Configure Cloudinary ONCE at startup to avoid connection pool issues
        # (Previously configured per-upload, causing "Connection pool is full" warnings)
        self._cloudinary_configured = False
        
        if self.cloudinary_cloud_name and self.cloudinary_api_key:
            try:
                import cloudinary
                
                cloudinary.config(
                    cloud_name=self.cloudinary_cloud_name,
                    api_key=self.cloudinary_api_key,
                    api_secret=self.cloudinary_api_secret,
                    secure=True
                )
                self._cloudinary_configured = True
                
                # FIX: Patch cloudinary's internal HTTP pool manager with larger pool size
                # This fixes "Connection pool is full, discarding connection: api.cloudinary.com. Connection pool size: 1"
                # The default pool size of 1 cannot handle parallel image uploads (we do 16+ at once)
                try:
                    import cloudinary.uploader
                    from urllib3 import PoolManager
                    
                    # Create a PoolManager with larger connection pool
                    # num_pools = number of pools (one per host:port)
                    # maxsize = max connections per pool (per host)
                    large_pool_manager = PoolManager(
                        num_pools=10,        # Number of host connection pools to cache
                        maxsize=50,          # Max connections per host - handles parallel uploads
                        block=False,         # Don't block when pool is full (just warn)
                        **cloudinary.CERT_KWARGS  # Include SSL cert settings
                    )
                    
                    # Replace cloudinary's default _http with our larger pool
                    cloudinary.uploader._http = large_pool_manager
                    logger.info("✅ Cloudinary HTTP pool patched: num_pools=10, maxsize=50")
                except (ImportError, AttributeError) as e:
                    logger.warning(f"Could not patch cloudinary pool: {e}")
                
                logger.info("✅ Cloudinary configured globally at startup")
            except Exception as e:
                logger.warning(f"Failed to configure Cloudinary: {e}")
        
        logger.info(f"ImageLibraryService initialized")
        logger.info(f"  RunPod configured: {bool(self.runpod_api_key)}")
        logger.info(f"  OpenAI configured: {bool(self.openai_api_key)}")
        logger.info(f"  Cloudinary configured: {self._cloudinary_configured}")
        logger.info(f"  Supabase configured: {bool(self.supabase_url and self.supabase_key)}")
    
    async def get_or_generate_image(
        self,
        prompt: str,
        category: str,
        variant_type: Optional[str],
        user_id: str,
        db: AsyncSession,
        title_embedding: Optional[List[float]] = None,
        cache_key_text: Optional[str] = None,
    ) -> Tuple[str, bool, float]:
        """
        Get a cached image or generate a new one.
        
        TITLE-BASED EMBEDDING: We embed the TITLE (not full prompt) for cache matching.
        This gives stable cache hits even when prompt styling changes.
        
        Args:
            prompt: The action TITLE (e.g., "Salmon bowl") - used for BOTH embedding AND generation
            category: "food", "movement", or "mindfulness"
            variant_type: "hero", "tasty", "easy", "healthy", etc.
            user_id: User's UID to avoid showing same image twice
            db: Database session
            title_embedding: Optional pre-calculated embedding for the title
        
        Returns:
            Tuple of (image_url, was_cached, cost)
        """
        start_time = time.time()
        
        # Defensive: ensure prompt is never None
        if prompt is None:
            logger.warning(f"[IMAGE] ⚠️ Received None prompt, using fallback for {category}/{variant_type}")
            fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS.get("food", ""))
            return (fallback_url, False, 0.0)
        
        # Use a stable cache key (usually the action title) even if the generation prompt changes.
        # This improves image quality without destroying semantic cache hit rates.
        embed_text = cache_key_text or prompt

        # Log what we're processing
        if cache_key_text and cache_key_text != prompt:
            logger.info(
                f"🖼️ [IMAGE] Processing: cache_key='{embed_text[:40]}...' gen_prompt='{prompt[:40]}...' "
                f"category={category} variant={variant_type}"
            )
        else:
            logger.info(f"🖼️ [IMAGE] Processing: title='{prompt[:40]}...' category={category} variant={variant_type}")
        
        try:
            # Step 1: Get embedding for the CACHE KEY (if not provided)
            # This gives stable cache matching regardless of generation prompt style changes
            if title_embedding is None:
                logger.info(f"[IMAGE] Step 1: Getting embedding for cache key '{embed_text[:30]}...'")
                title_embedding = await self._get_embedding(embed_text)
            
            if not title_embedding:
                logger.warning(f"[IMAGE] ⚠️ Embedding failed for '{embed_text[:30]}...' - generating without cache")
                return await self._generate_and_store_image(
                    prompt, category, variant_type, user_id, None, db, prompt_text_for_library=embed_text
                )
            
            logger.info(f"[IMAGE] Step 1: ✅ Got embedding (dim={len(title_embedding)})")
            
            # Step 2: Search for semantically similar cached images by TITLE
            logger.info(f"[IMAGE] Step 2: Searching cache with threshold {self.SIMILARITY_THRESHOLD}")
            cached_image = await self._find_similar_image(
                title_embedding, 
                category, 
                variant_type, 
                user_id, 
                db
            )
            
            if cached_image:
                # Found a semantically similar image!
                # CRITICAL: Validate that the cached URL is permanent (Cloudinary), not temporary (RunPod)
                cached_url = cached_image.get("image_url", "")
                if "runpod.ai" in cached_url or "image.runpod" in cached_url:
                    # BAD DATA: This is a temporary RunPod URL that will expire
                    # Skip this cache entry and regenerate
                    logger.warning(f"[IMAGE] ⚠️ Found expired RunPod URL in cache - skipping and regenerating")
                    logger.warning(f"[IMAGE]   Bad URL: {cached_url[:60]}...")
                    # Don't use this cache entry - fall through to generation
                else:
                    await self._update_image_usage(cached_image["id"], user_id, db)
                    elapsed = time.time() - start_time
                    logger.info(f"[IMAGE] Step 2: ✅ CACHE HIT!")
                    logger.info(f"[IMAGE]   Cache key: '{embed_text[:40]}...'")
                    logger.info(f"[IMAGE]   Matched: '{cached_image.get('prompt_text', '')[:40]}...'")
                    logger.info(f"[IMAGE]   Similarity: {cached_image['similarity']:.4f} (threshold: {self.SIMILARITY_THRESHOLD})")
                    logger.info(f"[IMAGE]   Time: {elapsed:.2f}s, Cost: $0.00")
                    return (cached_image["image_url"], True, 0.0)
            
            # Step 3: No cache hit - generate new image
            elapsed_cache = time.time() - start_time
            logger.info(f"[IMAGE] Step 2: ❌ CACHE MISS (checked in {elapsed_cache:.2f}s)")
            logger.info(f"[IMAGE] Step 3: 🎨 Generating new image for '{embed_text[:40]}...'")
            
            result = await self._generate_and_store_image(
                prompt, category, variant_type, user_id, title_embedding, db, prompt_text_for_library=embed_text
            )
            
            elapsed = time.time() - start_time
            logger.info(f"[IMAGE] Step 3: ✅ Generation complete in {elapsed:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"[IMAGE] ❌ Error: {e}")
            # Fallback: try to generate without caching
            return await self._generate_and_store_image(
                prompt, category, variant_type, user_id, None, db, prompt_text_for_library=embed_text
            )
    
    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding using OpenAI ada-002.
        
        Cost: $0.0001 per 1K tokens (~$0.0001 per call for typical prompts)
        
        NOTE: If OpenAI returns 429 (quota exceeded), we gracefully return None
        and the image will still be generated/stored without embedding-based search.
        """
        # Check in-memory cache first
        cache_key = hashlib.md5(text.encode()).hexdigest()
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]
        
        if not self.openai_api_key:
            # Silently skip - not critical
            return None
        
        try:
            response = await self.client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "input": text,
                    "model": self.EMBEDDING_MODEL
                }
            )
            
            # Handle 429 gracefully - embeddings are optional, not critical
            if response.status_code == 429:
                logger.warning("[IMAGE] ⚠️ OpenAI embedding 429 - quota exceeded, caching disabled for this image")
                return None
            
            response.raise_for_status()
            data = response.json()
            embedding = data["data"][0]["embedding"]
            
            # Cache it
            self._embedding_cache[cache_key] = embedding
            
            return embedding
            
        except Exception as e:
            # Log the error so we can diagnose cache issues
            logger.warning(f"[IMAGE] ⚠️ Embedding failed: {type(e).__name__}: {str(e)[:100]}")
            return None
    
    async def _get_batch_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts in a single API call (Fix #17).
        More efficient than calling _get_embedding multiple times.
        
        Cost: $0.0001 per 1K tokens (batches are more efficient)
        
        NOTE: If OpenAI returns 429, gracefully return None for uncached items.
        """
        if not self.openai_api_key:
            # Silently skip - not critical
            return [None] * len(texts)
        
        if not texts:
            return []
        
        # Check cache first, identify what needs to be fetched
        results = []
        uncached_indices = []
        uncached_texts = []
        
        for i, text in enumerate(texts):
            cache_key = hashlib.md5(text.encode()).hexdigest()
            if cache_key in self._embedding_cache:
                results.append(self._embedding_cache[cache_key])
            else:
                results.append(None)  # Placeholder
                uncached_indices.append(i)
                uncached_texts.append(text)
        
        if not uncached_texts:
            return results
        
        try:
            response = await self.client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "input": uncached_texts,
                    "model": self.EMBEDDING_MODEL
                }
            )
            
            # Handle 429 gracefully - embeddings are optional
            if response.status_code == 429:
                # Return partial results (cached ones) - don't spam logs
                return results
            
            response.raise_for_status()
            data = response.json()
            
            # Fill in the results and cache
            for item in data["data"]:
                idx = item["index"]
                embedding = item["embedding"]
                original_idx = uncached_indices[idx]
                results[original_idx] = embedding
                
                # Cache the embedding
                cache_key = hashlib.md5(uncached_texts[idx].encode()).hexdigest()
                self._embedding_cache[cache_key] = embedding
            
            return results
            
        except Exception as e:
            # Silently skip - embeddings are optional
            return results  # Return partial results (cached ones)
    
    async def _find_similar_image(
        self,
        prompt_embedding: List[float],
        category: str,
        variant_type: Optional[str],
        user_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """
        Find a semantically similar image that hasn't been shown to this user.
        
        Uses cosine similarity on stored embeddings.
        """
        from app.core.database import ImageLibrary
        
        try:
            # Query ONLY the columns we need for similarity matching and basic identification
            from sqlalchemy import select
            query = select(
                ImageLibrary.id, 
                ImageLibrary.image_url, 
                ImageLibrary.prompt_text, 
                ImageLibrary.prompt_embedding, 
                ImageLibrary.used_by_users
            ).where(
                ImageLibrary.category == category
            )
            
            if variant_type:
                query = query.where(ImageLibrary.variant_type == variant_type)
            
            result = await db.execute(query)
            # Result contains tuples due to specific column selection
            rows = result.all()
            
            if not rows:
                logger.debug(f"[IMAGE-CACHE] No candidates in library for {category}/{variant_type}")
                return None
            
            # Diagnostic counters
            total_candidates = len(rows)
            skipped_by_user = 0
            no_embedding = 0
            below_threshold = 0
            similarities = []
            
            # Find best match that user hasn't seen
            best_match = None
            best_similarity = 0.0
            
            for row in rows:
                # row structure: (id, image_url, prompt_text, prompt_embedding, used_by_users)
                used_by = row.used_by_users or []
                if user_id in used_by:
                    skipped_by_user += 1
                    continue
                
                # Calculate cosine similarity
                stored_embedding = row.prompt_embedding
                if not stored_embedding:
                    no_embedding += 1
                    continue
                
                similarity = self._cosine_similarity(prompt_embedding, stored_embedding)
                similarities.append((similarity, row.prompt_text[:30] if row.prompt_text else "???"))
                
                if similarity > self.SIMILARITY_THRESHOLD and similarity > best_similarity:
                    best_similarity = similarity
                    best_match = {
                        "id": row.id,
                        "image_url": row.image_url,
                        "prompt_text": row.prompt_text,
                        "similarity": similarity
                    }
                else:
                    below_threshold += 1
            
            # Log diagnostic info
            top_similarities = sorted(similarities, key=lambda x: -x[0])[:3] if similarities else []
            if best_match:
                logger.info(f"[IMAGE-CACHE] MATCH FOUND!")
            else:
                logger.info(f"[IMAGE-CACHE] NO MATCH - {category}/{variant_type}: "
                           f"candidates={total_candidates}, no_embed={no_embedding}, "
                           f"user_seen={skipped_by_user}, below_thresh={below_threshold}")
                if top_similarities:
                    logger.info(f"[IMAGE-CACHE] Top similarities (threshold={self.SIMILARITY_THRESHOLD}): "
                               f"{[(f'{s:.3f}', t) for s, t in top_similarities]}")
            
            return best_match
            
        except Exception as e:
            logger.error(f"Error finding similar image: {e}")
            return None
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        import math
        
        if len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    async def _update_image_usage(
        self,
        image_id: int,
        user_id: str,
        db: AsyncSession
    ) -> None:
        """Update image usage statistics.
        
        Note: Does NOT commit - let parent transaction handle it.
        """
        from app.core.database import ImageLibrary
        
        try:
            # Get current image
            result = await db.execute(
                select(ImageLibrary).where(ImageLibrary.id == image_id)
            )
            image = result.scalar_one_or_none()
            
            if image:
                # Update usage count and user list
                used_by = image.used_by_users or []
                if user_id not in used_by:
                    used_by.append(user_id)
                
                await db.execute(
                    update(ImageLibrary)
                    .where(ImageLibrary.id == image_id)
                    .values(
                        usage_count=ImageLibrary.usage_count + 1,
                        last_used_at=datetime.utcnow(),
                        used_by_users=used_by
                    )
                )
                # Do NOT commit here - let parent transaction handle it
                
        except Exception as e:
            logger.error(f"Error updating image usage (non-critical): {e}")
    
    async def _generate_and_store_image(
        self,
        prompt: str,
        category: str,
        variant_type: Optional[str],
        user_id: str,
        prompt_embedding: Optional[List[float]],
        db: AsyncSession,
        prompt_text_for_library: Optional[str] = None,
    ) -> Tuple[str, bool, float]:
        """Generate a new image using RunPod Flux Schnell and store it."""
        start_time = time.time()
        
        try:
            # Generate image via RunPod with retry logic (Fix #14)
            result, generation_time_ms = await self._call_runpod_flux_with_retry(prompt, category)
            
            if not result:
                logger.error(f"Failed to generate image via RunPod, using fallback for {category}")
                fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS["food"])
                return (fallback_url, False, 0.0)
            
            # Check if result is already a URL (from RunPod) or bytes
            if isinstance(result, str) and result.startswith("http"):
                # RunPod returned a URL directly - upload to Cloudinary for permanent storage
                # CRITICAL: RunPod URLs expire! We MUST upload to Cloudinary.
                image_url = await self._upload_to_cloudinary_from_url(result, category, variant_type)
                
                if image_url:
                    # SUCCESS: Store permanent Cloudinary URL in library
                    library_prompt_text = prompt_text_for_library or prompt
                    await self._store_in_library(
                        image_url=image_url,
                        prompt_text=library_prompt_text,
                        prompt_embedding=prompt_embedding,
                        category=category,
                        variant_type=variant_type,
                        user_id=user_id,
                        generation_time_ms=generation_time_ms,
                        db=db
                    )
                    logger.info(f"📚 Image stored in library: {category}/{variant_type}")
                else:
                    # CRITICAL: Cloudinary failed - DO NOT store in library!
                    # Use RunPod URL temporarily but don't cache it
                    logger.warning(f"⚠️ Cloudinary upload failed - using temporary RunPod URL (NOT caching)")
                    image_url = result  # Use temporarily but NOT stored in library

            elif isinstance(result, bytes):
                # We have image bytes - upload to Cloudinary or Supabase
                image_url = await self._upload_to_cloudinary(result, category, variant_type)
                if not image_url:
                    image_url = await self._upload_to_supabase(result, category, variant_type)
                if not image_url:
                    logger.error(f"Failed to upload image to any storage, using fallback for {category}")
                    fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS["food"])
                    return (fallback_url, False, 0.0)

                # Store in image library for future semantic matching
                await self._store_in_library(
                    image_url=image_url,
                    prompt_text=prompt,
                    prompt_embedding=prompt_embedding,
                    category=category,
                    variant_type=variant_type,
                    user_id=user_id,
                    generation_time_ms=generation_time_ms,
                    db=db
                )
            else:
                logger.error(f"Unexpected result type: {type(result)}, using fallback for {category}")
                fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS["food"])
                return (fallback_url, False, 0.0)
            
            elapsed = time.time() - start_time
            logger.info(f"🎨 New image generated. Time: {elapsed:.2f}s, Cost: ${self.COST_PER_IMAGE}")
            
            return (image_url, False, self.COST_PER_IMAGE)
            
        except Exception as e:
            logger.error(f"Error generating and storing image: {e}, using fallback for {category}")
            fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS["food"])
            return (fallback_url, False, 0.0)
    
    async def _call_runpod_flux(self, prompt: str, category: str = "food") -> Tuple[Optional[Any], int]:
        """
        Call RunPod FLUX.1 Schnell serverless endpoint using /run + /status async pattern.
        
        FLUX.1 Schnell features:
        - Rectified Flow Transformer optimized for 1-4 step sampling
        - Ultra-fast generation with crisp results
        - High prompt fidelity
        - 512x512 resolution for speed and cost optimization
        - ~$0.0006 per image
        """
        if not self.runpod_api_key:
            logger.warning("RunPod API key not configured, using placeholder")
            return await self._generate_placeholder_image(prompt)
        
        start_time = time.time()
        
        try:
            # Enhanced prompt with category-specific styling
            enhanced_prompt = self._enhance_prompt(prompt, category)
            
            # FLUX.1 Schnell payload format
            # Optimized for speed: 512x512, 4 steps, moderate guidance
            payload = {
                "input": {
                    "prompt": enhanced_prompt,
                    "width": 512,           # Small for speed and cost
                    "height": 512,          # Square images for action cards
                    "num_inference_steps": 4,  # Ultra-fast 4-step sampling
                    "guidance": 5.0,        # Moderate prompt adherence
                    "seed": -1,             # Random seed
                    "image_format": "webp"  # Smaller file size
                }
            }
            
            headers = {
                "Authorization": f"Bearer {self.runpod_api_key}",
                "Content-Type": "application/json"
            }
            
            # Step 1: Submit job to /run endpoint
            run_url = f"https://api.runpod.ai/v2/{self.runpod_endpoint}/run"
            
            logger.info(f"🎨 [Schnell] Submitting job: {prompt[:50]}...")
            
            response = await self.client.post(
                run_url,
                json=payload,
                headers=headers,
                timeout=10.0  # 10s timeout for job submission
            )
            
            if response.status_code != 200:
                logger.error(f"❌ [Schnell] Submission failed: {response.status_code} - {response.text[:200]}")
                return await self._generate_placeholder_image(prompt)
            
            result = response.json()
            job_id = result.get("id")
            
            if not job_id:
                logger.error(f"❌ [Schnell] No job_id in response")
                return await self._generate_placeholder_image(prompt)
            
            logger.debug(f"[Schnell] Job ID: {job_id}")
            
            # Step 2: Poll /status/{job_id} - Schnell is ultra-fast
            status_url = f"https://api.runpod.ai/v2/{self.runpod_endpoint}/status/{job_id}"
            
            # OPTIMIZED POLLING: Use exponential backoff to reduce HTTP requests
            # Schnell typically completes in 1-3 seconds, so start fast then slow down
            # Pattern: 0.3s, 0.3s, 0.5s, 0.5s, 1s, 1s, 2s, 2s, 3s, 3s, 5s, 5s...
            # This reduces ~150 requests to ~20-25 while keeping responsiveness
            poll_intervals = [0.3, 0.3, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0] + [5.0] * 7  # ~45s total
            max_wait_seconds = 45
            total_waited = 0.0
            poll_num = 0
            
            for poll_interval in poll_intervals:
                if total_waited >= max_wait_seconds:
                    break
                    
                await asyncio.sleep(poll_interval)
                total_waited += poll_interval
                poll_num += 1
                
                try:
                    status_response = await self.client.get(
                        status_url,
                        headers=headers,
                        timeout=5.0
                    )
                    
                    if status_response.status_code != 200:
                        logger.warning(f"⚠️ [Schnell] Status check failed: {status_response.status_code}")
                        continue
                    
                    status_result = status_response.json()
                    status = status_result.get("status")
                    
                    if status == "COMPLETED":
                        output = status_result.get("output", {})
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        
                        logger.info(f"✅ [Schnell] Completed in {elapsed_ms}ms")
                        
                        # FLUX.1 Schnell output format
                        if isinstance(output, dict):
                            # Primary: check 'image_url' key
                            image_url = output.get("image_url")
                            if image_url and image_url.startswith("http"):
                                return (image_url, elapsed_ms)
                            
                            # Fallback: check 'result' key
                            image_url = output.get("result")
                            if image_url and image_url.startswith("http"):
                                return (image_url, elapsed_ms)
                        
                        logger.error(f"❌ [Schnell] Unexpected output format: {output}")
                        return await self._generate_placeholder_image(prompt)
                    
                    elif status == "FAILED":
                        error = status_result.get("error", "Unknown error")
                        logger.error(f"❌ [Schnell] Job failed: {error}")
                        return await self._generate_placeholder_image(prompt)
                    
                    elif status in ["IN_QUEUE", "IN_PROGRESS"]:
                        # Continue polling
                        if poll_num % 5 == 0:  # Log every few polls
                            logger.debug(f"[Schnell] Poll {poll_num}/{len(poll_intervals)}: {status}, waited {total_waited:.1f}s")
                        continue
                    
                    else:
                        logger.warning(f"⚠️ [Schnell] Unknown status: {status}")
                        continue
                
                except Exception as poll_error:
                    logger.warning(f"⚠️ [Schnell] Poll error: {poll_error}")
                    continue
            
            # Timeout - return None to trigger retry logic
            elapsed = time.time() - start_time
            logger.warning(f"⚠️ [Schnell] Timeout after {elapsed:.1f}s (job: {job_id}) - will retry")
            return (None, 0)  # Return None to trigger retry
            
        except Exception as e:
            logger.warning(f"⚠️ [Schnell] Error: {type(e).__name__}: {e} - will retry")
            return (None, 0)  # Return None to trigger retry


    async def _call_runpod_flux_legacy(self, prompt: str, category: str = "food") -> Tuple[Optional[Any], int]:
        """
        Legacy polling logic for RunPod (used as fallback).
        """
        start_time = time.time()
        try:
            endpoint_url = f"https://api.runpod.ai/v2/{self.runpod_endpoint}/run"
            enhanced_prompt = self._enhance_prompt(prompt, category)
            
            payload = {
                "input": {
                    "prompt": enhanced_prompt,
                    "width": 512,
                    "height": 512,
                    "num_inference_steps": 4,
                    "guidance": 3.5,
                    "seed": -1,
                    "image_format": "png"
                }
            }
            
            headers = {
                "Authorization": f"Bearer {self.runpod_api_key}",
                "Content-Type": "application/json"
            }
            
            response = await self.client.post(endpoint_url, json=payload, headers=headers, timeout=30.0)
            if response.status_code != 200:
                return await self._generate_placeholder_image(prompt)
            
            job_id = response.json().get("id")
            if not job_id:
                return await self._generate_placeholder_image(prompt)
            
            status_url = f"https://api.runpod.ai/v2/{self.runpod_endpoint}/status/{job_id}"
            poll_interval = self.RUNPOD_POLL_INTERVAL
            max_polls = int(self.RUNPOD_POLL_TIMEOUT / poll_interval)
            
            for poll_num in range(max_polls):
                await asyncio.sleep(poll_interval)
                status_response = await self.client.get(status_url, headers=headers)
                if status_response.status_code != 200:
                    continue
                
                status_result = status_response.json()
                status = status_result.get("status")
                
                if status == "COMPLETED":
                    output = status_result.get("output", {})
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    
                    if isinstance(output, dict):
                        if output.get("image_url"):
                            return (output["image_url"], elapsed_ms)
                        image_base64 = output.get("image") or output.get("image_base64")
                        if image_base64:
                            if image_base64.startswith("data:"):
                                image_base64 = image_base64.split(",")[1]
                            return (base64.b64decode(image_base64), elapsed_ms)
                    elif isinstance(output, str):
                        if output.startswith("data:"):
                            output = output.split(",")[1]
                        return (base64.b64decode(output), elapsed_ms)
                    return await self._generate_placeholder_image(prompt)
                
                elif status == "FAILED":
                    return await self._generate_placeholder_image(prompt)
                
                elif status in ["IN_QUEUE", "IN_PROGRESS"]:
                    continue
            
            return (None, int((time.time() - start_time) * 1000))
            
        except Exception as e:
            logger.error(f"Error in legacy polling: {e}")
            return await self._generate_placeholder_image(prompt)
    
    async def _call_runpod_flux_with_retry(self, prompt: str, category: str = "food") -> Tuple[Optional[Any], int]:
        """
        Call RunPod FLUX.1 Schnell with retry logic.
        
        Schnell is ultra-fast so retries should be rare, but we keep retry logic
        for robustness against transient network issues.
        """
        total_start = time.time()
        
        for attempt in range(self.MAX_IMAGE_RETRIES):
            try:
                logger.info(f"🎨 Schnell attempt {attempt + 1}/{self.MAX_IMAGE_RETRIES} for: {prompt[:50]}...")
                result, gen_time = await self._call_runpod_flux(prompt, category)
                
                if result:
                    total_time = time.time() - total_start
                    if attempt > 0:
                        logger.info(f"✅ Schnell succeeded on retry {attempt + 1} (total time: {total_time:.1f}s)")
                    return (result, gen_time)
                
                # Returned None - timeout or transient error, retry
                if attempt < self.MAX_IMAGE_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"⚠️ Image generation timed out. "
                                 f"Retrying in {delay}s (attempt {attempt + 1}/{self.MAX_IMAGE_RETRIES})")
                    await asyncio.sleep(delay)
            except Exception as e:
                if attempt < self.MAX_IMAGE_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"⚠️ Image generation error: {e}, retrying in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"❌ Image generation failed after {self.MAX_IMAGE_RETRIES} retries: {e}")
                    return await self._generate_placeholder_image(prompt)
        
        logger.error(f"❌ All {self.MAX_IMAGE_RETRIES} retry attempts exhausted, using placeholder")
        return await self._generate_placeholder_image(prompt)
    
    def _enhance_prompt(self, prompt: str, category: str) -> str:
        """
        Create "founder-quality" prompts optimized for FLUX.1 Schnell.
        
        Strategy:
        1. Subject-First: Tell the model exactly what to draw immediately.
        2. Style: "Editorial", "Cinematic", "Professional".
        3. Tech Specs: "f/1.8", "4k", "sharp focus" - FLUX loves these.
        """
        logger.info(f"[PROMPT] Enhancing: '{prompt}' (category: {category})")

        
        prompt_str = prompt or "wellness activity"
        prompt_l = prompt_str.lower()
        
        # Heuristic: if prompt is already detailed/optimized, trust it
        if "editorial" in prompt_l or "photography" in prompt_l or "4k" in prompt_l:
            return f"{prompt_str}, high quality, sharp focus"
        

        if category == "food":
            # FOOD: Editorial magazine style
            # Goal: Mouth-watering, fresh, clean
            enhanced = (
                f"Editorial food photography of {prompt_str}, "
                f"overhead shot on a rustic ceramic plate, "
                f"fresh vibrant ingredients, soft morning window light, "
                f"shallow depth of field f/2.8, sharp focus on food, "
                f"clean minimalist styling, Bon Appetit magazine style, "
                f"4k, highly detailed, photorealistic"
            )
            
        elif category == "movement":
            # MOVEMENT: Clean studio fitness style
            # Goal: Clear form, aspirational, bright
            enhanced = (
                f"Professional fitness photography of a woman demonstrating {prompt_str}, "
                f"perfect form, clean bright modern yoga studio, "
                f"soft diffused daylight, neutral colors, "
                f"athletic wear, wide shot capturing full body, "
                f"cinematic lighting, high resolution, 4k, sharp focus"
            )
            
        elif category == "mindfulness":
            # MINDFULNESS: Atmospheric lifestyle style
            # Goal: Peaceful, emotional, warm
            if "journal" in prompt_l:
                enhanced = (
                    f"Close-up lifestyle photography of hands writing in a journal, "
                    f"soft focus, cozy atmosphere, warm golden hour lighting, "
                    f"cup of tea nearby, wooden texture, "
                    f"peaceful mood, cinematic, 4k, highly detailed"
                )
            else:
                enhanced = (
                    f"Serene lifestyle photography of a woman practicing {prompt_str}, "
                    f"peaceful calm expression, closed eyes, "
                    f"cozy minimalist living space, warm ambient lighting, "
                    f"soft textures, cinematic composition, "
                    f"photorealistic, 4k, high quality"
                )
        
        else:
            # Fallback
            enhanced = (
                f"Professional wellness photography of {prompt_str}, "
                f"clean minimalist background, soft lighting, "
                f"high quality, 4k, sharp focus, photorealistic"
            )
        
        logger.info(f"[PROMPT] Enhanced: '{enhanced[:80]}...'")
        return enhanced
    
    async def _generate_placeholder_image(self, prompt: str) -> Tuple[bytes, int]:
        """
        Generate a placeholder image when RunPod is not configured.
        Uses a simple colored rectangle with text.
        """
        # Create a simple placeholder using PIL if available
        try:
            from PIL import Image, ImageDraw, ImageFont
            import io
            
            # Create image
            img = Image.new('RGB', (512, 512), color=(240, 240, 240))
            draw = ImageDraw.Draw(img)
            
            # Add text
            text = prompt[:50] + "..." if len(prompt) > 50 else prompt
            draw.text((50, 230), text, fill=(100, 100, 100))
            draw.text((150, 260), "🍃 AUVRA", fill=(100, 150, 100))
            
            # Convert to bytes
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            
            return (buffer.getvalue(), 100)
            
        except ImportError:
            logger.warning("PIL not available for placeholder images")
            return (None, 0)
    
    async def _upload_to_cloudinary(
        self,
        image_data: bytes,
        category: str,
        variant_type: Optional[str]
    ) -> Optional[str]:
        """Upload image bytes to Cloudinary and return public URL."""
        if not self.cloudinary_cloud_name or not self.cloudinary_api_key:
            logger.warning("Cloudinary not configured")
            return None
        
        try:
            import cloudinary
            import cloudinary.uploader
            
            # Cloudinary is configured once in __init__ - no need to reconfigure here
            # This prevents "Connection pool is full" warnings during parallel uploads
            
            # Generate unique public_id
            timestamp = int(time.time() * 1000)
            file_hash = hashlib.md5(image_data).hexdigest()[:8]
            variant_str = f"_{variant_type}" if variant_type else ""
            public_id = f"auvra/{category}{variant_str}_{timestamp}_{file_hash}"
            
            # Upload image bytes in a separate thread to avoid blocking the event loop
            result = await asyncio.to_thread(
                cloudinary.uploader.upload,
                image_data,
                public_id=public_id,
                folder="action-plan-images",
                resource_type="image"
            )
            
            image_url = result.get("secure_url")
            if image_url:
                logger.info(f"📤 Image uploaded to Cloudinary: {image_url}")
                return image_url
            
            return None
            
        except ImportError:
            logger.warning("cloudinary package not installed")
            return None
        except Exception as e:
            logger.error(f"Error uploading to Cloudinary: {e}")
            return None
    
    async def _upload_to_cloudinary_from_url(
        self,
        image_url: str,
        category: str,
        variant_type: Optional[str],
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Upload image from URL to Cloudinary for permanent storage.
        
        CRITICAL: This MUST succeed for the image to be stored in the library.
        RunPod URLs expire, so we NEVER want to cache them directly.
        
        Args:
            image_url: The source URL (usually RunPod temporary URL)
            category: food, movement, mindfulness
            variant_type: hero, tasty, easy, etc.
            max_retries: Number of retry attempts (default 3)
        
        Returns:
            Cloudinary permanent URL, or None if all retries fail
        """
        if not self.cloudinary_cloud_name or not self.cloudinary_api_key:
            logger.warning("Cloudinary not configured")
            return None
        
        import cloudinary
        import cloudinary.uploader
        
        for attempt in range(1, max_retries + 1):
            try:
                # Generate unique public_id
                timestamp = int(time.time() * 1000)
                file_hash = hashlib.md5(image_url.encode()).hexdigest()[:8]
                variant_str = f"_{variant_type}" if variant_type else ""
                public_id = f"auvra/{category}{variant_str}_{timestamp}_{file_hash}"
                
                # Upload from URL in a separate thread to avoid blocking the event loop
                result = await asyncio.to_thread(
                    cloudinary.uploader.upload,
                    image_url,
                    public_id=public_id,
                    folder="action-plan-images",
                    resource_type="image",
                    timeout=30  # Add timeout
                )
                
                cloudinary_url = result.get("secure_url")
                if cloudinary_url:
                    logger.info(f"📤 Image uploaded to Cloudinary from URL: {cloudinary_url}")
                    return cloudinary_url
                
            except ImportError:
                logger.warning("cloudinary package not installed")
                return None
            except Exception as e:
                logger.warning(f"⚠️ Cloudinary upload attempt {attempt}/{max_retries} failed: {type(e).__name__}: {e}")
                if attempt < max_retries:
                    # Wait before retry with exponential backoff
                    await asyncio.sleep(0.5 * attempt)
                else:
                    logger.error(f"❌ Cloudinary upload FAILED after {max_retries} attempts - image will NOT be cached")
        
        return None
    
    async def _upload_to_supabase(
        self,
        image_data: bytes,
        category: str,
        variant_type: Optional[str]
    ) -> Optional[str]:
        """Upload image to Supabase Storage and return public URL."""
        if not self.supabase_url or not self.supabase_key:
            logger.warning("Supabase not configured, returning base64 data URL")
            base64_data = base64.b64encode(image_data).decode()
            return f"data:image/png;base64,{base64_data}"
        
        try:
            # Generate unique filename
            timestamp = int(time.time() * 1000)
            file_hash = hashlib.md5(image_data).hexdigest()[:8]
            variant_str = f"_{variant_type}" if variant_type else ""
            filename = f"{category}{variant_str}_{timestamp}_{file_hash}.png"
            file_path = f"generated/{category}/{filename}"
            
            url = f"{self.supabase_url}/storage/v1/object/{self.storage_bucket}/{file_path}"
            
            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "image/png",
                "x-upsert": "true"
            }
            
            response = await self.client.post(url, content=image_data, headers=headers)
            
            if response.status_code in [200, 201]:
                # Return public URL
                public_url = f"{self.supabase_url}/storage/v1/object/public/{self.storage_bucket}/{file_path}"
                return public_url
            else:
                logger.error(f"Supabase upload failed: {response.status_code} {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error uploading to Supabase: {e}")
            return None
    
    async def _store_in_library(
        self,
        image_url: str,
        prompt_text: str,
        prompt_embedding: Optional[List[float]],
        category: str,
        variant_type: Optional[str],
        user_id: str,
        generation_time_ms: int,
        db: AsyncSession
    ) -> None:
        """Store generated image in the library for future semantic matching.
        
        Note: Does NOT commit - parent transaction will handle commit.
        This avoids "can't commit during flush" errors when called from replace_action.
        """
        from app.core.database import ImageLibrary
        
        try:
            new_image = ImageLibrary(
                image_url=image_url,
                prompt_text=prompt_text,
                prompt_embedding=prompt_embedding,
                category=category,
                variant_type=variant_type,
                generation_model="pruna-p-image",
                generation_cost=str(self.COST_PER_IMAGE),
                generation_time_ms=generation_time_ms,
                image_width=512,
                image_height=512,
                usage_count=1,
                last_used_at=datetime.utcnow(),
                used_by_users=[user_id],
                created_at=datetime.utcnow()
            )
            
            db.add(new_image)
            # Do NOT commit here - let parent transaction handle it
            # This prevents "can't commit during flush" errors
            
            logger.info(f"📚 Image stored in library: {category}/{variant_type}")
            
        except Exception as e:
            # Log but don't fail - image library storage is non-critical
            logger.error(f"Error storing in library (non-critical): {e}")
    
    async def generate_batch_images(
        self,
        prompts: List[Dict[str, Any]],
        user_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Generate multiple images IN PARALLEL for maximum performance.
        
        Args:
            prompts: List of {"prompt": str, "category": str, "variant_type": str}
            user_id: User's UID
            db: Database session
        
        Returns:
            List of {"image_url": str, "was_cached": bool, "cost": float}
        """
        logger.info(f"📸 Starting PARALLEL batch: {len(prompts)} images")
        
        # Generate all images in parallel
        tasks = [
            self.get_or_generate_image(
                prompt=prompt_info["prompt"],
                category=prompt_info["category"],
                variant_type=prompt_info.get("variant_type"),
                user_id=user_id,
                db=db
            )
            for prompt_info in prompts
        ]
        
        # Wait for all to complete in parallel
        image_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        results = []
        total_cost = 0.0
        cache_hits = 0
        errors = 0
        
        for prompt_info, result in zip(prompts, image_results):
            if isinstance(result, Exception):
                logger.error(f"❌ Batch error for {prompt_info['prompt'][:30]}: {result}")
                errors += 1
                results.append({
                    "prompt": prompt_info["prompt"],
                    "category": prompt_info["category"],
                    "variant_type": prompt_info.get("variant_type"),
                    "image_url": "",
                    "was_cached": False,
                    "cost": 0.0,
                    "error": str(result)
                })
            else:
                image_url, was_cached, cost = result
                results.append({
                    "prompt": prompt_info["prompt"],
                    "category": prompt_info["category"],
                    "variant_type": prompt_info.get("variant_type"),
                    "image_url": image_url,
                    "was_cached": was_cached,
                    "cost": cost
                })
                
                total_cost += cost
                if was_cached:
                    cache_hits += 1
        
        logger.info(f"✅ Batch complete: {len(prompts)} images, "
                   f"{cache_hits} cached, {errors} errors, ${total_cost:.4f} total")
        
        return results
    
    async def get_library_stats(self, db: AsyncSession) -> Dict[str, Any]:
        """Get statistics about the image library."""
        from app.core.database import ImageLibrary
        
        try:
            # Total images
            total_result = await db.execute(select(func.count(ImageLibrary.id)))
            total_images = total_result.scalar()
            
            # By category
            category_result = await db.execute(
                select(
                    ImageLibrary.category,
                    func.count(ImageLibrary.id)
                ).group_by(ImageLibrary.category)
            )
            by_category = {row[0]: row[1] for row in category_result.all()}
            
            # Total usage
            usage_result = await db.execute(
                select(func.sum(ImageLibrary.usage_count))
            )
            total_usage = usage_result.scalar() or 0
            
            return {
                "total_images": total_images,
                "by_category": by_category,
                "total_usage": total_usage,
                "cache_hit_rate": (total_usage - total_images) / total_usage if total_usage > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Error getting library stats: {e}")
            return {}

    async def cleanup_expired_runpod_urls(self, db: AsyncSession) -> Dict[str, int]:
        """
        Remove images with expired RunPod URLs from the library.
        
        RunPod URLs are temporary and expire. If any got stored in the library
        (due to Cloudinary upload failures), this cleans them up.
        
        Returns:
            Dict with counts of deleted images
        """
        from app.core.database import ImageLibrary
        
        try:
            # Find all images with RunPod URLs
            result = await db.execute(
                select(ImageLibrary).where(
                    ImageLibrary.image_url.like('%runpod.ai%')
                )
            )
            bad_images = result.scalars().all()
            
            if not bad_images:
                logger.info("✅ No expired RunPod URLs found in image library")
                return {"deleted": 0, "found": 0}
            
            logger.warning(f"🗑️ Found {len(bad_images)} images with expired RunPod URLs - deleting...")
            
            # Delete them
            for img in bad_images:
                logger.info(f"   Deleting ID {img.id}: {img.prompt_text[:40]}...")
                await db.delete(img)
            
            await db.commit()
            
            logger.info(f"✅ Cleaned up {len(bad_images)} expired RunPod URLs from image library")
            return {"deleted": len(bad_images), "found": len(bad_images)}
            
        except Exception as e:
            logger.error(f"Error cleaning up RunPod URLs: {e}")
            await db.rollback()
            return {"deleted": 0, "found": 0, "error": str(e)}


# Global instance
_image_library_service: Optional[ImageLibraryService] = None


def get_image_library_service() -> ImageLibraryService:
    """Get or create the image library service singleton."""
    global _image_library_service
    if _image_library_service is None:
        _image_library_service = ImageLibraryService()
    return _image_library_service
