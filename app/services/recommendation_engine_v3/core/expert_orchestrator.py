"""
Expert Orchestrator - Routes to Specialized Domain Experts
==========================================================

This module routes the focused problem to appropriate domain expert modules
and synthesizes their outputs into a cohesive recommendation set.

Architecture Pattern: Orchestrator-Workers (from Anthropic's agent patterns)
- Orchestrator determines which experts to invoke
- Workers (experts) operate in parallel
- Results are synthesized with conflict resolution
"""

from typing import List, Dict, Any, Optional
import asyncio
import logging

from app.services.recommendation_engine_v3.core.problem_narrower import (
    FocusedProblem,
    UserConcern
)

logger = logging.getLogger(__name__)


class ExpertOrchestrator:
    """
    Orchestrates the domain expert modules to generate recommendations.
    
    Key responsibilities:
    1. Determine which expert modules to activate based on focused problem
    2. Run expert modules in parallel with real RAG retrieval
    3. Resolve conflicts between expert outputs
    4. Synthesize final recommendation set
    """
    
    # Expert module activation rules
    EXPERT_ACTIVATION_RULES = {
        'nutrition': {
            'always_active': True,
            'root_cause_triggers': ['insulin_resistance', 'inflammation', 'androgen_high', 
                                    'estrogen_dominance', 'thyroid_low', 'cortisol_high'],
            'concern_triggers': ['weight_gain', 'acne', 'fatigue', 'bloating'],
        },
        'movement': {
            'always_active': True,
            'root_cause_triggers': ['insulin_resistance', 'cortisol_high', 'weight_related'],
            'concern_triggers': ['weight_gain', 'fatigue', 'anxiety', 'mood_swings'],
        },
        'mindfulness': {
            'always_active': True,
            'root_cause_triggers': ['cortisol_high', 'cortisol_dysregulation', 'stress_high',
                                    'anxiety', 'progesterone_low'],
            'concern_triggers': ['anxiety', 'mood_swings', 'insomnia', 'fatigue', 'pms'],
        }
    }
    
    # Sub-module selection based on root causes
    SUBMODULE_SELECTION = {
        'nutrition': {
            'insulin_resistance': 'insulin_resistance_diet',
            'androgen_high': 'androgen_reduction_diet',
            'inflammation': 'anti_inflammatory_diet',
            'thyroid_low': 'thyroid_support_diet',
            'estrogen_dominance': 'estrogen_balance_diet',
            'cortisol_high': 'cortisol_diet',
        },
        'movement': {
            'insulin_resistance': 'weight_management_exercise',
            'cortisol_high': 'stress_relief_exercise',
            'weight_related': 'weight_management_exercise',
            'default': 'pcos_general_exercise',
        },
        'mindfulness': {
            'cortisol_high': 'cortisol_stress_management',
            'anxiety': 'anxiety_management',
            'insomnia': 'sleep_quality',
            'default': 'general_mindfulness',
        }
    }
    
    def __init__(self, retrieval_component=None):
        self.experts = {}
        self.retrieval = retrieval_component
        self._initialize_experts()
    
    def set_retrieval(self, retrieval_component):
        """Set retrieval component and propagate to all experts"""
        self.retrieval = retrieval_component
        for expert in self.experts.values():
            if expert and hasattr(expert, 'set_retrieval'):
                expert.set_retrieval(retrieval_component)
                logger.info(f"✅ Retrieval set for {expert.DOMAIN_NAME} expert")
    
    def _initialize_experts(self):
        """Initialize available expert modules with retrieval"""
        try:
            from app.services.recommendation_engine_v3.experts.nutrition_expert import NutritionExpert
            self.experts['nutrition'] = NutritionExpert(retrieval_component=self.retrieval)
            logger.info("✅ NutritionExpert loaded")
        except ImportError as e:
            logger.warning(f"⚠️ NutritionExpert not available: {e}")
            self.experts['nutrition'] = None
        
        try:
            from app.services.recommendation_engine_v3.experts.movement_expert import MovementExpert
            self.experts['movement'] = MovementExpert(retrieval_component=self.retrieval)
            logger.info("✅ MovementExpert loaded")
        except ImportError as e:
            logger.warning(f"⚠️ MovementExpert not available: {e}")
            self.experts['movement'] = None
        
        try:
            from app.services.recommendation_engine_v3.experts.mindfulness_expert import MindfulnessExpert
            self.experts['mindfulness'] = MindfulnessExpert(retrieval_component=self.retrieval)
            logger.info("✅ MindfulnessExpert loaded")
        except ImportError as e:
            logger.warning(f"⚠️ MindfulnessExpert not available: {e}")
            self.experts['mindfulness'] = None
    
    def _determine_active_experts(self, focused_problem: FocusedProblem) -> Dict[str, List[str]]:
        """
        Determine which experts and sub-modules to activate.
        
        Returns:
            Dict mapping expert name to list of sub-modules to activate
        """
        active = {}
        root_causes = focused_problem.get_all_root_causes()
        primary_concern = focused_problem.primary_concern.concern_type
        
        for expert_name, rules in self.EXPERT_ACTIVATION_RULES.items():
            if self.experts.get(expert_name) is None:
                continue
            
            # Check if always active
            if rules.get('always_active'):
                active[expert_name] = []
            
            # Check root cause triggers
            for cause in root_causes:
                if cause in rules.get('root_cause_triggers', []):
                    if expert_name not in active:
                        active[expert_name] = []
                    # Add relevant sub-module
                    submodule = self.SUBMODULE_SELECTION.get(expert_name, {}).get(cause)
                    if submodule and submodule not in active[expert_name]:
                        active[expert_name].append(submodule)
            
            # Check concern triggers
            if primary_concern in rules.get('concern_triggers', []):
                if expert_name not in active:
                    active[expert_name] = []
        
        # Ensure at least default sub-module for each active expert
        for expert_name in active:
            if not active[expert_name]:
                default = self.SUBMODULE_SELECTION.get(expert_name, {}).get('default')
                if default:
                    active[expert_name] = [default]
        
        logger.info(f"🎯 Active experts: {active}")
        return active
    
    async def generate_from_experts(
        self,
        focused_problem: FocusedProblem
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generate recommendations from all relevant experts in parallel.
        
        Args:
            focused_problem: The narrowed problem definition
        
        Returns:
            Dict mapping category to list of recommendations
        """
        logger.info("🚀 Starting parallel expert generation...")
        
        # Determine which experts to activate
        active_experts = self._determine_active_experts(focused_problem)
        
        # Create tasks for parallel execution
        tasks = []
        expert_names = []
        
        for expert_name, submodules in active_experts.items():
            expert = self.experts.get(expert_name)
            if expert:
                task = expert.generate_recommendations(
                    focused_problem=focused_problem,
                    active_submodules=submodules
                )
                tasks.append(task)
                expert_names.append(expert_name)
        
        # Run all experts in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect results
        expert_results = {}
        for expert_name, result in zip(expert_names, results):
            if isinstance(result, Exception):
                logger.error(f"❌ {expert_name} expert failed: {result}")
                expert_results[expert_name] = []
            else:
                expert_results[expert_name] = result
                logger.info(f"✅ {expert_name}: {len(result)} recommendations")
        
        return expert_results
    
    def synthesize(
        self,
        expert_results: Dict[str, List[Dict[str, Any]]],
        max_per_category: int = 5
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Synthesize expert outputs into final recommendation set.
        
        Handles:
        - Conflict resolution
        - Priority ranking
        - Deduplication
        - Limit enforcement
        """
        logger.info("🔄 Synthesizing expert outputs...")
        
        synthesized = {}
        
        for category, recommendations in expert_results.items():
            if not recommendations:
                synthesized[category] = []
                continue
            
            # Step 1: Resolve conflicts within category
            resolved = self._resolve_conflicts(recommendations)
            
            # Step 2: Rank by priority
            ranked = self._rank_recommendations(resolved)
            
            # Step 3: Deduplicate similar recommendations
            deduped = self._deduplicate(ranked)
            
            # Step 4: Apply limit
            synthesized[category] = deduped[:max_per_category]
            
            logger.info(f"📊 {category}: {len(recommendations)} → {len(synthesized[category])} after synthesis")
        
        return synthesized
    
    def _resolve_conflicts(
        self,
        recommendations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Resolve conflicting recommendations.
        
        Example conflicts:
        - "Avoid dairy" vs "Consume yogurt for probiotics"
        - "High-intensity exercise" vs "Rest and recovery"
        """
        # Simple implementation: flag conflicts, let evaluator handle
        # TODO: Implement sophisticated conflict resolution
        
        conflict_pairs = [
            ('avoid_dairy', 'consume_dairy'),
            ('high_intensity', 'low_intensity'),
            ('intermittent_fasting', 'regular_meals'),
        ]
        
        # For now, just return as-is with conflict flags
        for rec in recommendations:
            rec['has_conflict'] = False  # Placeholder
        
        return recommendations
    
    def _rank_recommendations(
        self,
        recommendations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Rank recommendations by priority and evidence strength"""
        
        def get_score(rec):
            score = 0
            # Evidence strength
            evidence = rec.get('evidence_strength', 'moderate')
            evidence_scores = {'strong': 3, 'moderate': 2, 'weak': 1}
            score += evidence_scores.get(evidence, 1)
            
            # Priority
            priority = rec.get('priority', 'medium')
            priority_scores = {'high': 3, 'medium': 2, 'low': 1}
            score += priority_scores.get(priority, 1)
            
            # Citation verification
            if rec.get('citation_verified'):
                score += 2
            
            # Relevance to primary concern
            score += rec.get('relevance_score', 0)
            
            return score
        
        return sorted(recommendations, key=get_score, reverse=True)
    
    def _deduplicate(
        self,
        recommendations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Remove duplicate or very similar recommendations"""
        seen_actions = set()
        unique = []
        
        for rec in recommendations:
            # Create a simplified action key for comparison
            action = rec.get('specificAction', rec.get('title', ''))
            action_key = action.lower()[:50]  # First 50 chars, lowercase
            
            if action_key not in seen_actions:
                seen_actions.add(action_key)
                unique.append(rec)
        
        return unique
