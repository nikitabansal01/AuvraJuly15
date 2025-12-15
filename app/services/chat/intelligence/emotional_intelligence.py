"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA EMOTIONAL INTELLIGENCE - Understanding & Responding to Feelings
═══════════════════════════════════════════════════════════════════════════════
A good doctor doesn't just treat symptoms - they see the whole person.

CAPABILITIES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. EMOTION DETECTION - Identify emotional tone in messages
2. EMPATHY MATCHING - Respond with appropriate emotional resonance
3. TONE ADAPTATION - Adjust communication style to user's state
4. CELEBRATION ENGINE - Recognize and celebrate wins appropriately
5. COMFORT SYSTEM - Provide appropriate support during struggles
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class EmotionalState(Enum):
    """Primary emotional states."""
    JOYFUL = "joyful"
    CONTENT = "content"
    NEUTRAL = "neutral"
    ANXIOUS = "anxious"
    STRESSED = "stressed"
    SAD = "sad"
    FRUSTRATED = "frustrated"
    OVERWHELMED = "overwhelmed"
    STRUGGLING = "struggling"
    HOPEFUL = "hopeful"
    PROUD = "proud"


class EnergyLevel(Enum):
    """Energy levels affecting communication style."""
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    DEPLETED = "depleted"


@dataclass
class EmotionalReading:
    """Result of emotional analysis."""
    primary_emotion: EmotionalState
    secondary_emotions: List[EmotionalState]
    energy_level: EnergyLevel
    confidence: float
    emotional_intensity: float  # 0-1 scale
    key_triggers: List[str]
    needs_support: bool
    ready_to_celebrate: bool
    communication_approach: str  # "warm", "gentle", "energetic", "calm", "supportive"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_emotion": self.primary_emotion.value,
            "secondary_emotions": [e.value for e in self.secondary_emotions],
            "energy_level": self.energy_level.value,
            "confidence": self.confidence,
            "emotional_intensity": self.emotional_intensity,
            "key_triggers": self.key_triggers,
            "needs_support": self.needs_support,
            "ready_to_celebrate": self.ready_to_celebrate,
            "communication_approach": self.communication_approach
        }


class EmotionalIntelligence:
    """
    The emotional awareness system that makes AUVRA truly empathetic.
    
    A good doctor:
    - Notices when something is wrong before being told
    - Adjusts their tone to match the patient's state
    - Celebrates successes genuinely
    - Provides comfort without being patronizing
    """
    
    # Emotion lexicons
    EMOTION_PATTERNS = {
        EmotionalState.JOYFUL: {
            "keywords": ["so happy", "amazing", "wonderful", "fantastic", "thrilled", "love it", 
                        "best day", "so excited", "yay", "🎉", "😊", "💕", "❤️", "can't believe"],
            "weight": 1.0
        },
        EmotionalState.PROUD: {
            "keywords": ["finally did it", "proud of", "accomplished", "achieved", "completed",
                        "managed to", "first time", "personal best", "breakthrough"],
            "weight": 0.9
        },
        EmotionalState.HOPEFUL: {
            "keywords": ["hoping", "looking forward", "can't wait", "excited about", "optimistic",
                        "thinking about trying", "want to start", "planning to"],
            "weight": 0.7
        },
        EmotionalState.CONTENT: {
            "keywords": ["good", "nice", "okay", "fine", "pretty good", "not bad", "decent",
                        "going well", "comfortable"],
            "weight": 0.5
        },
        EmotionalState.NEUTRAL: {
            "keywords": ["just", "normal", "usual", "same as always", "whatever", "i guess"],
            "weight": 0.3
        },
        EmotionalState.ANXIOUS: {
            "keywords": ["worried", "anxious", "nervous", "scared", "afraid", "panicking",
                        "can't stop thinking", "what if", "restless", "on edge"],
            "weight": 0.8
        },
        EmotionalState.STRESSED: {
            "keywords": ["stressed", "pressure", "too much", "deadline", "busy", "hectic",
                        "no time", "behind", "rushing", "crazy week"],
            "weight": 0.7
        },
        EmotionalState.FRUSTRATED: {
            "keywords": ["frustrated", "annoyed", "irritated", "ugh", "why can't", "so hard",
                        "doesn't work", "keep trying", "hate", "sick of"],
            "weight": 0.8
        },
        EmotionalState.SAD: {
            "keywords": ["sad", "depressed", "down", "blue", "unhappy", "crying", "tears",
                        "miss", "lonely", "empty", "😢", "😔", "💔"],
            "weight": 0.9
        },
        EmotionalState.OVERWHELMED: {
            "keywords": ["overwhelmed", "too much", "can't handle", "breaking down", "falling apart",
                        "don't know what to do", "lost", "drowning", "exhausted", "burned out"],
            "weight": 1.0
        },
        EmotionalState.STRUGGLING: {
            "keywords": ["struggling", "hard time", "difficult", "tough", "challenging",
                        "can't seem to", "having trouble", "not easy"],
            "weight": 0.7
        }
    }
    
    # Energy indicators
    ENERGY_PATTERNS = {
        EnergyLevel.HIGH: ["excited", "energized", "pumped", "ready", "motivated", "can't wait"],
        EnergyLevel.MODERATE: ["okay", "fine", "managing", "doing alright"],
        EnergyLevel.LOW: ["tired", "exhausted", "drained", "sleepy", "fatigued", "no energy"],
        EnergyLevel.DEPLETED: ["completely drained", "can't function", "barely", "burned out", "running on empty"]
    }
    
    # Communication approach mapping
    APPROACH_MAPPING = {
        (EmotionalState.JOYFUL, EnergyLevel.HIGH): "celebratory",
        (EmotionalState.PROUD, EnergyLevel.HIGH): "celebratory",
        (EmotionalState.PROUD, EnergyLevel.MODERATE): "warm",
        (EmotionalState.HOPEFUL, EnergyLevel.HIGH): "encouraging",
        (EmotionalState.HOPEFUL, EnergyLevel.MODERATE): "supportive",
        (EmotionalState.CONTENT, EnergyLevel.MODERATE): "friendly",
        (EmotionalState.NEUTRAL, EnergyLevel.MODERATE): "professional",
        (EmotionalState.ANXIOUS, EnergyLevel.HIGH): "calming",
        (EmotionalState.ANXIOUS, EnergyLevel.LOW): "gentle",
        (EmotionalState.STRESSED, EnergyLevel.HIGH): "grounding",
        (EmotionalState.STRESSED, EnergyLevel.LOW): "supportive",
        (EmotionalState.FRUSTRATED, EnergyLevel.HIGH): "validating",
        (EmotionalState.FRUSTRATED, EnergyLevel.LOW): "empathetic",
        (EmotionalState.SAD, EnergyLevel.LOW): "nurturing",
        (EmotionalState.OVERWHELMED, EnergyLevel.DEPLETED): "gentle",
        (EmotionalState.STRUGGLING, EnergyLevel.LOW): "supportive",
    }
    
    def __init__(self):
        self.history: List[EmotionalReading] = []
    
    def analyze_message(
        self, 
        message: str,
        context: Optional[Dict[str, Any]] = None,
        memory_emotional: Optional[Dict[str, Any]] = None
    ) -> EmotionalReading:
        """
        Analyze a message for emotional content.
        Returns comprehensive emotional reading.
        """
        message_lower = message.lower()
        
        # Score each emotion
        emotion_scores: Dict[EmotionalState, float] = {}
        key_triggers: List[str] = []
        
        for emotion, data in self.EMOTION_PATTERNS.items():
            score = 0.0
            for keyword in data["keywords"]:
                if keyword in message_lower:
                    score += data["weight"]
                    key_triggers.append(keyword)
            emotion_scores[emotion] = score
        
        # Determine primary emotion
        if not any(emotion_scores.values()):
            primary_emotion = EmotionalState.NEUTRAL
        else:
            primary_emotion = max(emotion_scores, key=emotion_scores.get)
        
        # Get secondary emotions (above threshold)
        threshold = 0.3
        secondary_emotions = [
            e for e, s in emotion_scores.items() 
            if s >= threshold and e != primary_emotion
        ][:2]
        
        # Analyze energy level
        energy_level = self._detect_energy_level(message_lower)
        
        # Calculate emotional intensity
        max_score = max(emotion_scores.values()) if any(emotion_scores.values()) else 0
        intensity = min(max_score / 2, 1.0)  # Normalize to 0-1
        
        # Boost intensity for exclamation marks and caps
        if message.count('!') >= 2 or len(re.findall(r'[A-Z]{3,}', message)) >= 1:
            intensity = min(intensity + 0.2, 1.0)
        
        # Determine needs
        needs_support = primary_emotion in [
            EmotionalState.ANXIOUS, EmotionalState.STRESSED, 
            EmotionalState.SAD, EmotionalState.OVERWHELMED,
            EmotionalState.STRUGGLING
        ] or (memory_emotional and memory_emotional.get("needs_extra_care"))
        
        ready_to_celebrate = primary_emotion in [
            EmotionalState.JOYFUL, EmotionalState.PROUD
        ] and intensity > 0.5
        
        # Determine communication approach
        approach = self._determine_approach(primary_emotion, energy_level)
        
        # Consider memory context
        if memory_emotional:
            if memory_emotional.get("recent_mood_trend") == "struggling":
                approach = "gentle" if approach == "professional" else approach
                needs_support = True
            elif memory_emotional.get("recent_mood_trend") == "positive" and not needs_support:
                approach = "warm" if approach == "professional" else approach
        
        # Calculate confidence
        confidence = min(intensity + 0.3, 1.0) if any(emotion_scores.values()) else 0.5
        
        reading = EmotionalReading(
            primary_emotion=primary_emotion,
            secondary_emotions=secondary_emotions,
            energy_level=energy_level,
            confidence=confidence,
            emotional_intensity=intensity,
            key_triggers=list(set(key_triggers))[:5],
            needs_support=needs_support,
            ready_to_celebrate=ready_to_celebrate,
            communication_approach=approach
        )
        
        self.history.append(reading)
        
        return reading
    
    def _detect_energy_level(self, message: str) -> EnergyLevel:
        """Detect energy level from message."""
        for level, keywords in self.ENERGY_PATTERNS.items():
            for keyword in keywords:
                if keyword in message:
                    return level
        return EnergyLevel.MODERATE
    
    def _determine_approach(self, emotion: EmotionalState, energy: EnergyLevel) -> str:
        """Determine the best communication approach."""
        # Check exact match first
        if (emotion, energy) in self.APPROACH_MAPPING:
            return self.APPROACH_MAPPING[(emotion, energy)]
        
        # Fallback based on emotion category
        positive_emotions = [EmotionalState.JOYFUL, EmotionalState.PROUD, 
                           EmotionalState.HOPEFUL, EmotionalState.CONTENT]
        negative_emotions = [EmotionalState.ANXIOUS, EmotionalState.STRESSED,
                           EmotionalState.SAD, EmotionalState.OVERWHELMED,
                           EmotionalState.FRUSTRATED, EmotionalState.STRUGGLING]
        
        if emotion in positive_emotions:
            return "warm" if energy != EnergyLevel.DEPLETED else "gentle"
        elif emotion in negative_emotions:
            if energy in [EnergyLevel.LOW, EnergyLevel.DEPLETED]:
                return "gentle"
            return "supportive"
        
        return "friendly"
    
    def get_tone_guidance(self, reading: EmotionalReading) -> Dict[str, Any]:
        """
        Get specific guidance for response tone based on emotional reading.
        """
        guidance = {
            "primary_tone": reading.communication_approach,
            "emoji_usage": "moderate",  # none, light, moderate, expressive
            "response_length": "standard",  # brief, standard, detailed
            "validation_first": False,
            "celebration_ok": False,
            "direct_advice_ok": True,
            "question_style": "gentle",  # direct, gentle, exploratory
            "sentence_style": "clear",  # clear, soft, enthusiastic
            "opening_approach": "neutral"
        }
        
        # Adjust based on emotional state
        if reading.needs_support:
            guidance["validation_first"] = True
            guidance["direct_advice_ok"] = False
            guidance["question_style"] = "gentle"
            guidance["sentence_style"] = "soft"
            guidance["opening_approach"] = "empathetic"
            guidance["emoji_usage"] = "light"  # Subtler, caring emojis
        
        if reading.ready_to_celebrate:
            guidance["celebration_ok"] = True
            guidance["emoji_usage"] = "expressive"
            guidance["sentence_style"] = "enthusiastic"
            guidance["opening_approach"] = "celebratory"
        
        # Adjust for energy level
        if reading.energy_level == EnergyLevel.DEPLETED:
            guidance["response_length"] = "brief"
            guidance["sentence_style"] = "soft"
            guidance["emoji_usage"] = "light"
        elif reading.energy_level == EnergyLevel.HIGH:
            guidance["emoji_usage"] = "moderate"
        
        # Specific emotion adjustments
        if reading.primary_emotion == EmotionalState.ANXIOUS:
            guidance["opening_approach"] = "grounding"
            guidance["sentence_style"] = "calm"
        elif reading.primary_emotion == EmotionalState.OVERWHELMED:
            guidance["response_length"] = "brief"
            guidance["opening_approach"] = "acknowledging"
        elif reading.primary_emotion == EmotionalState.FRUSTRATED:
            guidance["validation_first"] = True
            guidance["opening_approach"] = "validating"
        
        return guidance
    
    def generate_empathetic_opener(self, reading: EmotionalReading) -> str:
        """
        Generate an appropriate opening phrase based on emotional reading.
        """
        openers = {
            # Positive states
            "celebratory": [
                "That's wonderful! 🎉",
                "I'm so happy for you! 💜",
                "This is amazing news!",
                "What a great accomplishment!"
            ],
            "warm": [
                "I'm glad to hear that 💜",
                "That sounds lovely!",
                "How nice!",
                "I appreciate you sharing that."
            ],
            "encouraging": [
                "That's exciting!",
                "I love that energy!",
                "Great attitude!",
                "You've got this!"
            ],
            
            # Neutral states
            "friendly": [
                "Thanks for sharing.",
                "I hear you.",
                "Got it!",
                "Okay, let's work with that."
            ],
            "professional": [
                "I understand.",
                "Thank you for sharing that.",
                "Let me help with that.",
                ""  # Sometimes no opener is best
            ],
            
            # Support states
            "supportive": [
                "I hear you, and that makes total sense.",
                "That's completely understandable.",
                "I get it – that's tough.",
                "Thank you for being open about that."
            ],
            "gentle": [
                "I'm here for you 💜",
                "That sounds really hard.",
                "I'm sorry you're going through this.",
                "Take your time – there's no rush."
            ],
            "nurturing": [
                "Oh, I'm sorry 💜",
                "That sounds really difficult.",
                "My heart goes out to you.",
                "Please be gentle with yourself."
            ],
            
            # Calming states
            "calming": [
                "Take a deep breath with me.",
                "Let's slow down for a moment.",
                "You're okay. Let's work through this together.",
                "One thing at a time."
            ],
            "grounding": [
                "Let's pause and breathe.",
                "Right now, in this moment, you're safe.",
                "Let's break this down together.",
                "You don't have to figure it all out right now."
            ],
            
            # Validation states
            "validating": [
                "I completely understand why you'd feel that way.",
                "Your frustration is totally valid.",
                "That would be frustrating for anyone.",
                "It makes sense that you feel this way."
            ],
            "empathetic": [
                "I can hear how much this is affecting you.",
                "That sounds really challenging.",
                "I wish I could make it easier.",
                "You're dealing with a lot right now."
            ],
            "acknowledging": [
                "That's a lot to carry.",
                "I hear you – this is overwhelming.",
                "Let's take this one step at a time.",
                "You don't have to do everything at once."
            ]
        }
        
        approach = reading.communication_approach
        if approach in openers:
            import random
            options = [o for o in openers[approach] if o]  # Filter empty strings
            return random.choice(options) if options else ""
        
        return ""
    
    def should_ask_followup(self, reading: EmotionalReading) -> Tuple[bool, Optional[str]]:
        """
        Determine if we should ask an emotional follow-up question.
        Returns (should_ask, suggested_question).
        """
        if reading.emotional_intensity < 0.5:
            return False, None
        
        followups = {
            EmotionalState.ANXIOUS: [
                "Would you like to talk about what's on your mind?",
                "What would feel helpful right now?",
                "Is there something specific you're worried about?"
            ],
            EmotionalState.STRESSED: [
                "What's weighing on you the most right now?",
                "Would it help to talk through what's going on?",
                "Is there anything I can help you with today?"
            ],
            EmotionalState.SAD: [
                "Do you want to share what's going on?",
                "I'm here if you want to talk about it.",
                "How can I support you today?"
            ],
            EmotionalState.OVERWHELMED: [
                "What feels most urgent right now?",
                "Would it help to pick just one small thing to focus on?",
                "How can we make today a little easier?"
            ],
            EmotionalState.PROUD: [
                "What made this achievement special for you?",
                "How are you planning to celebrate?",
                "What helped you get there?"
            ],
            EmotionalState.JOYFUL: [
                "What's making you feel so good?",
                "I'd love to hear more!",
                "What's the best part?"
            ]
        }
        
        if reading.primary_emotion in followups:
            import random
            return True, random.choice(followups[reading.primary_emotion])
        
        return False, None


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def format_emotional_guidance_for_prompt(reading: EmotionalReading, guidance: Dict[str, Any]) -> str:
    """Format emotional intelligence findings for the LLM prompt."""
    lines = ["\n💜 EMOTIONAL CONTEXT:"]
    
    lines.append(f"   User appears: {reading.primary_emotion.value}")
    if reading.secondary_emotions:
        secondary = ", ".join(e.value for e in reading.secondary_emotions)
        lines.append(f"   Also sensing: {secondary}")
    
    lines.append(f"   Energy: {reading.energy_level.value}")
    lines.append(f"   Recommended approach: {guidance['primary_tone']}")
    
    if guidance.get("validation_first"):
        lines.append("   ⚡ VALIDATE FEELINGS BEFORE GIVING ADVICE")
    
    if guidance.get("celebration_ok"):
        lines.append("   ⚡ CELEBRATE THIS WIN WITH THEM!")
    
    if not guidance.get("direct_advice_ok"):
        lines.append("   ⚡ Ask before advising - they need to feel heard first")
    
    if guidance.get("opening_approach") not in ["neutral", "professional"]:
        lines.append(f"   ⚡ Start with {guidance['opening_approach']} tone")
    
    return "\n".join(lines)
