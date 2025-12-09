"""
Medical Safety Module for AUVRA
Contains safety guardrails, quality scoring, audit logging, and A/B testing
"""

from app.services.safety.medical_safety import (
    PaperQualityScorer,
    SafetyGuardrails,
    RecommendationAuditLog,
    ContradictionResolver,
    EvidenceThresholdChecker,
    PaperVersionControl,
    PromptExperiments,
    StudyType
)

__all__ = [
    'PaperQualityScorer',
    'SafetyGuardrails',
    'RecommendationAuditLog',
    'ContradictionResolver',
    'EvidenceThresholdChecker',
    'PaperVersionControl',
    'PromptExperiments',
    'StudyType',
]
