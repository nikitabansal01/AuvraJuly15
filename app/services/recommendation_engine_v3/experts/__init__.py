"""
Expert modules for Recommendation Engine V3
"""

from .base_expert import BaseDomainExpert, BaseExpertSubModule, ExpertRecommendation
from .nutrition_expert import NutritionExpert
from .movement_expert import MovementExpert
from .mindfulness_expert import MindfulnessExpert

# Alias for backward compatibility
BaseExpert = BaseDomainExpert

__all__ = [
    'BaseDomainExpert',
    'BaseExpert',  # Alias
    'BaseExpertSubModule',
    'ExpertRecommendation',
    'NutritionExpert',
    'MovementExpert',
    'MindfulnessExpert'
]
