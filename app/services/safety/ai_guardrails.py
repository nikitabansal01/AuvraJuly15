"""
AI Security Guardrails for AUVRA
================================

This module provides INPUT/OUTPUT security guardrails for AI interactions.
Separate from medical_safety.py which handles health-related safety.

IMPORTANT: This module is COMPLETELY STANDALONE and OPTIONAL.
- It does NOT modify any existing code
- It does NOT auto-run or intercept existing flows
- It must be EXPLICITLY called to be used
- Existing functionality works 100% the same

Security Features:
1. Prompt Injection Detection - Detects attempts to override system prompts
2. Data Leakage Prevention - Prevents secrets/PII from leaking
3. Output Validation - Validates AI responses for safety

Usage (when ready to integrate):
    from app.services.safety.ai_guardrails import AIGuardrails
    
    guardrails = AIGuardrails()
    
    # Validate user input before sending to LLM
    result = guardrails.validate_input(user_input)
    if not result.is_safe:
        return {"error": "Invalid input"}
    
    # Validate AI output before returning to user
    output_result = guardrails.validate_output(ai_response)
    return output_result.cleaned_content
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class ThreatLevel(Enum):
    """Threat severity levels."""
    SAFE = "safe"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass
class GuardrailResult:
    """
    Result of guardrail validation.
    
    Attributes:
        is_safe: Whether the content passed validation
        threat_level: Severity level (safe/warning/blocked)
        reason: Human-readable explanation
        cleaned_content: Sanitized content (secrets redacted)
        threats_detected: List of specific threats found
    """
    is_safe: bool
    threat_level: ThreatLevel
    reason: str
    cleaned_content: Optional[str] = None
    threats_detected: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN GUARDRAILS CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class AIGuardrails:
    """
    AI Security Guardrails for protecting against prompt injection,
    data leakage, and unsafe outputs.
    
    This class is COMPLETELY STANDALONE and does not modify any existing code.
    It must be explicitly instantiated and called to be used.
    
    Example:
        guardrails = AIGuardrails()
        
        # Check user input
        input_result = guardrails.validate_input(user_message)
        if not input_result.is_safe:
            logger.warning(f"Blocked input: {input_result.reason}")
            return "I can only help with wellness questions."
        
        # After getting AI response, check output
        output_result = guardrails.validate_output(ai_response)
        return output_result.cleaned_content
    """
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CONFIGURATION - Customize thresholds and patterns here
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Maximum input length (characters)
    MAX_INPUT_LENGTH = 15000
    
    # Whether to block or just warn on injection attempts
    BLOCK_INJECTIONS = True
    
    # Whether to redact secrets in output (vs blocking entirely)
    REDACT_SECRETS = True
    
    # ═══════════════════════════════════════════════════════════════════════════
    # THREAT PATTERNS
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Prompt injection patterns - attempts to override system instructions
    INJECTION_PATTERNS = {
        "instruction_override": [
            r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
            r"disregard\s+(all\s+)?(your|previous|the)\s+(instructions?|rules?)",
            r"forget\s+(all\s+)?(your|previous)\s+(instructions?|training)",
            r"do\s+not\s+follow\s+(your|the)\s+(previous\s+)?instructions?",
        ],
        "jailbreak": [
            r"you\s+are\s+now\s+(DAN|unrestricted|jailbroken|unfiltered)",
            r"pretend\s+(you\s+)?(are|have)\s+no\s+(restrictions?|rules?|limits?)",
            r"act\s+as\s+(if|though)\s+you\s+(have\s+)?no\s+(guidelines?|restrictions?)",
            r"developer\s+mode|god\s+mode|sudo\s+mode",
            r"enable\s+(DAN|jailbreak|developer)\s+mode",
        ],
        "prompt_extraction": [
            r"(reveal|show|display|print|output)\s+(your|the|my)?\s*(system\s+)?prompt",
            r"what\s+(are|were)\s+your\s+(original\s+|initial\s+)?instructions",
            r"repeat\s+(your|the)\s+(system\s+)?prompt",
            r"(tell|show)\s+me\s+(your|the)\s+(system\s+)?prompt",
            r"copy\s+(your|the)\s+entire\s+(system\s+)?prompt",
        ],
        "control_tokens": [
            r"\[SYSTEM\]|\[INST\]|\[/INST\]",
            r"<\|im_start\|>|<\|im_end\|>",
            r"<<SYS>>|<</SYS>>",
            r"###\s*(Human|Assistant|System)\s*:",
        ],
        "role_confusion": [
            r"you\s+are\s+(actually|really)\s+(a|an)",
            r"your\s+(true|real|actual)\s+(purpose|role|identity)",
            r"stop\s+being\s+(an?\s+)?(AI|assistant|bot)",
            r"from\s+now\s+on\s+you\s+are",
        ],
    }
    
    # Secret/credential patterns that shouldn't appear in AI output
    SECRET_PATTERNS = {
        "api_key": r"(api[_-]?key|apikey|access[_-]?token)\s*[:=]\s*['\"]?[\w-]{20,}",
        "openai_key": r"sk-[a-zA-Z0-9]{20,}",
        "firebase_key": r"AIza[a-zA-Z0-9_-]{35}",
        "db_url": r"(postgres|postgresql|mysql|mongodb)://[^\s]+",
        "jwt_token": r"eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*",
        "generic_secret": r"(secret|password|token|credential)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
    }
    
    # PII patterns (informational - health app may need some PII)
    PII_PATTERNS = {
        "ssn": r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b",
        "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    }
    
    # Harmful content patterns (for output validation)
    HARMFUL_CONTENT = {
        "self_harm": r"\b(how\s+to\s+)?(suicide|self[- ]?harm|kill\s+(yourself|myself))\b",
        "violence": r"\b(how\s+to\s+(kill|murder|harm|hurt)\s+someone)\b",
        "illegal_instructions": r"\b(how\s+to\s+(make|build|create)\s+(a\s+)?(bomb|weapon|explosive|drug))\b",
    }
    
    # ═══════════════════════════════════════════════════════════════════════════
    # INPUT VALIDATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def validate_input(self, user_input: str) -> GuardrailResult:
        """
        Validate user input before sending to LLM.
        
        Checks for:
        - Excessive length
        - Prompt injection attempts
        - Jailbreak attempts
        - Prompt extraction attempts
        
        Args:
            user_input: The raw user input string
            
        Returns:
            GuardrailResult with validation status and details
            
        Example:
            result = guardrails.validate_input("Tell me about cinnamon")
            if result.is_safe:
                # Proceed with LLM call
                pass
            else:
                # Block or sanitize
                logger.warning(f"Blocked: {result.reason}")
        """
        if not user_input:
            return GuardrailResult(
                is_safe=True,
                threat_level=ThreatLevel.SAFE,
                reason="Empty input",
                cleaned_content=""
            )
        
        threats = []
        
        # Length check
        if len(user_input) > self.MAX_INPUT_LENGTH:
            return GuardrailResult(
                is_safe=False,
                threat_level=ThreatLevel.BLOCKED,
                reason=f"Input exceeds maximum length ({len(user_input)} > {self.MAX_INPUT_LENGTH})",
                threats_detected=["excessive_length"]
            )
        
        # Check all injection patterns
        for category, patterns in self.INJECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, user_input, re.IGNORECASE):
                    threats.append(f"{category}")
                    logger.warning(f"🚨 [AI-GUARDRAIL] Injection attempt: {category}")
                    # Only log first match per category
                    break
        
        if threats:
            if self.BLOCK_INJECTIONS:
                return GuardrailResult(
                    is_safe=False,
                    threat_level=ThreatLevel.BLOCKED,
                    reason="Potential prompt manipulation detected",
                    threats_detected=threats
                )
            else:
                # Warning mode - allow but flag
                return GuardrailResult(
                    is_safe=True,
                    threat_level=ThreatLevel.WARNING,
                    reason="Suspicious patterns detected but allowed",
                    cleaned_content=user_input,
                    threats_detected=threats
                )
        
        return GuardrailResult(
            is_safe=True,
            threat_level=ThreatLevel.SAFE,
            reason="Input validated successfully",
            cleaned_content=user_input
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # OUTPUT VALIDATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def validate_output(self, ai_response: str) -> GuardrailResult:
        """
        Validate AI response before returning to user.
        
        Checks for:
        - Harmful content
        - Secret/credential leakage
        - PII exposure (informational)
        
        Args:
            ai_response: The AI-generated response string
            
        Returns:
            GuardrailResult with cleaned content (secrets redacted)
            
        Example:
            result = guardrails.validate_output(ai_response)
            if result.is_safe:
                return result.cleaned_content
            else:
                return "Unable to generate safe response"
        """
        if not ai_response:
            return GuardrailResult(
                is_safe=True,
                threat_level=ThreatLevel.SAFE,
                reason="Empty response",
                cleaned_content=""
            )
        
        threats = []
        cleaned = ai_response
        
        # Check for harmful content (block these)
        for category, pattern in self.HARMFUL_CONTENT.items():
            if re.search(pattern, ai_response, re.IGNORECASE):
                threats.append(f"harmful_{category}")
                logger.error(f"🚨 [AI-GUARDRAIL] Harmful content detected: {category}")
                return GuardrailResult(
                    is_safe=False,
                    threat_level=ThreatLevel.BLOCKED,
                    reason="Response contains potentially harmful content",
                    threats_detected=threats
                )
        
        # Check and redact secrets
        for secret_type, pattern in self.SECRET_PATTERNS.items():
            if re.search(pattern, ai_response, re.IGNORECASE):
                if self.REDACT_SECRETS:
                    cleaned = re.sub(pattern, "[REDACTED]", cleaned, flags=re.IGNORECASE)
                    threats.append(f"secret_redacted_{secret_type}")
                    logger.warning(f"🚨 [AI-GUARDRAIL] Secret redacted: {secret_type}")
                else:
                    threats.append(f"secret_leak_{secret_type}")
                    logger.error(f"🚨 [AI-GUARDRAIL] Secret leak blocked: {secret_type}")
                    return GuardrailResult(
                        is_safe=False,
                        threat_level=ThreatLevel.BLOCKED,
                        reason="Response contains sensitive data",
                        threats_detected=threats
                    )
        
        # Check for PII (informational only - don't block for health app)
        for pii_type, pattern in self.PII_PATTERNS.items():
            if re.search(pattern, ai_response):
                threats.append(f"pii_detected_{pii_type}")
                logger.info(f"ℹ️ [AI-GUARDRAIL] PII detected in output: {pii_type}")
        
        threat_level = ThreatLevel.WARNING if threats else ThreatLevel.SAFE
        
        return GuardrailResult(
            is_safe=True,
            threat_level=threat_level,
            reason="Output validated" if not threats else "Output cleaned/flagged",
            cleaned_content=cleaned,
            threats_detected=threats if threats else []
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CONTEXT ISOLATION (For multi-user data safety)
    # ═══════════════════════════════════════════════════════════════════════════
    
    def sanitize_context(
        self, 
        context: Dict[str, Any], 
        current_user_id: str,
        sensitive_fields: List[str] = None
    ) -> Dict[str, Any]:
        """
        Ensure context only contains data for the current user.
        Prevents cross-user data leakage in multi-tenant systems.
        
        Args:
            context: Dictionary of context data to be sent to LLM
            current_user_id: The ID of the current user
            sensitive_fields: Additional fields to remove (default: common secrets)
            
        Returns:
            Sanitized context dictionary
            
        Example:
            safe_context = guardrails.sanitize_context(
                context={"user_data": {...}, "api_key": "sk-..."},
                current_user_id="user123"
            )
        """
        if sensitive_fields is None:
            sensitive_fields = [
                "api_key", "password", "secret", "token", "credential",
                "private_key", "auth", "bearer"
            ]
        
        sanitized = {}
        
        for key, value in context.items():
            # Skip sensitive fields
            key_lower = key.lower()
            if any(sf in key_lower for sf in sensitive_fields):
                logger.debug(f"[AI-GUARDRAIL] Removed sensitive field: {key}")
                continue
            
            # Check for cross-user data
            if isinstance(value, dict):
                user_id_in_value = value.get("user_id") or value.get("uid")
                if user_id_in_value and user_id_in_value != current_user_id:
                    logger.warning(f"🚨 [AI-GUARDRAIL] Cross-user data blocked: {key}")
                    continue
            
            sanitized[key] = value
        
        return sanitized
    
    # ═══════════════════════════════════════════════════════════════════════════
    # UTILITY METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_safe_error_message(self) -> str:
        """
        Return a safe, generic error message for blocked content.
        Doesn't reveal why content was blocked (security through obscurity).
        """
        return "I'm here to help with your wellness journey. How can I assist you today?"
    
    def is_input_safe(self, user_input: str) -> bool:
        """
        Quick check if input is safe (no details).
        
        Args:
            user_input: The user input to check
            
        Returns:
            True if safe, False if blocked
        """
        return self.validate_input(user_input).is_safe
    
    def is_output_safe(self, ai_response: str) -> bool:
        """
        Quick check if output is safe (no details).
        
        Args:
            ai_response: The AI response to check
            
        Returns:
            True if safe, False if blocked
        """
        return self.validate_output(ai_response).is_safe
    
    def clean_output(self, ai_response: str) -> str:
        """
        Get cleaned output with secrets redacted.
        
        Args:
            ai_response: The AI response to clean
            
        Returns:
            Cleaned response string
        """
        result = self.validate_output(ai_response)
        return result.cleaned_content or ai_response


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE (Optional - for convenience)
# ═══════════════════════════════════════════════════════════════════════════════

_guardrails_instance: Optional[AIGuardrails] = None


def get_ai_guardrails() -> AIGuardrails:
    """
    Get singleton instance of AIGuardrails.
    
    Usage:
        from app.services.safety.ai_guardrails import get_ai_guardrails
        
        guardrails = get_ai_guardrails()
        result = guardrails.validate_input(user_input)
    """
    global _guardrails_instance
    if _guardrails_instance is None:
        _guardrails_instance = AIGuardrails()
    return _guardrails_instance


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE INFO
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    'AIGuardrails',
    'GuardrailResult', 
    'ThreatLevel',
    'get_ai_guardrails',
]

# Log module load (only in debug)
logger.debug("AI Guardrails module loaded (standalone, not auto-enabled)")
