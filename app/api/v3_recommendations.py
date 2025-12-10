"""
Recommendation Engine V3 API Endpoint
=====================================

FastAPI endpoint for the V3 modular recommendation system.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3", tags=["Recommendations V3"])


# Request/Response Models
class V3RecommendationRequestModel(BaseModel):
    """Request model for V3 recommendations"""
    user_id: str = Field(..., description="User identifier")
    user_profile: Dict[str, Any] = Field(
        default_factory=dict,
        description="User profile including age, symptoms, preferences"
    )
    hormone_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Hormone analysis results"
    )
    symptoms: List[str] = Field(
        default_factory=list,
        description="List of user symptoms"
    )
    preferences: Optional[Dict[str, Any]] = Field(
        default=None,
        description="User preferences for recommendations"
    )
    focus_areas: Optional[List[str]] = Field(
        default=None,
        description="Specific areas to focus on (e.g., 'insulin', 'stress')"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
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
                    "insulin": {"level": "elevated", "homa_ir": 2.8},
                    "cortisol": {"level": "normal"}
                },
                "symptoms": [
                    "fatigue",
                    "weight gain",
                    "irregular periods",
                    "difficulty sleeping"
                ],
                "focus_areas": ["insulin", "energy"]
            }
        }


class V3RecommendationResponseModel(BaseModel):
    """Response model for V3 recommendations"""
    request_id: str
    user_id: str
    timestamp: str
    
    # Problem Analysis
    problem_analysis: Dict[str, Any]
    
    # Recommendations by Category
    nutrition_recommendations: List[Dict[str, Any]]
    movement_recommendations: List[Dict[str, Any]]
    mindfulness_recommendations: List[Dict[str, Any]]
    
    # Quality Metrics
    quality_scores: Dict[str, float]
    evidence_summary: Dict[str, Any]
    
    # Personalization
    personalization_applied: List[str]
    constraints_considered: List[str]
    
    # Meta
    processing_time_ms: float
    experts_consulted: List[str]
    confidence_level: str


@router.post(
    "/recommendations",
    response_model=V3RecommendationResponseModel,
    summary="Generate V3 Recommendations",
    description="""
    Generate personalized recommendations using the V3 modular expert architecture.
    
    This endpoint uses:
    - Problem Narrower to focus on user's specific situation
    - Specialized Expert Modules (Nutrition, Movement, Mindfulness)
    - Evidence Grader for research quality scoring
    - Personalization Engine for user constraints
    - Evaluator-Optimizer for quality assurance
    """
)
async def generate_v3_recommendations(
    request: V3RecommendationRequestModel
):
    """
    Generate recommendations using V3 modular architecture.
    """
    logger.info(f"📥 V3 API: Request received for user {request.user_id}")
    
    try:
        # Import V3 engine
        from app.services.recommendation_engine_v3.core.v3_orchestrator import (
            RecommendationEngineV3,
            V3RecommendationRequest
        )
        
        # Create engine instance
        engine = RecommendationEngineV3()
        
        # Create internal request
        internal_request = V3RecommendationRequest(
            user_id=request.user_id,
            user_profile=request.user_profile,
            hormone_data=request.hormone_data,
            symptoms=request.symptoms,
            preferences=request.preferences or {},
            focus_areas=request.focus_areas
        )
        
        # Generate recommendations
        response = await engine.generate_recommendations(internal_request)
        
        logger.info(f"✅ V3 API: Generated recommendations in {response.processing_time_ms:.0f}ms")
        
        # Convert to response model
        return V3RecommendationResponseModel(
            request_id=response.request_id,
            user_id=response.user_id,
            timestamp=response.timestamp,
            problem_analysis=response.problem_analysis,
            nutrition_recommendations=response.nutrition_recommendations,
            movement_recommendations=response.movement_recommendations,
            mindfulness_recommendations=response.mindfulness_recommendations,
            quality_scores=response.quality_scores,
            evidence_summary=response.evidence_summary,
            personalization_applied=response.personalization_applied,
            constraints_considered=response.constraints_considered,
            processing_time_ms=response.processing_time_ms,
            experts_consulted=response.experts_consulted,
            confidence_level=response.confidence_level
        )
        
    except ImportError as e:
        logger.error(f"❌ V3 API: Import error - {e}")
        raise HTTPException(
            status_code=500,
            detail=f"V3 engine components not available: {str(e)}"
        )
    except Exception as e:
        logger.error(f"❌ V3 API: Error - {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error generating V3 recommendations: {str(e)}"
        )


@router.get(
    "/health",
    summary="V3 Engine Health Check",
    description="Check if V3 recommendation engine components are loaded"
)
async def v3_health_check():
    """Check V3 engine health and component availability"""
    
    components = {
        'problem_narrower': False,
        'expert_orchestrator': False,
        'evaluator_optimizer': False,
        'retrieval_component': False,
        'evidence_grader': False,
        'personalization_engine': False,
        'nutrition_expert': False,
        'movement_expert': False,
        'mindfulness_expert': False
    }
    
    try:
        from app.services.recommendation_engine_v3.core.problem_narrower import ProblemFocusNarrower
        components['problem_narrower'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.core.expert_orchestrator import ExpertOrchestrator
        components['expert_orchestrator'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.core.evaluator_optimizer import RecommendationEvaluator
        components['evaluator_optimizer'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.components.retrieval_component import RetrievalComponent
        components['retrieval_component'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.components.evidence_grader import EvidenceGrader
        components['evidence_grader'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.components.personalization_engine import PersonalizationEngine
        components['personalization_engine'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.experts.nutrition_expert import NutritionExpert
        components['nutrition_expert'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.experts.movement_expert import MovementExpert
        components['movement_expert'] = True
    except ImportError:
        pass
    
    try:
        from app.services.recommendation_engine_v3.experts.mindfulness_expert import MindfulnessExpert
        components['mindfulness_expert'] = True
    except ImportError:
        pass
    
    loaded_count = sum(1 for v in components.values() if v)
    total_count = len(components)
    
    return {
        'status': 'healthy' if loaded_count == total_count else 'degraded',
        'components_loaded': f"{loaded_count}/{total_count}",
        'components': components,
        'version': '3.0.0'
    }


@router.get(
    "/architecture",
    summary="V3 Architecture Overview",
    description="Get overview of V3 recommendation engine architecture"
)
async def v3_architecture_overview():
    """Return V3 architecture documentation"""
    return {
        'version': '3.0.0',
        'architecture': 'Modular Expert-Based RAG',
        'components': {
            'core': {
                'problem_narrower': 'Analyzes user context to create focused problem definition',
                'expert_orchestrator': 'Routes problems to specialized domain experts',
                'evaluator_optimizer': 'LLM-as-Judge quality evaluation with optimization loop',
                'v3_orchestrator': 'Main pipeline coordinator'
            },
            'experts': {
                'nutrition_expert': 'Specialized in dietary interventions for hormonal health',
                'movement_expert': 'Exercise and physical activity recommendations',
                'mindfulness_expert': 'Stress management, sleep, mental wellness'
            },
            'reusable_components': {
                'retrieval_component': 'Semantic search with configurable parameters',
                'evidence_grader': 'Research quality scoring (study type, sample size, relevance)',
                'personalization_engine': 'User constraint handling and adaptation'
            },
            'specialized_modules': {
                'insulin_diet_module': 'Insulin-sensitive dietary interventions',
                'cortisol_management_module': 'Stress and adrenal support',
                'sleep_optimization_module': 'Sleep hygiene and circadian rhythm'
            }
        },
        'pipeline_steps': [
            '1. Problem Narrowing - Focus on user\'s specific context',
            '2. Expert Routing - Route to appropriate domain experts',
            '3. Recommendation Generation - Domain-specific recommendations',
            '4. Evidence Grading - Score research quality',
            '5. Personalization - Adapt to user constraints',
            '6. Quality Evaluation - LLM-as-Judge validation',
            '7. Optimization Loop - Improve if below threshold'
        ],
        'key_improvements': [
            'Narrowed user problem focus (addresses "not on point" feedback)',
            'Modular architecture with different expertise',
            'Reusable AI components across modules',
            'Evidence-based quality grading',
            'Iterative optimization with LLM feedback'
        ]
    }
