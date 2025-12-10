"""
Recommendation Engine V3 - Main Orchestrator
============================================

This is the main entry point for the V3 recommendation system.
It orchestrates the entire pipeline from user input to final
personalized, evidence-graded recommendations.

Architecture follows Anthropic's agentic patterns:
- Orchestrator-Workers: Main orchestrator delegates to expert workers
- Evaluator-Optimizer: Quality assurance loop
- Router: Problem narrowing and expert routing
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class V3RecommendationRequest:
    """Input request for V3 recommendation engine"""
    user_id: str
    user_profile: Dict[str, Any]
    hormone_data: Dict[str, Any]
    symptoms: List[str] = field(default_factory=list)
    preferences: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    focus_areas: List[str] = None  # Optional: user can specify focus


@dataclass
class V3RecommendationResponse:
    """Output response from V3 recommendation engine"""
    request_id: str
    user_id: str
    timestamp: str
    
    # Problem analysis
    problem_analysis: Dict[str, Any]
    
    # Recommendations by category
    nutrition_recommendations: List[Dict[str, Any]]
    movement_recommendations: List[Dict[str, Any]]
    mindfulness_recommendations: List[Dict[str, Any]]
    
    # Quality metrics
    quality_scores: Dict[str, float]
    evidence_summary: Dict[str, Any]
    
    # Personalization info
    personalization_applied: List[str]
    constraints_considered: List[str]
    
    # Meta information
    processing_time_ms: float
    experts_consulted: List[str]
    confidence_level: str


# ============================================
# SINGLETON V3 ENGINE INSTANCE (OPTIMIZATION)
# ============================================
# Thread-safe singleton pattern using threading.Lock
# Prevents re-initialization for each category (food, movement, mindfulness)
import threading

_v3_engine_instance: Optional['RecommendationEngineV3'] = None
_v3_engine_lock = threading.Lock()


def get_v3_engine() -> 'RecommendationEngineV3':
    """
    Get singleton V3 engine instance (thread-safe).
    Use this instead of creating new RecommendationEngineV3() each time.
    
    OPTIMIZATION: Prevents re-initialization overhead for each category.
    Components (ProblemNarrower, ExpertOrchestrator, etc.) are only
    initialized once and reused across all requests.
    """
    global _v3_engine_instance
    if _v3_engine_instance is None:
        with _v3_engine_lock:
            # Double-checked locking pattern
            if _v3_engine_instance is None:
                _v3_engine_instance = RecommendationEngineV3()
                logger.info("✅ V3 Engine singleton created")
    return _v3_engine_instance


def reset_v3_engine():
    """Reset the singleton engine (for testing or cache invalidation)"""
    global _v3_engine_instance
    with _v3_engine_lock:
        _v3_engine_instance = None
        logger.info("🔄 V3 Engine singleton reset")


class RecommendationEngineV3:
    """
    Main V3 Recommendation Engine Orchestrator.
    
    This orchestrator implements a professional-grade recommendation
    pipeline with:
    
    1. Problem Narrowing: Focuses on user's specific situation
    2. Expert Routing: Routes to specialized domain experts
    3. Evidence Grading: Scores research quality
    4. Personalization: Adapts to user constraints
    5. Quality Evaluation: LLM-as-Judge validation
    6. Optimization Loop: Iterative improvement if needed
    
    OPTIMIZATION: Use get_v3_engine() singleton instead of creating new instances.
    """
    
    def __init__(self):
        # Initialize core components
        self.problem_narrower = None
        self.expert_orchestrator = None
        self.evaluator = None
        
        # Initialize reusable components
        self.retrieval = None
        self.evidence_grader = None
        self.personalization_engine = None
        
        # Track initialization state
        self._initialized = False
        
        self._initialize_components()
    
    def _initialize_components(self):
        """Initialize all components with graceful fallbacks"""
        try:
            from app.services.recommendation_engine_v3.core.problem_narrower import ProblemFocusNarrower
            self.problem_narrower = ProblemFocusNarrower()
            logger.info("✅ ProblemFocusNarrower initialized")
        except ImportError as e:
            logger.warning(f"⚠️ ProblemFocusNarrower not available: {e}")
        
        try:
            from app.services.recommendation_engine_v3.components.retrieval_component import RetrievalComponent
            self.retrieval = RetrievalComponent()
            logger.info("✅ RetrievalComponent initialized")
        except ImportError as e:
            logger.warning(f"⚠️ RetrievalComponent not available: {e}")
        
        try:
            from app.services.recommendation_engine_v3.core.expert_orchestrator import ExpertOrchestrator
            # Pass retrieval to expert orchestrator so experts can use real RAG
            self.expert_orchestrator = ExpertOrchestrator(retrieval_component=self.retrieval)
            logger.info("✅ ExpertOrchestrator initialized with retrieval")
        except ImportError as e:
            logger.warning(f"⚠️ ExpertOrchestrator not available: {e}")
        
        try:
            from app.services.recommendation_engine_v3.core.evaluator_optimizer import RecommendationEvaluator
            self.evaluator = RecommendationEvaluator()
            logger.info("✅ RecommendationEvaluator initialized")
        except ImportError as e:
            logger.warning(f"⚠️ RecommendationEvaluator not available: {e}")
        
        try:
            from app.services.recommendation_engine_v3.components.evidence_grader import EvidenceGrader
            self.evidence_grader = EvidenceGrader()
            logger.info("✅ EvidenceGrader initialized")
        except ImportError as e:
            logger.warning(f"⚠️ EvidenceGrader not available: {e}")
        
        try:
            from app.services.recommendation_engine_v3.components.personalization_engine import PersonalizationEngine
            self.personalization_engine = PersonalizationEngine()
            logger.info("✅ PersonalizationEngine initialized")
        except ImportError as e:
            logger.warning(f"⚠️ PersonalizationEngine not available: {e}")
    
    async def generate_recommendations(
        self,
        request: V3RecommendationRequest
    ) -> V3RecommendationResponse:
        """
        Main entry point for generating recommendations.
        
        Pipeline:
        1. Narrow the problem to user's specific context
        2. Route to appropriate experts
        3. Generate domain-specific recommendations
        4. Grade evidence quality
        5. Personalize to user constraints
        6. Evaluate overall quality
        7. Optimize if below threshold
        """
        import uuid
        start_time = datetime.now()
        request_id = str(uuid.uuid4())[:8]
        
        logger.info(f"🚀 V3 Pipeline started for user {request.user_id} [req:{request_id}]")
        
        try:
            # ========================================
            # STEP 1: NARROW THE PROBLEM
            # ========================================
            logger.info("📍 Step 1: Narrowing problem space...")
            
            focused_problem = None
            if self.problem_narrower:
                # Build profile dict from request
                profile_for_narrower = {
                    **request.user_profile,
                    'symptoms': request.symptoms,
                    'hormone_data': request.hormone_data,
                }
                # narrow_focus is sync, not async
                focused_problem = self.problem_narrower.narrow_focus(
                    user_profile=profile_for_narrower,
                    focus_mode="auto"
                )
                logger.info(f"   ↳ Primary focus: {focused_problem.primary_concern.concern_type}")
                logger.info(f"   ↳ Urgency: {focused_problem.primary_concern.urgency.value}")
            else:
                # Fallback problem analysis
                focused_problem = self._fallback_problem_analysis(request)
            
            # ========================================
            # STEP 2: ROUTE TO EXPERTS
            # ========================================
            logger.info("🔀 Step 2: Routing to domain experts...")
            
            expert_results = {}
            experts_consulted = []
            
            if self.expert_orchestrator:
                expert_results = await self.expert_orchestrator.generate_from_experts(
                    focused_problem=focused_problem
                )
                experts_consulted = list(expert_results.keys())
                logger.info(f"   ↳ Experts consulted: {experts_consulted}")
            else:
                # Fallback to basic recommendations
                expert_results = self._fallback_expert_results(request)
                experts_consulted = ['fallback']
            
            # ========================================
            # STEP 3: GRADE EVIDENCE
            # ========================================
            logger.info("📊 Step 3: Grading evidence quality...")
            
            evidence_summary = {'strength': 'not_graded', 'details': [], 'graded_count': 0}
            if self.evidence_grader:
                # Build target conditions for relevance scoring
                target_conditions = ['PCOS', 'polycystic ovary syndrome', 'hormone']
                if request.hormone_data.get('primary_imbalance'):
                    target_conditions.append(request.hormone_data['primary_imbalance'])
                
                # Grade evidence for each recommendation
                for category, recs in expert_results.items():
                    for rec in recs:
                        # Get evidence sources (full docs with text) or fall back to citations
                        sources = rec.get('evidence_sources') or rec.get('citations', [])
                        
                        if sources and len(sources) > 0:
                            try:
                                grading = self.evidence_grader.grade_multiple(
                                    sources,
                                    target_conditions=target_conditions
                                )
                                
                                # Store full grading result
                                rec['evidence_grade'] = grading['aggregate']
                                
                                # FIXED: Update evidence_strength with REAL grade from Evidence Grader
                                # This replaces the 'pending_grade' placeholder
                                rec['evidence_strength'] = grading['aggregate'].get('evidence_strength', 'moderate')
                                
                                # Store individual grades for transparency
                                rec['evidence_grades_detail'] = [
                                    {
                                        'title': g['document'].get('title', 'Unknown')[:60],
                                        'grade': g['grade'].grade_letter,
                                        'score': round(g['grade'].overall_score, 2),
                                        'study_type': g['grade'].study_quality
                                    }
                                    for g in grading.get('graded_documents', [])[:3]
                                ]
                                
                                evidence_summary['graded_count'] += 1
                                logger.debug(f"   📊 Graded '{rec.get('title', 'Unknown')[:30]}': {rec['evidence_strength']}")
                                
                            except Exception as e:
                                logger.warning(f"   ⚠️ Evidence grading failed for '{rec.get('title', 'Unknown')[:30]}': {e}")
                                # Fall back to template hint if grading fails
                                rec['evidence_strength'] = rec.get('evidence_strength_hint', 'moderate')
                        else:
                            # No evidence sources - use template hint as fallback
                            rec['evidence_strength'] = rec.get('evidence_strength_hint', 'weak')
                            rec['evidence_grade'] = {'average_score': 0.3, 'evidence_strength': 'weak'}
                
                logger.info(f"   ↳ Graded {evidence_summary['graded_count']} recommendations with real evidence")
            
            # ========================================
            # STEP 4: PERSONALIZE
            # ========================================
            logger.info("👤 Step 4: Personalizing recommendations...")
            
            personalization_applied = []
            constraints_considered = []
            
            if self.personalization_engine:
                constraints = self.personalization_engine.extract_constraints(request.user_profile)
                constraints_considered = [c.name for c in constraints]
                
                for category, recs in expert_results.items():
                    for i, rec in enumerate(recs):
                        result = self.personalization_engine.personalize(
                            rec, request.user_profile
                        )
                        expert_results[category][i] = result.personalized_recommendation
                        personalization_applied.extend(result.modifications_made)
                
                logger.info(f"   ↳ Constraints applied: {constraints_considered}")
            
            # ========================================
            # STEP 5: EVALUATE & OPTIMIZE QUALITY
            # ========================================
            logger.info("✅ Step 5: Evaluating and optimizing recommendations...")
            
            quality_scores = {}
            if self.evaluator:
                # Run evaluate_and_optimize which handles both evaluation and optimization
                optimized_results = await self.evaluator.evaluate_and_optimize(
                    recommendations=expert_results,
                    focused_problem=focused_problem,
                    max_iterations=2
                )
                # Replace expert_results with optimized version
                expert_results = optimized_results
                
                # Calculate average quality scores from optimized recommendations
                all_scores = []
                for recs in expert_results.values():
                    for rec in recs:
                        if 'evaluation_scores' in rec:
                            all_scores.append(rec['evaluation_scores'].get('overall', 0.7))
                
                avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.7
                quality_scores = {
                    'overall': avg_score,
                    'recommendations_evaluated': len(all_scores),
                }
                logger.info(f"   ↳ Average quality: {avg_score:.2f}/1.0")
            else:
                quality_scores = {'overall': 0.7, 'note': 'evaluation_skipped'}
            
            # ========================================
            # STEP 7: COMPILE RESPONSE
            # ========================================
            logger.info("📦 Step 7: Compiling final response...")
            
            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds() * 1000
            
            response = V3RecommendationResponse(
                request_id=request_id,
                user_id=request.user_id,
                timestamp=datetime.now().isoformat(),
                problem_analysis=self._serialize_problem(focused_problem),
                nutrition_recommendations=expert_results.get('nutrition', []),
                movement_recommendations=expert_results.get('movement', []),
                mindfulness_recommendations=expert_results.get('mindfulness', []),
                quality_scores=quality_scores,
                evidence_summary=evidence_summary,
                personalization_applied=personalization_applied,
                constraints_considered=constraints_considered,
                processing_time_ms=processing_time,
                experts_consulted=experts_consulted,
                confidence_level=self._determine_confidence(quality_scores)
            )
            
            logger.info(f"✨ V3 Pipeline complete in {processing_time:.0f}ms [req:{request_id}]")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ V3 Pipeline failed: {e}")
            raise
    
    def _fallback_problem_analysis(self, request: V3RecommendationRequest) -> Any:
        """Fallback problem analysis when ProblemNarrower unavailable"""
        from dataclasses import dataclass, field
        
        @dataclass
        class FallbackProblem:
            primary_focus: str = "general_wellness"
            urgency_score: float = 5.0
            actionable_targets: List[str] = field(default_factory=list)
        
        # Simple analysis
        symptoms = request.symptoms or []
        hormone_data = request.hormone_data or {}
        
        focus = "hormonal_balance"
        if any('insulin' in s.lower() for s in symptoms):
            focus = "insulin_resistance"
        elif any('stress' in s.lower() or 'anxiety' in s.lower() for s in symptoms):
            focus = "stress_management"
        elif any('weight' in s.lower() for s in symptoms):
            focus = "metabolic_health"
        
        return FallbackProblem(
            primary_focus=focus,
            urgency_score=6.0,
            actionable_targets=['diet', 'exercise', 'stress_management']
        )
    
    def _fallback_expert_results(self, request: V3RecommendationRequest) -> Dict[str, List]:
        """Fallback recommendations when experts unavailable"""
        return {
            'nutrition': [
                {
                    'title': 'Focus on whole foods',
                    'description': 'Emphasize vegetables, lean proteins, and healthy fats',
                    'priority': 'high',
                    'evidence_level': 'strong'
                }
            ],
            'movement': [
                {
                    'title': 'Regular moderate exercise',
                    'description': '30 minutes of walking or similar activity most days',
                    'priority': 'high',
                    'evidence_level': 'strong'
                }
            ],
            'mindfulness': [
                {
                    'title': 'Daily relaxation practice',
                    'description': '10-15 minutes of meditation or deep breathing',
                    'priority': 'medium',
                    'evidence_level': 'moderate'
                }
            ]
        }
    
    def _serialize_problem(self, focused_problem) -> Dict[str, Any]:
        """Serialize focused problem for response"""
        if hasattr(focused_problem, '__dict__'):
            return {
                k: v for k, v in focused_problem.__dict__.items()
                if not k.startswith('_')
            }
        return {'raw': str(focused_problem)}
    
    def _determine_confidence(self, quality_scores: Dict[str, float]) -> str:
        """Determine overall confidence level"""
        overall = quality_scores.get('overall', 0.5)
        if overall >= 0.8:
            return 'high'
        elif overall >= 0.6:
            return 'medium'
        else:
            return 'low'


# Convenience function for easy access
async def generate_v3_recommendations(
    user_id: str,
    user_profile: Dict[str, Any],
    hormone_data: Dict[str, Any],
    symptoms: List[str] = None,
    preferences: Dict[str, Any] = None
) -> V3RecommendationResponse:
    """
    Convenience function to generate V3 recommendations.
    
    Usage:
        response = await generate_v3_recommendations(
            user_id="user123",
            user_profile={"age": 28, "diet_preference": "vegetarian"},
            hormone_data={"insulin": {"level": "elevated"}},
            symptoms=["fatigue", "weight gain"]
        )
    """
    engine = RecommendationEngineV3()
    request = V3RecommendationRequest(
        user_id=user_id,
        user_profile=user_profile or {},
        hormone_data=hormone_data or {},
        symptoms=symptoms or [],
        preferences=preferences or {}
    )
    return await engine.generate_recommendations(request)
