"""
Mindfulness Expert - Specialized Mental Health & Stress Management
===================================================================

Sub-modules:
- CortisolStressModule: Cortisol/stress reduction
- SleepQualityModule: Sleep improvement
- AnxietyManagementModule: Anxiety and mood support
- GeneralMindfulnessModule: General mindfulness practices
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


class CortisolStressModule(BaseExpertSubModule):
    """Cortisol and stress reduction module"""
    
    MODULE_NAME = "cortisol_stress_management"
    TARGET_ROOT_CAUSES = ['cortisol_high', 'cortisol_dysregulation', 'stress_high']
    DOMAIN_CATEGORY = "mindfulness"  # For RAG retrieval
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            'meditation cortisol reduction women',
            'stress management PCOS anxiety',
            'mindfulness hormonal balance',
        ],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    INTERVENTION_TEMPLATES = {
        'daily_meditation': {
            'title': 'Daily Meditation',
            'purpose': 'Reduce cortisol levels and activate relaxation response',
            'action': 'Practice guided meditation for 10-20 minutes daily',
            'specifics': [
                'Start with 5 minutes and gradually increase',
                'Use apps like Headspace, Calm, or Insight Timer',
                'Best done in morning or before bed',
            ],
            'mindfulness_techniques': ['guided meditation', 'mindfulness meditation'],
            'mindfulness_durations': ['10-20 min'],
            'optimal_times': ['morning', 'evening'],
            'frequency': 'daily:1',
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '4-8 weeks',
            'root_causes': ['cortisol_high'],
        },
        'deep_breathing': {
            'title': 'Deep Breathing Exercises',
            'purpose': 'Activate parasympathetic nervous system to lower cortisol',
            'action': 'Practice diaphragmatic breathing 3 times daily',
            'specifics': [
                '4-7-8 technique: inhale 4 counts, hold 7, exhale 8',
                'Box breathing: 4-4-4-4 counts',
                'Do 5-10 breath cycles per session',
            ],
            'mindfulness_techniques': ['deep breathing', 'diaphragmatic breathing'],
            'mindfulness_durations': ['3-5 min'],
            'optimal_times': ['morning', 'midday', 'evening'],
            'frequency': 'daily:3',
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '1-2 weeks',
            'root_causes': ['cortisol_high', 'stress_high'],
        },
        'progressive_muscle_relaxation': {
            'title': 'Progressive Muscle Relaxation',
            'purpose': 'Release physical tension associated with stress',
            'action': 'Practice progressive muscle relaxation before bed',
            'specifics': [
                'Systematically tense and release each muscle group',
                '15-20 minutes lying down',
                'Particularly effective for sleep',
            ],
            'mindfulness_techniques': ['progressive muscle relaxation'],
            'mindfulness_durations': ['15-20 min'],
            'optimal_times': ['evening'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '2-4 weeks',
            'root_causes': ['cortisol_high', 'insomnia'],
        },
        'cortisol_rhythm': {
            'title': 'Morning Sunlight Exposure',
            'purpose': 'Reset cortisol circadian rhythm naturally',
            'action': 'Get 10-15 minutes of morning sunlight within 1 hour of waking',
            'specifics': [
                'Step outside without sunglasses for optimal effect',
                'Even cloudy days provide beneficial light',
                'Combine with a short walk for added benefit',
            ],
            'mindfulness_techniques': ['sunlight exposure'],
            'mindfulness_durations': ['10-15 min'],
            'optimal_times': ['morning'],
            'frequency': 'daily:1',
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': '1-2 weeks',
            'root_causes': ['cortisol_dysregulation'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate cortisol/stress management recommendations with real RAG evidence"""
        logger.info(f"🧘 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Step 1: Retrieve real evidence from Pinecone
        retrieved_docs = await self.retrieve_evidence(
            topic="meditation stress cortisol women PCOS",
            focused_problem=focused_problem,
            category=self.DOMAIN_CATEGORY
        )
        citations = self._extract_citations(retrieved_docs)
        logger.info(f"📚 {self.MODULE_NAME}: Found {len(citations)} citations from research")
        
        # Prioritize deep breathing (immediate effect, no equipment)
        breath_citations = citations[:2] if citations else []
        rec = self._create_recommendation('deep_breathing', focused_problem, citations=breath_citations)
        if rec:
            rec['relevance_score'] = 1.0
            recommendations.append(rec)
        
        # Add daily meditation
        med_citations = citations[:2] if citations else []
        rec = self._create_recommendation('daily_meditation', focused_problem, citations=med_citations)
        if rec:
            recommendations.append(rec)
        
        # Add cortisol rhythm reset
        rec = self._create_recommendation('cortisol_rhythm', focused_problem, citations=citations[:1] if citations else [])
        if rec:
            recommendations.append(rec)
        
        # Add PMR for those with sleep issues
        if any('insomnia' in str(c.concern_type) for c in [focused_problem.primary_concern] + focused_problem.secondary_concerns):
            rec = self._create_recommendation('progressive_muscle_relaxation', focused_problem, citations=citations[:1] if citations else [])
            if rec:
                recommendations.append(rec)
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


class SleepQualityModule(BaseExpertSubModule):
    """Sleep improvement module"""
    
    MODULE_NAME = "sleep_quality"
    TARGET_ROOT_CAUSES = ['insomnia', 'cortisol_dysregulation', 'melatonin_disruption']
    DOMAIN_CATEGORY = "mindfulness"  # For RAG retrieval
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            'sleep quality PCOS women',
            'insomnia management mindfulness',
            'sleep hygiene hormone balance',
        ],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    INTERVENTION_TEMPLATES = {
        'sleep_hygiene': {
            'title': 'Sleep Hygiene Protocol',
            'purpose': 'Establish habits that promote quality sleep',
            'action': 'Implement a consistent sleep routine',
            'specifics': [
                'Go to bed and wake at same time daily (even weekends)',
                'Create dark, cool (65-68°F) sleeping environment',
                'Avoid screens 1-2 hours before bed',
            ],
            'mindfulness_techniques': ['sleep routine'],
            'mindfulness_durations': ['30 min wind-down'],
            'optimal_times': ['evening'],
            'frequency': 'daily:1',
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '2-4 weeks',
            'root_causes': ['insomnia'],
        },
        'bedtime_meditation': {
            'title': 'Bedtime Relaxation',
            'purpose': 'Calm the mind before sleep',
            'action': 'Practice guided sleep meditation or yoga nidra',
            'specifics': [
                'Use sleep-focused meditation apps',
                'Yoga nidra (yogic sleep) is particularly effective',
                '15-30 minutes lying in bed',
            ],
            'mindfulness_techniques': ['sleep meditation', 'yoga nidra'],
            'mindfulness_durations': ['15-30 min'],
            'optimal_times': ['bedtime'],
            'frequency': 'daily:1',
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': '2-4 weeks',
            'root_causes': ['insomnia'],
        },
        'limit_caffeine': {
            'title': 'Caffeine Management',
            'purpose': 'Prevent caffeine interference with sleep quality',
            'action': 'Limit caffeine intake and stop by early afternoon',
            'specifics': [
                'No caffeine after 2pm (or 8+ hours before bed)',
                'Limit to 1-2 cups of coffee per day',
                'Be aware of hidden caffeine in chocolate, sodas',
            ],
            'mindfulness_techniques': ['caffeine awareness'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'strong',
            'timeline': '1-2 weeks',
            'root_causes': ['insomnia', 'cortisol_high'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate sleep quality recommendations"""
        logger.info(f"🧘 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        for template_key, template in self.INTERVENTION_TEMPLATES.items():
            rec = self._create_recommendation(template_key, focused_problem)
            if rec:
                recommendations.append(rec)
        
        # High relevance if sleep is primary concern
        primary = focused_problem.primary_concern
        for rec in recommendations:
            if primary.concern_type == 'insomnia':
                rec['relevance_score'] = 1.0
            elif 'sleep' in str(primary.root_causes).lower():
                rec['relevance_score'] = 0.8
            else:
                rec['relevance_score'] = 0.5
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


class AnxietyManagementModule(BaseExpertSubModule):
    """Anxiety and mood management module"""
    
    MODULE_NAME = "anxiety_management"
    TARGET_ROOT_CAUSES = ['anxiety', 'cortisol_high', 'progesterone_low']
    
    INTERVENTION_TEMPLATES = {
        'grounding_techniques': {
            'title': 'Grounding Techniques',
            'purpose': 'Interrupt anxiety and bring focus to present moment',
            'action': 'Practice 5-4-3-2-1 grounding when feeling anxious',
            'specifics': [
                'Name 5 things you see, 4 you hear, 3 you touch, 2 you smell, 1 you taste',
                'Feel your feet on the ground',
                'Use this technique at first signs of anxiety',
            ],
            'mindfulness_techniques': ['grounding', '5-4-3-2-1 technique'],
            'mindfulness_durations': ['2-5 min'],
            'frequency': 'As needed',
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': 'Immediate',
            'root_causes': ['anxiety'],
        },
        'body_scan': {
            'title': 'Body Scan Meditation',
            'purpose': 'Develop body awareness and release tension',
            'action': 'Practice body scan meditation daily',
            'specifics': [
                'Systematically notice sensations from head to toe',
                '10-20 minute practice',
                'Good for recognizing physical signs of anxiety',
            ],
            'mindfulness_techniques': ['body scan meditation'],
            'mindfulness_durations': ['10-20 min'],
            'optimal_times': ['morning', 'evening'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '4-8 weeks',
            'root_causes': ['anxiety'],
        },
        'journaling': {
            'title': 'Anxiety Journaling',
            'purpose': 'Process anxious thoughts through writing',
            'action': 'Write for 10-15 minutes daily about thoughts and feelings',
            'specifics': [
                'Brain dump: write without filtering',
                'Identify triggers and patterns',
                'Include gratitude practice (3 things grateful for)',
            ],
            'mindfulness_techniques': ['journaling', 'gratitude practice'],
            'mindfulness_durations': ['10-15 min'],
            'optimal_times': ['morning', 'evening'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '4-8 weeks',
            'root_causes': ['anxiety', 'mood_swings'],
        },
        'cognitive_reframing': {
            'title': 'Cognitive Reframing',
            'purpose': 'Challenge anxious thought patterns',
            'action': 'Practice identifying and reframing negative thoughts',
            'specifics': [
                'Notice automatic negative thoughts',
                'Ask: Is this thought true? Is it helpful?',
                'Create alternative, balanced perspective',
            ],
            'mindfulness_techniques': ['cognitive reframing', 'thought awareness'],
            'mindfulness_durations': ['5-10 min'],
            'frequency': 'As needed',
            'priority': 'medium',
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
            'root_causes': ['anxiety'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate anxiety management recommendations"""
        logger.info(f"🧘 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Prioritize grounding (immediate, no prep needed)
        rec = self._create_recommendation('grounding_techniques', focused_problem)
        if rec:
            rec['relevance_score'] = 1.0
            recommendations.append(rec)
        
        # Add other techniques
        for template_key in ['body_scan', 'journaling', 'cognitive_reframing']:
            rec = self._create_recommendation(template_key, focused_problem)
            if rec:
                recommendations.append(rec)
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


class GeneralMindfulnessModule(BaseExpertSubModule):
    """General mindfulness practices"""
    
    MODULE_NAME = "general_mindfulness"
    TARGET_ROOT_CAUSES = ['stress_high', 'mood_swings']
    
    INTERVENTION_TEMPLATES = {
        'mindful_eating': {
            'title': 'Mindful Eating',
            'purpose': 'Improve relationship with food and digestion',
            'action': 'Practice mindful eating at least one meal per day',
            'specifics': [
                'Eat without distractions (no phone, TV)',
                'Chew thoroughly and eat slowly',
                'Notice flavors, textures, hunger/fullness cues',
            ],
            'mindfulness_techniques': ['mindful eating'],
            'mindfulness_durations': ['20-30 min per meal'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '4-8 weeks',
            'root_causes': ['stress_high'],
        },
        'digital_detox': {
            'title': 'Digital Detox Time',
            'purpose': 'Reduce stress from constant connectivity',
            'action': 'Take daily breaks from screens and social media',
            'specifics': [
                'No phones during meals',
                'Screen-free time 1 hour before bed',
                'Consider one "digital sabbath" day per week',
            ],
            'mindfulness_techniques': ['digital detox'],
            'mindfulness_durations': ['1+ hours'],
            'optimal_times': ['evening'],
            'frequency': 'daily:1',
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '2-4 weeks',
            'root_causes': ['stress_high', 'insomnia'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate general mindfulness recommendations"""
        logger.info(f"🧘 {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        for template_key, template in self.INTERVENTION_TEMPLATES.items():
            rec = self._create_recommendation(template_key, focused_problem)
            if rec:
                rec['relevance_score'] = 0.6  # Lower relevance since general
                recommendations.append(rec)
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations


# =============================================================================
# MAIN MINDFULNESS EXPERT
# =============================================================================

class MindfulnessExpert(BaseDomainExpert):
    """
    Mindfulness Domain Expert
    
    Generates evidence-based mindfulness and mental health recommendations
    using specialized sub-modules with real RAG retrieval.
    """
    
    DOMAIN_NAME = "mindfulness"
    
    def _initialize_submodules(self):
        """Initialize mindfulness sub-modules"""
        self.submodules = {
            'cortisol_stress_management': CortisolStressModule(),
            'sleep_quality': SleepQualityModule(),
            'anxiety_management': AnxietyManagementModule(),
            'general_mindfulness': GeneralMindfulnessModule(),
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
        """Generate mindfulness recommendations from relevant sub-modules."""
        logger.info(f"🧘 MindfulnessExpert: Starting recommendation generation")
        
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
        
        logger.info(f"✅ MindfulnessExpert: Generated {len(merged)} total recommendations")
        
        return merged
