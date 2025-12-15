"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA INTELLIGENCE ENGINE
═══════════════════════════════════════════════════════════════════════════════
The brain of AUVRA - making her feel like an exceptional doctor.

Modules:
- memory_engine: Multi-layer memory (episodic, semantic, emotional, predictive)
- emotional_intelligence: Emotion detection, empathy, tone adaptation
- context_engine: Deep personalization, pattern recognition
- prompt_architect: Doctor-like prompts with adaptive personality
- response_composer: Smart responses with dynamic UI elements
- proactive_engine: Anticipatory engagement and gentle nudges
"""

from app.services.chat.intelligence.memory_engine import MemoryEngine
from app.services.chat.intelligence.emotional_intelligence import EmotionalIntelligence
from app.services.chat.intelligence.context_engine import ContextEngine
from app.services.chat.intelligence.prompt_architect import PromptArchitect
from app.services.chat.intelligence.response_composer import ResponseComposer
from app.services.chat.intelligence.proactive_engine import ProactiveEngine

__all__ = [
    "MemoryEngine",
    "EmotionalIntelligence", 
    "ContextEngine",
    "PromptArchitect",
    "ResponseComposer",
    "ProactiveEngine"
]
