"""
Personalization Engine - User Profile Matching
==============================================

Reusable component for personalizing recommendations based on
user profiles, constraints, and preferences.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ConstraintType(Enum):
    """Types of user constraints"""
    DIETARY = "dietary"
    PHYSICAL = "physical"
    MEDICAL = "medical"
    TIME = "time"
    ACCESS = "access"
    PREFERENCE = "preference"


@dataclass
class Constraint:
    """A user constraint that affects recommendations"""
    type: ConstraintType
    name: str
    description: str
    excludes: List[str] = field(default_factory=list)  # Things to avoid
    requires: List[str] = field(default_factory=list)  # Things to include
    modifies: Dict[str, str] = field(default_factory=dict)  # Substitutions


@dataclass
class PersonalizationResult:
    """Result of personalization process"""
    original_recommendation: Dict[str, Any]
    personalized_recommendation: Dict[str, Any]
    modifications_made: List[str]
    constraints_applied: List[str]
    compatibility_score: float  # 0.0 - 1.0
    warnings: List[str]


class PersonalizationEngine:
    """
    Engine for personalizing recommendations to user profiles.
    
    This component:
    - Extracts constraints from user profiles
    - Checks recommendation compatibility
    - Modifies recommendations to fit constraints
    - Ensures cultural/dietary/medical appropriateness
    """
    
    def __init__(self):
        # Common dietary constraints and their implications
        self.dietary_constraints = {
            'vegetarian': Constraint(
                type=ConstraintType.DIETARY,
                name='Vegetarian',
                description='No meat or fish',
                excludes=['meat', 'chicken', 'fish', 'seafood', 'poultry', 'beef', 'pork'],
                modifies={
                    'protein': 'plant-based protein sources like legumes, tofu, tempeh',
                    'omega-3': 'flaxseed, chia seeds, walnuts, algae supplements'
                }
            ),
            'vegan': Constraint(
                type=ConstraintType.DIETARY,
                name='Vegan',
                description='No animal products',
                excludes=['meat', 'fish', 'dairy', 'eggs', 'honey', 'milk', 'cheese', 'yogurt'],
                modifies={
                    'protein': 'plant-based sources: legumes, tofu, tempeh, seitan',
                    'calcium': 'fortified plant milk, leafy greens, tofu',
                    'b12': 'fortified foods or B12 supplement'
                }
            ),
            'gluten-free': Constraint(
                type=ConstraintType.DIETARY,
                name='Gluten-Free',
                description='No gluten-containing foods',
                excludes=['wheat', 'barley', 'rye', 'bread', 'pasta', 'most cereals'],
                modifies={
                    'grains': 'rice, quinoa, buckwheat, millet, gluten-free oats'
                }
            ),
            'dairy-free': Constraint(
                type=ConstraintType.DIETARY,
                name='Dairy-Free',
                description='No dairy products',
                excludes=['milk', 'cheese', 'yogurt', 'butter', 'cream', 'whey'],
                modifies={
                    'calcium': 'fortified plant milk, leafy greens',
                    'dairy': 'plant-based alternatives'
                }
            ),
            'keto': Constraint(
                type=ConstraintType.DIETARY,
                name='Ketogenic',
                description='Very low carbohydrate',
                excludes=['sugar', 'grains', 'high-carb fruits', 'starchy vegetables'],
                requires=['healthy fats', 'moderate protein', 'low-carb vegetables']
            ),
        }
        
        # Physical constraints
        self.physical_constraints = {
            'limited_mobility': Constraint(
                type=ConstraintType.PHYSICAL,
                name='Limited Mobility',
                description='Restricted physical movement',
                excludes=['high-impact exercise', 'running', 'jumping', 'standing exercises'],
                modifies={
                    'exercise': 'seated exercises, chair yoga, resistance bands',
                    'cardio': 'seated cardio, arm exercises, water exercises'
                }
            ),
            'joint_issues': Constraint(
                type=ConstraintType.PHYSICAL,
                name='Joint Issues',
                description='Joint pain or arthritis',
                excludes=['high-impact', 'heavy weights', 'deep squats'],
                modifies={
                    'exercise': 'low-impact activities: swimming, cycling, yoga',
                    'strength': 'light weights with higher repetitions'
                }
            ),
            'pregnancy': Constraint(
                type=ConstraintType.PHYSICAL,
                name='Pregnancy',
                description='Currently pregnant',
                excludes=['intense cardio', 'lying flat after first trimester', 'contact sports'],
                requires=['prenatal-safe exercises', 'proper modifications'],
                modifies={
                    'exercise': 'prenatal yoga, walking, swimming',
                    'intensity': 'moderate, conversational pace'
                }
            ),
        }
        
        # Time constraints
        self.time_constraints = {
            'very_limited': Constraint(
                type=ConstraintType.TIME,
                name='Very Limited Time',
                description='Less than 15 minutes available',
                modifies={
                    'exercise': '10-minute HIIT or quick routine',
                    'meals': 'quick prep meals, batch cooking suggestions'
                }
            ),
            'limited': Constraint(
                type=ConstraintType.TIME,
                name='Limited Time',
                description='15-30 minutes available',
                modifies={
                    'exercise': '20-minute efficient workouts',
                    'meals': 'simple recipes with minimal prep'
                }
            ),
        }
    
    def extract_constraints(
        self, 
        user_profile: Dict[str, Any]
    ) -> List[Constraint]:
        """
        Extract all applicable constraints from user profile.
        
        Args:
            user_profile: User's health and preference data
        
        Returns:
            List of applicable constraints
        """
        constraints = []
        
        # Check dietary preferences
        diet_pref = user_profile.get('diet_preference', '').lower()
        if diet_pref in self.dietary_constraints:
            constraints.append(self.dietary_constraints[diet_pref])
        
        # Check allergies/intolerances
        allergies = user_profile.get('allergies', [])
        if isinstance(allergies, str):
            allergies = [a.strip() for a in allergies.split(',')]
        
        for allergy in allergies:
            allergy_lower = allergy.lower()
            if 'gluten' in allergy_lower:
                constraints.append(self.dietary_constraints.get('gluten-free'))
            if 'dairy' in allergy_lower or 'lactose' in allergy_lower:
                constraints.append(self.dietary_constraints.get('dairy-free'))
        
        # Check physical limitations
        physical_limits = user_profile.get('physical_limitations', [])
        if isinstance(physical_limits, str):
            physical_limits = [p.strip() for p in physical_limits.split(',')]
        
        for limit in physical_limits:
            limit_lower = limit.lower()
            if 'mobility' in limit_lower:
                constraints.append(self.physical_constraints.get('limited_mobility'))
            if 'joint' in limit_lower or 'arthritis' in limit_lower:
                constraints.append(self.physical_constraints.get('joint_issues'))
            if 'pregnant' in limit_lower or 'pregnancy' in limit_lower:
                constraints.append(self.physical_constraints.get('pregnancy'))
        
        # Check time availability
        time_available = user_profile.get('time_available_minutes', 30)
        if time_available < 15:
            constraints.append(self.time_constraints.get('very_limited'))
        elif time_available < 30:
            constraints.append(self.time_constraints.get('limited'))
        
        # Filter None values
        constraints = [c for c in constraints if c is not None]
        
        logger.info(f"📋 Extracted {len(constraints)} constraints from user profile")
        
        return constraints
    
    def check_compatibility(
        self,
        recommendation: Dict[str, Any],
        constraints: List[Constraint]
    ) -> Dict[str, Any]:
        """
        Check if a recommendation is compatible with constraints.
        
        Returns compatibility analysis with score and issues.
        """
        issues = []
        warnings = []
        score = 1.0
        
        rec_text = str(recommendation).lower()
        
        for constraint in constraints:
            # Check exclusions
            for excluded in constraint.excludes:
                if excluded.lower() in rec_text:
                    issues.append(
                        f"Contains '{excluded}' which conflicts with {constraint.name}"
                    )
                    score -= 0.2
            
            # Check requirements
            for required in constraint.requires:
                if required.lower() not in rec_text:
                    warnings.append(
                        f"May need to add '{required}' for {constraint.name} compliance"
                    )
                    score -= 0.05
        
        return {
            'compatible': score >= 0.6,
            'score': max(0, score),
            'issues': issues,
            'warnings': warnings
        }
    
    def personalize(
        self,
        recommendation: Dict[str, Any],
        user_profile: Dict[str, Any]
    ) -> PersonalizationResult:
        """
        Personalize a recommendation for a specific user.
        
        Args:
            recommendation: Original recommendation
            user_profile: User's profile data
        
        Returns:
            PersonalizationResult with modified recommendation
        """
        # Extract constraints
        constraints = self.extract_constraints(user_profile)
        
        # Check initial compatibility
        compat = self.check_compatibility(recommendation, constraints)
        
        # Create personalized copy
        personalized = dict(recommendation)
        modifications = []
        applied_constraints = []
        
        # Apply modifications for each constraint
        for constraint in constraints:
            applied_constraints.append(constraint.name)
            
            # Apply text modifications
            for key, replacement in constraint.modifies.items():
                if key in str(personalized).lower():
                    # Add modification note
                    if 'modifications' not in personalized:
                        personalized['modifications'] = []
                    
                    personalized['modifications'].append({
                        'type': constraint.name,
                        'note': f"Consider: {replacement}"
                    })
                    modifications.append(
                        f"Added {constraint.name} modification for {key}"
                    )
        
        # Add personalization metadata
        personalized['personalization'] = {
            'constraints_applied': applied_constraints,
            'user_profile_summary': self._summarize_profile(user_profile)
        }
        
        return PersonalizationResult(
            original_recommendation=recommendation,
            personalized_recommendation=personalized,
            modifications_made=modifications,
            constraints_applied=applied_constraints,
            compatibility_score=compat['score'],
            warnings=compat['warnings']
        )
    
    def personalize_multiple(
        self,
        recommendations: List[Dict[str, Any]],
        user_profile: Dict[str, Any]
    ) -> List[PersonalizationResult]:
        """Personalize a list of recommendations."""
        return [
            self.personalize(rec, user_profile)
            for rec in recommendations
        ]
    
    def filter_compatible(
        self,
        recommendations: List[Dict[str, Any]],
        user_profile: Dict[str, Any],
        min_score: float = 0.6
    ) -> List[Dict[str, Any]]:
        """
        Filter recommendations to only include compatible ones.
        """
        constraints = self.extract_constraints(user_profile)
        
        compatible = []
        for rec in recommendations:
            compat = self.check_compatibility(rec, constraints)
            if compat['score'] >= min_score:
                compatible.append(rec)
        
        logger.info(
            f"📋 Filtered to {len(compatible)}/{len(recommendations)} "
            f"compatible recommendations"
        )
        
        return compatible
    
    def _summarize_profile(self, user_profile: Dict[str, Any]) -> Dict[str, Any]:
        """Create a summary of relevant profile info."""
        return {
            'age': user_profile.get('age'),
            'dietary_preference': user_profile.get('diet_preference'),
            'activity_level': user_profile.get('activity_level'),
            'primary_symptoms': user_profile.get('symptoms', [])[:3],
            'has_physical_limitations': bool(
                user_profile.get('physical_limitations')
            )
        }
    
    def get_adaptation_suggestions(
        self,
        recommendation: Dict[str, Any],
        user_profile: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """
        Generate specific adaptation suggestions for a recommendation.
        """
        constraints = self.extract_constraints(user_profile)
        suggestions = []
        
        for constraint in constraints:
            for original, adapted in constraint.modifies.items():
                if original.lower() in str(recommendation).lower():
                    suggestions.append({
                        'constraint': constraint.name,
                        'original': original,
                        'suggestion': adapted,
                        'reason': constraint.description
                    })
        
        return suggestions
