# AUVRA Action Plan System V2 - Organic Semantic Image Cache

**Date:** December 18, 2025  
**Key Insight:** Store every generated image with text embedding, match semantically for reuse

---

## 🎯 Core Concept

```
GPT has FULL FREEDOM to generate any food recommendation
                     ↓
We check if similar image already exists (semantic matching)
                     ↓
Match found (>85% similar)? → REUSE existing image
No match? → Generate new, store for future reuse
                     ↓
Cache grows organically, hit rate increases over time
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ACTION PLAN IMAGE PIPELINE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. GPT-4o-mini generates action: "Grilled Salmon with Lemon"              │
│                           ↓                                                 │
│  2. Generate text embedding via OpenAI (ada-002)                           │
│     Cost: $0.0001 per embedding                                            │
│                           ↓                                                 │
│  3. Search image_cache table using pgvector cosine similarity              │
│                           ↓                                                 │
│     ┌────────────────────┴────────────────────┐                            │
│     │                                         │                            │
│     ▼                                         ▼                            │
│  [CACHE HIT]                            [CACHE MISS]                       │
│  similarity > 0.85                      similarity < 0.85                  │
│     │                                         │                            │
│     │                                         ▼                            │
│     │                               Generate via RunPod                    │
│     │                               Cost: $0.0006                          │
│     │                                         │                            │
│     │                                         ▼                            │
│     │                               Upload to Supabase Storage             │
│     │                                         │                            │
│     │                                         ▼                            │
│     │                               Store in image_cache with:             │
│     │                               - food_text                            │
│     │                               - text_embedding                       │
│     │                               - image_url                            │
│     │                                         │                            │
│     └────────────────────┬────────────────────┘                            │
│                          ▼                                                  │
│  4. Return image_url to user                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📊 Database Schema

### Enable pgvector Extension
```sql
-- Run once on Supabase/PostgreSQL
CREATE EXTENSION IF NOT EXISTS vector;
```

### image_cache Table
```sql
CREATE TABLE image_cache (
    id SERIAL PRIMARY KEY,
    
    -- Text that was used to generate this image
    food_text TEXT NOT NULL,
    
    -- Text embedding for semantic matching (OpenAI ada-002 = 1536 dimensions)
    text_embedding vector(1536) NOT NULL,
    
    -- Generated image details
    image_url TEXT NOT NULL,
    image_variant VARCHAR(20) DEFAULT 'hero',  -- hero, easy, tasty, healthy
    
    -- Metadata
    generation_cost DECIMAL(10, 6),
    generation_time_ms INTEGER,
    
    -- Usage tracking
    usage_count INTEGER DEFAULT 1,
    last_used_at TIMESTAMP DEFAULT NOW(),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- Index for fast vector search
    CONSTRAINT unique_text_variant UNIQUE (food_text, image_variant)
);

-- Create vector index for fast similarity search
CREATE INDEX idx_image_cache_embedding ON image_cache 
USING ivfflat (text_embedding vector_cosine_ops) WITH (lists = 100);

-- Index for usage stats
CREATE INDEX idx_image_cache_usage ON image_cache (usage_count DESC);
CREATE INDEX idx_image_cache_last_used ON image_cache (last_used_at DESC);
```

---

## 🔧 Implementation

### 1. Image Cache Service

```python
# /app/services/image_cache_service.py

import os
import httpx
import numpy as np
from typing import Optional, Tuple, List
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

@dataclass
class CacheResult:
    image_url: str
    similarity_score: float
    is_cache_hit: bool
    cache_id: Optional[int] = None
    generation_cost: float = 0.0

class ImageCacheService:
    """
    Semantic image cache using text embeddings.
    - Stores every generated image with its text embedding
    - Matches new requests against existing images
    - Reuses images when similarity > threshold
    """
    
    SIMILARITY_THRESHOLD = 0.85  # Cosine similarity threshold for reuse
    EMBEDDING_MODEL = "text-embedding-ada-002"
    EMBEDDING_DIMENSION = 1536
    EMBEDDING_COST = 0.0001  # $0.0001 per 1K tokens
    
    def __init__(self, db: Session):
        self.db = db
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        self.runpod_api_key = os.environ.get("RUNPOD_API_KEY")
        self.runpod_endpoint = os.environ.get("RUNPOD_SERVERLESS_ENDPOINT")
        self.supabase_url = os.environ.get("SUPABASE_URL")
        self.supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
    
    async def get_or_generate_image(
        self,
        food_text: str,
        variant: str = "hero",
        hormone: str = None
    ) -> CacheResult:
        """
        Get image from cache or generate new one.
        
        Args:
            food_text: The food description (e.g., "Grilled Salmon with Lemon")
            variant: Image variant (hero, easy, tasty, healthy)
            hormone: Target hormone for prompt enhancement
            
        Returns:
            CacheResult with image URL and cache info
        """
        
        # Step 1: Generate text embedding
        embedding = await self._get_embedding(food_text)
        
        # Step 2: Search for similar images
        similar_image = await self._find_similar_image(embedding, variant)
        
        if similar_image:
            # Cache HIT - update usage stats and return
            await self._update_usage_stats(similar_image['id'])
            
            logger.info(f"🎯 Cache HIT: '{food_text}' matched '{similar_image['food_text']}' "
                       f"(similarity: {similar_image['similarity']:.3f})")
            
            return CacheResult(
                image_url=similar_image['image_url'],
                similarity_score=similar_image['similarity'],
                is_cache_hit=True,
                cache_id=similar_image['id'],
                generation_cost=self.EMBEDDING_COST  # Only embedding cost
            )
        
        # Cache MISS - generate new image
        logger.info(f"🆕 Cache MISS: Generating new image for '{food_text}'")
        
        image_url, gen_cost, gen_time = await self._generate_image(food_text, variant, hormone)
        
        # Store in cache for future reuse
        cache_id = await self._store_in_cache(
            food_text=food_text,
            embedding=embedding,
            image_url=image_url,
            variant=variant,
            generation_cost=gen_cost,
            generation_time_ms=gen_time
        )
        
        total_cost = self.EMBEDDING_COST + gen_cost
        
        return CacheResult(
            image_url=image_url,
            similarity_score=1.0,  # Exact match (just generated)
            is_cache_hit=False,
            cache_id=cache_id,
            generation_cost=total_cost
        )
    
    async def _get_embedding(self, text: str) -> List[float]:
        """Generate text embedding using OpenAI ada-002."""
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.EMBEDDING_MODEL,
                    "input": text
                },
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]
    
    async def _find_similar_image(
        self, 
        embedding: List[float], 
        variant: str
    ) -> Optional[dict]:
        """
        Find most similar image in cache using pgvector.
        Returns None if no match above threshold.
        """
        
        # Convert embedding to PostgreSQL vector format
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        
        query = text("""
            SELECT 
                id,
                food_text,
                image_url,
                1 - (text_embedding <=> :embedding::vector) as similarity
            FROM image_cache
            WHERE image_variant = :variant
            ORDER BY text_embedding <=> :embedding::vector
            LIMIT 1
        """)
        
        result = self.db.execute(query, {
            "embedding": embedding_str,
            "variant": variant
        }).fetchone()
        
        if result and result.similarity >= self.SIMILARITY_THRESHOLD:
            return {
                "id": result.id,
                "food_text": result.food_text,
                "image_url": result.image_url,
                "similarity": result.similarity
            }
        
        return None
    
    async def _generate_image(
        self,
        food_text: str,
        variant: str,
        hormone: str = None
    ) -> Tuple[str, float, int]:
        """
        Generate image using RunPod Flux Schnell.
        Returns: (image_url, cost, generation_time_ms)
        """
        import time
        
        # Build optimized prompt
        prompt = self._build_image_prompt(food_text, variant, hormone)
        
        start_time = time.time()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.runpod_endpoint}/run",
                headers={
                    "Authorization": f"Bearer {self.runpod_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "input": {
                        "prompt": prompt,
                        "width": 512,
                        "height": 512,
                        "num_inference_steps": 4,
                        "guidance_scale": 0.0,
                        "seed": -1
                    }
                },
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()
        
        gen_time_ms = int((time.time() - start_time) * 1000)
        
        # Get the base64 image from response
        image_base64 = data.get("output", {}).get("image")
        
        # Upload to Supabase Storage
        image_url = await self._upload_to_supabase(image_base64, food_text, variant)
        
        # RunPod Flux Schnell cost: ~$0.0006 per image
        generation_cost = 0.0006
        
        return image_url, generation_cost, gen_time_ms
    
    def _build_image_prompt(
        self,
        food_text: str,
        variant: str,
        hormone: str = None
    ) -> str:
        """Build optimized image generation prompt."""
        
        base_style = "Professional food photography, bright natural lighting, "
        
        variant_styles = {
            "hero": "elegant plating on white ceramic, top-down angle, minimal garnish, clean composition",
            "easy": "casual home kitchen setting, simple preparation visible, approachable and doable",
            "tasty": "close-up macro shot, vibrant colors, steam or freshness visible, appetizing",
            "healthy": "fresh ingredients visible, leafy greens nearby, wellness aesthetic, light and bright"
        }
        
        hormone_hints = {
            "insulin": ", blood sugar friendly, low glycemic appearance",
            "cortisol": ", calming presentation, stress-reducing foods",
            "estrogen": ", phytoestrogen rich ingredients visible",
            "progesterone": ", warming comfort food appearance",
            "testosterone": ", protein-rich, energetic presentation"
        }
        
        style = variant_styles.get(variant, variant_styles["hero"])
        hormone_hint = hormone_hints.get(hormone.lower(), "") if hormone else ""
        
        prompt = f"{base_style}{style}. {food_text}{hormone_hint}"
        
        return prompt
    
    async def _upload_to_supabase(
        self,
        image_base64: str,
        food_text: str,
        variant: str
    ) -> str:
        """Upload image to Supabase Storage and return public URL."""
        import base64
        import uuid
        from datetime import datetime
        
        # Generate unique filename
        safe_name = "".join(c if c.isalnum() else "_" for c in food_text[:30])
        filename = f"{safe_name}_{variant}_{uuid.uuid4().hex[:8]}.webp"
        path = f"action-images/{datetime.now().strftime('%Y/%m')}/{filename}"
        
        # Decode base64
        image_bytes = base64.b64decode(image_base64)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.supabase_url}/storage/v1/object/auvra-images/{path}",
                headers={
                    "Authorization": f"Bearer {self.supabase_key}",
                    "Content-Type": "image/webp"
                },
                content=image_bytes,
                timeout=30.0
            )
            response.raise_for_status()
        
        # Return public URL
        return f"{self.supabase_url}/storage/v1/object/public/auvra-images/{path}"
    
    async def _store_in_cache(
        self,
        food_text: str,
        embedding: List[float],
        image_url: str,
        variant: str,
        generation_cost: float,
        generation_time_ms: int
    ) -> int:
        """Store generated image in cache for future reuse."""
        
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        
        query = text("""
            INSERT INTO image_cache 
                (food_text, text_embedding, image_url, image_variant, 
                 generation_cost, generation_time_ms)
            VALUES 
                (:food_text, :embedding::vector, :image_url, :variant,
                 :cost, :time_ms)
            ON CONFLICT (food_text, image_variant) DO UPDATE SET
                usage_count = image_cache.usage_count + 1,
                last_used_at = NOW()
            RETURNING id
        """)
        
        result = self.db.execute(query, {
            "food_text": food_text,
            "embedding": embedding_str,
            "image_url": image_url,
            "variant": variant,
            "cost": generation_cost,
            "time_ms": generation_time_ms
        })
        
        self.db.commit()
        return result.fetchone().id
    
    async def _update_usage_stats(self, cache_id: int):
        """Update usage statistics for cache hit."""
        
        query = text("""
            UPDATE image_cache 
            SET usage_count = usage_count + 1,
                last_used_at = NOW()
            WHERE id = :cache_id
        """)
        
        self.db.execute(query, {"cache_id": cache_id})
        self.db.commit()
    
    # =========================================================================
    # Analytics Methods
    # =========================================================================
    
    def get_cache_stats(self) -> dict:
        """Get cache performance statistics."""
        
        query = text("""
            SELECT 
                COUNT(*) as total_images,
                SUM(usage_count) as total_uses,
                AVG(usage_count) as avg_uses_per_image,
                SUM(generation_cost) as total_generation_cost,
                COUNT(CASE WHEN usage_count > 1 THEN 1 END) as reused_images,
                SUM(CASE WHEN usage_count > 1 THEN (usage_count - 1) * 0.0006 ELSE 0 END) as savings
            FROM image_cache
        """)
        
        result = self.db.execute(query).fetchone()
        
        return {
            "total_images": result.total_images,
            "total_uses": result.total_uses,
            "avg_uses_per_image": round(result.avg_uses_per_image or 0, 2),
            "total_generation_cost": round(result.total_generation_cost or 0, 4),
            "reused_images": result.reused_images,
            "estimated_savings": round(result.savings or 0, 4),
            "cache_hit_rate": round((1 - result.total_images / result.total_uses) * 100, 1) if result.total_uses else 0
        }
    
    def get_most_used_images(self, limit: int = 20) -> List[dict]:
        """Get most frequently reused images."""
        
        query = text("""
            SELECT food_text, image_url, image_variant, usage_count, created_at
            FROM image_cache
            ORDER BY usage_count DESC
            LIMIT :limit
        """)
        
        results = self.db.execute(query, {"limit": limit}).fetchall()
        
        return [
            {
                "food_text": r.food_text,
                "image_url": r.image_url,
                "variant": r.image_variant,
                "usage_count": r.usage_count,
                "created_at": r.created_at.isoformat()
            }
            for r in results
        ]
```

---

## 📈 Cache Growth Projection

| Week | Images in Cache | Estimated Hit Rate | Avg Cost/Image |
|------|-----------------|-------------------|----------------|
| 1 | 100-200 | 30-40% | $0.00048 |
| 2 | 300-500 | 60-70% | $0.00030 |
| 4 | 600-800 | 80-85% | $0.00020 |
| 8+ | 1000+ | 90%+ | $0.00015 |

**Long-term steady state:**
- ~$0.00015 per image request
- vs $0.0006 if generating every time
- **75% cost savings**

---

## 🔄 Integration with Action Plan

```python
# In action_plan_generator.py

async def generate_action_with_image(
    action_text: str,
    hormone: str,
    variant: str = "hero"
) -> dict:
    """Generate action and get/create image."""
    
    cache_service = ImageCacheService(db)
    
    # Get image (from cache or newly generated)
    result = await cache_service.get_or_generate_image(
        food_text=action_text,
        variant=variant,
        hormone=hormone
    )
    
    return {
        "action_text": action_text,
        "hormone": hormone,
        "image_url": result.image_url,
        "image_cache_hit": result.is_cache_hit,
        "image_cost": result.generation_cost
    }
```

---

## 🎯 Key Benefits

1. **GPT Freedom**: No vocabulary restrictions, generate any food
2. **Semantic Matching**: "Salmon" matches "Grilled Salmon with Herbs"  
3. **Organic Growth**: Cache gets smarter over time
4. **Cost Efficient**: 75%+ savings after warm-up period
5. **No Waste**: Only generate what's actually needed
6. **Cross-User Sharing**: Automatic image reuse across all users

---

## 🛡️ Edge Cases Handled

| Scenario | Solution |
|----------|----------|
| Very unique food | Generate new, store for future |
| Slight variations | Semantic matching finds similar |
| Different variants | Each variant cached separately |
| Typos in GPT output | Embedding similarity still works |
| Different languages | Embeddings are semantic, not lexical |

---

## 📊 Monitoring Dashboard Queries

```sql
-- Daily cache performance
SELECT 
    DATE(created_at) as date,
    COUNT(*) as new_images,
    SUM(usage_count) as total_requests,
    ROUND(1 - COUNT(*)::decimal / SUM(usage_count), 3) as hit_rate
FROM image_cache
GROUP BY DATE(created_at)
ORDER BY date DESC
LIMIT 30;

-- Most valuable cached images (highest reuse)
SELECT food_text, usage_count, 
       usage_count * 0.0006 as savings_generated
FROM image_cache
ORDER BY usage_count DESC
LIMIT 20;
```
