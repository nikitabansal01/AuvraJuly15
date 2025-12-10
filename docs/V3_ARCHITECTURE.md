# AUVRA V3 Recommendation Engine - Verified Architecture

> ✅ **100% Code-Verified** - All components traced from actual source files

---

## Complete System Flow

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                                    📱 MOBILE APP                                      │
│                                                                                       │
│    User Profile + Symptoms + Family History + Lifestyle Data                          │
│                                        │                                              │
└────────────────────────────────────────┼──────────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              🔧 FASTAPI BACKEND                                       │
│                                                                                       │
│    POST /api/v1/questions/recommendations                                             │
│                        │                                                              │
│                        ▼                                                              │
│    ┌────────────────────────────────────────────────────────────────────────────┐    │
│    │                     🧹 CACHE SERVICE                                        │    │
│    │              clear_session_caches() at start                                │    │
│    └────────────────────────────────────────────────────────────────────────────┘    │
│                        │                                                              │
│                        ▼                                                              │
│    ┌────────────────────────────────────────────────────────────────────────────┐    │
│    │                   🩺 ROOT CAUSE ENGINE                                      │    │
│    │                                                                             │    │
│    │   • Analyzes "Others" free-text via OpenAI (gpt-4o-mini)                   │    │
│    │   • Hormone scoring: estrogen, progesterone, androgens, insulin,           │    │
│    │                      cortisol, thyroid                                      │    │
│    │   • Caches results by symptom/family hash                                   │    │
│    └────────────────────────────────────────────────────────────────────────────┘    │
│                        │                                                              │
│                        ▼                                                              │
│    ┌────────────────────────────────────────────────────────────────────────────┐    │
│    │                   🧠 V3 ENGINE (Singleton)                                  │    │
│    │                      get_v3_engine()                                        │    │
│    └────────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                          🎯 V3 PIPELINE (5 STEPS)                                    │
│                                                                                       │
│  ┌────────────────────────────────────────────────────────────────────────────────┐  │
│  │ STEP 1: PROBLEM NARROWER                                                        │  │
│  │ File: problem_narrower.py                                                       │  │
│  │                                                                                 │  │
│  │ Input: User profile, symptoms, hormone data                                     │  │
│  │ Output: FocusedProblem with:                                                    │  │
│  │   • primary_concern (type, urgency, symptoms)                                   │  │
│  │   • identified_root_causes [insulin_resistance, androgen_high, etc.]            │  │
│  │   • actionable_targets                                                          │  │
│  └─────────────────────────────────────┬──────────────────────────────────────────┘  │
│                                        │                                              │
│                                        ▼                                              │
│  ┌────────────────────────────────────────────────────────────────────────────────┐  │
│  │ STEP 2: EXPERT ORCHESTRATOR                                                     │  │
│  │ File: expert_orchestrator.py                                                    │  │
│  │                                                                                 │  │
│  │ Routes to 3 Domain Experts (PARALLEL EXECUTION):                                │  │
│  │                                                                                 │  │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                │  │
│  │   │ 🥗 NUTRITION    │  │ 🏃 MOVEMENT     │  │ 🧘 MINDFULNESS  │                │  │
│  │   │    EXPERT       │  │    EXPERT       │  │    EXPERT       │                │  │
│  │   └────────┬────────┘  └────────┬────────┘  └────────┬────────┘                │  │
│  │            │                    │                    │                          │  │
│  │            │     SUB-MODULES ACTIVATED BY ROOT CAUSE:                           │  │
│  │            │     ┌─────────────────────────────────────────────┐                │  │
│  │            │     │ insulin_resistance → insulin_resistance_diet │                │  │
│  │            │     │ androgen_high → androgen_reduction_diet      │                │  │
│  │            │     │ cortisol_high → cortisol_stress_management   │                │  │
│  │            │     │ inflammation → anti_inflammatory_diet        │                │  │
│  │            │     └─────────────────────────────────────────────┘                │  │
│  │            │                    │                    │                          │  │
│  │            └────────────────────┴────────────────────┘                          │  │
│  │                                 │                                               │  │
│  │                                 ▼                                               │  │
│  │            ┌────────────────────────────────────────────────────┐               │  │
│  │            │ 📚 RETRIEVAL COMPONENT (Shared + Cached)           │               │  │
│  │            │                                                    │               │  │
│  │            │  Query: "low glycemic index diet PCOS insulin..."  │               │  │
│  │            │                      │                             │               │  │
│  │            │                      ▼                             │               │  │
│  │            │  ┌─────────────────────────────────────────────┐   │               │  │
│  │            │  │ 🔍 PINECONE RAG (Singleton)                 │   │               │  │
│  │            │  │                                             │   │               │  │
│  │            │  │  Index: auvra-papers                        │   │               │  │
│  │            │  │  Vectors: 6,040                             │   │               │  │
│  │            │  │  Namespace: pcos-rag-gpt_4o                 │   │               │  │
│  │            │  │                                             │   │               │  │
│  │            │  │  Hybrid Search:                             │   │               │  │
│  │            │  │  • Dense (semantic embeddings)              │   │               │  │
│  │            │  │  • Sparse (BM25 keywords)                   │   │               │  │
│  │            │  │  • Alpha: 0.5 (balanced)                    │   │               │  │
│  │            │  └─────────────────────────────────────────────┘   │               │  │
│  │            │                      │                             │               │  │
│  │            │                      ▼                             │               │  │
│  │            │  Returns: Research paper chunks + citations        │               │  │
│  │            └────────────────────────────────────────────────────┘               │  │
│  │                                 │                                               │  │
│  │                                 ▼                                               │  │
│  │            ┌────────────────────────────────────────────────────┐               │  │
│  │            │ 🤖 OpenAI GPT-4o                                   │               │  │
│  │            │                                                    │               │  │
│  │            │ Generates 3-5 recommendations per expert           │               │  │
│  │            │ with citations from retrieved research             │               │  │
│  │            └────────────────────────────────────────────────────┘               │  │
│  └─────────────────────────────────────┬──────────────────────────────────────────┘  │
│                                        │                                              │
│                                        ▼                                              │
│  ┌────────────────────────────────────────────────────────────────────────────────┐  │
│  │ STEP 3: EVIDENCE GRADER                                                         │  │
│  │ File: evidence_grader.py                                                        │  │
│  │                                                                                 │  │
│  │ Grades each recommendation's evidence:                                          │  │
│  │   • meta-analysis: 1.0                                                          │  │
│  │   • systematic review: 0.95                                                     │  │
│  │   • RCT: 0.9                                                                    │  │
│  │   • cohort study: 0.7                                                           │  │
│  │   • case study: 0.5                                                             │  │
│  └─────────────────────────────────────┬──────────────────────────────────────────┘  │
│                                        │                                              │
│                                        ▼                                              │
│  ┌────────────────────────────────────────────────────────────────────────────────┐  │
│  │ STEP 4: PERSONALIZATION ENGINE                                                  │  │
│  │ File: personalization_engine.py                                                 │  │
│  │                                                                                 │  │
│  │ Adapts recommendations to user constraints:                                     │  │
│  │   • dietary: vegetarian, vegan, gluten-free                                     │  │
│  │   • allergies: dairy, nuts, soy                                                 │  │
│  │   • lifestyle: budget, time, equipment                                          │  │
│  └─────────────────────────────────────┬──────────────────────────────────────────┘  │
│                                        │                                              │
│                                        ▼                                              │
│  ┌────────────────────────────────────────────────────────────────────────────────┐  │
│  │ STEP 5: EVALUATOR & OPTIMIZER                                                   │  │
│  │ File: evaluator_optimizer.py                                                    │  │
│  │                                                                                 │  │
│  │ LLM-as-Judge pattern:                                                           │  │
│  │   • Scores recommendations (0-1)                                                │  │
│  │   • Checks relevance to problem focus                                           │  │
│  │   • Deduplicates across categories                                              │  │
│  │   • Re-optimizes if quality < 0.7                                               │  │
│  │   • Max 2 optimization iterations                                               │  │
│  └─────────────────────────────────────┬──────────────────────────────────────────┘  │
│                                        │                                              │
└────────────────────────────────────────┼──────────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                            📤 V3RecommendationResponse                               │
│                                                                                       │
│  {                                                                                    │
│    "request_id": "abc123",                                                            │
│    "problem_analysis": { "primary_concern": "insulin_resistance", ... },              │
│    "nutrition_recommendations": [...],    // 🥗                                       │
│    "movement_recommendations": [...],     // 🏃                                       │
│    "mindfulness_recommendations": [...],  // 🧘                                       │
│    "quality_scores": { "overall": 0.85 },                                             │
│    "evidence_summary": {...},                                                         │
│    "personalization_applied": ["vegetarian_swap", ...],                               │
│    "processing_time_ms": 2340,                                                        │
│    "experts_consulted": ["nutrition", "movement", "mindfulness"],                     │
│    "confidence_level": "high"                                                         │
│  }                                                                                    │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Caching Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                               🗄️ 4-LAYER CACHE SYSTEM                                │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 1: Hormone Analysis Cache                                                  │ │
│  │ File: root_cause_engine.py                                                       │ │
│  │ Key: hash(symptoms_others + family_others)                                       │ │
│  │ Prevents: 4x duplicate OpenAI calls per session                                  │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 2: Pinecone Query Cache                                                    │ │
│  │ File: rag_retriever.py                                                           │ │
│  │ Key: hash(query + category + top_k)                                              │ │
│  │ Prevents: 3x duplicate Pinecone API calls                                        │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 3: Retrieval Component Cache                                               │ │
│  │ File: retrieval_component.py                                                     │ │
│  │ Key: hash(expert_query)                                                          │ │
│  │ Benefit: Shared across 3 experts                                                 │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 4: V3 Engine Singleton                                                     │ │
│  │ File: v3_orchestrator.py                                                         │ │
│  │ Pattern: get_v3_engine() returns single instance                                 │ │
│  │ Prevents: Re-initialization per category                                         │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │ 🧹 CACHE SERVICE (cache_service.py)                                              │ │
│  │   clear_session_caches() → Clears layers 1-3 at session start                    │ │
│  │   get_cache_stats() → Returns cache sizes for monitoring                         │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## File Structure (Verified)

```
app/services/
├── ai_service.py                          # Entry point, uses get_v3_engine()
├── root_cause_engine.py                   # Hormone analysis (OpenAI)
├── cache_service.py                       # Central cache management
│
├── rag/
│   └── rag_retriever.py                   # Pinecone hybrid search singleton
│
└── recommendation_engine_v3/
    │
    ├── core/
    │   ├── v3_orchestrator.py             # Main engine (SINGLETON)
    │   ├── problem_narrower.py            # Step 1: Focus narrowing
    │   ├── expert_orchestrator.py         # Step 2: Expert routing
    │   └── evaluator_optimizer.py         # Step 5: Quality evaluation
    │
    ├── components/
    │   ├── retrieval_component.py         # Shared RAG for experts
    │   ├── evidence_grader.py             # Step 3: Evidence scoring
    │   └── personalization_engine.py      # Step 4: User adaptation
    │
    └── experts/
        ├── base_expert.py                 # Abstract base class
        ├── nutrition_expert.py            # 🥗 Food recommendations
        ├── movement_expert.py             # 🏃 Exercise recommendations
        └── mindfulness_expert.py          # 🧘 Stress/sleep recommendations
```

---

## Simplified Visual

```
                        📱 Mobile App
                             │
                             ▼
                    ┌─────────────────┐
                    │ User Profile +  │
                    │ Symptoms        │
                    └────────┬────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │   🧠 V3 ENGINE (Singleton)   │
              │                              │
              │  ┌────────────────────────┐  │
              │  │ 1️⃣ Problem Narrower    │  │
              │  │    Focus user's needs  │  │
              │  └──────────┬─────────────┘  │
              │             │                │
              │             ▼                │
              │  ┌────────────────────────┐  │
              │  │ 2️⃣ Expert Orchestrator │  │
              │  │                        │  │
              │  │  🥗    🏃    🧘       │  │
              │  │  ↓     ↓     ↓        │  │
              │  │  ───── RAG ─────      │  │
              │  │        │              │  │
              │  │   ┌────▼────┐         │  │
              │  │   │ 6,040   │         │  │
              │  │   │ vectors │         │  │
              │  │   └─────────┘         │  │
              │  └──────────┬─────────────┘  │
              │             │                │
              │             ▼                │
              │  ┌────────────────────────┐  │
              │  │ 3️⃣ Evidence Grader     │  │
              │  │    Score research      │  │
              │  └──────────┬─────────────┘  │
              │             │                │
              │             ▼                │
              │  ┌────────────────────────┐  │
              │  │ 4️⃣ Personalization     │  │
              │  │    Adapt to user       │  │
              │  └──────────┬─────────────┘  │
              │             │                │
              │             ▼                │
              │  ┌────────────────────────┐  │
              │  │ 5️⃣ Evaluator          │  │
              │  │    Quality check       │  │
              │  └────────────────────────┘  │
              └──────────────┬───────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Recommendations │
                    │  🥗 🏃 🧘       │
                    └─────────────────┘
```

---

## Key Components Summary

| Step | Component | File | Function |
|------|-----------|------|----------|
| 0 | Root Cause Engine | `root_cause_engine.py` | Hormone analysis via OpenAI |
| 1 | Problem Narrower | `problem_narrower.py` | Focus on user's top concerns |
| 2 | Expert Orchestrator | `expert_orchestrator.py` | Route to 3 experts (parallel) |
| 2a | RAG Retrieval | `retrieval_component.py` | Fetch research from Pinecone |
| 3 | Evidence Grader | `evidence_grader.py` | Score research quality |
| 4 | Personalization | `personalization_engine.py` | Adapt to user constraints |
| 5 | Evaluator | `evaluator_optimizer.py` | LLM-as-Judge quality check |

---

## External Services

| Service | Purpose | Details |
|---------|---------|---------|
| **OpenAI** | LLM Generation | gpt-4o for recommendations, gpt-4o-mini for hormone analysis |
| **Pinecone** | Vector Search | 6,040 vectors, hybrid search (dense + sparse) |
| **PostgreSQL** | User Data | Profiles, sessions, history |
