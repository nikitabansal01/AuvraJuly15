"""
Cortisol Management Module - Stress & Adrenal Support
=====================================================

Specialized module for managing cortisol imbalances through
lifestyle interventions - both high and low cortisol patterns.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class CortisolPattern(Enum):
    """Cortisol dysregulation patterns"""
    HIGH_CHRONIC = "high_chronic"  # Chronic elevation
    HIGH_ACUTE = "high_acute"  # Acute stress response
    LOW_FATIGUE = "low_fatigue"  # Adrenal fatigue pattern
    FLAT_LINE = "flat_line"  # Loss of diurnal rhythm
    NORMAL = "normal"


@dataclass
class CortisolRecommendation:
    """A cortisol management recommendation"""
    intervention: str
    category: str  # lifestyle, nutrition, supplement, timing
    cortisol_effect: str  # reducing, supporting, rhythm_restoring
    best_time: str  # morning, afternoon, evening, throughout_day
    duration: str  # acute, short_term, long_term
    evidence_level: str
    contraindications: List[str]
    explanation: str
    implementation_steps: List[str]


class CortisolManagementModule:
    """
    Module for cortisol pattern assessment and management.
    
    Addresses:
    - High cortisol (chronic stress, anxiety)
    - Low cortisol (adrenal fatigue)
    - Disrupted cortisol rhythm
    """
    
    MODULE_ID = "cortisol_management"
    MODULE_NAME = "Cortisol & Stress Management"
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            "cortisol reduction stress management women",
            "HPA axis dysregulation PCOS interventions",
            "adrenal fatigue cortisol support",
            "circadian rhythm cortisol restoration",
            "stress reduction techniques hormonal health"
        ],
        'must_include_terms': [
            'cortisol', 'stress', 'HPA', 'adrenal'
        ],
        'max_results': 15
    }
    
    # High cortisol interventions
    HIGH_CORTISOL_INTERVENTIONS = {
        'lifestyle': [
            {
                'intervention': 'Morning sunlight exposure',
                'duration': '10-30 minutes within 1 hour of waking',
                'effect': 'Sets circadian rhythm, improves cortisol awakening response',
                'evidence': 'strong'
            },
            {
                'intervention': 'Evening blue light blocking',
                'duration': '2-3 hours before bed',
                'effect': 'Allows natural cortisol decline, improves melatonin',
                'evidence': 'moderate'
            },
            {
                'intervention': 'Consistent sleep schedule',
                'duration': 'Same wake time ±30 min, including weekends',
                'effect': 'Stabilizes HPA axis, improves cortisol rhythm',
                'evidence': 'strong'
            }
        ],
        'mind_body': [
            {
                'intervention': 'Diaphragmatic breathing',
                'duration': '5-10 minutes, 2-3x daily',
                'effect': 'Activates vagus nerve, reduces cortisol acutely',
                'evidence': 'strong'
            },
            {
                'intervention': 'Yoga nidra (yogic sleep)',
                'duration': '20-45 minutes daily or every other day',
                'effect': 'Deep relaxation, significant cortisol reduction',
                'evidence': 'strong'
            },
            {
                'intervention': 'Progressive muscle relaxation',
                'duration': '15-20 minutes before bed',
                'effect': 'Reduces physical tension, lowers evening cortisol',
                'evidence': 'moderate'
            }
        ],
        'nutrition': [
            {
                'intervention': 'Reduce caffeine after 12 PM',
                'duration': 'Ongoing lifestyle change',
                'effect': 'Prevents cortisol spikes in afternoon/evening',
                'evidence': 'strong'
            },
            {
                'intervention': 'Magnesium-rich foods or supplement',
                'duration': '200-400mg glycinate before bed',
                'effect': 'Calms nervous system, supports cortisol metabolism',
                'evidence': 'moderate'
            },
            {
                'intervention': 'Adaptogenic herbs (ashwagandha)',
                'duration': '300-600mg daily for 8+ weeks',
                'effect': 'Modulates cortisol, reduces chronic stress',
                'evidence': 'strong'
            }
        ]
    }
    
    # Low cortisol interventions
    LOW_CORTISOL_INTERVENTIONS = {
        'lifestyle': [
            {
                'intervention': 'Gentle movement (not intense exercise)',
                'duration': '20-30 minutes walking, swimming',
                'effect': 'Supports adrenal recovery without additional stress',
                'evidence': 'moderate'
            },
            {
                'intervention': 'Rest before fatigue',
                'duration': 'Schedule rest breaks throughout day',
                'effect': 'Prevents adrenal crashes, conserves energy',
                'evidence': 'moderate'
            },
            {
                'intervention': 'Earlier bedtime',
                'duration': 'In bed by 10 PM',
                'effect': 'Aligns with natural cortisol rhythm, supports recovery',
                'evidence': 'moderate'
            }
        ],
        'nutrition': [
            {
                'intervention': 'Regular balanced meals',
                'duration': 'Every 3-4 hours with protein',
                'effect': 'Maintains blood sugar, prevents cortisol spikes',
                'evidence': 'strong'
            },
            {
                'intervention': 'Salt intake (Celtic/Himalayan)',
                'duration': 'Add to meals if craving salt',
                'effect': 'Supports adrenal function, replaces lost minerals',
                'evidence': 'moderate'
            },
            {
                'intervention': 'Vitamin C rich foods',
                'duration': '500-1000mg daily from food/supplements',
                'effect': 'Adrenals use significant vitamin C',
                'evidence': 'moderate'
            },
            {
                'intervention': 'B-vitamin complex',
                'duration': 'With breakfast daily',
                'effect': 'Essential for adrenal hormone production',
                'evidence': 'moderate'
            }
        ]
    }
    
    def __init__(self, retrieval_component=None, evidence_grader=None):
        self.retrieval = retrieval_component
        self.evidence_grader = evidence_grader
    
    async def generate_recommendations(
        self,
        user_profile: Dict[str, Any],
        hormone_data: Dict[str, Any],
        focus_areas: List[str] = None
    ) -> List[CortisolRecommendation]:
        """
        Generate cortisol management recommendations.
        """
        # Assess cortisol pattern
        pattern = self._assess_cortisol_pattern(hormone_data, user_profile)
        logger.info(f"📊 Cortisol pattern assessed: {pattern.value}")
        
        recommendations = []
        
        if pattern in [CortisolPattern.HIGH_CHRONIC, CortisolPattern.HIGH_ACUTE]:
            recommendations.extend(
                self._create_high_cortisol_recs(user_profile, pattern)
            )
        elif pattern in [CortisolPattern.LOW_FATIGUE, CortisolPattern.FLAT_LINE]:
            recommendations.extend(
                self._create_low_cortisol_recs(user_profile, pattern)
            )
        else:
            # Normal - maintenance recommendations
            recommendations.extend(
                self._create_maintenance_recs(user_profile)
            )
        
        return recommendations
    
    def _assess_cortisol_pattern(
        self, 
        hormone_data: Dict[str, Any],
        user_profile: Dict[str, Any]
    ) -> CortisolPattern:
        """Assess cortisol dysregulation pattern"""
        cortisol_data = hormone_data.get('cortisol', {})
        symptoms = user_profile.get('symptoms', [])
        
        # Check for high cortisol indicators
        high_indicators = [
            'anxiety', 'insomnia', 'weight_gain_abdomen',
            'high_blood_pressure', 'racing_thoughts'
        ]
        high_count = sum(1 for s in symptoms if any(h in s.lower() for h in high_indicators))
        
        # Check for low cortisol indicators
        low_indicators = [
            'fatigue', 'exhaustion', 'salt_craving',
            'low_blood_pressure', 'brain_fog', 'crash_afternoon'
        ]
        low_count = sum(1 for s in symptoms if any(l in s.lower() for l in low_indicators))
        
        # Use hormone data if available
        if cortisol_data.get('level') == 'high':
            return CortisolPattern.HIGH_CHRONIC
        elif cortisol_data.get('level') == 'low':
            return CortisolPattern.LOW_FATIGUE
        
        # Symptom-based assessment
        if high_count >= 2:
            return CortisolPattern.HIGH_CHRONIC
        elif low_count >= 2:
            return CortisolPattern.LOW_FATIGUE
        
        return CortisolPattern.NORMAL
    
    def _create_high_cortisol_recs(
        self, 
        user_profile: Dict[str, Any],
        pattern: CortisolPattern
    ) -> List[CortisolRecommendation]:
        """Create recommendations for high cortisol"""
        recs = []
        
        # Add lifestyle interventions
        for intervention in self.HIGH_CORTISOL_INTERVENTIONS['lifestyle']:
            recs.append(CortisolRecommendation(
                intervention=intervention['intervention'],
                category='lifestyle',
                cortisol_effect='reducing',
                best_time='morning' if 'morning' in intervention['intervention'].lower() else 'evening',
                duration='long_term',
                evidence_level=intervention['evidence'],
                contraindications=[],
                explanation=intervention['effect'],
                implementation_steps=[
                    f"Start with {intervention['duration']}",
                    "Track how you feel before and after",
                    "Adjust timing based on your schedule"
                ]
            ))
        
        # Add mind-body practices
        for intervention in self.HIGH_CORTISOL_INTERVENTIONS['mind_body']:
            recs.append(CortisolRecommendation(
                intervention=intervention['intervention'],
                category='mind_body',
                cortisol_effect='reducing',
                best_time='throughout_day',
                duration='short_term',
                evidence_level=intervention['evidence'],
                contraindications=[],
                explanation=intervention['effect'],
                implementation_steps=[
                    f"Practice for {intervention['duration']}",
                    "Use guided apps like Insight Timer or Calm",
                    "Be consistent - daily practice shows best results"
                ]
            ))
        
        # Add select nutrition interventions
        time_available = user_profile.get('time_available_minutes', 30)
        if time_available >= 15:  # Only if they have time for prep
            for intervention in self.HIGH_CORTISOL_INTERVENTIONS['nutrition'][:2]:
                recs.append(CortisolRecommendation(
                    intervention=intervention['intervention'],
                    category='nutrition',
                    cortisol_effect='reducing',
                    best_time='evening' if 'bed' in intervention['intervention'].lower() else 'morning',
                    duration='long_term',
                    evidence_level=intervention['evidence'],
                    contraindications=self._get_contraindications(intervention['intervention']),
                    explanation=intervention['effect'],
                    implementation_steps=[
                        intervention['duration'],
                        "Start with smallest effective dose",
                        "Monitor for any side effects"
                    ]
                ))
        
        return recs
    
    def _create_low_cortisol_recs(
        self, 
        user_profile: Dict[str, Any],
        pattern: CortisolPattern
    ) -> List[CortisolRecommendation]:
        """Create recommendations for low cortisol/adrenal fatigue"""
        recs = []
        
        # Lifestyle - gentler approach
        for intervention in self.LOW_CORTISOL_INTERVENTIONS['lifestyle']:
            recs.append(CortisolRecommendation(
                intervention=intervention['intervention'],
                category='lifestyle',
                cortisol_effect='supporting',
                best_time='throughout_day',
                duration='long_term',
                evidence_level=intervention['evidence'],
                contraindications=[],
                explanation=intervention['effect'],
                implementation_steps=[
                    intervention['duration'],
                    "Listen to your body - rest when needed",
                    "Avoid pushing through exhaustion"
                ]
            ))
        
        # Nutrition support
        for intervention in self.LOW_CORTISOL_INTERVENTIONS['nutrition']:
            recs.append(CortisolRecommendation(
                intervention=intervention['intervention'],
                category='nutrition',
                cortisol_effect='supporting',
                best_time='morning',
                duration='long_term',
                evidence_level=intervention['evidence'],
                contraindications=self._get_contraindications(intervention['intervention']),
                explanation=intervention['effect'],
                implementation_steps=[
                    intervention['duration'],
                    "Consistency is key for adrenal recovery",
                    "May take 3-6 months to see full benefits"
                ]
            ))
        
        return recs
    
    def _create_maintenance_recs(
        self, 
        user_profile: Dict[str, Any]
    ) -> List[CortisolRecommendation]:
        """Create maintenance recommendations for normal cortisol"""
        return [
            CortisolRecommendation(
                intervention='Maintain regular sleep schedule',
                category='lifestyle',
                cortisol_effect='rhythm_restoring',
                best_time='evening',
                duration='long_term',
                evidence_level='strong',
                contraindications=[],
                explanation='Consistent sleep timing maintains healthy HPA axis function',
                implementation_steps=[
                    'Wake at same time daily, including weekends',
                    'Aim for 7-9 hours of sleep',
                    'Create a relaxing bedtime routine'
                ]
            ),
            CortisolRecommendation(
                intervention='Daily stress management practice',
                category='mind_body',
                cortisol_effect='reducing',
                best_time='throughout_day',
                duration='long_term',
                evidence_level='strong',
                contraindications=[],
                explanation='Preventive stress management maintains cortisol health',
                implementation_steps=[
                    'Choose one practice: meditation, breathing, yoga',
                    'Start with 5-10 minutes daily',
                    'Build up gradually as habit forms'
                ]
            )
        ]
    
    def _get_contraindications(self, intervention: str) -> List[str]:
        """Get contraindications for specific interventions"""
        contraindications = {
            'ashwagandha': ['pregnancy', 'thyroid medication', 'autoimmune conditions'],
            'magnesium': ['kidney disease', 'certain heart medications'],
            'caffeine': [],
            'salt': ['high blood pressure', 'kidney disease']
        }
        
        for key, contra in contraindications.items():
            if key.lower() in intervention.lower():
                return contra
        
        return []
    
    def get_evidence_summary(self) -> Dict[str, Any]:
        """Get summary of evidence supporting this module"""
        return {
            'module': self.MODULE_NAME,
            'key_studies': [
                {
                    'finding': 'Ashwagandha significantly reduces cortisol levels',
                    'pmid': '23439798',
                    'quality': 'RCT'
                },
                {
                    'finding': 'Yoga reduces cortisol in women with stress',
                    'pmid': '28986866',
                    'quality': 'Systematic Review'
                },
                {
                    'finding': 'Sleep consistency improves HPA axis function',
                    'pmid': '25450058',
                    'quality': 'Cohort Study'
                }
            ],
            'evidence_strength': 'strong'
        }
