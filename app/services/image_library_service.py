"""
AUVRA Image Library Service

Semantic image caching and generation using:
- RunPod FLUX.1 Schnell for fast, high-quality image generation
- OpenAI ada-002 for embeddings ($0.0001/call)  
- Cloudinary for image hosting
- PostgreSQL for semantic matching

Features:
- Semantic embedding matching (cosine similarity > 0.85)
- Cross-user image reuse (never same image for same user)
- All 16 images generated per day (4 actions × 4 variants)
- Fast 4-step inference with 512x512 resolution
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
    
    Uses RunPod FLUX.1 Schnell for fast, high-quality images with 512x512 resolution.
    Stores all images with embeddings for semantic reuse.
    """
    
    # SIMILARITY_THRESHOLD optimized for speed + quality balance
    # 0.88 provides good semantic matching while maximizing cache hits
    # Lower = more reuse (faster), Higher = more specificity
    SIMILARITY_THRESHOLD = 0.88  # Cosine similarity threshold for semantic image matching
    
    # RunPod FLUX.1 Schnell pricing
    COST_PER_IMAGE = 0.005  # $0.005 per image
    
    # Embedding model
    EMBEDDING_MODEL = "text-embedding-ada-002"  # or "text-embedding-3-small"
    EMBEDDING_DIMENSION = 1536
    
    # Retry settings - fast failure, fast retry
    MAX_IMAGE_RETRIES = 3  # 3 retries for shared endpoint variability
    RETRY_DELAYS = [1.0, 2.0, 4.0]  # Progressive delays for retries
    
    # RunPod timeout settings - synchronous call is much faster
    RUNPOD_SYNC_TIMEOUT = 60.0  # 60s for sync call (includes cold start)
    RUNPOD_POLL_TIMEOUT = 120  # 120 seconds (2 min) max wait - shared endpoint can be slow
    RUNPOD_POLL_INTERVAL = 0.5  # Poll every 0.5 second
    
    # Fallback images when generation fails - prevents empty image URLs in database
    # These are hosted on Cloudinary and match the app's visual style
    FALLBACK_IMAGE_URLS = {
        "food": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_food_fzjqkl.jpg",
        "movement": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_movement_k8zq3n.jpg",
        "mindfulness": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_mindfulness_pqwz9m.jpg",
    }
    
    def __init__(self):
        """Initialize the image library service."""
        # RunPod configuration - uses FLUX.1 Schnell for fast, high-quality generation
        self.runpod_api_key = os.getenv("RUNPOD_API_KEY")
        # FLUX.1 Schnell endpoint - fast inference, 512x512 resolution
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
        self.client = httpx.AsyncClient(timeout=120.0)
        
        # In-memory cache for embeddings (avoid repeated API calls)
        self._embedding_cache: Dict[str, List[float]] = {}
        
        # Configure Cloudinary ONCE at startup to avoid connection pool issues
        # (Previously configured per-upload, causing "Connection pool is full" warnings)
        self._cloudinary_configured = False
        if self.cloudinary_cloud_name and self.cloudinary_api_key:
            try:
                import cloudinary
                import urllib3
                # Increase urllib3 connection pool size to prevent "Connection pool is full" warnings
                # Default is 1, which causes issues with concurrent uploads
                urllib3.util.connection.HAS_IPV6 = False  # Force IPv4 to reduce pool fragmentation
                from urllib3 import HTTPConnectionPool
                HTTPConnectionPool.DEFAULT_MAXSIZE = 20  # Increase from default 1
                
                cloudinary.config(
                    cloud_name=self.cloudinary_cloud_name,
                    api_key=self.cloudinary_api_key,
                    api_secret=self.cloudinary_api_secret,
                    secure=True
                )
                self._cloudinary_configured = True
                logger.info("✅ Cloudinary configured globally at startup (pool size: 20)")
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
        title_embedding: Optional[List[float]] = None
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
        
        # Log what we're processing
        logger.info(f"🖼️ [IMAGE] Processing: title='{prompt[:40]}...' category={category} variant={variant_type}")
        
        try:
            # Step 1: Get embedding for the TITLE (if not provided)
            # This gives stable cache matching regardless of prompt style changes
            if title_embedding is None:
                logger.info(f"[IMAGE] Step 1: Getting embedding for title '{prompt[:30]}...'")
                title_embedding = await self._get_embedding(prompt)
            
            if not title_embedding:
                logger.warning(f"[IMAGE] ⚠️ Embedding failed for '{prompt[:30]}...' - generating without cache")
                return await self._generate_and_store_image(
                    prompt, category, variant_type, user_id, None, db
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
                cached_url = cached_image.get("image_url", "")
                # Defensive: verify cached URL is not empty (shouldn't happen after _find_similar_image fix)
                if cached_url and cached_url.strip():
                    # Found a semantically similar image!
                    await self._update_image_usage(cached_image["id"], user_id, db)
                    elapsed = time.time() - start_time
                    logger.info(f"[IMAGE] Step 2: ✅ CACHE HIT!")
                    logger.info(f"[IMAGE]   Title: '{prompt[:40]}...'")
                    logger.info(f"[IMAGE]   Matched: '{cached_image.get('prompt_text', '')[:40]}...'")
                    logger.info(f"[IMAGE]   Similarity: {cached_image['similarity']:.4f} (threshold: {self.SIMILARITY_THRESHOLD})")
                    logger.info(f"[IMAGE]   Time: {elapsed:.2f}s, Cost: $0.00")
                    return (cached_url, True, 0.0)
                else:
                    logger.warning(f"[IMAGE] ⚠️ Cache hit but empty URL for '{prompt[:30]}...', regenerating")
            
            # Step 3: No cache hit - generate new image
            elapsed_cache = time.time() - start_time
            logger.info(f"[IMAGE] Step 2: ❌ CACHE MISS (checked in {elapsed_cache:.2f}s)")
            logger.info(f"[IMAGE] Step 3: 🎨 Generating new image for '{prompt[:40]}...'")
            
            result = await self._generate_and_store_image(
                prompt, category, variant_type, user_id, title_embedding, db
            )
            
            elapsed = time.time() - start_time
            logger.info(f"[IMAGE] Step 3: ✅ Generation complete in {elapsed:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"[IMAGE] ❌ Error: {e}")
            # Fallback: try to generate without caching
            return await self._generate_and_store_image(
                prompt, category, variant_type, user_id, None, db
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
                # Don't log error - just silently skip embedding (OpenAI quota issue)
                return None
            
            response.raise_for_status()
            data = response.json()
            embedding = data["data"][0]["embedding"]
            
            # Cache it
            self._embedding_cache[cache_key] = embedding
            
            return embedding
            
        except Exception as e:
            # Silently skip - embeddings are optional for image generation
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
                
                # Skip cached images with no actual URL (Fix: prevents returning empty image_url)
                if not row.image_url or row.image_url.strip() == "":
                    no_embedding += 1  # Reusing counter for "unusable" entries
                    continue
                
                # Skip cached images with expired/non-permanent URLs
                # Only accept Cloudinary (res.cloudinary.com) or Supabase URLs - RunPod URLs expire!
                is_cloudinary = "res.cloudinary.com" in row.image_url
                is_supabase = "supabase" in row.image_url and "/storage/" in row.image_url
                if not (is_cloudinary or is_supabase):
                    logger.debug(f"[IMAGE-CACHE] Skipping non-permanent URL: {row.image_url[:50]}...")
                    no_embedding += 1
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
        db: AsyncSession
    ) -> Tuple[str, bool, float]:
        """Generate a new image using RunPod FLUX.1 Schnell and store it."""
        start_time = time.time()
        
        try:
            # Generate image via RunPod with retry logic (Fix #14)
            result, generation_time_ms = await self._call_runpod_flux_with_retry(prompt, category, variant_type)
            
            if not result:
                logger.error(f"Failed to generate image via RunPod, using fallback for {category}")
                fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS["food"])
                return (fallback_url, False, 0.0)
            
            image_url = None
            
            # Handle different result formats from FLUX.1 Schnell
            if isinstance(result, dict):
                # FLUX returns {"image": "base64data"} - decode and upload
                base64_data = result.get("image")
                if base64_data:
                    try:
                        # Decode base64 to bytes
                        image_bytes = base64.b64decode(base64_data)
                        image_url = await self._upload_to_cloudinary(image_bytes, category, variant_type)
                        if not image_url:
                            image_url = await self._upload_to_supabase(image_bytes, category, variant_type)
                    except Exception as decode_error:
                        logger.error(f"Failed to decode base64 image: {decode_error}")
                
                # Also check for direct URL in dict
                if not image_url:
                    url_from_dict = result.get("image_url") or result.get("result")
                    if url_from_dict and isinstance(url_from_dict, str) and url_from_dict.startswith("http"):
                        image_url = await self._upload_to_cloudinary_from_url(url_from_dict, category, variant_type)
                        # DO NOT fall back to RunPod URL - it expires!
                        # If Cloudinary upload failed, we'll use the fallback URL below
            
            elif isinstance(result, str) and result.startswith("http"):
                # RunPod returned a URL directly - upload to Cloudinary for permanent storage
                image_url = await self._upload_to_cloudinary_from_url(result, category, variant_type)
                # DO NOT fall back to RunPod URL - it expires!
                # If Cloudinary upload failed, image_url stays None and we'll use fallback below

            elif isinstance(result, bytes):
                # We have image bytes - upload to Cloudinary or Supabase
                image_url = await self._upload_to_cloudinary(result, category, variant_type)
                if not image_url:
                    image_url = await self._upload_to_supabase(result, category, variant_type)
            
            if not image_url:
                logger.error(f"Failed to process image result (type: {type(result)}), using fallback for {category}")
                fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS["food"])
                # DO NOT cache fallback URLs - return directly
                return (fallback_url, False, 0.0)

            # CRITICAL: Only store in cache if it's a permanent URL (Cloudinary or Supabase)
            # This prevents caching of expiring RunPod URLs or fallback URLs
            is_cloudinary = "res.cloudinary.com" in image_url
            is_supabase = "supabase" in image_url and "/storage/" in image_url
            
            if is_cloudinary or is_supabase:
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
                
                elapsed = time.time() - start_time
                storage_type = "Cloudinary" if is_cloudinary else "Supabase"
                logger.info(f"🎨 New image generated & cached ({storage_type}). Time: {elapsed:.2f}s, Cost: ${self.COST_PER_IMAGE}")
            else:
                # Non-permanent URL - return but DON'T cache
                logger.warning(f"⚠️ Image URL is not permanent storage, returning without caching: {image_url[:50]}...")
            
            return (image_url, False, self.COST_PER_IMAGE)
            
        except Exception as e:
            logger.error(f"Error generating and storing image: {e}, using fallback for {category}")
            fallback_url = self.FALLBACK_IMAGE_URLS.get(category, self.FALLBACK_IMAGE_URLS["food"])
            return (fallback_url, False, 0.0)
    
    async def _call_runpod_flux(self, prompt: str, category: str = "food", variant_type: Optional[str] = None) -> Tuple[Optional[Any], int]:
        """
        Call RunPod FLUX.1 Schnell serverless endpoint using /runsync (synchronous).
        
        OPTIMIZED for speed:
        - Uses /runsync instead of /run + polling (eliminates polling overhead)
        - 2-step inference (vs 4) - 2x faster generation
        - guidance=3.5 (vs 7) - faster processing with good quality
        - 512x512 resolution for action cards
        - $0.005 per image, standard RunPod pricing
        """
        if not self.runpod_api_key:
            logger.warning("RunPod API key not configured, using placeholder")
            return await self._generate_placeholder_image(prompt)
        
        start_time = time.time()
        
        try:
            # Enhanced prompt with category-specific styling (hero vs variant)
            enhanced_prompt = self._enhance_prompt(prompt, category, variant_type)
            
            # FLUX.1 Schnell payload - OPTIMIZED for speed
            payload = {
                "input": {
                    "prompt": enhanced_prompt,
                    "seed": -1,  # Random seed
                    "num_inference_steps": 2,  # OPTIMIZED: 2 steps (was 4) - 2x faster
                    "guidance": 3.5,  # OPTIMIZED: 3.5 (was 7) - faster processing
                    "negative_prompt": "",
                    "image_format": "png",
                    "width": 512,  # 512x512 resolution
                    "height": 512
                }
            }
            
            headers = {
                "Authorization": f"Bearer {self.runpod_api_key}",
                "Content-Type": "application/json"
            }
            
            # Use /runsync for synchronous execution - no polling needed!
            runsync_url = f"https://api.runpod.ai/v2/{self.runpod_endpoint}/runsync"
            
            logger.info(f"🎨 [FLUX] runsync: {prompt[:50]}...")
            
            response = await self.client.post(
                runsync_url,
                json=payload,
                headers=headers,
                timeout=60.0  # 60s timeout for runsync (includes generation time)
            )
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            if response.status_code != 200:
                logger.error(f"❌ [FLUX] runsync failed: {response.status_code} - {response.text[:200]}")
                return await self._generate_placeholder_image(prompt)
            
            result = response.json()
            status = result.get("status")
            
            if status == "COMPLETED":
                output = result.get("output", {})
                logger.info(f"✅ [FLUX] Completed in {elapsed_ms}ms")
                
                # FLUX.1 Schnell returns image in various formats
                if isinstance(output, dict):
                    # Check for base64 encoded image (FLUX standard)
                    if "image" in output:
                        return (output, elapsed_ms)
                    
                    # Check for direct image URL
                    image_url = output.get("image_url")
                    if image_url and image_url.startswith("http"):
                        return (image_url, elapsed_ms)
                    
                    # Check 'result' key (some RunPod formats)
                    image_url = output.get("result")
                    if image_url and isinstance(image_url, str) and image_url.startswith("http"):
                        return (image_url, elapsed_ms)
                    
                    # Check images array (some FLUX versions)
                    images = output.get("images")
                    if images and isinstance(images, list) and len(images) > 0:
                        return ({"image": images[0]}, elapsed_ms)
                
                # If output is a string (base64 or URL)
                if isinstance(output, str):
                    if output.startswith("http"):
                        return (output, elapsed_ms)
                    else:
                        return ({"image": output}, elapsed_ms)
                
                logger.error(f"❌ [FLUX] Unexpected output format: {type(output)} - {str(output)[:200]}")
                return await self._generate_placeholder_image(prompt)
            
            elif status == "FAILED":
                error = result.get("error", "Unknown error")
                logger.error(f"❌ [FLUX] Job failed: {error}")
                return await self._generate_placeholder_image(prompt)
            
            elif status == "IN_QUEUE":
                # Job queued but not started within timeout - fall back to async pattern
                job_id = result.get("id")
                logger.warning(f"⚠️ [FLUX] Job queued (runsync timeout), falling back to polling: {job_id}")
                return await self._poll_job_status(job_id, headers, start_time, prompt)
            
            else:
                logger.warning(f"⚠️ [FLUX] Unknown status: {status}")
                return (None, 0)
            
        except httpx.TimeoutException:
            elapsed = time.time() - start_time
            logger.warning(f"⚠️ [FLUX] runsync timeout after {elapsed:.1f}s - will retry")
            return (None, 0)
            
        except Exception as e:
            logger.warning(f"⚠️ [FLUX] Error: {type(e).__name__}: {e} - will retry")
            return (None, 0)
    
    async def _poll_job_status(self, job_id: str, headers: dict, start_time: float, prompt: str) -> Tuple[Optional[Any], int]:
        """Poll job status as fallback when runsync times out (job queued)."""
        if not job_id:
            return await self._generate_placeholder_image(prompt)
        
        status_url = f"https://api.runpod.ai/v2/{self.runpod_endpoint}/status/{job_id}"
        max_polls = 60  # 30 seconds max (60 × 0.5s)
        poll_interval = 0.5
        
        for poll_num in range(max_polls):
            await asyncio.sleep(poll_interval)
            
            try:
                status_response = await self.client.get(
                    status_url,
                    headers=headers,
                    timeout=5.0
                )
                
                if status_response.status_code != 200:
                    continue
                
                status_result = status_response.json()
                status = status_result.get("status")
                
                if status == "COMPLETED":
                    output = status_result.get("output", {})
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    logger.info(f"✅ [FLUX] Completed in {elapsed_ms}ms (via polling)")
                    
                    if isinstance(output, dict) and "image" in output:
                        return (output, elapsed_ms)
                    if isinstance(output, dict):
                        images = output.get("images")
                        if images and len(images) > 0:
                            return ({"image": images[0]}, elapsed_ms)
                    if isinstance(output, str):
                        return ({"image": output}, elapsed_ms) if not output.startswith("http") else (output, elapsed_ms)
                    return await self._generate_placeholder_image(prompt)
                
                elif status == "FAILED":
                    return await self._generate_placeholder_image(prompt)
                
            except Exception:
                continue
        
        return (None, 0)

    async def _call_runpod_flux_legacy(self, prompt: str, category: str = "food", variant_type: Optional[str] = None) -> Tuple[Optional[Any], int]:
        """
        Legacy polling logic for RunPod (used as fallback).
        """
        start_time = time.time()
        try:
            endpoint_url = f"https://api.runpod.ai/v2/{self.runpod_endpoint}/run"
            enhanced_prompt = self._enhance_prompt(prompt, category, variant_type)
            
            payload = {
                "input": {
                    "prompt": enhanced_prompt,
                    "width": 512,
                    "height": 512,
                    "num_inference_steps": 2,  # OPTIMIZED: 2 steps (was 4)
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
    
    async def _call_runpod_flux_with_retry(self, prompt: str, category: str = "food", variant_type: Optional[str] = None) -> Tuple[Optional[Any], int]:
        """
        Call RunPod FLUX.1 Schnell with retry logic.
        
        FLUX.1 Schnell is fast (4-step inference) so retries should be rare,
        but we keep retry logic for robustness against transient network issues.
        """
        total_start = time.time()
        
        for attempt in range(self.MAX_IMAGE_RETRIES):
            try:
                logger.info(f"🎨 FLUX attempt {attempt + 1}/{self.MAX_IMAGE_RETRIES} for: {prompt[:50]}...")
                result, gen_time = await self._call_runpod_flux(prompt, category, variant_type)
                
                if result:
                    total_time = time.time() - total_start
                    if attempt > 0:
                        logger.info(f"✅ FLUX succeeded on retry {attempt + 1} (total time: {total_time:.1f}s)")
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
    
    def _enhance_prompt(self, prompt: str, category: str, variant_type: Optional[str] = None) -> str:
        """
        Create prompts that show users EXACTLY what to eat or do.
        
        AUVRA-specific approach:
        1. Focus on the ACTUAL CONTENT - what does this food/exercise look like?
        2. Make it actionable - user should understand what to prepare/do
        3. Keep it simple - AI image models don't need camera specs
        4. Be specific about the subject, not the photography
        
        HERO vs VARIANT distinction (for food category):
        - HERO images: Show the ingredient/supplement beautifully and aesthetically
          (e.g., "Turmeric" shows golden turmeric root, "Ashwagandha" shows the herb)
        - VARIANT images (tasty/easy/healthy): Show the prepared dish/recipe
          (e.g., "Turmeric Latte", "Ashwagandha Tea" shows the finished drink)
        
        Args:
            prompt: Action title (e.g., "Chickpea Salad", "Swimming", "Deep Breathing")
            category: "food", "movement", or "mindfulness"
            variant_type: "hero", "tasty", "easy", or "healthy" (optional)
            
        Returns:
            Enhanced prompt that accurately represents the action
        """
        logger.info(f"[PROMPT] Enhancing: '{prompt}' (category: {category}, variant: {variant_type})")
        
        if category == "food":
            # HERO images: Show the ingredient/supplement beautifully
            # Action titles are nouns (Turmeric, Ashwagandha, Vitamin B6, Chasteberry)
            # These should show the actual ingredient in an aesthetic, wellness style
            if variant_type == "hero":
                enhanced = (
                    f"Beautiful aesthetic product photography of {prompt}, "
                    f"natural wellness ingredient styled like a premium supplement brand, "
                    f"artistic composition with soft natural lighting, "
                    f"clean minimalist background, golden warm tones, "
                    f"the {prompt} looks premium and luxurious, "
                    f"wellness lifestyle aesthetic, Instagram-worthy, "
                    f"show the raw natural form of {prompt}, "
                    f"apothecary wellness vibes, holistic health aesthetic"
                )
            else:
                # VARIANT images (tasty/easy/healthy): Show FINISHED, READY-TO-EAT dishes
                # Think: Instagram food photography, cozy wellness aesthetic
                # Users want to see appetizing meals that inspire them to eat healthy
                enhanced = (
                    f"Professional Instagram-style food photography of {prompt}, "
                    f"beautifully plated finished dish ready to eat, "
                    f"warm cozy aesthetic, appetizing and delicious looking, "
                    f"soft natural morning light from window, "
                    f"styled like a wellness food blog, minimalist background, "
                    f"the food looks fresh, nourishing, and inviting, "
                    f"NOT raw ingredients - show the prepared meal, "
                    f"healthy comfort food aesthetic, women's wellness lifestyle"
                )
            
        elif category == "movement":
            # MOVEMENT: Show the ACTUAL exercise position and form
            # User should be able to understand how to do the exercise from the image
            enhanced = (
                f"A woman demonstrating {prompt} exercise, "
                f"clear view of the body position and correct form, "
                f"showing exactly how to perform {prompt}, "
                f"wearing comfortable athletic clothes, "
                f"focused calm expression, bright clean background, "
                f"the exercise pose should be clearly recognizable as {prompt}, "
                f"fitness photography style, full body visible, "
                f"instructional and easy to follow, women's wellness exercise"
            )
            
        elif category == "mindfulness":
            # MINDFULNESS: Show the ACTUAL meditation or breathing practice
            # User should understand what the practice looks like
            enhanced = (
                f"A peaceful woman practicing {prompt}, "
                f"showing the meditation or breathing position clearly, "
                f"eyes closed, serene relaxed expression, "
                f"comfortable seated or resting position for {prompt}, "
                f"wearing soft comfortable clothes, "
                f"calm cozy setting with soft natural light, "
                f"the practice should be recognizable as {prompt}, "
                f"peaceful wellness moment, self-care atmosphere"
            )
            
        else:
            # Fallback: simple clear wellness image
            enhanced = (
                f"A beautiful wellness image of {prompt}, "
                f"clean minimalist style, bright natural lighting, "
                f"clearly showing what {prompt} looks like"
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
        variant_type: Optional[str]
    ) -> Optional[str]:
        """Upload image from URL to Cloudinary for permanent storage."""
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
            file_hash = hashlib.md5(image_url.encode()).hexdigest()[:8]
            variant_str = f"_{variant_type}" if variant_type else ""
            public_id = f"auvra/{category}{variant_str}_{timestamp}_{file_hash}"
            
            # Upload from URL in a separate thread to avoid blocking the event loop
            result = await asyncio.to_thread(
                cloudinary.uploader.upload,
                image_url,
                public_id=public_id,
                folder="action-plan-images",
                resource_type="image"
            )
            
            cloudinary_url = result.get("secure_url")
            if cloudinary_url:
                logger.info(f"📤 Image uploaded to Cloudinary from URL: {cloudinary_url}")
                return cloudinary_url
            
            return None
            
        except ImportError:
            logger.warning("cloudinary package not installed")
            return None
        except Exception as e:
            logger.error(f"Error uploading to Cloudinary from URL: {e}")
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


# Global instance
_image_library_service: Optional[ImageLibraryService] = None


def get_image_library_service() -> ImageLibraryService:
    """Get or create the image library service singleton."""
    global _image_library_service
    if _image_library_service is None:
        _image_library_service = ImageLibraryService()
    return _image_library_service
