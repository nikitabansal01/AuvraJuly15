"""
Recommendation Engine V3 - Modular Expert-Based Architecture
============================================================

This module implements a professional, modular RAG architecture with:
- User Problem Narrowing Layer
- Domain-Specialized Expert Modules  
- Reusable AI Components
- Evaluator-Optimizer Quality Loop

Key Improvements over RAG v2:
- Narrowed user problem focus before generating recommendations
- Multiple specialized modules for different hormone/condition focuses
- Evidence-based intervention templates
- Iterative quality refinement with LLM-as-judge
"""

import logging
logger = logging.getLogger(__name__)

# Core Components - Safe imports
ProblemFocusNarrower = None
FocusedProblem = None
UserConcern = None
ExpertOrchestrator = None
RecommendationEvaluator = None
EvaluationResult = None
RecommendationEngineV3 = None
V3RecommendationRequest = None
V3RecommendationResponse = None

try:
    from app.services.recommendation_engine_v3.core.problem_narrower import (
        ProblemFocusNarrower,
        FocusedProblem,
        UserConcern
    )
    logger.info("✅ V3 Core: ProblemFocusNarrower loaded")
except ImportError as e:
    logger.warning(f"⚠️ V3 Engine: problem_narrower import failed: {e}")
    ProblemFocusNarrower = None
    FocusedProblem = None
    UserConcern = None

try:
    from app.services.recommendation_engine_v3.core.expert_orchestrator import (
        ExpertOrchestrator
    )
    logger.info("✅ V3 Core: ExpertOrchestrator loaded")
except ImportError as e:
    logger.warning(f"⚠️ V3 Engine: expert_orchestrator import failed: {e}")

try:
    from app.services.recommendation_engine_v3.core.evaluator_optimizer import (
        RecommendationEvaluator,
        EvaluationResult
    )
    logger.info("✅ V3 Core: RecommendationEvaluator loaded")
except ImportError as e:
    logger.warning(f"⚠️ V3 Engine: evaluator_optimizer import failed: {e}")

try:
    from app.services.recommendation_engine_v3.core.v3_orchestrator import (
        RecommendationEngineV3,
        V3RecommendationRequest,
        V3RecommendationResponse,
        generate_v3_recommendations
    )
    logger.info("✅ V3 Core: Main Orchestrator loaded")
except ImportError as e:
    logger.warning(f"⚠️ V3 Engine: v3_orchestrator import failed: {e}")

# Expert Modules - Safe imports
NutritionExpert = None
MovementExpert = None
MindfulnessExpert = None
BaseDomainExpert = None

try:
    from app.services.recommendation_engine_v3.experts.base_expert import BaseDomainExpert
    from app.services.recommendation_engine_v3.experts.nutrition_expert import NutritionExpert
    from app.services.recommendation_engine_v3.experts.movement_expert import MovementExpert
    from app.services.recommendation_engine_v3.experts.mindfulness_expert import MindfulnessExpert
    logger.info("✅ V3 Experts: All expert modules loaded")
except ImportError as e:
    logger.warning(f"⚠️ V3 Engine: expert modules import failed: {e}")

# Reusable Components - Safe imports
RetrievalComponent = None
EvidenceGrader = None
EvidenceGrade = None
PersonalizationEngine = None
PersonalizationResult = None

try:
    from app.services.recommendation_engine_v3.components.retrieval_component import RetrievalComponent
    from app.services.recommendation_engine_v3.components.evidence_grader import EvidenceGrader, EvidenceGrade
    from app.services.recommendation_engine_v3.components.personalization_engine import PersonalizationEngine, PersonalizationResult
    logger.info("✅ V3 Components: All reusable components loaded")
except ImportError as e:
    logger.warning(f"⚠️ V3 Engine: reusable components import failed: {e}")

# Specialized Modules - Safe imports
InsulinDietModule = None
CortisolManagementModule = None
SleepOptimizationModule = None

try:
    from app.services.recommendation_engine_v3.modules.insulin_diet_module import InsulinDietModule
    from app.services.recommendation_engine_v3.modules.cortisol_management_module import CortisolManagementModule
    from app.services.recommendation_engine_v3.modules.sleep_optimization_module import SleepOptimizationModule
    logger.info("✅ V3 Modules: Specialized modules loaded")
except ImportError as e:
    logger.warning(f"⚠️ V3 Engine: specialized modules import failed: {e}")


__all__ = [
    # Main Entry Point
    'generate_v3_recommendations',
    'RecommendationEngineV3',
    'V3RecommendationRequest',
    'V3RecommendationResponse',
    
    # Core Components
    'ProblemFocusNarrower',
    'FocusedProblem',
    'ExpertOrchestrator',
    'EvaluatorOptimizer',
    'EvaluationResult',
    
    # Experts
    'BaseDomainExpert',
    'NutritionExpert',
    'MovementExpert',
    'MindfulnessExpert',
    
    # Reusable Components
    'RetrievalComponent',
    'EvidenceGrader',
    'EvidenceGrade',
    'PersonalizationEngine',
    'PersonalizationResult',
    
    # Specialized Modules
    'InsulinDietModule',
    'CortisolManagementModule',
    'SleepOptimizationModule',
]
