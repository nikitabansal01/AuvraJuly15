# AUVRA V3 Recommendation Engine Architecture

## High-Level System Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              MOBILE APP (React Native)                          │
│                                                                                 │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│   │   Symptom   │───▶│   Family    │───▶│  Lifestyle  │───▶│    View     │     │
│   │   Screen    │    │   History   │    │  Questions  │    │   Results   │     │
│   └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘     │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FASTAPI BACKEND                                    │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                     /api/v1/questions/recommendations                    │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                          │
│                                      ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                          AI SERVICE                                      │   │
│   │                                                                          │   │
│   │    ┌────────────────┐         ┌────────────────┐                        │   │
│   │    │ Root Cause     │────────▶│ V3 Engine      │                        │   │
│   │    │ Engine         │         │ (Singleton)    │                        │   │
│   │    └────────────────┘         └────────────────┘                        │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL SERVICES                                     │
│                                                                                 │
│   ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐         │
│   │    OpenAI API    │    │   Pinecone RAG   │    │   PostgreSQL     │         │
│   │   (gpt-4o-mini)  │    │  (6,040 vectors) │    │   (User Data)    │         │
│   └──────────────────┘    └──────────────────┘    └──────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## V3 Engine Internal Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         V3 RECOMMENDATION ENGINE                                │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                     PROBLEM FOCUS NARROWER                                 │  │
│  │                                                                            │  │
│  │   User Answers ───▶ Analyze Severity ───▶ Identify Top 3 Problem Areas    │  │
│  │                                                                            │  │
│  │   Output: ["Hormonal Imbalance", "Insulin Resistance", "Weight Gain"]     │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                          │
│                                      ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                       EXPERT ORCHESTRATOR                                  │  │
│  │                                                                            │  │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │  │
│  │   │  Hormonal   │  │  Metabolic  │  │  Fertility  │  │  Lifestyle  │      │  │
│  │   │   Expert    │  │   Expert    │  │   Expert    │  │   Expert    │      │  │
│  │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘      │  │
│  │          │                │                │                │              │  │
│  │          └────────────────┴────────────────┴────────────────┘              │  │
│  │                                   │                                        │  │
│  │                                   ▼                                        │  │
│  │                    ┌─────────────────────────────┐                         │  │
│  │                    │  RETRIEVAL COMPONENT        │                         │  │
│  │                    │  (Shared + Cached)          │                         │  │
│  │                    └──────────────┬──────────────┘                         │  │
│  │                                   │                                        │  │
│  │                                   ▼                                        │  │
│  │                    ┌─────────────────────────────┐                         │  │
│  │                    │     PINECONE RAG            │                         │  │
│  │                    │  (Research Papers)          │                         │  │
│  │                    └─────────────────────────────┘                         │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                          │
│                                      ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                    RECOMMENDATION EVALUATOR                                │  │
│  │                                                                            │  │
│  │   Expert Outputs ───▶ Deduplicate ───▶ Rank by Impact ───▶ Format JSON    │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Domain Expert Detail

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            DOMAIN EXPERT MODULE                                 │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                         EXPERT (e.g., Hormonal)                          │   │
│   │                                                                          │   │
│   │   Input:                                                                 │   │
│   │   • User symptoms & severity                                             │   │
│   │   • Family history context                                               │   │
│   │   • Narrowed problem focus                                               │   │
│   │                                                                          │   │
│   │   ┌─────────────────────────────────────────────────────────────────┐    │   │
│   │   │              RETRIEVAL COMPONENT                                 │    │   │
│   │   │                                                                  │    │   │
│   │   │   Query: "PCOS hormonal imbalance treatment evidence based"     │    │   │
│   │   │                          │                                       │    │   │
│   │   │                          ▼                                       │    │   │
│   │   │   ┌──────────────────────────────────────────────────────────┐  │    │   │
│   │   │   │  Pinecone Hybrid Search (Dense + Sparse)                 │  │    │   │
│   │   │   │                                                          │  │    │   │
│   │   │   │  • Dense: Semantic meaning (embeddings)                  │  │    │   │
│   │   │   │  • Sparse: Keywords (BM25)                               │  │    │   │
│   │   │   │  • Alpha: 0.5 (balanced)                                 │  │    │   │
│   │   │   └──────────────────────────────────────────────────────────┘  │    │   │
│   │   │                          │                                       │    │   │
│   │   │                          ▼                                       │    │   │
│   │   │   Returns: Top 5 research paper chunks with citations           │    │   │
│   │   └─────────────────────────────────────────────────────────────────┘    │   │
│   │                              │                                           │   │
│   │                              ▼                                           │   │
│   │   ┌─────────────────────────────────────────────────────────────────┐    │   │
│   │   │                    OpenAI GPT-4o                                 │    │   │
│   │   │                                                                  │    │   │
│   │   │   System Prompt: "You are a {domain} expert for PCOS..."        │    │   │
│   │   │   Context: Retrieved research + user data                        │    │   │
│   │   │                                                                  │    │   │
│   │   │   Output: 3-5 expert recommendations with citations              │    │   │
│   │   └─────────────────────────────────────────────────────────────────┘    │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Caching Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            CACHING LAYERS                                       │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  Layer 1: HORMONE ANALYSIS CACHE (root_cause_engine.py)                  │   │
│   │                                                                          │   │
│   │  Key: hash(symptoms + family_history)                                    │   │
│   │  Value: Analyzed hormone profile                                         │   │
│   │  Benefit: Prevents 4x duplicate LLM calls per session                    │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  Layer 2: RAG QUERY CACHE (rag_retriever.py)                             │   │
│   │                                                                          │   │
│   │  Key: hash(query + category + top_k)                                     │   │
│   │  Value: Pinecone search results                                          │   │
│   │  Benefit: Prevents 3x duplicate Pinecone queries                         │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  Layer 3: RETRIEVAL CACHE (retrieval_component.py)                       │   │
│   │                                                                          │   │
│   │  Key: hash(expert_query)                                                 │   │
│   │  Value: Formatted research context                                       │   │
│   │  Benefit: Reuse across multiple experts                                  │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  Layer 4: V3 ENGINE SINGLETON (v3_orchestrator.py)                       │   │
│   │                                                                          │   │
│   │  Pattern: get_v3_engine() returns single instance                        │   │
│   │  Benefit: No re-initialization per request                               │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        CACHE SERVICE                                     │   │
│   │                                                                          │   │
│   │  clear_session_caches() ─── Called at start of new recommendation       │   │
│   │  clear_all_caches()     ─── Full reset                                   │   │
│   │  get_cache_stats()      ─── Monitoring                                   │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Request Flow Sequence

```
User Request
     │
     ▼
┌────────────────┐
│ POST /recommend│
└───────┬────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ 1. Clear Session Caches                                        │
└───────┬────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ 2. Root Cause Engine                                           │
│    • Check hormone cache → HIT? Return cached                  │
│    • MISS? Call OpenAI → Cache result                          │
└───────┬────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ 3. Problem Focus Narrower                                      │
│    • Analyze answers → Identify top 3 problem areas            │
└───────┬────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ 4. Expert Orchestrator (Parallel Execution)                    │
│                                                                │
│    ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│    │Hormonal  │  │Metabolic │  │Fertility │  │Lifestyle │     │
│    │Expert    │  │Expert    │  │Expert    │  │Expert    │     │
│    └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘     │
│         │             │             │             │            │
│         └─────────────┴─────────────┴─────────────┘            │
│                           │                                    │
│                           ▼                                    │
│              RAG Retrieval (Cached)                            │
│                           │                                    │
│                           ▼                                    │
│              OpenAI Generation                                 │
└───────┬────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ 5. Recommendation Evaluator                                    │
│    • Merge expert outputs                                      │
│    • Deduplicate recommendations                               │
│    • Rank by relevance & impact                                │
│    • Format final JSON response                                │
└───────┬────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ 6. Return V3RecommendationResponse                             │
│                                                                │
│    {                                                           │
│      "focused_problems": [...],                                │
│      "recommendations": {                                      │
│        "diet": [...],                                          │
│        "lifestyle": [...],                                     │
│        "supplements": [...],                                   │
│        "medical": [...]                                        │
│      },                                                        │
│      "action_plan": {...},                                     │
│      "research_citations": [...]                               │
│    }                                                           │
└────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
app/services/
├── ai_service.py                    # Main entry, uses V3 singleton
├── root_cause_engine.py             # Hormone analysis (OpenAI + cache)
├── cache_service.py                 # Central cache management
│
├── rag/
│   └── rag_retriever.py             # Pinecone singleton + cache
│
└── recommendation_engine_v3/
    ├── core/
    │   ├── v3_orchestrator.py       # Main engine (singleton)
    │   ├── problem_focus_narrower.py
    │   └── recommendation_evaluator.py
    │
    ├── components/
    │   └── retrieval_component.py   # Shared RAG + cache
    │
    └── experts/
        ├── base_expert.py           # Abstract base class
        ├── hormonal_expert.py
        ├── metabolic_expert.py
        ├── fertility_expert.py
        └── lifestyle_expert.py
```

---

## Key Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Singleton Pattern** | V3Engine, RAGRetriever - single instance per process |
| **Caching** | 3-layer cache (LLM, RAG, Retrieval) - minimize API calls |
| **Modularity** | Independent experts - add/remove without affecting others |
| **Reusability** | Shared RetrievalComponent - all experts use same RAG |
| **Separation of Concerns** | Narrower → Experts → Evaluator - clear responsibilities |
