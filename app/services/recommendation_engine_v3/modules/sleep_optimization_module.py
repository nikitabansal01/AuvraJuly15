"""
Sleep Optimization Module - Rest & Recovery
==========================================

Specialized module for sleep optimization with hormone-aware
recommendations for women with PCOS and hormonal imbalances.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SleepIssueType(Enum):
    """Types of sleep issues"""
    ONSET_INSOMNIA = "difficulty_falling_asleep"
    MAINTENANCE_INSOMNIA = "waking_during_night"
    EARLY_WAKING = "waking_too_early"
    NON_RESTORATIVE = "unrefreshing_sleep"
    CIRCADIAN_DISRUPTION = "shifted_sleep_cycle"
    HORMONE_RELATED = "hormone_disrupted_sleep"


@dataclass
class SleepRecommendation:
    """A sleep optimization recommendation"""
    intervention: str
    category: str  # environment, behavior, nutrition, timing
    sleep_phase_target: str  # onset, maintenance, quality, rhythm
    implementation_time: str  # immediate, 1_week, 2_weeks, ongoing
    hormone_connection: str
    evidence_level: str
    steps: List[str]
    expected_outcome: str


class SleepOptimizationModule:
    """
    Module for comprehensive sleep optimization.
    
    Addresses sleep issues with hormone-aware interventions,
    recognizing the bidirectional relationship between
    sleep and hormonal health.
    """
    
    MODULE_ID = "sleep_optimization"
    MODULE_NAME = "Sleep Optimization"
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            "sleep quality PCOS women interventions",
            "insomnia hormonal imbalance treatment",
            "circadian rhythm hormones women",
            "sleep hygiene evidence-based recommendations",
            "melatonin cortisol sleep cycle"
        ],
        'must_include_terms': [
            'sleep', 'insomnia', 'circadian', 'melatonin'
        ],
        'max_results': 15
    }
    
    # Environment optimizations
    ENVIRONMENT_INTERVENTIONS = [
        {
            'intervention': 'Temperature optimization (65-68°F / 18-20°C)',
            'phase': 'onset',
            'effect': 'Cool temperatures trigger melatonin release and sleep onset',
            'evidence': 'strong',
            'steps': [
                'Set bedroom temperature to 65-68°F (18-20°C)',
                'Use breathable bedding materials',
                'Consider a cooling mattress pad if needed'
            ]
        },
        {
            'intervention': 'Complete darkness',
            'phase': 'quality',
            'effect': 'Light exposure suppresses melatonin; darkness supports deep sleep',
            'evidence': 'strong',
            'steps': [
                'Use blackout curtains or shades',
                'Cover or remove all light sources (LEDs, electronics)',
                'Use a sleep mask if complete darkness not possible'
            ]
        },
        {
            'intervention': 'White noise or silence',
            'phase': 'maintenance',
            'effect': 'Consistent sound environment prevents awakenings',
            'evidence': 'moderate',
            'steps': [
                'Use white noise machine or fan',
                'Alternatively, use earplugs if very noise-sensitive',
                'Keep consistent throughout the night'
            ]
        }
    ]
    
    # Behavioral interventions
    BEHAVIORAL_INTERVENTIONS = [
        {
            'intervention': 'Consistent sleep-wake schedule',
            'phase': 'rhythm',
            'effect': 'Entrains circadian rhythm, optimizes cortisol/melatonin patterns',
            'evidence': 'strong',
            'hormone_impact': 'Regulates cortisol awakening response and evening melatonin',
            'steps': [
                'Set fixed wake time (same every day, including weekends)',
                'Calculate bedtime based on 7-9 hour sleep need',
                'Adjust bedtime only, keep wake time fixed'
            ]
        },
        {
            'intervention': 'Evening wind-down routine',
            'phase': 'onset',
            'effect': 'Signals brain to begin sleep preparation, lowers cortisol',
            'evidence': 'strong',
            'hormone_impact': 'Allows natural cortisol decline and melatonin rise',
            'steps': [
                'Begin 60-90 minutes before bed',
                'Dim lights throughout home',
                'Engage in calming activities: reading, gentle stretching, warm bath',
                'Avoid stimulating content (news, intense shows, work)'
            ]
        },
        {
            'intervention': 'Screen curfew 2 hours before bed',
            'phase': 'onset',
            'effect': 'Blue light from screens suppresses melatonin production',
            'evidence': 'strong',
            'hormone_impact': 'Protects natural melatonin production for sleep onset',
            'steps': [
                'Set phone/tablet to auto-disable at set time',
                'Use blue light blocking glasses if screens necessary',
                'Replace screen time with offline activities'
            ]
        },
        {
            'intervention': 'Stimulus control (bed = sleep only)',
            'phase': 'onset',
            'effect': 'Strengthens mental association between bed and sleep',
            'evidence': 'strong',
            'hormone_impact': 'Reduces bedtime anxiety/cortisol activation',
            'steps': [
                'Use bed only for sleep and intimacy',
                'If awake 20+ minutes, leave bedroom',
                'Return only when sleepy'
            ]
        }
    ]
    
    # Timing-based interventions
    TIMING_INTERVENTIONS = [
        {
            'intervention': 'Morning bright light exposure',
            'phase': 'rhythm',
            'effect': 'Sets circadian clock, promotes alertness now and sleep later',
            'evidence': 'strong',
            'hormone_impact': 'Triggers cortisol awakening response, times melatonin for evening',
            'steps': [
                'Get 10-30 minutes sunlight within 1 hour of waking',
                'On cloudy days, use 10,000 lux light box',
                'Combine with morning walk for double benefit'
            ]
        },
        {
            'intervention': 'Exercise timing optimization',
            'phase': 'quality',
            'effect': 'Exercise improves sleep quality but timing matters',
            'evidence': 'strong',
            'hormone_impact': 'Early exercise boosts cortisol appropriately; late exercise can disrupt',
            'steps': [
                'Complete vigorous exercise 4-6 hours before bed',
                'Gentle yoga/stretching OK within 2 hours of bed',
                'Morning exercise ideal for circadian alignment'
            ]
        },
        {
            'intervention': 'Caffeine cutoff by 2 PM',
            'phase': 'onset',
            'effect': 'Caffeine has 6+ hour half-life, blocks sleep-promoting adenosine',
            'evidence': 'strong',
            'hormone_impact': 'Late caffeine elevates cortisol, delays melatonin',
            'steps': [
                'No caffeine after 2 PM (or earlier if sensitive)',
                'Switch to herbal tea in afternoon',
                'Be aware of hidden caffeine (chocolate, some medications)'
            ]
        }
    ]
    
    # Nutrition for sleep
    NUTRITION_INTERVENTIONS = [
        {
            'intervention': 'Dinner timing (3+ hours before bed)',
            'phase': 'quality',
            'effect': 'Digestion interferes with deep sleep; insulin affects melatonin',
            'evidence': 'moderate',
            'steps': [
                'Finish dinner 3+ hours before bedtime',
                'If hungry near bed, small protein snack only',
                'Avoid heavy, spicy, or fatty foods in evening'
            ]
        },
        {
            'intervention': 'Magnesium supplementation',
            'phase': 'onset',
            'effect': 'Magnesium promotes GABA activity, relaxes muscles',
            'evidence': 'moderate',
            'steps': [
                '200-400mg magnesium glycinate 30-60 min before bed',
                'Can also use magnesium-rich foods: pumpkin seeds, almonds',
                'Epsom salt bath provides magnesium transdermally'
            ]
        },
        {
            'intervention': 'Tart cherry juice',
            'phase': 'onset',
            'effect': 'Natural source of melatonin and sleep-promoting compounds',
            'evidence': 'moderate',
            'steps': [
                '8 oz tart cherry juice 1-2 hours before bed',
                'Choose unsweetened variety',
                'Can substitute with tart cherry extract supplement'
            ]
        }
    ]
    
    def __init__(self, retrieval_component=None, evidence_grader=None):
        self.retrieval = retrieval_component
        self.evidence_grader = evidence_grader
    
    async def generate_recommendations(
        self,
        user_profile: Dict[str, Any],
        hormone_data: Dict[str, Any],
        focus_areas: List[str] = None
    ) -> List[SleepRecommendation]:
        """
        Generate sleep optimization recommendations.
        """
        # Identify sleep issues
        sleep_issues = self._identify_sleep_issues(user_profile)
        logger.info(f"🛏️ Sleep issues identified: {[s.value for s in sleep_issues]}")
        
        recommendations = []
        
        # Always include foundational interventions
        recommendations.extend(self._get_foundational_recs())
        
        # Add targeted interventions based on issues
        if SleepIssueType.ONSET_INSOMNIA in sleep_issues:
            recommendations.extend(self._get_onset_recs())
        
        if SleepIssueType.MAINTENANCE_INSOMNIA in sleep_issues:
            recommendations.extend(self._get_maintenance_recs())
        
        if SleepIssueType.CIRCADIAN_DISRUPTION in sleep_issues:
            recommendations.extend(self._get_rhythm_recs())
        
        if SleepIssueType.HORMONE_RELATED in sleep_issues:
            recommendations.extend(
                self._get_hormone_recs(hormone_data)
            )
        
        # Personalize based on user constraints
        recommendations = self._personalize_recs(recommendations, user_profile)
        
        return recommendations
    
    def _identify_sleep_issues(self, user_profile: Dict[str, Any]) -> List[SleepIssueType]:
        """Identify specific sleep issues from user profile"""
        issues = []
        symptoms = [s.lower() for s in user_profile.get('symptoms', [])]
        
        # Check for onset insomnia
        if any(x in str(symptoms) for x in ['fall asleep', 'falling asleep', 'cant sleep', 'insomnia']):
            issues.append(SleepIssueType.ONSET_INSOMNIA)
        
        # Check for maintenance insomnia
        if any(x in str(symptoms) for x in ['wake up', 'waking', 'stay asleep', 'interrupted']):
            issues.append(SleepIssueType.MAINTENANCE_INSOMNIA)
        
        # Check for non-restorative sleep
        if any(x in str(symptoms) for x in ['tired', 'fatigue', 'unrefreshed', 'not rested']):
            issues.append(SleepIssueType.NON_RESTORATIVE)
        
        # Check for circadian issues
        if any(x in str(symptoms) for x in ['night owl', 'shift work', 'jet lag', 'irregular schedule']):
            issues.append(SleepIssueType.CIRCADIAN_DISRUPTION)
        
        # Check for hormone-related sleep issues
        if user_profile.get('hormone_imbalance') or any(x in str(symptoms) for x in ['hot flash', 'night sweat', 'anxiety at night']):
            issues.append(SleepIssueType.HORMONE_RELATED)
        
        # Default to general if no specific issues
        if not issues:
            issues.append(SleepIssueType.NON_RESTORATIVE)
        
        return issues
    
    def _get_foundational_recs(self) -> List[SleepRecommendation]:
        """Get foundational sleep recommendations for everyone"""
        recs = []
        
        # Consistent schedule
        behavior = self.BEHAVIORAL_INTERVENTIONS[0]
        recs.append(SleepRecommendation(
            intervention=behavior['intervention'],
            category='behavior',
            sleep_phase_target=behavior['phase'],
            implementation_time='1_week',
            hormone_connection=behavior.get('hormone_impact', ''),
            evidence_level=behavior['evidence'],
            steps=behavior['steps'],
            expected_outcome='Improved sleep quality within 1-2 weeks'
        ))
        
        # Temperature
        env = self.ENVIRONMENT_INTERVENTIONS[0]
        recs.append(SleepRecommendation(
            intervention=env['intervention'],
            category='environment',
            sleep_phase_target=env['phase'],
            implementation_time='immediate',
            hormone_connection='Cool temperatures support natural melatonin production',
            evidence_level=env['evidence'],
            steps=env['steps'],
            expected_outcome='Faster sleep onset, fewer night wakings'
        ))
        
        return recs
    
    def _get_onset_recs(self) -> List[SleepRecommendation]:
        """Get recommendations for difficulty falling asleep"""
        recs = []
        
        # Wind-down routine
        behavior = self.BEHAVIORAL_INTERVENTIONS[1]
        recs.append(SleepRecommendation(
            intervention=behavior['intervention'],
            category='behavior',
            sleep_phase_target=behavior['phase'],
            implementation_time='immediate',
            hormone_connection=behavior.get('hormone_impact', ''),
            evidence_level=behavior['evidence'],
            steps=behavior['steps'],
            expected_outcome='Reduced time to fall asleep within 1 week'
        ))
        
        # Screen curfew
        behavior = self.BEHAVIORAL_INTERVENTIONS[2]
        recs.append(SleepRecommendation(
            intervention=behavior['intervention'],
            category='behavior',
            sleep_phase_target=behavior['phase'],
            implementation_time='immediate',
            hormone_connection=behavior.get('hormone_impact', ''),
            evidence_level=behavior['evidence'],
            steps=behavior['steps'],
            expected_outcome='Improved melatonin production, easier sleep onset'
        ))
        
        # Caffeine cutoff
        timing = self.TIMING_INTERVENTIONS[2]
        recs.append(SleepRecommendation(
            intervention=timing['intervention'],
            category='timing',
            sleep_phase_target=timing['phase'],
            implementation_time='immediate',
            hormone_connection=timing.get('hormone_impact', ''),
            evidence_level=timing['evidence'],
            steps=timing['steps'],
            expected_outcome='Better sleep onset within 3-5 days'
        ))
        
        return recs
    
    def _get_maintenance_recs(self) -> List[SleepRecommendation]:
        """Get recommendations for night waking"""
        recs = []
        
        # Darkness
        env = self.ENVIRONMENT_INTERVENTIONS[1]
        recs.append(SleepRecommendation(
            intervention=env['intervention'],
            category='environment',
            sleep_phase_target=env['phase'],
            implementation_time='immediate',
            hormone_connection='Light exposure during night suppresses melatonin',
            evidence_level=env['evidence'],
            steps=env['steps'],
            expected_outcome='Fewer night wakings, deeper sleep'
        ))
        
        # White noise
        env = self.ENVIRONMENT_INTERVENTIONS[2]
        recs.append(SleepRecommendation(
            intervention=env['intervention'],
            category='environment',
            sleep_phase_target=env['phase'],
            implementation_time='immediate',
            hormone_connection='Reduces stress response to environmental sounds',
            evidence_level=env['evidence'],
            steps=env['steps'],
            expected_outcome='Reduced noise-related awakenings'
        ))
        
        return recs
    
    def _get_rhythm_recs(self) -> List[SleepRecommendation]:
        """Get recommendations for circadian rhythm issues"""
        recs = []
        
        # Morning light
        timing = self.TIMING_INTERVENTIONS[0]
        recs.append(SleepRecommendation(
            intervention=timing['intervention'],
            category='timing',
            sleep_phase_target=timing['phase'],
            implementation_time='1_week',
            hormone_connection=timing.get('hormone_impact', ''),
            evidence_level=timing['evidence'],
            steps=timing['steps'],
            expected_outcome='Circadian rhythm shift within 1-2 weeks'
        ))
        
        return recs
    
    def _get_hormone_recs(self, hormone_data: Dict[str, Any]) -> List[SleepRecommendation]:
        """Get hormone-specific sleep recommendations"""
        recs = []
        
        # Magnesium for hormonal support
        nutrition = self.NUTRITION_INTERVENTIONS[1]
        recs.append(SleepRecommendation(
            intervention=nutrition['intervention'],
            category='nutrition',
            sleep_phase_target=nutrition['phase'],
            implementation_time='1_week',
            hormone_connection='Magnesium supports progesterone production and reduces cortisol',
            evidence_level=nutrition['evidence'],
            steps=nutrition['steps'],
            expected_outcome='Calmer nervous system, easier sleep onset'
        ))
        
        return recs
    
    def _personalize_recs(
        self, 
        recommendations: List[SleepRecommendation],
        user_profile: Dict[str, Any]
    ) -> List[SleepRecommendation]:
        """Personalize recommendations based on user constraints"""
        # Could filter or modify based on:
        # - Time constraints
        # - Budget constraints
        # - Living situation (can't control temperature, etc.)
        
        return recommendations
    
    def get_evidence_summary(self) -> Dict[str, Any]:
        """Get summary of evidence supporting this module"""
        return {
            'module': self.MODULE_NAME,
            'key_studies': [
                {
                    'finding': 'Sleep hygiene education improves sleep quality',
                    'pmid': '25903579',
                    'quality': 'Systematic Review'
                },
                {
                    'finding': 'Light exposure timing affects circadian rhythm',
                    'pmid': '25535358',
                    'quality': 'RCT'
                }
            ],
            'evidence_strength': 'strong'
        }
