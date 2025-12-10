"""
Specialized Modules for Expert System
=====================================

These modules provide deep expertise in specific intervention areas.
They can be composed and reused across different expert classes.
"""

from app.services.recommendation_engine_v3.modules.insulin_diet_module import (
    InsulinDietModule,
    InsulinDietRecommendation
)
from app.services.recommendation_engine_v3.modules.cortisol_management_module import (
    CortisolManagementModule,
    CortisolRecommendation,
    CortisolPattern
)
from app.services.recommendation_engine_v3.modules.sleep_optimization_module import (
    SleepOptimizationModule,
    SleepRecommendation,
    SleepIssueType
)

__all__ = [
    # Insulin/Diet
    'InsulinDietModule',
    'InsulinDietRecommendation',
    
    # Cortisol Management
    'CortisolManagementModule',
    'CortisolRecommendation',
    'CortisolPattern',
    
    # Sleep
    'SleepOptimizationModule',
    'SleepRecommendation',
    'SleepIssueType',
]