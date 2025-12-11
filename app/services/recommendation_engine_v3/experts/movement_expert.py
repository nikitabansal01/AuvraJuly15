"""
Movement Expert - Specialized Exercise & Physical Activity Recommendations
==========================================================================

Sub-modules:
- PCOSGeneralExerciseModule: General PCOS exercise guidelines
- WeightManagementExerciseModule: Weight loss focused
- HormoneSyncedWorkoutModule: Cycle-phase based exercise
- StressReliefExerciseModule: Low-cortisol exercises
"""

from typing import List, Dict, Any
import asyncio
import logging

from app.services.recommendation_engine_v3.experts.base_expert import (
    BaseDomainExpert,
    BaseExpertSubModule
)
from app.services.recommendation_engine_v3.core.problem_narrower import FocusedProblem

logger = logging.getLogger(__name__)


class PCOSGeneralExerciseModule(BaseExpertSubModule):
    """General PCOS exercise recommendations"""
    
    MODULE_NAME = "pcos_general_exercise"
    TARGET_ROOT_CAUSES = ['androgen_high', 'insulin_resistance', 'weight_related']
    DOMAIN_CATEGORY = "movement"  # For RAG retrieval
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            'exercise PCOS insulin resistance women',
            'physical activity polycystic ovary syndrome',
            'aerobic exercise hormone balance women',
        ],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    INTERVENTION_TEMPLATES = {
        'moderate_aerobic': {
            'title': 'Moderate Aerobic Exercise',
            'purpose': 'Improve insulin sensitivity and cardiovascular health',
            'action': 'Perform 150 minutes of moderate-intensity aerobic exercise per week',
            'specifics': [
                '30 minutes of brisk walking, cycling, or swimming 5 times per week',
                'Maintain heart rate at 50-70% of maximum',
                'Examples: brisk walking, cycling, swimming, dancing',
            ],
            'exercise_types': ['brisk walking', 'cycling', 'swimming'],
            'exercise_durations': ['30 min'],
            'exercise_intensities': ['moderate'],
            'frequency': 'weekly:5',
            'frequency_detail': 'weekly:5',
            'optimal_times': ['afternoon'],  # Afternoon is ideal for cardio
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
            'root_causes': ['insulin_resistance'],
        },
        'strength_training': {
            'title': 'Resistance Training',
            'purpose': 'Build muscle mass to improve metabolism and insulin sensitivity',
            'action': 'Perform strength training 2-3 times per week',
            'specifics': [
                'Target all major muscle groups',
                'Use weights, resistance bands, or bodyweight exercises',
                '2-3 sets of 8-12 repetitions per exercise',
            ],
            'exercise_types': ['weight training', 'resistance bands', 'bodyweight exercises'],
            'exercise_durations': ['30-45 min'],
            'exercise_intensities': ['moderate'],
            'frequency': 'weekly:3',
            'frequency_detail': 'weekly:3',
            'optimal_times': ['evening'],  # Evening is good for strength training
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
            'root_causes': ['insulin_resistance', 'weight_related'],
        },
        'yoga_pcos': {
            'title': 'Yoga for PCOS',
            'purpose': 'Reduce stress hormones while improving flexibility and strength',
            'action': 'Practice yoga 2-3 times per week for 30-45 minutes',
            'specifics': [
                'Focus on poses that stimulate pelvic area',
                'Include breathing exercises (pranayama)',
                'Beneficial poses: butterfly, bridge, cobra, child\'s pose',
            ],
            'exercise_types': ['yoga'],
            'exercise_durations': ['30-45 min'],
            'exercise_intensities': ['low to moderate'],
            'frequency': 'weekly:3',
            'frequency_detail': 'weekly:3',
            'optimal_times': ['morning', 'evening'],  # Yoga is flexible
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '8-12 weeks',
            'root_causes': ['cortisol_high', 'androgen_high'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate PCOS exercise recommendations with real RAG evidence"""
        logger.info(f"🏃 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Step 1: Retrieve real evidence from Pinecone
        retrieved_docs = await self.retrieve_evidence(
            topic="exercise PCOS insulin resistance",
            focused_problem=focused_problem,
            category=self.DOMAIN_CATEGORY
        )
        citations = self._extract_citations(retrieved_docs)
        logger.info(f"📚 {self.MODULE_NAME}: Found {len(citations)} citations from research")
        
        # Step 2: Generate recommendations from templates with real citations
        for template_key, template in self.INTERVENTION_TEMPLATES.items():
            rec_citations = citations[:2] if citations else []  # Use top 2 citations
            rec = self._create_recommendation(template_key, focused_problem, citations=rec_citations)
            if rec:
                recommendations.append(rec)
        
        # Adjust for physical constraints
        for constraint in focused_problem.constraints:
            if constraint.constraint_type == 'physical':
                for rec in recommendations:
                    if 'high intensity' in rec.get('exercise_intensities', []):
                        rec['constraint_warning'] = f'Modify due to: {constraint.description}'
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


class WeightManagementExerciseModule(BaseExpertSubModule):
    """Weight management focused exercise"""
    
    MODULE_NAME = "weight_management_exercise"
    TARGET_ROOT_CAUSES = ['weight_related', 'insulin_resistance', 'leptin_resistance']
    DOMAIN_CATEGORY = "movement"  # For RAG retrieval
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            'exercise weight loss PCOS women',
            'HIIT training metabolic health',
            'resistance training insulin sensitivity',
        ],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    INTERVENTION_TEMPLATES = {
        'hiit_training': {
            'title': 'High-Intensity Interval Training (HIIT)',
            'purpose': 'Maximize calorie burn and improve insulin sensitivity efficiently',
            'action': 'Perform HIIT workouts 2-3 times per week',
            'specifics': [
                '20-30 minute sessions with alternating high/low intensity',
                '30 seconds high intensity, 60-90 seconds recovery',
                'Examples: sprint intervals, cycling intervals, bodyweight HIIT',
            ],
            'exercise_types': ['HIIT', 'interval training'],
            'exercise_durations': ['20-30 min'],
            'exercise_intensities': ['high'],
            'frequency': 'weekly:3',
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
            'contraindications': ['Start with moderate intensity if new to exercise'],
            'root_causes': ['weight_related', 'insulin_resistance'],
        },
        'daily_walking': {
            'title': 'Daily Walking',
            'purpose': 'Increase daily energy expenditure and improve metabolic health',
            'action': 'Walk at least 10,000 steps daily',
            'specifics': [
                'Break into multiple walks if needed (morning, lunch, evening)',
                'Include 2-3 brisk walking sessions of 10+ minutes',
                'Use stairs instead of elevator when possible',
            ],
            'exercise_types': ['walking'],
            'exercise_durations': ['30-60 min total'],
            'exercise_intensities': ['low to moderate'],
            'frequency': 'daily:1',
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '4-8 weeks',
            'root_causes': ['weight_related'],
        },
        'metabolic_conditioning': {
            'title': 'Metabolic Conditioning',
            'purpose': 'Combine strength and cardio for maximum metabolic benefit',
            'action': 'Perform circuit training 2-3 times per week',
            'specifics': [
                'Combine strength exercises with minimal rest between sets',
                '8-10 exercises, 45-60 seconds each, circuit format',
                'Include compound movements (squats, lunges, push-ups)',
            ],
            'exercise_types': ['circuit training', 'compound exercises'],
            'exercise_durations': ['30-40 min'],
            'exercise_intensities': ['moderate to high'],
            'frequency': 'weekly:3',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '8-12 weeks',
            'root_causes': ['weight_related', 'insulin_resistance'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate weight management exercise recommendations with real RAG evidence"""
        logger.info(f"🏃 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Step 1: Retrieve real evidence from Pinecone
        retrieved_docs = await self.retrieve_evidence(
            topic="exercise weight loss PCOS metabolic",
            focused_problem=focused_problem,
            category=self.DOMAIN_CATEGORY
        )
        citations = self._extract_citations(retrieved_docs)
        logger.info(f"📚 {self.MODULE_NAME}: Found {len(citations)} citations from research")
        
        # Always include daily walking (accessible to all)
        walk_citations = citations[:2] if citations else []
        rec = self._create_recommendation('daily_walking', focused_problem, citations=walk_citations)
        if rec:
            rec['relevance_score'] = 1.0
            recommendations.append(rec)
        
        # Add HIIT and metabolic conditioning based on fitness level
        activity_level = focused_problem.lifestyle_context.get('activity_level', 'moderate')
        
        if activity_level in ['moderate', 'high']:
            for template_key in ['hiit_training', 'metabolic_conditioning']:
                rec_citations = citations[:2] if citations else []
                rec = self._create_recommendation(template_key, focused_problem, citations=rec_citations)
                if rec:
                    recommendations.append(rec)
        else:
            # For lower activity levels, recommend building up gradually
            rec_citations = citations[:2] if citations else []
            rec = self._create_recommendation('hiit_training', focused_problem, citations=rec_citations)
            if rec:
                rec['specificAction'] = 'Start with low-intensity interval training, gradually build up to HIIT'
                rec['priority'] = 'medium'
                recommendations.append(rec)
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


class HormoneSyncedWorkoutModule(BaseExpertSubModule):
    """Cycle-phase synchronized exercise recommendations"""
    
    MODULE_NAME = "hormone_synced_workout"
    TARGET_ROOT_CAUSES = ['hormone_fluctuation', 'estrogen_fluctuation', 'progesterone_low']
    
    PHASE_TEMPLATES = {
        'menstrual': {
            'title': 'Menstrual Phase Exercise',
            'purpose': 'Support recovery during menstruation with gentle movement',
            'action': 'Practice gentle yoga, walking, or light stretching',
            'specifics': [
                'Focus on restorative and yin yoga',
                'Gentle walks of 20-30 minutes',
                'Listen to your body - rest if needed',
            ],
            'exercise_types': ['gentle yoga', 'walking', 'stretching'],
            'exercise_durations': ['20-30 min'],
            'exercise_intensities': ['low'],
            'optimal_times': ['morning', 'evening'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'moderate',
        },
        'follicular': {
            'title': 'Follicular Phase Exercise',
            'purpose': 'Take advantage of rising energy with higher intensity workouts',
            'action': 'Increase workout intensity - try new challenging exercises',
            'specifics': [
                'Best time for HIIT and strength training',
                'Energy and recovery are optimal',
                'Good time to try new workouts or increase weights',
            ],
            'exercise_types': ['HIIT', 'strength training', 'cardio'],
            'exercise_durations': ['30-45 min'],
            'exercise_intensities': ['moderate to high'],
            'frequency': 'weekly:5',
            'priority': 'high',
            'evidence_strength': 'moderate',
        },
        'ovulatory': {
            'title': 'Ovulatory Phase Exercise',
            'purpose': 'Maximize peak energy with intense workouts',
            'action': 'Perform high-intensity workouts during peak energy',
            'specifics': [
                'Peak performance window',
                'Great for personal records and competitions',
                'High-energy group classes work well',
            ],
            'exercise_types': ['high-intensity cardio', 'heavy lifting', 'sports'],
            'exercise_durations': ['45-60 min'],
            'exercise_intensities': ['high'],
            'frequency': 'weekly:5',
            'priority': 'high',
            'evidence_strength': 'moderate',
        },
        'luteal': {
            'title': 'Luteal Phase Exercise',
            'purpose': 'Adjust intensity as energy decreases approaching menstruation',
            'action': 'Focus on moderate steady-state cardio and strength maintenance',
            'specifics': [
                'Reduce high-intensity sessions',
                'Focus on pilates, swimming, moderate weight training',
                'Increase rest days as period approaches',
            ],
            'exercise_types': ['pilates', 'swimming', 'moderate strength training'],
            'exercise_durations': ['30-40 min'],
            'exercise_intensities': ['moderate'],
            'frequency': 'weekly:4',
            'priority': 'medium',
            'evidence_strength': 'moderate',
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate cycle-synced exercise recommendations"""
        logger.info(f"🏃 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Get current cycle phase if available
        current_phase = focused_problem.lifestyle_context.get('cycle_phase', 'unknown')
        
        if current_phase != 'unknown' and current_phase in self.PHASE_TEMPLATES:
            # Generate recommendation for current phase
            template = self.PHASE_TEMPLATES[current_phase]
            rec = {
                'title': template['title'],
                'purpose': template['purpose'],
                'specificAction': template['action'],
                'specifics': template.get('specifics', []),
                'exercise_types': template.get('exercise_types', []),
                'exercise_durations': template.get('exercise_durations', []),
                'exercise_intensities': template.get('exercise_intensities', []),
                'frequency': template.get('frequency', 'daily:1'),
                'priority': template.get('priority', 'medium'),
                'evidence_strength': template.get('evidence_strength', 'moderate'),
                'module_source': self.MODULE_NAME,
                'citation_verified': False,
                'confidence': 0.6,
                'root_causes_addressed': self.TARGET_ROOT_CAUSES,
                'relevance_score': 0.8,
            }
            recommendations.append(rec)
        
        # Always add general cycle-syncing education
        recommendations.append({
            'title': 'Cycle-Synced Exercise',
            'purpose': 'Optimize workouts based on menstrual cycle phases',
            'specificAction': 'Adjust exercise intensity based on your cycle phase: gentle during menstruation, high intensity during follicular/ovulatory, moderate during luteal',
            'frequency': 'Ongoing',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'module_source': self.MODULE_NAME,
            'citation_verified': False,
            'confidence': 0.6,
            'root_causes_addressed': self.TARGET_ROOT_CAUSES,
            'relevance_score': 0.7,
        })
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


class StressReliefExerciseModule(BaseExpertSubModule):
    """Low-cortisol stress relief exercise"""
    
    MODULE_NAME = "stress_relief_exercise"
    TARGET_ROOT_CAUSES = ['cortisol_high', 'cortisol_dysregulation', 'stress_high']
    
    INTERVENTION_TEMPLATES = {
        'restorative_yoga': {
            'title': 'Restorative Yoga',
            'purpose': 'Activate parasympathetic nervous system to reduce cortisol',
            'action': 'Practice restorative or yin yoga 2-3 times per week',
            'specifics': [
                'Hold poses for 3-5 minutes with props',
                'Focus on deep, slow breathing',
                'Best done in evening to promote sleep',
            ],
            'exercise_types': ['restorative yoga', 'yin yoga'],
            'exercise_durations': ['30-45 min'],
            'exercise_intensities': ['low'],
            'optimal_times': ['evening'],
            'frequency': 'weekly:3',
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': '4-8 weeks',
            'root_causes': ['cortisol_high'],
        },
        'nature_walks': {
            'title': 'Nature Walks',
            'purpose': 'Reduce stress through gentle movement in natural settings',
            'action': 'Take 20-30 minute walks in nature daily',
            'specifics': [
                'Walk in parks, trails, or green spaces',
                'Practice mindful awareness of surroundings',
                'Morning sunlight exposure helps regulate cortisol rhythm',
            ],
            'exercise_types': ['walking', 'nature walks'],
            'exercise_durations': ['20-30 min'],
            'exercise_intensities': ['low'],
            'optimal_times': ['morning'],
            'frequency': 'daily:1',
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': '2-4 weeks',
            'root_causes': ['cortisol_high', 'cortisol_dysregulation'],
        },
        'tai_chi': {
            'title': 'Tai Chi / Qigong',
            'purpose': 'Gentle movement meditation to calm the nervous system',
            'action': 'Practice Tai Chi or Qigong 15-20 minutes daily',
            'specifics': [
                'Slow, flowing movements with deep breathing',
                'Focus on balance and body awareness',
                'Many free videos available for beginners',
            ],
            'exercise_types': ['tai chi', 'qigong'],
            'exercise_durations': ['15-20 min'],
            'exercise_intensities': ['low'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '4-8 weeks',
            'root_causes': ['cortisol_high'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate stress relief exercise recommendations"""
        logger.info(f"🏃 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        for template_key, template in self.INTERVENTION_TEMPLATES.items():
            rec = self._create_recommendation(template_key, focused_problem)
            if rec:
                recommendations.append(rec)
        
        # Prioritize based on stress level
        stress_level = focused_problem.lifestyle_context.get('stress_level', 'moderate')
        for rec in recommendations:
            if stress_level == 'high':
                rec['relevance_score'] = 1.0
            elif stress_level == 'moderate':
                rec['relevance_score'] = 0.7
            else:
                rec['relevance_score'] = 0.5
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


# =============================================================================
# MAIN MOVEMENT EXPERT
# =============================================================================

class MovementExpert(BaseDomainExpert):
    """
    Movement Domain Expert
    
    Generates evidence-based exercise recommendations using specialized
    sub-modules with real RAG retrieval for evidence.
    """
    
    DOMAIN_NAME = "movement"
    
    def _initialize_submodules(self):
        """Initialize movement sub-modules"""
        self.submodules = {
            'pcos_general_exercise': PCOSGeneralExerciseModule(),
            'weight_management_exercise': WeightManagementExerciseModule(),
            'hormone_synced_workout': HormoneSyncedWorkoutModule(),
            'stress_relief_exercise': StressReliefExerciseModule(),
        }
        # Pass retrieval to submodules if available
        if self.retrieval:
            for submodule in self.submodules.values():
                submodule.set_retrieval(self.retrieval)
    
    async def generate_recommendations(
        self,
        focused_problem: FocusedProblem,
        active_submodules: List[str] = None
    ) -> List[Dict[str, Any]]:
        """Generate movement recommendations from relevant sub-modules."""
        logger.info(f"🏃 MovementExpert: Starting recommendation generation")
        
        selected_modules = self._select_submodules(focused_problem, active_submodules)
        logger.info(f"   Selected modules: {[m.MODULE_NAME for m in selected_modules]}")
        
        tasks = [module.generate(focused_problem) for module in selected_modules]
        module_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_recommendations = []
        for module, result in zip(selected_modules, module_results):
            if isinstance(result, Exception):
                logger.error(f"❌ {module.MODULE_NAME} failed: {result}")
            else:
                all_recommendations.extend(result)
        
        merged = self._merge_recommendations([all_recommendations])
        
        logger.info(f"✅ MovementExpert: Generated {len(merged)} total recommendations")
        
        return merged
