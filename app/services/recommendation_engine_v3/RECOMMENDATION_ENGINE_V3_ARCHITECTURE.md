# Recommendation Engine V3 - Architecture & Implementation

## Executive Summary

The V3 Recommendation Engine is a **modular, expert-based RAG architecture** designed to address the feedback that "the recommended action plan was not on point." This new architecture implements:

1. **Narrowed User Problem Focus** - Analyzes user context before generating recommendations
2. **Modular Expert Architecture** - Specialized domain experts with different expertise
3. **Reusable AI Components** - Shared components across modules for consistency

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    RECOMMENDATION ENGINE V3                          │
│                   Modular Expert Architecture                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                   USER REQUEST                               │    │
│  │  • User Profile (age, preferences, constraints)             │    │
│  │  • Hormone Data (insulin, cortisol, thyroid, etc.)          │    │
│  │  • Symptoms (fatigue, weight gain, etc.)                    │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │                                          │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              1. PROBLEM NARROWER                             │    │
│  │  ────────────────────────────────────────────────────────── │    │
│  │  • Analyzes user context                                    │    │
│  │  • Identifies root causes (hormone imbalances)              │    │
│  │  • Categorizes by urgency (acute vs long-term)              │    │
│  │  • Creates FocusedProblem with primary_focus                │    │
│  │                                                              │    │
│  │  OUTPUT: FocusedProblem with actionable_targets             │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │                                          │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              2. EXPERT ORCHESTRATOR                          │    │
│  │  ────────────────────────────────────────────────────────── │    │
│  │                                                              │    │
│  │  Routes to specialized experts based on problem focus:      │    │
│  │                                                              │    │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐            │    │
│  │  │  NUTRITION  │ │  MOVEMENT   │ │ MINDFULNESS │            │    │
│  │  │   EXPERT    │ │   EXPERT    │ │   EXPERT    │            │    │
│  │  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘            │    │
│  │         │               │               │                    │    │
│  │         ▼               ▼               ▼                    │    │
│  │  ┌───────────────────────────────────────────────────┐      │    │
│  │  │           SPECIALIZED SUB-MODULES                 │      │    │
│  │  │  • InsulinDietModule    • PCOSExerciseModule     │      │    │
│  │  │  • AntiInflammatory     • CortisolManagement     │      │    │
│  │  │  • ThyroidNutrition     • SleepOptimization      │      │    │
│  │  └───────────────────────────────────────────────────┘      │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │                                          │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │           3. REUSABLE AI COMPONENTS                          │    │
│  │  ────────────────────────────────────────────────────────── │    │
│  │                                                              │    │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌───────────────┐  │    │
│  │  │   RETRIEVAL     │ │   EVIDENCE      │ │PERSONALIZATION│  │    │
│  │  │   COMPONENT     │ │   GRADER        │ │    ENGINE     │  │    │
│  │  │                 │ │                 │ │               │  │    │
│  │  │ • Semantic      │ │ • Study type    │ │ • Constraint  │  │    │
│  │  │   search        │ │   scoring       │ │   extraction  │  │    │
│  │  │ • Domain        │ │ • Sample size   │ │ • Dietary     │  │    │
│  │  │   config        │ │   analysis      │ │   adaptations │  │    │
│  │  │ • Pinecone      │ │ • Recency       │ │ • Physical    │  │    │
│  │  │   integration   │ │   scoring       │ │   limitations │  │    │
│  │  └─────────────────┘ └─────────────────┘ └───────────────┘  │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │                                          │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │           4. EVALUATOR-OPTIMIZER LOOP                        │    │
│  │  ────────────────────────────────────────────────────────── │    │
│  │                                                              │    │
│  │  LLM-as-Judge evaluates on 6 dimensions:                    │    │
│  │  • Evidence Grounding (research support)                    │    │
│  │  • Personalization Fit (matches user constraints)           │    │
│  │  • Actionability (clear, practical steps)                   │    │
│  │  • Safety (appropriate for condition)                       │    │
│  │  • Hormone Alignment (addresses root cause)                 │    │
│  │  • Completeness (covers all focus areas)                    │    │
│  │                                                              │    │
│  │  If score < 0.7: Optimize with feedback and iterate         │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │                                          │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                   FINAL OUTPUT                               │    │
│  │  • Nutrition Recommendations (with evidence grades)         │    │
│  │  • Movement Recommendations (personalized)                  │    │
│  │  • Mindfulness Recommendations                              │    │
│  │  • Quality Scores & Confidence Level                        │    │
│  │  • Problem Analysis Summary                                 │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Implemented Components

### Core Components (`/core/`)

| Component | File | Description |
|-----------|------|-------------|
| **ProblemNarrower** | `problem_narrower.py` | Analyzes user context, categorizes by urgency, identifies root causes |
| **ExpertOrchestrator** | `expert_orchestrator.py` | Routes problems to appropriate experts, parallel execution |
| **EvaluatorOptimizer** | `evaluator_optimizer.py` | LLM-as-Judge quality evaluation, 6-dimension scoring |
| **V3Orchestrator** | `v3_orchestrator.py` | Main pipeline coordinator, 7-step process |

### Expert Modules (`/experts/`)

| Expert | File | Sub-Modules |
|--------|------|-------------|
| **NutritionExpert** | `nutrition_expert.py` | InsulinDiet, AntiInflammatory, HormoneBalancing, ThyroidNutrition |
| **MovementExpert** | `movement_expert.py` | PCOSGeneral, InsulinSensitivity, CortisolManagement, ThyroidMetabolism |
| **MindfulnessExpert** | `mindfulness_expert.py` | CortisolStress, AnxietyManagement, SleepOptimization, HormoneAwareMeditation |

### Reusable Components (`/components/`)

| Component | File | Capabilities |
|-----------|------|--------------|
| **RetrievalComponent** | `retrieval_component.py` | Semantic search, domain configuration, Pinecone integration |
| **EvidenceGrader** | `evidence_grader.py` | Study type classification, sample size scoring, relevance calculation |
| **PersonalizationEngine** | `personalization_engine.py` | Constraint extraction, dietary adaptations, physical limitations |

### Specialized Modules (`/modules/`)

| Module | File | Focus Area |
|--------|------|------------|
| **InsulinDietModule** | `insulin_diet_module.py` | Low glycemic foods, blood sugar stabilization, insulin sensitivity |
| **CortisolManagementModule** | `cortisol_management_module.py` | Stress reduction, adrenal support, HPA axis balance |
| **SleepOptimizationModule** | `sleep_optimization_module.py` | Sleep hygiene, circadian rhythm, hormone-aware interventions |

## Key Design Decisions

### 1. Narrowed User Problem Focus
```python
# Before: Generic recommendations based on symptoms
# After: Focused problem definition

focused_problem = await problem_narrower.analyze_user_context(
    user_profile=user_profile,
    hormone_data=hormone_data,
    symptoms=symptoms
)
# Returns: FocusedProblem with primary_focus, urgency_score, actionable_targets
```

### 2. Modular Expert Routing
```python
# Experts are selected based on problem focus
# Each expert has specialized knowledge and sub-modules

expert_results = await expert_orchestrator.orchestrate(
    focused_problem=focused_problem,
    user_profile=user_profile
)
# Routes to: nutrition, movement, mindfulness experts in parallel
```

### 3. Reusable Component Pattern
```python
# Components are shared across all experts
retrieval = RetrievalComponent()
grader = EvidenceGrader()
personalizer = PersonalizationEngine()

# Each expert uses the same components for consistency
```

### 4. LLM-as-Judge Quality Loop
```python
# 6-dimension evaluation based on Anthropic patterns
evaluation = await evaluator.evaluate_recommendations(
    recommendations=recommendations,
    user_profile=user_profile,
    focused_problem=focused_problem
)

# Optimization if quality below threshold
if evaluation.overall_score < 0.7:
    optimized = await evaluator.optimize_with_feedback(...)
```

## API Endpoint

```
POST /api/v3/recommendations
```

**Request:**
```json
{
  "user_id": "user_123",
  "user_profile": {
    "age": 28,
    "diet_preference": "vegetarian",
    "activity_level": "moderate",
    "allergies": ["dairy"],
    "time_available_minutes": 30
  },
  "hormone_data": {
    "primary_imbalance": "insulin_resistance",
    "insulin": {"level": "elevated", "homa_ir": 2.8}
  },
  "symptoms": ["fatigue", "weight gain", "irregular periods"]
}
```

**Response:**
```json
{
  "request_id": "abc123",
  "problem_analysis": {
    "primary_focus": "insulin_resistance",
    "urgency_score": 7,
    "actionable_targets": ["diet", "exercise", "stress"]
  },
  "nutrition_recommendations": [...],
  "movement_recommendations": [...],
  "mindfulness_recommendations": [...],
  "quality_scores": {
    "overall": 0.85,
    "evidence_grounding": 0.9,
    "personalization_fit": 0.8
  },
  "confidence_level": "high"
}
```

## Directory Structure

```
recommendation_engine_v3/
├── __init__.py                 # Main exports
├── RECOMMENDATION_ENGINE_V3_ARCHITECTURE.md
│
├── core/                       # Core orchestration
│   ├── __init__.py
│   ├── problem_narrower.py     # User context analysis
│   ├── expert_orchestrator.py  # Expert routing
│   ├── evaluator_optimizer.py  # Quality evaluation
│   └── v3_orchestrator.py      # Main pipeline
│
├── experts/                    # Domain experts
│   ├── __init__.py
│   ├── base_expert.py          # Abstract base class
│   ├── nutrition_expert.py     # Dietary expertise
│   ├── movement_expert.py      # Exercise expertise
│   └── mindfulness_expert.py   # Mental wellness
│
├── components/                 # Reusable AI components
│   ├── __init__.py
│   ├── retrieval_component.py  # Semantic search
│   ├── evidence_grader.py      # Research quality
│   └── personalization_engine.py
│
└── modules/                    # Specialized sub-modules
    ├── __init__.py
    ├── insulin_diet_module.py
    ├── cortisol_management_module.py
    └── sleep_optimization_module.py
```

## Addressing Manager's Feedback

### "Should we start off with a narrowed user problem focus?"
✅ **Implemented:** `ProblemNarrower` class analyzes user context before any recommendations are generated. Creates `FocusedProblem` with:
- Primary focus area
- Urgency scoring (1-10)
- Actionable targets
- Root cause identification

### "Should we create multiple modules of RAG with different expertise?"
✅ **Implemented:** Three domain experts (`NutritionExpert`, `MovementExpert`, `MindfulnessExpert`) each with 4 specialized sub-modules. Each expert has:
- Domain-specific knowledge base
- Specialized retrieval configurations
- Condition-specific intervention templates

### "Think more about reusable AI components"
✅ **Implemented:** Three reusable components that all experts share:
- `RetrievalComponent` - Consistent semantic search across domains
- `EvidenceGrader` - Standardized research quality scoring
- `PersonalizationEngine` - Uniform constraint handling

## Research References

This architecture is informed by:

1. **Anthropic's Agentic Patterns** (from building-effective-agents blog):
   - Orchestrator-Workers pattern for expert routing
   - Evaluator-Optimizer pattern for quality loop

2. **Modular RAG Survey** (arXiv:2407.21059):
   - Modular component design
   - Specialized retrieval configurations
   - Iterative refinement patterns

## Next Steps

1. **Integration Testing** - Test full pipeline with real user data
2. **Production Deployment** - Connect to existing RAG v2 infrastructure
3. **Performance Monitoring** - Track quality scores and processing times
4. **Module Expansion** - Add more specialized modules as needed
