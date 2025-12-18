# AUVRA Action Plan System - Complete Master Specification

**Date:** December 18, 2025  
**Version:** 2.0  
**Status:** Ready for Implementation

---

## 📋 Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Architecture](#system-architecture)
3. [Core Requirements](#core-requirements)
4. [Database Schema](#database-schema)
5. [Image Cache System](#image-cache-system)
6. [Action Plan Generator](#action-plan-generator)
7. [GPT Prompts](#gpt-prompts)
8. [API Endpoints](#api-endpoints)
9. [Mobile Integration](#mobile-integration)
10. [Feedback System](#feedback-system)
11. [Cost Analysis](#cost-analysis)
12. [Implementation Timeline](#implementation-timeline)

---

## 1. Executive Summary

### What We're Building
A daily action plan system that delivers **4 personalized health actions** to users each day, each targeting a **single hormone** with beautiful AI-generated images.

### Key Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Image Generation | RunPod Flux Schnell | $0.0006/image, 2s generation |
| Action Generation | GPT-4o-mini | Cost-effective, good quality |
| Image Caching | Organic Semantic Cache | GPT freedom + smart reuse |
| Actions per Day | 4 total | 1 Food, 1 Movement, 1 Mindfulness, 1 Flex |
| Hormone Mapping | 1 action = 1 hormone | Clear, focused recommendations |
| Image Reuse | Cross-user sharing | 75%+ cost savings |
| Feedback Timer | 30 seconds | User must try before replacing |
| Existing System | Replace scheduling endpoint | Clean integration |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         AUVRA ACTION PLAN SYSTEM                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────┐                                                               │
│  │   Mobile    │                                                               │
│  │   App       │                                                               │
│  └──────┬──────┘                                                               │
│         │                                                                       │
│         ▼                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        FastAPI Backend                                   │   │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐               │   │
│  │  │ /action-plan  │  │ /action-plan  │  │ /action-plan  │               │   │
│  │  │ /today        │  │ /feedback     │  │ /replace      │               │   │
│  │  └───────┬───────┘  └───────────────┘  └───────────────┘               │   │
│  └──────────┼───────────────────────────────────────────────────────────────┘   │
│             │                                                                   │
│             ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                     Action Plan Generator                                │   │
│  │                                                                          │   │
│  │  1. Get user profile + hormone data                                     │   │
│  │  2. Call V3 Recommendation Engine (get evidence-based actions)          │   │
│  │  3. Use GPT-4o-mini to personalize 4 actions (1 per hormone focus)      │   │
│  │  4. Get/generate images via Semantic Cache                              │   │
│  │  5. Return complete action plan                                         │   │
│  │                                                                          │   │
│  └──────────┬────────────────────────────┬──────────────────────────────────┘   │
│             │                            │                                      │
│             ▼                            ▼                                      │
│  ┌─────────────────────┐     ┌─────────────────────────────────────────────┐   │
│  │  V3 Recommendation  │     │         Semantic Image Cache                │   │
│  │  Engine             │     │                                             │   │
│  │  ┌───────────────┐  │     │  ┌───────────────┐   ┌─────────────────┐   │   │
│  │  │ NutritionExpert│  │     │  │ Get Embedding │──▶│ pgvector Search │   │   │
│  │  │ MovementExpert │  │     │  │ (ada-002)     │   │ (similarity)    │   │   │
│  │  │MindfulnessExpert│ │     │  └───────────────┘   └────────┬────────┘   │   │
│  │  └───────────────┘  │     │                               │            │   │
│  │         │           │     │         ┌─────────────────────┴──────┐     │   │
│  │         ▼           │     │         │                            │     │   │
│  │  ┌───────────────┐  │     │    [HIT > 0.85]              [MISS]  │     │   │
│  │  │ Pinecone RAG  │  │     │         │                       │    │     │   │
│  │  │ (Research)    │  │     │         ▼                       ▼    │     │   │
│  │  └───────────────┘  │     │   Return cached          Generate new│     │   │
│  └─────────────────────┘     │   image URL          via RunPod Flux │     │   │
│                              │                              │       │     │   │
│                              │                              ▼       │     │   │
│                              │                    Upload to Supabase│     │   │
│                              │                    Store in cache    │     │   │
│                              └──────────────────────────────────────┘     │   │
│                                                                           │   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         PostgreSQL + Supabase                        │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐  │   │
│  │  │ image_cache  │ │action_plans  │ │action_feedback│ │user_profiles│ │   │
│  │  │ (pgvector)   │ │              │ │               │ │            │  │   │
│  │  └──────────────┘ └──────────────┘ └──────────────┘ └────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Requirements

### 3.1 Daily Action Structure

Each day, user receives **4 actions**:

| Slot | Category | Hormone Focus | Example |
|------|----------|---------------|---------|
| 1 | Food | User's primary imbalance | "Pumpkin Seeds" → Progesterone |
| 2 | Movement | User's secondary concern | "Morning Yoga" → Cortisol |
| 3 | Mindfulness | Stress/mood related | "Box Breathing" → Cortisol |
| 4 | Flex (any) | Next priority hormone | "Spearmint Tea" → Testosterone |

### 3.2 One Action = One Hormone (CRITICAL)

```
❌ WRONG: "Pumpkin Seeds" targets [insulin, testosterone, progesterone]
✅ RIGHT: "Pumpkin Seeds" targets [progesterone] - single focus
```

Each action card shows:
- **Title**: "Pumpkin Seeds"
- **Hormone Badge**: Single hormone icon (e.g., Progesterone)
- **Purpose**: "Supports healthy progesterone levels"
- **Image**: AI-generated food image

### 3.3 Image Requirements

| Aspect | Requirement |
|--------|-------------|
| Size | 512x512px (optimized for mobile) |
| Format | WebP (smaller file size) |
| Variants | 1 hero image per action |
| Style | Professional food photography, bright, appetizing |
| Storage | Supabase Storage with CDN |

---

## 4. Database Schema

### 4.1 Enable pgvector
```sql
-- Run once on PostgreSQL/Supabase
CREATE EXTENSION IF NOT EXISTS vector;
```

### 4.2 image_cache (Semantic Image Cache)
```sql
CREATE TABLE image_cache (
    id SERIAL PRIMARY KEY,
    
    -- The text used to generate this image
    food_text TEXT NOT NULL,
    
    -- Text embedding for semantic matching (OpenAI ada-002 = 1536 dimensions)
    text_embedding vector(1536) NOT NULL,
    
    -- Image details
    image_url TEXT NOT NULL,
    image_variant VARCHAR(20) DEFAULT 'hero',
    
    -- Generation metadata
    generation_cost DECIMAL(10, 6),
    generation_time_ms INTEGER,
    prompt_used TEXT,
    
    -- Usage tracking (for cache analytics)
    usage_count INTEGER DEFAULT 1,
    last_used_at TIMESTAMP DEFAULT NOW(),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- Prevent duplicate exact matches
    CONSTRAINT unique_food_variant UNIQUE (food_text, image_variant)
);

-- Vector similarity index (CRITICAL for performance)
CREATE INDEX idx_image_cache_embedding 
ON image_cache USING ivfflat (text_embedding vector_cosine_ops) 
WITH (lists = 100);

-- Usage analytics indexes
CREATE INDEX idx_image_cache_usage ON image_cache (usage_count DESC);
CREATE INDEX idx_image_cache_created ON image_cache (created_at DESC);
```

### 4.3 action_plans (Daily Plans)
```sql
CREATE TABLE action_plans (
    id SERIAL PRIMARY KEY,
    uid VARCHAR(255) NOT NULL REFERENCES user_profiles(uid) ON DELETE CASCADE,
    
    -- Plan date
    plan_date DATE NOT NULL,
    
    -- Actions stored as JSONB array
    actions JSONB NOT NULL,
    -- Structure:
    -- [
    --   {
    --     "id": "uuid",
    --     "slot": 1,
    --     "category": "food",
    --     "title": "Pumpkin Seeds",
    --     "purpose": "Supports progesterone production",
    --     "hormone": "progesterone",
    --     "image_url": "https://...",
    --     "image_cache_id": 123,
    --     "specific_action": "Add 2 tbsp to morning oatmeal",
    --     "research_summary": "Studies show zinc in pumpkin seeds...",
    --     "is_completed": false,
    --     "is_replaced": false,
    --     "feedback": null
    --   }
    -- ]
    
    -- Generation metadata
    generation_cost DECIMAL(10, 6),
    generation_time_ms INTEGER,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- One plan per user per day
    CONSTRAINT unique_user_date UNIQUE (uid, plan_date)
);

CREATE INDEX idx_action_plans_user_date ON action_plans (uid, plan_date DESC);
```

### 4.4 action_feedback (User Feedback)
```sql
CREATE TABLE action_feedback (
    id SERIAL PRIMARY KEY,
    
    -- References
    action_plan_id INTEGER NOT NULL REFERENCES action_plans(id) ON DELETE CASCADE,
    action_id VARCHAR(36) NOT NULL,  -- UUID of the action within the plan
    uid VARCHAR(255) NOT NULL,
    
    -- Feedback type
    feedback_type VARCHAR(20) NOT NULL,  -- 'completed', 'replaced', 'skipped'
    
    -- For replacements
    replacement_reason TEXT,  -- User's reason for replacing
    replaced_with_action_id VARCHAR(36),  -- New action that replaced this
    
    -- Timing
    time_to_feedback_seconds INTEGER,  -- How long user took to give feedback
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- One feedback per action
    CONSTRAINT unique_action_feedback UNIQUE (action_plan_id, action_id)
);

CREATE INDEX idx_feedback_user ON action_feedback (uid, created_at DESC);
CREATE INDEX idx_feedback_type ON action_feedback (feedback_type);
```

### 4.5 cost_tracking (Cost Monitoring)
```sql
CREATE TABLE action_plan_costs (
    id SERIAL PRIMARY KEY,
    
    -- Date for aggregation
    cost_date DATE NOT NULL,
    
    -- Costs by type
    gpt_calls INTEGER DEFAULT 0,
    gpt_cost DECIMAL(10, 6) DEFAULT 0,
    
    embedding_calls INTEGER DEFAULT 0,
    embedding_cost DECIMAL(10, 6) DEFAULT 0,
    
    image_generations INTEGER DEFAULT 0,
    image_generation_cost DECIMAL(10, 6) DEFAULT 0,
    
    cache_hits INTEGER DEFAULT 0,
    cache_misses INTEGER DEFAULT 0,
    
    -- Totals
    total_cost DECIMAL(10, 6) DEFAULT 0,
    total_requests INTEGER DEFAULT 0,
    
    -- One row per day
    CONSTRAINT unique_cost_date UNIQUE (cost_date)
);

CREATE INDEX idx_costs_date ON action_plan_costs (cost_date DESC);
```

---

## 5. Image Cache System

### 5.1 How It Works

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                        ORGANIC SEMANTIC IMAGE CACHE                            │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  GPT generates: "Grilled Salmon with Lemon Herbs"                             │
│                              │                                                 │
│                              ▼                                                 │
│  ┌────────────────────────────────────────────┐                               │
│  │  Step 1: Generate Text Embedding            │                               │
│  │  OpenAI ada-002 → 1536-dim vector          │                               │
│  │  Cost: $0.0001                              │                               │
│  └─────────────────────┬──────────────────────┘                               │
│                        │                                                       │
│                        ▼                                                       │
│  ┌────────────────────────────────────────────┐                               │
│  │  Step 2: Search image_cache via pgvector   │                               │
│  │  Query: "Find image where cosine           │                               │
│  │         similarity > 0.85"                 │                               │
│  └─────────────────────┬──────────────────────┘                               │
│                        │                                                       │
│           ┌────────────┴────────────┐                                          │
│           │                         │                                          │
│           ▼                         ▼                                          │
│    ┌─────────────┐          ┌─────────────┐                                   │
│    │  CACHE HIT  │          │ CACHE MISS  │                                   │
│    │ sim > 0.85  │          │ sim < 0.85  │                                   │
│    └──────┬──────┘          └──────┬──────┘                                   │
│           │                        │                                           │
│           ▼                        ▼                                           │
│    Return existing          Generate via RunPod                                │
│    image URL                 Cost: $0.0006                                     │
│           │                        │                                           │
│           │                        ▼                                           │
│           │                 Upload to Supabase                                 │
│           │                        │                                           │
│           │                        ▼                                           │
│           │                 Store in image_cache:                              │
│           │                 - food_text                                        │
│           │                 - text_embedding                                   │
│           │                 - image_url                                        │
│           │                        │                                           │
│           └────────────┬───────────┘                                           │
│                        ▼                                                       │
│                 Return image_url                                               │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Semantic Matching Examples

| New Request | Cached Image | Similarity | Action |
|-------------|--------------|------------|--------|
| "Pumpkin Seeds" | "Raw Pumpkin Seed Snack" | 0.94 | ✅ Reuse |
| "Grilled Salmon" | "Salmon with Herbs" | 0.91 | ✅ Reuse |
| "Greek Yogurt Bowl" | "Yogurt with Berries" | 0.87 | ✅ Reuse |
| "Morning Green Smoothie" | "Spinach Kale Smoothie" | 0.89 | ✅ Reuse |
| "Tempeh Stir Fry" | "Grilled Chicken" | 0.42 | ❌ Generate new |

### 5.3 Cache Service Implementation

```python
# /app/services/image_cache_service.py

import os
import httpx
from typing import Optional, List, Tuple
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import base64
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class ImageCacheResult:
    image_url: str
    similarity_score: float
    is_cache_hit: bool
    cache_id: Optional[int] = None
    generation_cost: float = 0.0

class ImageCacheService:
    """
    Semantic image cache using text embeddings.
    Stores every generated image and matches new requests semantically.
    """
    
    SIMILARITY_THRESHOLD = 0.85
    EMBEDDING_MODEL = "text-embedding-ada-002"
    EMBEDDING_COST_PER_CALL = 0.0001
    RUNPOD_COST_PER_IMAGE = 0.0006
    
    def __init__(self, db: Session):
        self.db = db
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        self.runpod_api_key = os.environ.get("RUNPOD_API_KEY")
        self.runpod_endpoint = os.environ.get("RUNPOD_ENDPOINT_URL")
        self.supabase_url = os.environ.get("SUPABASE_URL")
        self.supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
    
    async def get_or_generate_image(
        self,
        food_text: str,
        hormone: str = None,
        category: str = "food"
    ) -> ImageCacheResult:
        """
        Main entry point: get cached image or generate new one.
        """
        # Step 1: Generate text embedding
        embedding = await self._get_embedding(food_text)
        
        # Step 2: Search for similar cached image
        cached = await self._find_similar(embedding)
        
        if cached:
            # Cache HIT
            await self._increment_usage(cached['id'])
            logger.info(f"🎯 Cache HIT: '{food_text}' → '{cached['food_text']}' (sim: {cached['similarity']:.3f})")
            
            return ImageCacheResult(
                image_url=cached['image_url'],
                similarity_score=cached['similarity'],
                is_cache_hit=True,
                cache_id=cached['id'],
                generation_cost=self.EMBEDDING_COST_PER_CALL
            )
        
        # Cache MISS - generate new
        logger.info(f"🆕 Cache MISS: Generating image for '{food_text}'")
        
        image_url, gen_time = await self._generate_image(food_text, hormone, category)
        
        # Store in cache
        cache_id = await self._store_in_cache(
            food_text=food_text,
            embedding=embedding,
            image_url=image_url,
            generation_time_ms=gen_time
        )
        
        return ImageCacheResult(
            image_url=image_url,
            similarity_score=1.0,
            is_cache_hit=False,
            cache_id=cache_id,
            generation_cost=self.EMBEDDING_COST_PER_CALL + self.RUNPOD_COST_PER_IMAGE
        )
    
    async def _get_embedding(self, text: str) -> List[float]:
        """Generate text embedding using OpenAI."""
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
            return response.json()["data"][0]["embedding"]
    
    async def _find_similar(self, embedding: List[float]) -> Optional[dict]:
        """Find similar image using pgvector cosine similarity."""
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        
        query = text("""
            SELECT 
                id, food_text, image_url,
                1 - (text_embedding <=> :embedding::vector) as similarity
            FROM image_cache
            ORDER BY text_embedding <=> :embedding::vector
            LIMIT 1
        """)
        
        result = self.db.execute(query, {"embedding": embedding_str}).fetchone()
        
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
        hormone: str,
        category: str
    ) -> Tuple[str, int]:
        """Generate image via RunPod Flux Schnell."""
        import time
        
        prompt = self._build_prompt(food_text, hormone, category)
        start = time.time()
        
        async with httpx.AsyncClient() as client:
            # Call RunPod serverless endpoint
            response = await client.post(
                f"{self.runpod_endpoint}/runsync",
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
                        "guidance_scale": 0.0
                    }
                },
                timeout=120.0
            )
            response.raise_for_status()
            data = response.json()
        
        gen_time = int((time.time() - start) * 1000)
        
        # Extract base64 image
        image_b64 = data.get("output", {}).get("image_base64") or data.get("output")
        
        # Upload to Supabase
        image_url = await self._upload_to_supabase(image_b64, food_text)
        
        return image_url, gen_time
    
    def _build_prompt(self, food_text: str, hormone: str, category: str) -> str:
        """Build optimized image prompt."""
        if category == "food":
            base = "Professional food photography, bright natural lighting, "
            style = "elegant plating on white ceramic, top-down angle, appetizing, high-quality"
            return f"{base}{style}. {food_text}"
        elif category == "movement":
            return f"Minimalist illustration of person doing {food_text}, calm colors, wellness aesthetic"
        else:  # mindfulness
            return f"Serene calming image representing {food_text}, soft colors, peaceful, meditation aesthetic"
    
    async def _upload_to_supabase(self, image_b64: str, food_text: str) -> str:
        """Upload to Supabase Storage."""
        safe_name = "".join(c if c.isalnum() else "_" for c in food_text[:30])
        filename = f"{safe_name}_{uuid.uuid4().hex[:8]}.webp"
        path = f"action-images/{datetime.now().strftime('%Y/%m')}/{filename}"
        
        image_bytes = base64.b64decode(image_b64)
        
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.supabase_url}/storage/v1/object/auvra-images/{path}",
                headers={
                    "Authorization": f"Bearer {self.supabase_key}",
                    "Content-Type": "image/webp"
                },
                content=image_bytes,
                timeout=30.0
            )
        
        return f"{self.supabase_url}/storage/v1/object/public/auvra-images/{path}"
    
    async def _store_in_cache(
        self,
        food_text: str,
        embedding: List[float],
        image_url: str,
        generation_time_ms: int
    ) -> int:
        """Store new image in cache."""
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        
        query = text("""
            INSERT INTO image_cache (food_text, text_embedding, image_url, 
                                     generation_cost, generation_time_ms)
            VALUES (:food_text, :embedding::vector, :image_url, :cost, :time_ms)
            RETURNING id
        """)
        
        result = self.db.execute(query, {
            "food_text": food_text,
            "embedding": embedding_str,
            "image_url": image_url,
            "cost": self.RUNPOD_COST_PER_IMAGE,
            "time_ms": generation_time_ms
        })
        self.db.commit()
        
        return result.fetchone().id
    
    async def _increment_usage(self, cache_id: int):
        """Increment usage count for analytics."""
        query = text("""
            UPDATE image_cache 
            SET usage_count = usage_count + 1, last_used_at = NOW()
            WHERE id = :id
        """)
        self.db.execute(query, {"id": cache_id})
        self.db.commit()
    
    # Analytics
    def get_cache_stats(self) -> dict:
        """Get cache performance stats."""
        query = text("""
            SELECT 
                COUNT(*) as total_images,
                SUM(usage_count) as total_requests,
                SUM(generation_cost) as total_gen_cost,
                COUNT(CASE WHEN usage_count > 1 THEN 1 END) as reused_images
            FROM image_cache
        """)
        r = self.db.execute(query).fetchone()
        
        hit_rate = (1 - r.total_images / r.total_requests) * 100 if r.total_requests else 0
        savings = (r.total_requests - r.total_images) * self.RUNPOD_COST_PER_IMAGE
        
        return {
            "total_images": r.total_images,
            "total_requests": r.total_requests,
            "cache_hit_rate": round(hit_rate, 1),
            "total_generation_cost": round(r.total_gen_cost or 0, 4),
            "estimated_savings": round(savings, 4)
        }
```

---

## 6. Action Plan Generator

### 6.1 Main Generator Service

```python
# /app/services/action_plan_generator.py

import uuid
import logging
from datetime import date
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from dataclasses import dataclass

from app.services.ai_service import AIService
from app.services.image_cache_service import ImageCacheService
from app.services.recommendation_engine_v3.core.v3_orchestrator import (
    get_v3_engine, V3RecommendationRequest
)
from app.core.database import UserProfile

logger = logging.getLogger(__name__)

@dataclass
class Action:
    id: str
    slot: int
    category: str  # food, movement, mindfulness
    title: str
    purpose: str
    hormone: str  # SINGLE hormone only
    image_url: str
    specific_action: str
    research_summary: str
    is_completed: bool = False
    is_replaced: bool = False

@dataclass
class ActionPlan:
    user_id: str
    plan_date: date
    actions: List[Action]
    generation_cost: float
    generation_time_ms: int

class ActionPlanGenerator:
    """
    Generates personalized daily action plans using:
    1. V3 Recommendation Engine (evidence-based actions)
    2. GPT-4o-mini (personalization and formatting)
    3. Semantic Image Cache (smart image reuse)
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.ai_service = AIService()
        self.image_cache = ImageCacheService(db)
        self.v3_engine = get_v3_engine()
    
    async def generate_daily_plan(self, uid: str) -> ActionPlan:
        """
        Generate today's action plan for a user.
        
        Steps:
        1. Load user profile and hormone data
        2. Get recommendations from V3 engine
        3. Use GPT to select and personalize 4 actions
        4. Get/generate images for each action
        5. Save and return the plan
        """
        import time
        start = time.time()
        total_cost = 0.0
        
        # Step 1: Get user data
        user_profile = self._get_user_profile(uid)
        hormone_data = self._get_hormone_priorities(user_profile)
        
        # Step 2: Get evidence-based recommendations from V3 engine
        v3_request = V3RecommendationRequest(
            user_id=uid,
            user_profile=user_profile,
            hormone_data=hormone_data,
            symptoms=user_profile.get('symptoms', []),
            preferences=user_profile.get('dietary_preferences', {}),
            constraints=user_profile.get('constraints', {})
        )
        
        v3_response = await self.v3_engine.generate_recommendations(v3_request)
        
        # Step 3: Use GPT to select and personalize 4 actions
        gpt_actions, gpt_cost = await self._personalize_with_gpt(
            user_profile=user_profile,
            hormone_priorities=hormone_data,
            nutrition_recs=v3_response.nutrition_recommendations,
            movement_recs=v3_response.movement_recommendations,
            mindfulness_recs=v3_response.mindfulness_recommendations
        )
        total_cost += gpt_cost
        
        # Step 4: Get/generate images
        actions = []
        for i, action_data in enumerate(gpt_actions):
            # Get image from cache or generate
            image_result = await self.image_cache.get_or_generate_image(
                food_text=action_data['title'],
                hormone=action_data['hormone'],
                category=action_data['category']
            )
            total_cost += image_result.generation_cost
            
            action = Action(
                id=str(uuid.uuid4()),
                slot=i + 1,
                category=action_data['category'],
                title=action_data['title'],
                purpose=action_data['purpose'],
                hormone=action_data['hormone'],
                image_url=image_result.image_url,
                specific_action=action_data['specific_action'],
                research_summary=action_data['research_summary']
            )
            actions.append(action)
        
        gen_time = int((time.time() - start) * 1000)
        
        # Step 5: Save to database
        plan = ActionPlan(
            user_id=uid,
            plan_date=date.today(),
            actions=actions,
            generation_cost=total_cost,
            generation_time_ms=gen_time
        )
        
        await self._save_plan(plan)
        
        logger.info(f"✅ Generated action plan for {uid}: {len(actions)} actions, "
                   f"${total_cost:.4f}, {gen_time}ms")
        
        return plan
    
    def _get_user_profile(self, uid: str) -> dict:
        """Load user profile from database."""
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        if not profile:
            raise ValueError(f"User {uid} not found")
        
        return {
            "uid": profile.uid,
            "age": profile.age,
            "symptoms": profile.symptoms or [],
            "conditions": profile.conditions or [],
            "dietary_preferences": profile.dietary_preferences or {},
            "hormone_imbalances": profile.hormone_imbalances or {},
            "cycle_phase": profile.current_cycle_phase,
            "constraints": {
                "allergies": profile.allergies or [],
                "dietary_restrictions": profile.dietary_restrictions or []
            }
        }
    
    def _get_hormone_priorities(self, profile: dict) -> dict:
        """Determine hormone priorities for this user."""
        imbalances = profile.get('hormone_imbalances', {})
        
        # Sort by severity/priority
        sorted_hormones = sorted(
            imbalances.items(),
            key=lambda x: x[1].get('severity', 0),
            reverse=True
        )
        
        return {
            "primary": sorted_hormones[0][0] if sorted_hormones else "cortisol",
            "secondary": sorted_hormones[1][0] if len(sorted_hormones) > 1 else "insulin",
            "all_imbalances": imbalances
        }
    
    async def _personalize_with_gpt(
        self,
        user_profile: dict,
        hormone_priorities: dict,
        nutrition_recs: List[dict],
        movement_recs: List[dict],
        mindfulness_recs: List[dict]
    ) -> tuple:
        """
        Use GPT-4o-mini to select and personalize 4 actions.
        Returns: (actions_list, cost)
        """
        
        prompt = self._build_gpt_prompt(
            user_profile, hormone_priorities,
            nutrition_recs, movement_recs, mindfulness_recs
        )
        
        response = await self.ai_service.call_openai(
            prompt=prompt,
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0.7
        )
        
        # Parse response
        import json
        actions = json.loads(response['content'])['actions']
        
        # GPT-4o-mini cost: ~$0.00015 per 1K tokens, typical call ~500 tokens
        cost = 0.0003
        
        return actions, cost
    
    def _build_gpt_prompt(
        self,
        user_profile: dict,
        hormone_priorities: dict,
        nutrition_recs: List[dict],
        movement_recs: List[dict],
        mindfulness_recs: List[dict]
    ) -> str:
        """Build the GPT prompt for action selection."""
        
        return f"""You are AUVRA's health action planner for women's hormone health.

USER PROFILE:
- Age: {user_profile.get('age', 'Unknown')}
- Symptoms: {', '.join(user_profile.get('symptoms', []))}
- Conditions: {', '.join(user_profile.get('conditions', []))}
- Dietary restrictions: {user_profile.get('constraints', {}).get('dietary_restrictions', [])}
- Allergies: {user_profile.get('constraints', {}).get('allergies', [])}
- Current cycle phase: {user_profile.get('cycle_phase', 'unknown')}

HORMONE PRIORITIES:
- Primary focus: {hormone_priorities['primary']}
- Secondary focus: {hormone_priorities['secondary']}

AVAILABLE EVIDENCE-BASED RECOMMENDATIONS:

NUTRITION OPTIONS:
{self._format_recs(nutrition_recs)}

MOVEMENT OPTIONS:
{self._format_recs(movement_recs)}

MINDFULNESS OPTIONS:
{self._format_recs(mindfulness_recs)}

YOUR TASK:
Select exactly 4 actions for today's plan:
1. ONE food action targeting the PRIMARY hormone
2. ONE movement action targeting the SECONDARY hormone  
3. ONE mindfulness action for stress/cortisol
4. ONE flex action (any category) for next priority hormone

CRITICAL RULES:
- Each action targets EXACTLY ONE hormone (not multiple)
- Respect all dietary restrictions and allergies
- Make actions specific and actionable
- Include a brief research summary for each

Return JSON:
{{
  "actions": [
    {{
      "category": "food|movement|mindfulness",
      "title": "Short title (2-4 words)",
      "purpose": "One sentence explaining benefit",
      "hormone": "single hormone name",
      "specific_action": "Exactly what to do and when",
      "research_summary": "Brief evidence summary"
    }}
  ]
}}"""
    
    def _format_recs(self, recs: List[dict]) -> str:
        """Format recommendations for GPT prompt."""
        if not recs:
            return "No recommendations available"
        
        formatted = []
        for r in recs[:5]:  # Limit to top 5
            formatted.append(f"- {r.get('title', 'Unknown')}: {r.get('purpose', '')}")
            if r.get('hormones'):
                formatted.append(f"  Hormones: {', '.join(r['hormones'])}")
            if r.get('research_summary'):
                formatted.append(f"  Research: {r['research_summary'][:100]}...")
        
        return "\n".join(formatted)
    
    async def _save_plan(self, plan: ActionPlan):
        """Save action plan to database."""
        from sqlalchemy import text
        
        actions_json = [
            {
                "id": a.id,
                "slot": a.slot,
                "category": a.category,
                "title": a.title,
                "purpose": a.purpose,
                "hormone": a.hormone,
                "image_url": a.image_url,
                "specific_action": a.specific_action,
                "research_summary": a.research_summary,
                "is_completed": a.is_completed,
                "is_replaced": a.is_replaced
            }
            for a in plan.actions
        ]
        
        import json
        
        query = text("""
            INSERT INTO action_plans (uid, plan_date, actions, generation_cost, generation_time_ms)
            VALUES (:uid, :plan_date, :actions::jsonb, :cost, :time_ms)
            ON CONFLICT (uid, plan_date) DO UPDATE SET
                actions = :actions::jsonb,
                generation_cost = :cost,
                generation_time_ms = :time_ms,
                updated_at = NOW()
        """)
        
        self.db.execute(query, {
            "uid": plan.user_id,
            "plan_date": plan.plan_date,
            "actions": json.dumps(actions_json),
            "cost": plan.generation_cost,
            "time_ms": plan.generation_time_ms
        })
        self.db.commit()
```

---

## 7. GPT Prompts

### 7.1 Action Selection Prompt (Main)

See `_build_gpt_prompt()` above - key elements:
- User profile context
- Hormone priorities
- Available recommendations from V3 engine
- Clear output format

### 7.2 Replacement Action Prompt

```python
REPLACEMENT_PROMPT = """You are AUVRA's health assistant. The user rejected an action and needs a replacement.

REJECTED ACTION:
- Title: {rejected_title}
- Category: {rejected_category}
- Hormone: {rejected_hormone}
- Reason: {rejection_reason}

USER PROFILE:
{user_profile_summary}

CONSTRAINTS:
- Must be same category: {category}
- Must target same hormone: {hormone}
- Must be DIFFERENT from rejected action
- Must respect dietary restrictions

AVAILABLE ALTERNATIVES:
{available_alternatives}

Select ONE replacement action. Return JSON:
{{
  "title": "Short title",
  "purpose": "One sentence benefit",
  "hormone": "{hormone}",
  "specific_action": "What to do",
  "research_summary": "Brief evidence"
}}"""
```

---

## 8. API Endpoints

### 8.1 Endpoint Structure

```python
# /app/api/v1/endpoints/action_plan.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional

from app.core.database import get_db
from app.core.security import get_current_user
from app.services.action_plan_generator import ActionPlanGenerator
from app.models.action_plan_models import (
    ActionPlanResponse, 
    FeedbackRequest,
    ReplacementRequest
)

router = APIRouter(prefix="/action-plan", tags=["action-plan"])

@router.get("/today", response_model=ActionPlanResponse)
async def get_today_action_plan(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get today's action plan. Generates if doesn't exist.
    
    Returns 4 actions with images, each targeting one hormone.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID required")
    
    generator = ActionPlanGenerator(db)
    
    # Check if plan exists for today
    existing_plan = generator.get_existing_plan(uid, date.today())
    
    if existing_plan:
        return existing_plan
    
    # Generate new plan
    plan = await generator.generate_daily_plan(uid)
    
    return ActionPlanResponse(
        plan_date=plan.plan_date.isoformat(),
        actions=[
            {
                "id": a.id,
                "slot": a.slot,
                "category": a.category,
                "title": a.title,
                "purpose": a.purpose,
                "hormone": a.hormone,
                "image_url": a.image_url,
                "specific_action": a.specific_action,
                "research_summary": a.research_summary,
                "is_completed": a.is_completed,
                "is_replaced": a.is_replaced
            }
            for a in plan.actions
        ],
        generation_cost=plan.generation_cost
    )


@router.post("/actions/{action_id}/complete")
async def complete_action(
    action_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark an action as completed."""
    uid = current_user.get("uid")
    
    generator = ActionPlanGenerator(db)
    success = await generator.mark_completed(uid, action_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Action not found")
    
    return {"message": "Action completed", "action_id": action_id}


@router.post("/actions/{action_id}/feedback")
async def submit_feedback(
    action_id: str,
    request: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Submit feedback for an action.
    Must wait 30 seconds before replacing.
    """
    uid = current_user.get("uid")
    
    generator = ActionPlanGenerator(db)
    
    # Validate timing (30 second rule)
    action_shown_at = request.action_shown_at
    time_elapsed = (datetime.now() - action_shown_at).total_seconds()
    
    if request.feedback_type == "replace" and time_elapsed < 30:
        raise HTTPException(
            status_code=400,
            detail=f"Must wait 30 seconds before replacing. {30 - int(time_elapsed)}s remaining."
        )
    
    await generator.save_feedback(
        uid=uid,
        action_id=action_id,
        feedback_type=request.feedback_type,
        reason=request.reason
    )
    
    return {"message": "Feedback recorded"}


@router.post("/actions/{action_id}/replace")
async def replace_action(
    action_id: str,
    request: ReplacementRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Replace an action with a new one.
    Requires 30 seconds since action was shown.
    """
    uid = current_user.get("uid")
    
    generator = ActionPlanGenerator(db)
    
    new_action = await generator.replace_action(
        uid=uid,
        action_id=action_id,
        reason=request.reason
    )
    
    return {
        "message": "Action replaced",
        "old_action_id": action_id,
        "new_action": new_action
    }


@router.get("/stats")
async def get_action_plan_stats(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's action plan statistics."""
    uid = current_user.get("uid")
    
    generator = ActionPlanGenerator(db)
    stats = generator.get_user_stats(uid)
    
    return stats
```

### 8.2 Response Models

```python
# /app/models/action_plan_models.py

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ActionResponse(BaseModel):
    id: str
    slot: int
    category: str
    title: str
    purpose: str
    hormone: str
    image_url: str
    specific_action: str
    research_summary: str
    is_completed: bool
    is_replaced: bool

class ActionPlanResponse(BaseModel):
    plan_date: str
    actions: List[ActionResponse]
    generation_cost: Optional[float] = None

class FeedbackRequest(BaseModel):
    feedback_type: str  # "completed", "replace", "skip"
    reason: Optional[str] = None
    action_shown_at: datetime

class ReplacementRequest(BaseModel):
    reason: str
```

---

## 9. Mobile Integration

### 9.1 API Call Flow

```typescript
// Mobile: services/actionPlanService.ts

export interface Action {
  id: string;
  slot: number;
  category: 'food' | 'movement' | 'mindfulness';
  title: string;
  purpose: string;
  hormone: string;
  imageUrl: string;
  specificAction: string;
  researchSummary: string;
  isCompleted: boolean;
  isReplaced: boolean;
}

export interface ActionPlan {
  planDate: string;
  actions: Action[];
}

class ActionPlanService {
  
  async getTodayPlan(): Promise<ActionPlan> {
    const response = await api.get('/action-plan/today');
    return this.transformResponse(response.data);
  }
  
  async completeAction(actionId: string): Promise<void> {
    await api.post(`/action-plan/actions/${actionId}/complete`);
  }
  
  async replaceAction(actionId: string, reason: string): Promise<Action> {
    const response = await api.post(`/action-plan/actions/${actionId}/replace`, {
      reason
    });
    return response.data.new_action;
  }
  
  private transformResponse(data: any): ActionPlan {
    return {
      planDate: data.plan_date,
      actions: data.actions.map((a: any) => ({
        id: a.id,
        slot: a.slot,
        category: a.category,
        title: a.title,
        purpose: a.purpose,
        hormone: a.hormone,
        imageUrl: a.image_url,
        specificAction: a.specific_action,
        researchSummary: a.research_summary,
        isCompleted: a.is_completed,
        isReplaced: a.is_replaced
      }))
    };
  }
}
```

### 9.2 30-Second Timer Implementation

```typescript
// Mobile: hooks/useActionFeedback.ts

import { useState, useEffect } from 'react';

export function useActionFeedback(actionId: string) {
  const [canReplace, setCanReplace] = useState(false);
  const [secondsRemaining, setSecondsRemaining] = useState(30);
  const [actionShownAt] = useState(new Date());
  
  useEffect(() => {
    const timer = setInterval(() => {
      const elapsed = (Date.now() - actionShownAt.getTime()) / 1000;
      const remaining = Math.max(0, 30 - elapsed);
      
      setSecondsRemaining(Math.ceil(remaining));
      setCanReplace(remaining === 0);
      
      if (remaining === 0) {
        clearInterval(timer);
      }
    }, 1000);
    
    return () => clearInterval(timer);
  }, [actionShownAt]);
  
  return {
    canReplace,
    secondsRemaining,
    actionShownAt
  };
}
```

---

## 10. Feedback System

### 10.1 Feedback Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                      FEEDBACK FLOW                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  User sees action card                                             │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────────────────────────────────┐                   │
│  │          30-SECOND TIMER STARTS             │                   │
│  │  "Try this action before replacing"         │                   │
│  │  [████████░░░░░░░░░░░░░░░] 12s remaining    │                   │
│  └──────────────────────┬──────────────────────┘                   │
│                         │                                           │
│         ┌───────────────┼───────────────┐                          │
│         ▼               ▼               ▼                          │
│    [COMPLETE]      [REPLACE]       [SKIP]                          │
│         │          (locked)            │                           │
│         │               │              │                           │
│         ▼               │              ▼                           │
│   Mark completed        │         Mark skipped                     │
│   Show next action      │         Show next action                 │
│         │               │              │                           │
│         │               ▼              │                           │
│         │     After 30s: [REPLACE]     │                           │
│         │          unlocked            │                           │
│         │               │              │                           │
│         │               ▼              │                           │
│         │     Show replacement         │                           │
│         │     reason picker            │                           │
│         │               │              │                           │
│         │               ▼              │                           │
│         │     Generate new action      │                           │
│         │     (same hormone)           │                           │
│         │               │              │                           │
│         └───────────────┼──────────────┘                           │
│                         ▼                                           │
│                 Update action_plan                                  │
│                 Save feedback to DB                                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.2 Replacement Reasons

```python
REPLACEMENT_REASONS = [
    "I don't like this food",
    "I'm allergic to this",
    "I don't have the ingredients",
    "This doesn't fit my schedule",
    "I did this recently",
    "Other"
]
```

---

## 11. Cost Analysis

### 11.1 Per-Request Costs

| Component | Cost per Call | Calls per Plan | Total |
|-----------|---------------|----------------|-------|
| GPT-4o-mini | $0.0003 | 1 | $0.0003 |
| Embedding (ada-002) | $0.0001 | 4 | $0.0004 |
| Image Generation (RunPod) | $0.0006 | 0-4* | $0-$0.0024 |
| **Total (cold cache)** | | | **$0.0031** |
| **Total (warm cache 90%)** | | | **$0.0009** |

*Images reused from cache when similar exists

### 11.2 Monthly Projections

| Users | Plans/Day | Cold Cache | Warm Cache (90%) |
|-------|-----------|------------|------------------|
| 100 | 100 | $9.30/mo | $2.70/mo |
| 1,000 | 1,000 | $93/mo | $27/mo |
| 10,000 | 10,000 | $930/mo | $270/mo |

### 11.3 Cache Warmup Timeline

| Week | Images Cached | Hit Rate | Avg Cost/Plan |
|------|---------------|----------|---------------|
| 1 | 200 | 40% | $0.0022 |
| 2 | 500 | 70% | $0.0015 |
| 4 | 800 | 85% | $0.0011 |
| 8+ | 1000+ | 90%+ | $0.0009 |

---

## 12. Implementation Timeline

### Phase 1: Database Setup (Day 1)
- [ ] Enable pgvector extension
- [ ] Create image_cache table
- [ ] Create action_plans table
- [ ] Create action_feedback table
- [ ] Create cost tracking table

### Phase 2: Image Cache Service (Days 2-3)
- [ ] Implement ImageCacheService
- [ ] OpenAI embedding integration
- [ ] RunPod integration (reuse existing)
- [ ] Supabase upload logic
- [ ] Cache hit/miss logic with pgvector

### Phase 3: Action Plan Generator (Days 4-5)
- [ ] Implement ActionPlanGenerator
- [ ] Integrate with V3 engine
- [ ] GPT prompt for action selection
- [ ] Image attachment logic
- [ ] Database persistence

### Phase 4: API Endpoints (Day 6)
- [ ] /action-plan/today endpoint
- [ ] /actions/{id}/complete endpoint
- [ ] /actions/{id}/feedback endpoint
- [ ] /actions/{id}/replace endpoint
- [ ] Response models

### Phase 5: Mobile Integration (Days 7-8)
- [ ] ActionPlanService
- [ ] 30-second timer hook
- [ ] Action card component updates
- [ ] Feedback UI

### Phase 6: Testing & Optimization (Days 9-10)
- [ ] End-to-end testing
- [ ] Cache performance testing
- [ ] Cost monitoring setup
- [ ] Documentation

---

## Appendix: File Structure

```
app/
├── api/
│   └── v1/
│       └── endpoints/
│           └── action_plan.py          # NEW
├── models/
│   └── action_plan_models.py           # NEW
├── services/
│   ├── action_plan_generator.py        # NEW
│   ├── image_cache_service.py          # NEW
│   └── recommendation_engine_v3/       # EXISTING (use as-is)
└── core/
    └── database.py                      # ADD new tables

alembic/
└── versions/
    └── xxx_add_action_plan_tables.py   # NEW migration
```

---

## Summary

This system delivers:
1. **4 personalized daily actions** with beautiful images
2. **One action = One hormone** for clarity
3. **75%+ cost savings** through semantic image cache
4. **GPT freedom** - no vocabulary restrictions
5. **30-second feedback** with smart replacement
6. **Full integration** with existing V3 recommendation engine
