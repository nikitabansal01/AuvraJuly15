"""
Evaluator-Optimizer - Quality Assurance Loop
=============================================

This module implements the LLM-as-Judge pattern to evaluate and iteratively
improve recommendation quality.

Pattern: Evaluator-Optimizer (from Anthropic's agent patterns)
- Evaluator: Assesses recommendation quality on multiple criteria
- Optimizer: Refines weak recommendations based on feedback
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field
import logging
import json

from app.services.recommendation_engine_v3.core.problem_narrower import FocusedProblem

logger = logging.getLogger(__name__)


@dataclass
class EvaluationScore:
    """Scores for individual evaluation criteria"""
    relevance: float = 0.0         # How well it addresses primary concern
    specificity: float = 0.0       # How actionable/specific (not generic)
    evidence_quality: float = 0.0  # Quality of research backing
    personalization: float = 0.0   # Tailored to user constraints
    feasibility: float = 0.0       # Can user realistically do this
    safety: float = 0.0            # Medical safety
    
    @property
    def overall(self) -> float:
        """Calculate weighted overall score"""
        weights = {
            'relevance': 0.25,
            'specificity': 0.20,
            'evidence_quality': 0.20,
            'personalization': 0.15,
            'feasibility': 0.10,
            'safety': 0.10
        }
        return (
            self.relevance * weights['relevance'] +
            self.specificity * weights['specificity'] +
            self.evidence_quality * weights['evidence_quality'] +
            self.personalization * weights['personalization'] +
            self.feasibility * weights['feasibility'] +
            self.safety * weights['safety']
        )
    
    def to_dict(self) -> Dict[str, float]:
        return {
            'relevance': self.relevance,
            'specificity': self.specificity,
            'evidence_quality': self.evidence_quality,
            'personalization': self.personalization,
            'feasibility': self.feasibility,
            'safety': self.safety,
            'overall': self.overall
        }


@dataclass
class EvaluationResult:
    """Result of evaluating a single recommendation"""
    recommendation: Dict[str, Any]
    scores: EvaluationScore
    feedback: str = ""
    improvement_suggestions: List[str] = field(default_factory=list)
    passes_threshold: bool = False


class RecommendationEvaluator:
    """
    LLM-as-Judge for evaluating and improving recommendation quality.
    
    Uses iterative refinement to ensure recommendations are "on point".
    """
    
    # Minimum threshold for each criterion
    THRESHOLDS = {
        'relevance': 0.7,
        'specificity': 0.7,
        'evidence_quality': 0.6,
        'personalization': 0.6,
        'feasibility': 0.7,
        'safety': 0.9,
        'overall': 0.7
    }
    
    def __init__(self):
        logger.info("📊 RecommendationEvaluator initialized")
    
    async def evaluate_and_optimize(
        self,
        recommendations: Dict[str, List[Dict[str, Any]]],
        focused_problem: FocusedProblem,
        max_iterations: int = 2
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Evaluate all recommendations and iteratively improve weak ones.
        
        Args:
            recommendations: Dict mapping category to list of recommendations
            focused_problem: The user's focused problem definition
            max_iterations: Maximum refinement iterations
        
        Returns:
            Optimized recommendations dict
        """
        logger.info("🔄 Starting evaluation-optimization loop...")
        
        optimized = {}
        
        for category, recs in recommendations.items():
            logger.info(f"📊 Evaluating {category}: {len(recs)} recommendations")
            
            category_optimized = []
            
            for rec in recs:
                # Evaluate
                evaluation = await self._evaluate_single(rec, focused_problem)
                
                if evaluation.passes_threshold:
                    # Add evaluation metadata to recommendation
                    rec['evaluation_scores'] = evaluation.scores.to_dict()
                    rec['evaluation_passed'] = True
                    category_optimized.append(rec)
                else:
                    # Try to improve
                    improved = await self._optimize_recommendation(
                        rec, 
                        evaluation, 
                        focused_problem,
                        max_iterations
                    )
                    if improved:
                        improved['evaluation_scores'] = evaluation.scores.to_dict()
                        improved['evaluation_passed'] = False
                        improved['was_optimized'] = True
                        category_optimized.append(improved)
                    else:
                        # Include with warning if can't improve
                        rec['evaluation_scores'] = evaluation.scores.to_dict()
                        rec['evaluation_passed'] = False
                        rec['quality_warning'] = 'Recommendation may not be optimal for your specific situation'
                        category_optimized.append(rec)
            
            optimized[category] = category_optimized
            logger.info(f"✅ {category}: {len(category_optimized)} recommendations after optimization")
        
        return optimized
    
    async def _evaluate_single(
        self,
        recommendation: Dict[str, Any],
        focused_problem: FocusedProblem
    ) -> EvaluationResult:
        """
        Evaluate a single recommendation using rule-based heuristics.
        
        In production, this would use an LLM for more nuanced evaluation.
        For now, we use deterministic rules.
        """
        scores = EvaluationScore()
        feedback_parts = []
        suggestions = []
        
        # 1. Relevance: Does it address the primary concern?
        primary_concern = focused_problem.primary_concern.concern_type
        root_causes_addressed = recommendation.get('root_causes_addressed', [])
        primary_root_causes = focused_problem.primary_concern.root_causes
        
        # Check overlap between addressed root causes and primary root causes
        overlap = set(root_causes_addressed) & set(primary_root_causes)
        if overlap:
            scores.relevance = min(1.0, 0.5 + len(overlap) * 0.2)
        else:
            scores.relevance = 0.4
            feedback_parts.append("Does not directly address primary root causes")
            suggestions.append("Focus more on user's primary concern")
        
        # Boost if module is specifically for primary concern
        module_source = recommendation.get('module_source', '')
        if primary_concern in module_source:
            scores.relevance = min(1.0, scores.relevance + 0.2)
        
        # 2. Specificity: Is it actionable with specific details?
        specific_action = recommendation.get('specificAction', '')
        specifics = recommendation.get('specifics', [])
        
        # Check for specific quantities/times
        has_quantity = any(char.isdigit() for char in specific_action)
        has_timing = any(time_word in specific_action.lower() 
                        for time_word in ['daily', 'weekly', 'times', 'minutes', 'hours'])
        has_specifics = len(specifics) >= 2
        
        specificity_score = 0.4
        if has_quantity:
            specificity_score += 0.2
        if has_timing:
            specificity_score += 0.2
        if has_specifics:
            specificity_score += 0.2
        
        scores.specificity = min(1.0, specificity_score)
        
        if scores.specificity < 0.7:
            feedback_parts.append("Recommendation lacks specific details")
            suggestions.append("Add specific amounts, durations, and frequencies")
        
        # 3. Evidence Quality: Is it backed by research?
        evidence_strength = recommendation.get('evidence_strength', 'weak')
        citation_verified = recommendation.get('citation_verified', False)
        
        evidence_scores = {'strong': 0.9, 'moderate': 0.7, 'weak': 0.4}
        scores.evidence_quality = evidence_scores.get(evidence_strength, 0.5)
        
        if citation_verified:
            scores.evidence_quality = min(1.0, scores.evidence_quality + 0.1)
        
        if scores.evidence_quality < 0.6:
            feedback_parts.append("Weak evidence backing")
            suggestions.append("Consider stronger research support")
        
        # 4. Personalization: Does it consider user constraints?
        constraint_warning = recommendation.get('constraint_warning')
        if constraint_warning:
            scores.personalization = 0.5
            feedback_parts.append(f"Has constraint conflict: {constraint_warning}")
        else:
            scores.personalization = 0.8
        
        # 5. Feasibility: Can user realistically do this?
        # Simple heuristic based on complexity
        title = recommendation.get('title', '')
        action = recommendation.get('specificAction', '')
        
        # Check for complexity indicators
        complex_terms = ['specialized', 'professional', 'clinical', 'prescription']
        simple_terms = ['daily', 'walk', 'eat', 'breathe', 'simple']
        
        if any(term in title.lower() + action.lower() for term in complex_terms):
            scores.feasibility = 0.5
        elif any(term in title.lower() + action.lower() for term in simple_terms):
            scores.feasibility = 0.9
        else:
            scores.feasibility = 0.7
        
        # 6. Safety: Is it safe?
        contraindications = recommendation.get('contraindications', [])
        if contraindications:
            # Has warnings, slightly lower safety score
            scores.safety = 0.8
        else:
            scores.safety = 0.95
        
        # Compile result
        passes = all([
            scores.relevance >= self.THRESHOLDS['relevance'],
            scores.specificity >= self.THRESHOLDS['specificity'],
            scores.safety >= self.THRESHOLDS['safety'],
            scores.overall >= self.THRESHOLDS['overall']
        ])
        
        return EvaluationResult(
            recommendation=recommendation,
            scores=scores,
            feedback="; ".join(feedback_parts) if feedback_parts else "Good quality recommendation",
            improvement_suggestions=suggestions,
            passes_threshold=passes
        )
    
    async def _optimize_recommendation(
        self,
        recommendation: Dict[str, Any],
        evaluation: EvaluationResult,
        focused_problem: FocusedProblem,
        max_iterations: int
    ) -> Optional[Dict[str, Any]]:
        """
        Attempt to improve a weak recommendation.
        
        In production, this would use an LLM to rewrite.
        For now, we apply rule-based improvements.
        """
        improved = recommendation.copy()
        
        # Improve specificity
        if evaluation.scores.specificity < 0.7:
            specific_action = improved.get('specificAction', '')
            
            # Add default timing if missing
            if 'daily' not in specific_action.lower() and 'weekly' not in specific_action.lower():
                improved['specificAction'] = specific_action + ' daily'
            
            # Add specifics if missing
            if not improved.get('specifics'):
                improved['specifics'] = [
                    'Start gradually and increase over time',
                    'Track your progress',
                    'Adjust based on how you feel'
                ]
        
        # Improve relevance by adding context
        if evaluation.scores.relevance < 0.7:
            primary = focused_problem.primary_concern
            purpose = improved.get('purpose', '')
            if primary.concern_type not in purpose.lower():
                improved['purpose'] = f"{purpose} This helps with {primary.concern_type.replace('_', ' ')}."
        
        # Add personalization note if constraints exist
        if evaluation.scores.personalization < 0.7:
            for constraint in focused_problem.constraints:
                if constraint.constraint_type == 'dietary':
                    improved['personalization_note'] = f'Adapt for {constraint.description}'
        
        return improved
    
    async def evaluate_batch_with_llm(
        self,
        recommendations: List[Dict[str, Any]],
        focused_problem: FocusedProblem
    ) -> List[EvaluationResult]:
        """
        Evaluate recommendations using LLM.
        
        This method would call an LLM for more sophisticated evaluation.
        Placeholder for future implementation.
        """
        # In production, this would:
        # 1. Build evaluation prompt with criteria
        # 2. Call LLM to score each recommendation
        # 3. Parse LLM response into EvaluationResult
        
        # For now, fall back to rule-based evaluation
        results = []
        for rec in recommendations:
            result = await self._evaluate_single(rec, focused_problem)
            results.append(result)
        return results
