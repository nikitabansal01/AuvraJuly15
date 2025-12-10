"""
Reusable AI Components
======================

This module contains reusable AI components that can be used
across different expert modules and RAG pipelines.

Components:
- RetrievalComponent: Semantic search with configurable parameters
- EvidenceGrader: Research quality scoring and grading
- PersonalizationEngine: User profile matching and constraint handling
"""

from app.services.recommendation_engine_v3.components.retrieval_component import (
    RetrievalComponent
)
from app.services.recommendation_engine_v3.components.evidence_grader import (
    EvidenceGrader,
    EvidenceGrade,
    StudyType
)
from app.services.recommendation_engine_v3.components.personalization_engine import (
    PersonalizationEngine,
    PersonalizationResult,
    Constraint,
    ConstraintType
)

__all__ = [
    # Retrieval
    'RetrievalComponent',
    
    # Evidence Grading
    'EvidenceGrader',
    'EvidenceGrade', 
    'StudyType',
    
    # Personalization
    'PersonalizationEngine',
    'PersonalizationResult',
    'Constraint',
    'ConstraintType',
]