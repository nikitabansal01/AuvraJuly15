"""
Problem Focus Narrower - User Problem Narrowing Layer
=====================================================

This module narrows down user's broad health concerns into specific, 
actionable problem focuses before generating recommendations.

Key insight: Generic recommendations feel "not on point" because they
try to address everything at once. By narrowing focus first, we generate
targeted, relevant recommendations.
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ConcernPriority(Enum):
    """Priority levels for user concerns"""
    PRIMARY = "primary"
    SECONDARY = "secondary"
    BACKGROUND = "background"


class UrgencyLevel(Enum):
    """Urgency levels for addressing concerns"""
    HIGH = "high"        # Affecting daily life significantly
    MEDIUM = "medium"    # Bothersome but manageable
    LOW = "low"          # Minor concern, preventive focus


@dataclass
class UserConcern:
    """Represents a specific user concern with mapped root causes"""
    concern_type: str           # e.g., "weight_gain", "irregular_periods"
    user_description: str       # How user described it
    root_causes: List[str]      # Mapped hormone/metabolic causes
    priority: ConcernPriority
    urgency: UrgencyLevel
    success_metrics: List[str]  # How to measure improvement


@dataclass
class Constraint:
    """User constraints that affect recommendations"""
    constraint_type: str        # dietary, time, physical, medical
    description: str
    impacts: List[str]          # What categories it impacts


@dataclass
class FocusedProblem:
    """
    The narrowed, focused problem definition for recommendation generation.
    
    This is the OUTPUT of the Problem Narrower - a focused problem definition
    that guides all downstream expert modules.
    """
    primary_concern: UserConcern
    secondary_concerns: List[UserConcern] = field(default_factory=list)
    constraints: List[Constraint] = field(default_factory=list)
    hormone_priorities: Dict[str, float] = field(default_factory=dict)
    lifestyle_context: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'primary_concern': {
                'type': self.primary_concern.concern_type,
                'description': self.primary_concern.user_description,
                'root_causes': self.primary_concern.root_causes,
                'priority': self.primary_concern.priority.value,
                'urgency': self.primary_concern.urgency.value,
                'success_metrics': self.primary_concern.success_metrics
            },
            'secondary_concerns': [
                {
                    'type': c.concern_type,
                    'root_causes': c.root_causes
                } for c in self.secondary_concerns
            ],
            'constraints': [
                {
                    'type': c.constraint_type,
                    'description': c.description
                } for c in self.constraints
            ],
            'hormone_priorities': self.hormone_priorities,
        }
    
    def get_all_root_causes(self) -> List[str]:
        """Get all root causes from primary and secondary concerns"""
        causes = list(self.primary_concern.root_causes)
        for concern in self.secondary_concerns:
            causes.extend(concern.root_causes)
        return list(set(causes))  # Deduplicate
    
    def get_priority_hormones(self) -> List[str]:
        """Get hormones sorted by priority"""
        return sorted(
            self.hormone_priorities.keys(),
            key=lambda h: self.hormone_priorities[h],
            reverse=True
        )


class ProblemFocusNarrower:
    """
    Narrows user's broad health profile into a focused problem definition.
    
    This is the KEY component that addresses the feedback:
    "Should we start off with a narrowed user problem focus?"
    
    Instead of generating generic recommendations for all symptoms,
    we identify the PRIMARY concern and focus recommendations on that.
    """
    
    # Mapping from user symptoms/concerns to underlying root causes
    CONCERN_TO_ROOT_CAUSE_MAP = {
        # Weight-related
        'weight_gain': ['insulin_resistance', 'cortisol_dysregulation', 'thyroid_low'],
        'difficulty_losing_weight': ['insulin_resistance', 'cortisol_high', 'leptin_resistance'],
        'stubborn_belly_fat': ['insulin_resistance', 'cortisol_high'],
        
        # Period-related
        'irregular_periods': ['androgen_high', 'progesterone_low', 'insulin_resistance'],
        'missing_periods': ['androgen_high', 'estrogen_low', 'stress_high'],
        'painful_periods': ['prostaglandin_imbalance', 'inflammation', 'estrogen_dominance'],
        'heavy_periods': ['estrogen_dominance', 'progesterone_low'],
        'light_periods': ['estrogen_low', 'thyroid_low'],
        
        # Skin/Hair
        'acne': ['androgen_high', 'insulin_resistance', 'inflammation'],
        'hirsutism': ['androgen_high', 'insulin_resistance'],
        'hair_loss': ['androgen_high', 'thyroid_low', 'iron_deficiency'],
        
        # Energy/Mood
        'fatigue': ['thyroid_low', 'cortisol_dysregulation', 'iron_deficiency'],
        'low_energy': ['thyroid_low', 'blood_sugar_instability', 'cortisol_burnout'],
        'mood_swings': ['progesterone_low', 'estrogen_fluctuation', 'blood_sugar_instability'],
        'anxiety': ['cortisol_high', 'progesterone_low', 'inflammation'],
        'depression': ['estrogen_low', 'thyroid_low', 'inflammation'],
        
        # Reproductive
        'fertility_concerns': ['androgen_high', 'progesterone_low', 'insulin_resistance'],
        'pms': ['progesterone_low', 'estrogen_dominance', 'magnesium_deficiency'],
        'hot_flashes': ['estrogen_low', 'progesterone_low'],
        
        # Sleep
        'insomnia': ['cortisol_high', 'progesterone_low', 'melatonin_disruption'],
        'night_sweats': ['estrogen_low', 'cortisol_dysregulation'],
        
        # Digestive
        'bloating': ['inflammation', 'gut_dysbiosis', 'progesterone_fluctuation'],
    }
    
    # Success metrics for each concern type
    SUCCESS_METRICS = {
        'weight_gain': ['Weight loss of 5-10% in 12 weeks', 'Reduced waist circumference'],
        'irregular_periods': ['Regular cycles within 3-6 months', 'Predictable period timing'],
        'acne': ['Reduced breakouts within 8-12 weeks', 'Clearer skin'],
        'fatigue': ['Improved energy levels within 4-6 weeks', 'Better sleep quality'],
        'fertility_concerns': ['Improved cycle regularity', 'Better ovulation signs'],
        'anxiety': ['Reduced anxiety symptoms', 'Better stress management'],
        'hair_loss': ['Reduced shedding within 3-6 months', 'New hair growth'],
    }
    
    # Focus mode to concern type mapping
    FOCUS_MODE_MAP = {
        'weight': ['weight_gain', 'difficulty_losing_weight', 'stubborn_belly_fat'],
        'fertility': ['fertility_concerns', 'irregular_periods', 'missing_periods'],
        'acne': ['acne', 'hirsutism'],
        'energy': ['fatigue', 'low_energy'],
        'mood': ['mood_swings', 'anxiety', 'depression'],
        'periods': ['irregular_periods', 'painful_periods', 'heavy_periods'],
    }
    
    def __init__(self):
        logger.info("🎯 ProblemFocusNarrower initialized")
    
    def narrow_focus(
        self,
        user_profile,
        focus_mode: str = "auto"
    ) -> FocusedProblem:
        """
        Narrow user's profile into a focused problem definition.
        
        Args:
            user_profile: User's health profile (UserProfile or dict)
            focus_mode: 
                - "auto": Automatically determine primary concern
                - "weight", "fertility", "acne", "energy", "mood": Specific focus
        
        Returns:
            FocusedProblem with prioritized concerns and constraints
        """
        logger.info(f"🔍 Narrowing problem focus (mode={focus_mode})")
        
        # Convert to dict if needed
        profile = user_profile.dict() if hasattr(user_profile, 'dict') else user_profile
        
        # Step 1: Extract all user concerns from profile
        all_concerns = self._extract_concerns(profile)
        logger.info(f"📋 Extracted {len(all_concerns)} concerns from profile")
        
        # Step 2: Determine primary concern based on focus_mode
        if focus_mode != "auto" and focus_mode in self.FOCUS_MODE_MAP:
            primary_concern = self._get_focus_mode_concern(all_concerns, focus_mode)
        else:
            primary_concern = self._determine_primary_concern(all_concerns, profile)
        
        logger.info(f"🎯 Primary concern: {primary_concern.concern_type}")
        
        # Step 3: Identify secondary concerns (related but not primary)
        secondary_concerns = self._get_secondary_concerns(
            all_concerns, 
            primary_concern
        )
        
        # Step 4: Extract constraints
        constraints = self._extract_constraints(profile)
        
        # Step 5: Calculate hormone priorities based on all concerns
        hormone_priorities = self._calculate_hormone_priorities(
            primary_concern, 
            secondary_concerns
        )
        
        # Step 6: Build lifestyle context
        lifestyle_context = self._build_lifestyle_context(profile)
        
        focused_problem = FocusedProblem(
            primary_concern=primary_concern,
            secondary_concerns=secondary_concerns[:3],  # Max 3 secondary
            constraints=constraints,
            hormone_priorities=hormone_priorities,
            lifestyle_context=lifestyle_context
        )
        
        logger.info(f"✅ Focused problem created:")
        logger.info(f"   Primary: {focused_problem.primary_concern.concern_type}")
        logger.info(f"   Root causes: {focused_problem.primary_concern.root_causes}")
        logger.info(f"   Secondary: {[c.concern_type for c in focused_problem.secondary_concerns]}")
        
        return focused_problem
    
    def _extract_concerns(self, profile: Dict) -> List[UserConcern]:
        """Extract all concerns from user profile"""
        concerns = []
        
        # From explicit symptoms
        symptoms = profile.get('symptoms', [])
        for symptom in symptoms:
            symptom_key = self._normalize_symptom(symptom)
            if symptom_key in self.CONCERN_TO_ROOT_CAUSE_MAP:
                concerns.append(UserConcern(
                    concern_type=symptom_key,
                    user_description=symptom,
                    root_causes=self.CONCERN_TO_ROOT_CAUSE_MAP[symptom_key],
                    priority=ConcernPriority.SECONDARY,
                    urgency=UrgencyLevel.MEDIUM,
                    success_metrics=self.SUCCESS_METRICS.get(symptom_key, [])
                ))
        
        # From conditions (PCOS, etc.)
        conditions = profile.get('conditions', [])
        for condition in conditions:
            if 'pcos' in condition.lower() or 'pcod' in condition.lower():
                # PCOS implies multiple concerns
                if not any(c.concern_type == 'irregular_periods' for c in concerns):
                    concerns.append(UserConcern(
                        concern_type='irregular_periods',
                        user_description='PCOS-related cycle irregularity',
                        root_causes=['androgen_high', 'insulin_resistance'],
                        priority=ConcernPriority.SECONDARY,
                        urgency=UrgencyLevel.MEDIUM,
                        success_metrics=self.SUCCESS_METRICS.get('irregular_periods', [])
                    ))
        
        # From root cause engine analysis (if present)
        primary_imbalance = profile.get('primary_imbalance', '')
        if primary_imbalance:
            # Map hormone imbalance to related concerns
            hormone_concern_map = {
                'Insulin': 'weight_gain',
                'Androgens': 'acne',
                'Cortisol': 'fatigue',
                'Thyroid': 'fatigue',
                'Estrogen': 'mood_swings',
                'Progesterone': 'irregular_periods',
            }
            for hormone, concern_type in hormone_concern_map.items():
                if hormone.lower() in primary_imbalance.lower():
                    if not any(c.concern_type == concern_type for c in concerns):
                        concerns.append(UserConcern(
                            concern_type=concern_type,
                            user_description=f'{primary_imbalance}-related',
                            root_causes=self.CONCERN_TO_ROOT_CAUSE_MAP.get(concern_type, []),
                            priority=ConcernPriority.PRIMARY,
                            urgency=UrgencyLevel.MEDIUM,
                            success_metrics=self.SUCCESS_METRICS.get(concern_type, [])
                        ))
        
        return concerns
    
    def _normalize_symptom(self, symptom: str) -> str:
        """Normalize symptom text to standard key"""
        symptom_lower = symptom.lower().strip()
        
        # Mapping of variations to standard keys
        normalizations = {
            'weight gain': 'weight_gain',
            'weight-gain': 'weight_gain',
            'gaining weight': 'weight_gain',
            'difficulty losing weight': 'difficulty_losing_weight',
            'can\'t lose weight': 'difficulty_losing_weight',
            'stubborn belly fat': 'stubborn_belly_fat',
            'belly fat': 'stubborn_belly_fat',
            'irregular periods': 'irregular_periods',
            'irregular menstruation': 'irregular_periods',
            'no periods': 'missing_periods',
            'amenorrhea': 'missing_periods',
            'missing period': 'missing_periods',
            'painful periods': 'painful_periods',
            'period pain': 'painful_periods',
            'dysmenorrhea': 'painful_periods',
            'cramps': 'painful_periods',
            'heavy periods': 'heavy_periods',
            'heavy bleeding': 'heavy_periods',
            'menorrhagia': 'heavy_periods',
            'light periods': 'light_periods',
            'acne': 'acne',
            'adult acne': 'acne',
            'breakouts': 'acne',
            'hirsutism': 'hirsutism',
            'excess hair': 'hirsutism',
            'facial hair': 'hirsutism',
            'hair loss': 'hair_loss',
            'thinning hair': 'hair_loss',
            'hairloss': 'hair_loss',
            'fatigue': 'fatigue',
            'tired': 'fatigue',
            'exhausted': 'fatigue',
            'low energy': 'low_energy',
            'no energy': 'low_energy',
            'mood swings': 'mood_swings',
            'moody': 'mood_swings',
            'anxiety': 'anxiety',
            'anxious': 'anxiety',
            'stress': 'anxiety',
            'depression': 'depression',
            'depressed': 'depression',
            'sad': 'depression',
            'fertility': 'fertility_concerns',
            'trying to conceive': 'fertility_concerns',
            'ttc': 'fertility_concerns',
            'infertility': 'fertility_concerns',
            'pms': 'pms',
            'premenstrual': 'pms',
            'hot flashes': 'hot_flashes',
            'hot flushes': 'hot_flashes',
            'insomnia': 'insomnia',
            'can\'t sleep': 'insomnia',
            'sleep issues': 'insomnia',
            'night sweats': 'night_sweats',
            'bloating': 'bloating',
            'bloated': 'bloating',
        }
        
        for pattern, key in normalizations.items():
            if pattern in symptom_lower:
                return key
        
        # Default: convert spaces to underscores
        return symptom_lower.replace(' ', '_').replace('-', '_')
    
    def _determine_primary_concern(
        self, 
        concerns: List[UserConcern], 
        profile: Dict
    ) -> UserConcern:
        """Determine which concern should be primary (most important to user)"""
        if not concerns:
            # Default concern based on conditions
            conditions = profile.get('conditions', [])
            if any('pcos' in c.lower() or 'pcod' in c.lower() for c in conditions):
                return UserConcern(
                    concern_type='irregular_periods',
                    user_description='PCOS management',
                    root_causes=['androgen_high', 'insulin_resistance', 'inflammation'],
                    priority=ConcernPriority.PRIMARY,
                    urgency=UrgencyLevel.MEDIUM,
                    success_metrics=['Improved cycle regularity', 'Better symptom management']
                )
            return UserConcern(
                concern_type='general_wellness',
                user_description='General hormone health',
                root_causes=['hormone_balance'],
                priority=ConcernPriority.PRIMARY,
                urgency=UrgencyLevel.LOW,
                success_metrics=['Overall wellbeing improvement']
            )
        
        # Priority hierarchy (what users typically care most about)
        priority_order = [
            'fertility_concerns',       # Life goal
            'weight_gain',              # Daily impact
            'difficulty_losing_weight',
            'acne',                      # Visible/confidence
            'irregular_periods',         # Health signal
            'fatigue',                   # Quality of life
            'anxiety',
            'hair_loss',
            'mood_swings',
        ]
        
        for concern_type in priority_order:
            for concern in concerns:
                if concern.concern_type == concern_type:
                    concern.priority = ConcernPriority.PRIMARY
                    concern.urgency = UrgencyLevel.HIGH
                    return concern
        
        # Default to first concern
        concerns[0].priority = ConcernPriority.PRIMARY
        return concerns[0]
    
    def _get_focus_mode_concern(
        self, 
        concerns: List[UserConcern], 
        focus_mode: str
    ) -> UserConcern:
        """Get primary concern based on explicit focus mode"""
        target_types = self.FOCUS_MODE_MAP.get(focus_mode, [])
        
        for concern in concerns:
            if concern.concern_type in target_types:
                concern.priority = ConcernPriority.PRIMARY
                concern.urgency = UrgencyLevel.HIGH
                return concern
        
        # Create synthetic concern for focus mode
        if focus_mode == 'weight':
            return UserConcern(
                concern_type='weight_gain',
                user_description='Weight management focus',
                root_causes=['insulin_resistance', 'cortisol_dysregulation'],
                priority=ConcernPriority.PRIMARY,
                urgency=UrgencyLevel.HIGH,
                success_metrics=['Weight loss of 5-10% in 12 weeks']
            )
        elif focus_mode == 'fertility':
            return UserConcern(
                concern_type='fertility_concerns',
                user_description='Fertility support focus',
                root_causes=['androgen_high', 'progesterone_low', 'insulin_resistance'],
                priority=ConcernPriority.PRIMARY,
                urgency=UrgencyLevel.HIGH,
                success_metrics=['Improved cycle regularity', 'Better ovulation']
            )
        # Add other focus modes as needed
        
        return self._determine_primary_concern(concerns, {})
    
    def _get_secondary_concerns(
        self, 
        all_concerns: List[UserConcern], 
        primary: UserConcern
    ) -> List[UserConcern]:
        """Get secondary concerns (excluding primary)"""
        secondary = []
        for concern in all_concerns:
            if concern.concern_type != primary.concern_type:
                concern.priority = ConcernPriority.SECONDARY
                secondary.append(concern)
        return secondary
    
    def _extract_constraints(self, profile: Dict) -> List[Constraint]:
        """Extract user constraints from profile"""
        constraints = []
        
        # Dietary constraints - safely handle None
        dietary_prefs = profile.get('dietary_preferences') or []
        if not isinstance(dietary_prefs, list):
            dietary_prefs = [dietary_prefs] if dietary_prefs else []
        
        for pref in dietary_prefs:
            pref_lower = pref.lower()
            if 'vegetarian' in pref_lower or 'vegan' in pref_lower:
                constraints.append(Constraint(
                    constraint_type='dietary',
                    description=pref,
                    impacts=['nutrition']
                ))
            elif 'gluten' in pref_lower:
                constraints.append(Constraint(
                    constraint_type='dietary',
                    description='Gluten-free',
                    impacts=['nutrition']
                ))
            elif 'dairy' in pref_lower:
                constraints.append(Constraint(
                    constraint_type='dietary',
                    description='Dairy-free',
                    impacts=['nutrition']
                ))
        
        # Time constraints
        lifestyle = profile.get('lifestyle', {})
        if lifestyle.get('busy_schedule'):
            constraints.append(Constraint(
                constraint_type='time',
                description='Limited time available',
                impacts=['movement', 'mindfulness', 'nutrition']
            ))
        
        # Physical constraints
        physical_limitations = profile.get('physical_limitations', [])
        for limitation in physical_limitations:
            constraints.append(Constraint(
                constraint_type='physical',
                description=limitation,
                impacts=['movement']
            ))
        
        return constraints
    
    def _calculate_hormone_priorities(
        self,
        primary: UserConcern,
        secondary: List[UserConcern]
    ) -> Dict[str, float]:
        """Calculate hormone priority scores based on concerns"""
        hormone_scores = {}
        
        # Primary concern root causes get highest weight
        for cause in primary.root_causes:
            hormone_scores[cause] = hormone_scores.get(cause, 0) + 1.0
        
        # Secondary concerns get lower weight
        for concern in secondary:
            for cause in concern.root_causes:
                hormone_scores[cause] = hormone_scores.get(cause, 0) + 0.3
        
        # Normalize to 0-1 range
        max_score = max(hormone_scores.values()) if hormone_scores else 1
        return {k: v / max_score for k, v in hormone_scores.items()}
    
    def _build_lifestyle_context(self, profile: Dict) -> Dict[str, Any]:
        """Build lifestyle context for personalization"""
        return {
            'age': profile.get('age'),
            'cycle_phase': profile.get('cyclePhase'),
            'activity_level': profile.get('activity_level'),
            'stress_level': profile.get('stress_level'),
            'sleep_quality': profile.get('sleep_quality'),
            'work_schedule': profile.get('work_schedule'),
        }
