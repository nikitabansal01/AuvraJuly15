"""
Insulin Diet Module - Specialized Nutrition for Insulin Resistance
=================================================================

This module provides specialized dietary recommendations for
managing insulin resistance, a core issue in PCOS.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class InsulinDietRecommendation:
    """A single insulin-diet recommendation"""
    food_item: str
    category: str  # protein, carb, fat, fiber
    glycemic_impact: str  # low, medium, high
    insulin_effect: str  # stabilizing, neutral, spiking
    portion_guidance: str
    timing_advice: str
    evidence_level: str  # strong, moderate, limited
    pmid: Optional[str] = None
    explanation: str = ""


class InsulinDietModule:
    """
    Specialized module for insulin-sensitive dietary recommendations.
    
    Key principles:
    - Low glycemic index foods
    - Blood sugar stabilization
    - Insulin sensitivity improvement
    - Anti-inflammatory eating patterns
    """
    
    MODULE_ID = "insulin_diet"
    MODULE_NAME = "Insulin-Sensitive Diet"
    
    # Retrieval configuration for this module
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            "insulin resistance diet PCOS glycemic index",
            "blood sugar stabilization nutrition women",
            "low glycemic eating pattern metabolic syndrome",
            "dietary intervention insulin sensitivity",
            "carbohydrate quality PCOS metabolic health"
        ],
        'must_include_terms': [
            'insulin', 'glycemic', 'glucose', 'diet'
        ],
        'boost_terms': [
            'PCOS', 'women', 'randomized', 'clinical trial'
        ],
        'max_results': 15
    }
    
    # Evidence-based food categories
    RECOMMENDED_FOODS = {
        'proteins': [
            {
                'item': 'Wild-caught salmon',
                'gi': 0,
                'benefit': 'Omega-3s improve insulin sensitivity',
                'evidence': 'strong'
            },
            {
                'item': 'Organic eggs',
                'gi': 0, 
                'benefit': 'Complete protein, supports hormone synthesis',
                'evidence': 'strong'
            },
            {
                'item': 'Legumes (lentils, chickpeas)',
                'gi': 30,
                'benefit': 'Fiber + protein combination, slow glucose release',
                'evidence': 'strong'
            },
            {
                'item': 'Greek yogurt (unsweetened)',
                'gi': 15,
                'benefit': 'Probiotics, protein, calcium for metabolic health',
                'evidence': 'moderate'
            }
        ],
        'complex_carbs': [
            {
                'item': 'Quinoa',
                'gi': 53,
                'benefit': 'Complete protein, fiber, low GI grain alternative',
                'evidence': 'strong'
            },
            {
                'item': 'Steel-cut oats',
                'gi': 42,
                'benefit': 'Beta-glucan fiber improves insulin response',
                'evidence': 'strong'
            },
            {
                'item': 'Sweet potato',
                'gi': 54,
                'benefit': 'Fiber, vitamin A, moderate GI when cooled',
                'evidence': 'moderate'
            }
        ],
        'healthy_fats': [
            {
                'item': 'Extra virgin olive oil',
                'gi': 0,
                'benefit': 'Oleic acid reduces inflammation, improves insulin sensitivity',
                'evidence': 'strong'
            },
            {
                'item': 'Avocado',
                'gi': 0,
                'benefit': 'Monounsaturated fats, fiber, potassium',
                'evidence': 'strong'
            },
            {
                'item': 'Walnuts',
                'gi': 0,
                'benefit': 'ALA omega-3, polyphenols, improves metabolic markers',
                'evidence': 'moderate'
            }
        ],
        'vegetables': [
            {
                'item': 'Leafy greens (spinach, kale)',
                'gi': 0,
                'benefit': 'Magnesium, fiber, minimal glucose impact',
                'evidence': 'strong'
            },
            {
                'item': 'Broccoli',
                'gi': 10,
                'benefit': 'Sulforaphane supports detoxification, chromium content',
                'evidence': 'strong'
            },
            {
                'item': 'Bitter melon',
                'gi': 0,
                'benefit': 'Contains compounds that mimic insulin',
                'evidence': 'moderate'
            }
        ]
    }
    
    # Foods to limit/avoid
    FOODS_TO_AVOID = [
        {
            'item': 'White bread/refined grains',
            'gi': 75,
            'reason': 'Rapid blood sugar spike, promotes insulin resistance'
        },
        {
            'item': 'Sugary beverages',
            'gi': 63,
            'reason': 'Liquid sugar bypasses satiety signals, high fructose load'
        },
        {
            'item': 'Processed snacks',
            'gi': 70,
            'reason': 'Refined carbs, trans fats, inflammatory'
        },
        {
            'item': 'Fruit juice (even 100%)',
            'gi': 66,
            'reason': 'Concentrated sugar without fiber'
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
    ) -> List[InsulinDietRecommendation]:
        """
        Generate insulin-specific diet recommendations.
        """
        recommendations = []
        
        # Get user-specific factors
        insulin_severity = self._assess_insulin_severity(hormone_data)
        dietary_restrictions = user_profile.get('dietary_restrictions', [])
        preferences = user_profile.get('food_preferences', {})
        
        logger.info(f"📊 Insulin severity assessed: {insulin_severity}")
        
        # Generate meal structure recommendation
        meal_rec = self._create_meal_structure_rec(insulin_severity)
        recommendations.append(meal_rec)
        
        # Generate food category recommendations
        for category, foods in self.RECOMMENDED_FOODS.items():
            for food in foods:
                # Check against dietary restrictions
                if self._is_compatible(food['item'], dietary_restrictions):
                    rec = InsulinDietRecommendation(
                        food_item=food['item'],
                        category=category,
                        glycemic_impact='low' if food['gi'] < 55 else 'medium',
                        insulin_effect='stabilizing',
                        portion_guidance=self._get_portion(category, insulin_severity),
                        timing_advice=self._get_timing(category),
                        evidence_level=food['evidence'],
                        explanation=food['benefit']
                    )
                    recommendations.append(rec)
        
        # Add avoidance recommendations
        avoid_rec = self._create_avoidance_rec()
        recommendations.append(avoid_rec)
        
        return recommendations
    
    def _assess_insulin_severity(self, hormone_data: Dict[str, Any]) -> str:
        """Assess insulin resistance severity from hormone data"""
        insulin_indicators = hormone_data.get('insulin', {})
        homa_ir = insulin_indicators.get('homa_ir', 0)
        fasting_insulin = insulin_indicators.get('fasting_insulin', 0)
        
        if homa_ir > 2.5 or fasting_insulin > 15:
            return 'severe'
        elif homa_ir > 1.9 or fasting_insulin > 10:
            return 'moderate'
        else:
            return 'mild'
    
    def _create_meal_structure_rec(self, severity: str) -> InsulinDietRecommendation:
        """Create meal timing/structure recommendation"""
        if severity == 'severe':
            timing = "Eat 3 balanced meals with no snacking. Consider 16:8 intermittent fasting under supervision."
        elif severity == 'moderate':
            timing = "3 meals + 1-2 small protein-based snacks. Avoid eating after 8 PM."
        else:
            timing = "Regular meal timing with protein at each meal. Can include healthy snacks."
        
        return InsulinDietRecommendation(
            food_item="Meal Structure & Timing",
            category="meal_pattern",
            glycemic_impact="n/a",
            insulin_effect="stabilizing",
            portion_guidance="See specific meal recommendations",
            timing_advice=timing,
            evidence_level="strong",
            explanation="Consistent meal timing helps regulate insulin patterns"
        )
    
    def _create_avoidance_rec(self) -> InsulinDietRecommendation:
        """Create recommendation for foods to avoid"""
        avoid_list = ", ".join([f['item'] for f in self.FOODS_TO_AVOID])
        
        return InsulinDietRecommendation(
            food_item="Foods to Minimize",
            category="avoidance",
            glycemic_impact="high",
            insulin_effect="spiking",
            portion_guidance="Limit to occasional treats, not daily consumption",
            timing_advice="If consuming, pair with protein/fat to slow absorption",
            evidence_level="strong",
            explanation=f"Minimize: {avoid_list}"
        )
    
    def _is_compatible(self, food: str, restrictions: List[str]) -> bool:
        """Check if food is compatible with dietary restrictions"""
        food_lower = food.lower()
        for restriction in restrictions:
            restriction_lower = restriction.lower()
            if 'vegan' in restriction_lower:
                if any(x in food_lower for x in ['egg', 'salmon', 'fish', 'yogurt', 'dairy']):
                    return False
            if 'vegetarian' in restriction_lower:
                if any(x in food_lower for x in ['salmon', 'fish']):
                    return False
            if 'dairy' in restriction_lower:
                if 'yogurt' in food_lower:
                    return False
        return True
    
    def _get_portion(self, category: str, severity: str) -> str:
        """Get portion guidance based on category and severity"""
        portions = {
            'proteins': '4-6 oz per meal' if severity != 'severe' else '5-7 oz per meal',
            'complex_carbs': '1/2 cup' if severity == 'severe' else '3/4 cup',
            'healthy_fats': '1-2 tablespoons',
            'vegetables': 'Unlimited non-starchy vegetables'
        }
        return portions.get(category, 'As directed')
    
    def _get_timing(self, category: str) -> str:
        """Get timing advice for food category"""
        timing = {
            'proteins': 'Include with every meal for blood sugar stability',
            'complex_carbs': 'Best consumed earlier in the day, always with protein',
            'healthy_fats': 'Include at each meal for satiety',
            'vegetables': 'Half your plate at lunch and dinner'
        }
        return timing.get(category, 'With meals')
    
    def get_evidence_summary(self) -> Dict[str, Any]:
        """Get summary of evidence supporting this module"""
        return {
            'module': self.MODULE_NAME,
            'key_studies': [
                {
                    'finding': 'Low GI diet improves insulin sensitivity in PCOS',
                    'pmid': '22084891',
                    'quality': 'RCT'
                },
                {
                    'finding': 'Mediterranean diet reduces HOMA-IR in women',
                    'pmid': '28655897', 
                    'quality': 'Meta-analysis'
                }
            ],
            'evidence_strength': 'strong'
        }
