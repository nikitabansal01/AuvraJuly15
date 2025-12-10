"""
Core Module for Recommendation Engine V3
========================================

Contains the main orchestration components:
- ProblemNarrower: Focuses user problems
- ExpertOrchestrator: Routes to domain experts
- EvaluatorOptimizer: Quality assurance loop
- V3Orchestrator: Main pipeline coordinator
"""

from app.services.recommendation_engine_v3.core.problem_narrower import (
    ProblemFocusNarrower,
    FocusedProblem,
    UserConcern,
    ConcernPriority,
    UrgencyLevel,
    Constraint
)
from app.services.recommendation_engine_v3.core.expert_orchestrator import (
    ExpertOrchestrator
)
from app.services.recommendation_engine_v3.core.evaluator_optimizer import (
    RecommendationEvaluator,
    EvaluationResult,
    EvaluationScore
)
from app.services.recommendation_engine_v3.core.v3_orchestrator import (
    RecommendationEngineV3,
    V3RecommendationRequest,
    V3RecommendationResponse,
    generate_v3_recommendations
)

__all__ = [
    # Problem Narrowing
    'ProblemFocusNarrower',
    'FocusedProblem',
    'UserConcern',
    'ConcernPriority',
    'UrgencyLevel',
    'Constraint',
    
    # Expert Orchestration
    'ExpertOrchestrator',
    
    # Evaluation
    'RecommendationEvaluator',
    'EvaluationResult',
    'EvaluationScore',
    
    # Main Orchestrator
    'RecommendationEngineV3',
    'V3RecommendationRequest',
    'V3RecommendationResponse',
    'generate_v3_recommendations',
]