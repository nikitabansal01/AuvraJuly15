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

# AI Security Guardrails (optional import - won't break if issues)
# These are SEPARATE from medical safety - for prompt injection/data leakage
try:
    from app.services.safety.ai_guardrails import (
        AIGuardrails,
        GuardrailResult,
        ThreatLevel,
        get_ai_guardrails,
    )
    AI_GUARDRAILS_AVAILABLE = True
except ImportError:
    AI_GUARDRAILS_AVAILABLE = False
    AIGuardrails = None
    GuardrailResult = None
    ThreatLevel = None
    get_ai_guardrails = None

__all__ = [
    # Medical Safety (existing)
    'PaperQualityScorer',
    'SafetyGuardrails',
    'RecommendationAuditLog',
    'ContradictionResolver',
    'EvidenceThresholdChecker',
    'PaperVersionControl',
    'PromptExperiments',
    'StudyType',
    # AI Security Guardrails (new - optional)
    'AIGuardrails',
    'GuardrailResult',
    'ThreatLevel',
    'get_ai_guardrails',
    'AI_GUARDRAILS_AVAILABLE',
]
