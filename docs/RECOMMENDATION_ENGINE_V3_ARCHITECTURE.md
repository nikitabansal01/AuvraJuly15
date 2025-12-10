# AUVRA Recommendation Engine V3 - Professional Architecture Plan

## Executive Summary

This document outlines the professional improvement plan for AUVRA's Recommendation Engine, addressing the feedback that "The recommended action plan was not on point." The plan proposes a **Modular RAG Architecture** with **Domain-Specialized Expert Modules** and **Reusable AI Components**.

---

## 1. Current System Analysis

### Current Architecture (RAG v2)
```
┌─────────────────────────────────────────────────────────────────┐
│                    CURRENT RAG PIPELINE                          │
│                                                                   │
│   UserProfile → RootCauseEngine → Single RAG Orchestrator        │
│                           ↓                                       │
│              Pinecone (Generic Query) → LLM → Recommendations    │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Identified Weaknesses

| Issue | Description | Impact |
|-------|-------------|--------|
| **Generic Retrieval** | Single query for all hormone types | Low relevance for specific conditions |
| **No Problem Focus** | Recommendations lack user-specific targeting | Action plans feel generic |
| **Monolithic Design** | One orchestrator handles everything | Hard to optimize per domain |
| **No Evidence Ranking** | All studies weighted equally | Lower quality recommendations |
| **Missing Personalization** | Limited use of user's specific concerns | Not "on point" |
| **No Iterative Refinement** | Single-pass generation | No self-correction |

---

## 2. Proposed Architecture: Modular RAG with Expert Agents

### 2.1 High-Level Architecture
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     RECOMMENDATION ENGINE V3                                 │
│                   "Modular Expert-Based Architecture"                        │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    USER PROBLEM NARROWING LAYER                      │    │
│  │  ┌───────────────┐  ┌───────────────┐  ┌────────────────────────┐   │    │
│  │  │ ProblemFocus  │  │ ConcernMapper │  │ ConstraintExtractor    │   │    │
│  │  │ Classifier    │  │ (Symptoms →   │  │ (Medical conditions,   │   │    │
│  │  │               │  │  Root Cause)  │  │  lifestyle limits)     │   │    │
│  │  └───────────────┘  └───────────────┘  └────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    EXPERT ORCHESTRATOR (Router)                      │    │
│  │  Determines which domain experts to invoke based on problem focus    │    │
│  └───────────────────────────────┬─────────────────────────────────────┘    │
│                                  │                                           │
│       ┌──────────────────────────┼──────────────────────────┐               │
│       ▼                          ▼                          ▼               │
│  ┌──────────────┐        ┌──────────────┐        ┌──────────────┐          │
│  │  NUTRITION   │        │   MOVEMENT   │        │  MINDFULNESS │          │
│  │   EXPERT     │        │   EXPERT     │        │    EXPERT    │          │
│  │  ┌────────┐  │        │  ┌────────┐  │        │  ┌────────┐  │          │
│  │  │Insulin │  │        │  │PCOS    │  │        │  │Cortisol│  │          │
│  │  │Resist. │  │        │  │Exercise│  │        │  │Stress  │  │          │
│  │  │Module  │  │        │  │Module  │  │        │  │Module  │  │          │
│  │  ├────────┤  │        │  ├────────┤  │        │  ├────────┤  │          │
│  │  │Androgen│  │        │  │Hormone │  │        │  │Sleep   │  │          │
│  │  │Diet    │  │        │  │Synced  │  │        │  │Quality │  │          │
│  │  │Module  │  │        │  │Workout │  │        │  │Module  │  │          │
│  │  ├────────┤  │        │  ├────────┤  │        │  ├────────┤  │          │
│  │  │Anti-   │  │        │  │Weight  │  │        │  │Anxiety │  │          │
│  │  │Inflam. │  │        │  │Mgmt    │  │        │  │Mgmt    │  │          │
│  │  │Module  │  │        │  │Module  │  │        │  │Module  │  │          │
│  │  └────────┘  │        │  └────────┘  │        │  └────────┘  │          │
│  └──────────────┘        └──────────────┘        └──────────────┘          │
│       │                          │                          │               │
│       └──────────────────────────┼──────────────────────────┘               │
│                                  ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    RECOMMENDATION SYNTHESIZER                        │    │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │    │
│  │  │ Conflict      │  │ Priority      │  │ Action Plan   │            │    │
│  │  │ Resolver      │  │ Ranker        │  │ Generator     │            │    │
│  │  └───────────────┘  └───────────────┘  └───────────────┘            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                  │                                           │
│                                  ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    EVALUATOR-OPTIMIZER LOOP                          │    │
│  │  LLM evaluates recommendations → Refines if score < threshold        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Component Design

### 3.1 User Problem Narrowing Layer (NEW)

**Purpose**: Address "Should we start off with a narrowed user problem focus?"

```python
class ProblemFocusNarrower:
    """
    Narrows down user's problem to specific, actionable focus areas
    before generating recommendations.
    """
    
    def narrow_focus(self, user_profile: UserProfile) -> FocusedProblem:
        # Step 1: Identify PRIMARY concern (not just hormone)
        primary_concern = self._identify_primary_concern(user_profile)
        # Examples: "Can't lose weight despite trying"
        #           "Irregular periods affecting fertility planning"
        #           "Severe acne affecting confidence"
        
        # Step 2: Map concern to root causes (multiple possible)
        root_causes = self._map_to_root_causes(primary_concern, user_profile)
        # Example: weight_gain → [insulin_resistance, cortisol_high, thyroid_low]
        
        # Step 3: Identify constraints/limitations
        constraints = self._extract_constraints(user_profile)
        # Examples: dietary restrictions, time limitations, physical limitations
        
        # Step 4: Set success criteria
        success_criteria = self._define_success_metrics(primary_concern)
        # Example: "Weight loss of 5-10% in 12 weeks"
        
        return FocusedProblem(
            primary_concern=primary_concern,
            root_causes=root_causes,
            constraints=constraints,
            success_criteria=success_criteria,
            urgency_level=self._assess_urgency(user_profile)
        )
```

#### Problem Focus Categories (Expanded from current symptoms)

| User Concern | Root Cause Mapping | Recommended Expert Modules |
|--------------|-------------------|---------------------------|
| Weight gain/Can't lose weight | Insulin resistance, Cortisol dysregulation | NutritionExpert.InsulinModule, MovementExpert.WeightMgmtModule |
| Irregular/Missing periods | Androgen excess, Low progesterone | NutritionExpert.AndrogenModule, MindfulnessExpert.StressModule |
| Acne/Hirsutism | Androgen excess, Inflammation | NutritionExpert.AntiInflamModule, NutritionExpert.AndrogenModule |
| Fatigue/Low energy | Thyroid dysfunction, Cortisol burnout | NutritionExpert.ThyroidModule, MindfulnessExpert.SleepModule |
| Mood swings/Anxiety | Cortisol dysregulation, Estrogen dominance | MindfulnessExpert.AnxietyModule, NutritionExpert.EstrogenModule |
| Fertility concerns | Multiple hormone imbalances | All domains with fertility-specific focus |

---

### 3.2 Domain Expert Modules (Modular Architecture)

**Purpose**: Address "Should we create multiple modules of RAG with different expertise?"

Each expert module is a **reusable AI component** with:
- Specialized retrieval queries
- Domain-specific knowledge base filters
- Expert-level prompts
- Quality thresholds

#### 3.2.1 Nutrition Expert

```python
class NutritionExpert(DomainExpert):
    """
    Specialized nutrition/diet recommendation expert
    with sub-modules for specific hormone-diet interventions
    """
    
    SUB_MODULES = {
        'insulin_resistance': InsulinResistanceDietModule(),
        'androgen_reduction': AndrogenReductionDietModule(),
        'anti_inflammatory': AntiInflammatoryDietModule(),
        'thyroid_support': ThyroidSupportDietModule(),
        'estrogen_balance': EstrogenBalanceDietModule(),
        'cortisol_regulation': CortisolDietModule(),
    }
    
    def generate_recommendations(self, focused_problem: FocusedProblem) -> List[Recommendation]:
        # Determine which sub-modules to activate
        active_modules = self._select_modules(focused_problem.root_causes)
        
        # Parallel retrieval from each module's specialized knowledge base
        module_results = await asyncio.gather(*[
            module.retrieve_and_recommend(focused_problem)
            for module in active_modules
        ])
        
        # Resolve conflicts (e.g., one module says avoid dairy, another says consume)
        resolved = self._resolve_conflicts(module_results)
        
        # Rank by relevance to PRIMARY concern
        ranked = self._rank_by_relevance(resolved, focused_problem.primary_concern)
        
        return ranked

class InsulinResistanceDietModule(ExpertSubModule):
    """
    Specialized module for insulin-resistance focused nutrition
    """
    
    # Module-specific retrieval configuration
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            "low glycemic index diet PCOS insulin",
            "carbohydrate restriction insulin sensitivity women",
            "fiber intake insulin resistance PCOS",
            "intermittent fasting PCOS insulin",
        ],
        'must_include_terms': ['insulin', 'glucose', 'glycemic'],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    # Module-specific action templates (evidence-backed)
    ACTION_TEMPLATES = {
        'reduce_refined_carbs': {
            'action': "Replace refined carbohydrates with complex carbs",
            'specifics': [
                "Swap white rice for quinoa or brown rice",
                "Choose whole grain bread over white bread",
                "Limit added sugars to <25g/day"
            ],
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
        },
        'increase_fiber': {
            'action': "Increase daily fiber intake to 25-30g",
            'specifics': [
                "Add 2 tbsp ground flaxseed daily",
                "Include legumes in at least one meal",
                "Consume 5+ servings of vegetables"
            ],
            'evidence_strength': 'strong',
            'timeline': '4-8 weeks',
        },
        # ... more templates
    }
    
    async def retrieve_and_recommend(self, problem: FocusedProblem) -> ModuleRecommendations:
        # Step 1: Specialized retrieval
        papers = await self.specialized_retrieval(problem)
        
        # Step 2: Extract actionable interventions from papers
        interventions = self._extract_interventions(papers)
        
        # Step 3: Match to templates + customize for user
        recommendations = self._generate_personalized_recs(
            interventions=interventions,
            user_constraints=problem.constraints,
            success_criteria=problem.success_criteria
        )
        
        return ModuleRecommendations(
            module_name='insulin_resistance_diet',
            recommendations=recommendations,
            confidence=self._calculate_confidence(papers, interventions),
            citations=self._format_citations(papers)
        )
```

#### 3.2.2 Movement Expert

```python
class MovementExpert(DomainExpert):
    """
    Specialized exercise/physical activity expert
    """
    
    SUB_MODULES = {
        'pcos_exercise': PCOSExerciseModule(),          # General PCOS exercise
        'hormone_synced': HormoneSyncedWorkoutModule(), # Cycle-based exercise
        'weight_management': WeightMgmtExerciseModule(), # Weight loss focus
        'stress_relief': StressReliefExerciseModule(),  # Low cortisol exercises
        'fertility_support': FertilitySupportExerciseModule(),
    }
    
class HormoneSyncedWorkoutModule(ExpertSubModule):
    """
    Exercise recommendations synced to menstrual cycle phases
    """
    
    PHASE_RECOMMENDATIONS = {
        'menstrual': {
            'intensity': 'low_to_moderate',
            'types': ['gentle yoga', 'walking', 'light stretching'],
            'duration': '20-30 minutes',
            'frequency': '3-4 times/week',
            'rationale': 'Energy is typically lower; focus on recovery'
        },
        'follicular': {
            'intensity': 'moderate_to_high',
            'types': ['HIIT', 'strength training', 'running'],
            'duration': '30-45 minutes',
            'frequency': '4-5 times/week',
            'rationale': 'Rising estrogen supports higher intensity'
        },
        'ovulatory': {
            'intensity': 'high',
            'types': ['intense cardio', 'heavy lifting', 'high-energy workouts'],
            'duration': '45-60 minutes',
            'frequency': '5 times/week',
            'rationale': 'Peak energy and strength during ovulation'
        },
        'luteal': {
            'intensity': 'moderate',
            'types': ['pilates', 'swimming', 'moderate strength training'],
            'duration': '30-40 minutes',
            'frequency': '3-4 times/week',
            'rationale': 'Progesterone rises; avoid overexertion'
        }
    }
```

#### 3.2.3 Mindfulness Expert

```python
class MindfulnessExpert(DomainExpert):
    """
    Specialized mental health & stress management expert
    """
    
    SUB_MODULES = {
        'cortisol_stress': CortisolStressModule(),
        'sleep_quality': SleepQualityModule(),
        'anxiety_management': AnxietyManagementModule(),
        'mood_regulation': MoodRegulationModule(),
        'pcos_emotional': PCOSEmotionalSupportModule(),
    }
```

---

### 3.3 Reusable AI Components

**Purpose**: Address "Read about reusable AI components"

#### Core Reusable Components

```python
# ============================================================
# REUSABLE AI COMPONENTS
# These can be used across multiple experts and modules
# ============================================================

class RetrievalComponent:
    """
    Reusable semantic search component with configurable parameters
    """
    def __init__(self, vector_store: str, namespace: str):
        self.vector_store = vector_store
        self.namespace = namespace
    
    async def retrieve(
        self,
        query: str,
        filters: Dict[str, Any] = None,
        top_k: int = 20,
        rerank: bool = True,
        rerank_model: str = 'cross-encoder'
    ) -> List[Document]:
        # Shared retrieval logic
        pass

class EvidenceGrader:
    """
    Reusable component for grading research evidence quality
    """
    EVIDENCE_HIERARCHY = {
        'meta_analysis': 1.0,
        'systematic_review': 0.95,
        'rct': 0.90,
        'cohort': 0.70,
        'case_control': 0.60,
        'case_series': 0.40,
        'expert_opinion': 0.30,
    }
    
    def grade_evidence(self, paper: Paper) -> EvidenceGrade:
        # Reusable evidence grading
        pass

class CitationValidator:
    """
    Reusable component for validating and formatting citations
    """
    def validate(self, recommendation: Recommendation, sources: List[Paper]) -> ValidationResult:
        # Shared citation validation
        pass

class PersonalizationEngine:
    """
    Reusable component for personalizing recommendations
    """
    def personalize(
        self,
        base_recommendation: Recommendation,
        user_profile: UserProfile,
        constraints: List[Constraint]
    ) -> Recommendation:
        # Shared personalization logic
        pass

class ConflictResolver:
    """
    Reusable component for resolving conflicting recommendations
    """
    def resolve(
        self,
        recommendations: List[Recommendation],
        priority_rules: List[PriorityRule]
    ) -> List[Recommendation]:
        # Shared conflict resolution
        pass

class ActionPlanFormatter:
    """
    Reusable component for formatting action plans
    """
    FORMATS = ['immediate_actions', 'daily_habits', 'weekly_goals', 'monthly_milestones']
    
    def format(
        self,
        recommendations: List[Recommendation],
        timeline: str,
        format_type: str
    ) -> ActionPlan:
        # Shared formatting logic
        pass
```

---

### 3.4 Evaluator-Optimizer Loop (Quality Assurance)

**Purpose**: Ensure recommendations are "on point" through iterative refinement

```python
class RecommendationEvaluator:
    """
    LLM-as-Judge for evaluating recommendation quality
    Inspired by Anthropic's evaluator-optimizer workflow
    """
    
    EVALUATION_CRITERIA = {
        'relevance': {
            'description': 'How well does the recommendation address the user\'s PRIMARY concern?',
            'weight': 0.25,
            'threshold': 0.7
        },
        'specificity': {
            'description': 'Is the recommendation specific and actionable (not generic)?',
            'weight': 0.20,
            'threshold': 0.7
        },
        'evidence_quality': {
            'description': 'Is the recommendation backed by high-quality research?',
            'weight': 0.20,
            'threshold': 0.6
        },
        'personalization': {
            'description': 'Is the recommendation tailored to user\'s constraints?',
            'weight': 0.15,
            'threshold': 0.6
        },
        'feasibility': {
            'description': 'Can the user realistically implement this recommendation?',
            'weight': 0.10,
            'threshold': 0.7
        },
        'safety': {
            'description': 'Is the recommendation medically safe for the user?',
            'weight': 0.10,
            'threshold': 0.9
        }
    }
    
    async def evaluate_and_optimize(
        self,
        recommendations: List[Recommendation],
        focused_problem: FocusedProblem,
        max_iterations: int = 3
    ) -> List[Recommendation]:
        """
        Iteratively evaluate and improve recommendations
        """
        current_recs = recommendations
        
        for iteration in range(max_iterations):
            # Evaluate current recommendations
            scores = await self._evaluate_all(current_recs, focused_problem)
            
            # Check if all meet threshold
            if all(score.overall >= 0.7 for score in scores):
                break
            
            # Identify weak recommendations
            weak_recs = [
                (rec, score) for rec, score in zip(current_recs, scores)
                if score.overall < 0.7
            ]
            
            # Generate feedback for improvement
            feedback = await self._generate_feedback(weak_recs, focused_problem)
            
            # Optimize weak recommendations
            improved_recs = await self._optimize(weak_recs, feedback)
            
            # Replace weak with improved
            current_recs = self._merge_improvements(current_recs, improved_recs)
        
        return current_recs
    
    async def _evaluate_single(
        self,
        recommendation: Recommendation,
        focused_problem: FocusedProblem
    ) -> EvaluationScore:
        """
        Use LLM to evaluate a single recommendation
        """
        prompt = f"""Evaluate this health recommendation:

USER'S PRIMARY CONCERN: {focused_problem.primary_concern}
USER'S ROOT CAUSES: {focused_problem.root_causes}
USER'S CONSTRAINTS: {focused_problem.constraints}

RECOMMENDATION:
Title: {recommendation.title}
Action: {recommendation.specific_action}
Evidence: {recommendation.research_backing}

Rate on a scale of 0-1 for each criterion:
1. RELEVANCE: Does this directly address "{focused_problem.primary_concern}"?
2. SPECIFICITY: Is this actionable with exact amounts/times (not generic)?
3. EVIDENCE QUALITY: Is this backed by cited research?
4. PERSONALIZATION: Does it consider the user's constraints?
5. FEASIBILITY: Can an average person realistically do this?
6. SAFETY: Is this medically safe?

Return JSON: {{"relevance": 0.X, "specificity": 0.X, ...}}
"""
        # Call LLM and parse response
        pass
```

---

## 4. Implementation Roadmap

### Phase 1: Problem Narrowing Layer (Week 1-2)
- [ ] Implement `ProblemFocusNarrower` class
- [ ] Create concern-to-root-cause mapping
- [ ] Add constraint extraction logic
- [ ] Test with sample user profiles

### Phase 2: Expert Module Infrastructure (Week 3-4)
- [ ] Create `DomainExpert` base class
- [ ] Create `ExpertSubModule` base class
- [ ] Implement module selection logic
- [ ] Create reusable AI components

### Phase 3: Nutrition Expert Modules (Week 5-6)
- [ ] Implement `InsulinResistanceDietModule`
- [ ] Implement `AndrogenReductionDietModule`
- [ ] Implement `AntiInflammatoryDietModule`
- [ ] Test specialized retrieval queries

### Phase 4: Movement Expert Modules (Week 7-8)
- [ ] Implement `PCOSExerciseModule`
- [ ] Implement `HormoneSyncedWorkoutModule`
- [ ] Implement `WeightMgmtExerciseModule`
- [ ] Test cycle-phase integration

### Phase 5: Mindfulness Expert Modules (Week 9-10)
- [ ] Implement `CortisolStressModule`
- [ ] Implement `SleepQualityModule`
- [ ] Implement `AnxietyManagementModule`

### Phase 6: Evaluator-Optimizer Loop (Week 11-12)
- [ ] Implement `RecommendationEvaluator`
- [ ] Create evaluation prompts
- [ ] Implement iterative refinement
- [ ] End-to-end testing

---

## 5. Expected Improvements

| Metric | Current (RAG v2) | Target (RAG v3) | How |
|--------|------------------|-----------------|-----|
| **Relevance to Primary Concern** | ~60% | >85% | Problem narrowing + specialized modules |
| **Specificity of Actions** | ~50% | >80% | Expert templates + evidence-based specifics |
| **Citation Verification Rate** | ~70% | >90% | Module-specific retrieval + validation |
| **User-Perceived "On Point"** | Low | High | Personalization + evaluator loop |
| **Recommendation Diversity** | Limited | Balanced | Multi-expert parallel generation |

---

## 6. API Changes

### New Endpoints

```python
# V3 API
@router.post("/api/v3/recommendations/generate")
async def generate_v3_recommendations(
    user_profile: UserProfile,
    focus_mode: str = "auto",  # auto, weight, fertility, acne, energy, mood
    include_evaluation: bool = True,
    max_per_category: int = 5
) -> V3RecommendationResult:
    pass

@router.post("/api/v3/recommendations/evaluate")
async def evaluate_recommendations(
    recommendations: List[Recommendation],
    user_profile: UserProfile
) -> EvaluationResult:
    pass

@router.get("/api/v3/experts")
async def list_available_experts() -> List[ExpertInfo]:
    """List all available expert modules"""
    pass
```

---

## 7. File Structure

```
app/services/
├── rag/                    # Current RAG (v2) - keep for backward compatibility
│   ├── rag_orchestrator.py
│   ├── rag_retriever.py
│   ├── rag_context_compiler.py
│   └── rag_citation_validator.py
│
├── recommendation_engine_v3/   # NEW v3 Engine
│   ├── __init__.py
│   ├── core/
│   │   ├── problem_narrower.py     # User problem focus
│   │   ├── expert_orchestrator.py  # Routes to experts
│   │   ├── recommendation_synthesizer.py
│   │   └── evaluator_optimizer.py  # Quality loop
│   │
│   ├── experts/
│   │   ├── base_expert.py          # Abstract base class
│   │   ├── nutrition_expert.py
│   │   ├── movement_expert.py
│   │   └── mindfulness_expert.py
│   │
│   ├── modules/                    # Sub-modules for each expert
│   │   ├── nutrition/
│   │   │   ├── insulin_resistance.py
│   │   │   ├── androgen_reduction.py
│   │   │   ├── anti_inflammatory.py
│   │   │   └── thyroid_support.py
│   │   ├── movement/
│   │   │   ├── pcos_exercise.py
│   │   │   ├── hormone_synced.py
│   │   │   └── weight_management.py
│   │   └── mindfulness/
│   │       ├── cortisol_stress.py
│   │       ├── sleep_quality.py
│   │       └── anxiety_management.py
│   │
│   ├── components/                 # Reusable AI components
│   │   ├── retrieval_component.py
│   │   ├── evidence_grader.py
│   │   ├── citation_validator.py
│   │   ├── personalization_engine.py
│   │   ├── conflict_resolver.py
│   │   └── action_plan_formatter.py
│   │
│   └── knowledge_base/             # Module-specific prompts & templates
│       ├── intervention_templates.py
│       ├── evaluation_prompts.py
│       └── expert_prompts.py
```

---

## 8. Key Architectural Patterns Used

| Pattern | Description | Benefit |
|---------|-------------|---------|
| **Routing Workflow** | Expert orchestrator routes to specialized modules | Better accuracy per domain |
| **Parallelization** | Multiple expert modules run simultaneously | Faster generation |
| **Evaluator-Optimizer** | LLM evaluates and refines recommendations | Higher quality output |
| **Modular Design** | Each module is independent and reusable | Easy to extend/maintain |
| **Chain of Responsibility** | Problem → Experts → Synthesizer → Evaluator | Clear processing flow |

---

## 9. References

1. Anthropic - "Building Effective Agents" (2024)
   - Workflow patterns: Routing, Parallelization, Evaluator-Optimizer
   
2. "Retrieval-Augmented Generation for Large Language Models: A Survey" (arXiv:2312.10997)
   - Modular RAG architecture concepts
   
3. Model Context Protocol (MCP)
   - Reusable AI component patterns

---

## 10. Next Steps

1. **Review this architecture** with the team
2. **Prioritize modules** based on user needs (start with insulin resistance + weight management)
3. **Create POC** for one expert module end-to-end
4. **Measure improvement** against current RAG v2
5. **Iterate based on feedback**

---

*Document Version: 1.0*
*Created: December 10, 2024*
*Author: Development Team*
