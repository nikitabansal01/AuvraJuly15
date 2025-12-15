"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA RESPONSE COMPOSER - Intelligent Response Generation
═══════════════════════════════════════════════════════════════════════════════
Composing responses that feel human, helpful, and perfectly calibrated.

CAPABILITIES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. DYNAMIC CHOICES - Context-aware button options
2. INTELLIGENT FOLLOW-UPS - Smart questions based on conversation
3. UI RECOMMENDATIONS - When to show slider vs buttons vs text
4. RESPONSE ENHANCEMENT - Post-process for optimal user experience
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ResponseType(Enum):
    """Types of responses we can compose."""
    TEXT = "text"
    CHOICE_BUTTONS = "choice_buttons"
    SLIDER = "slider"
    CONFIRMATION = "confirmation"


@dataclass
class SliderConfig:
    """Configuration for slider UI."""
    min_value: int = 1
    max_value: int = 9
    step: int = 1
    labels: List[str] = None
    default_value: Optional[int] = None
    
    def __post_init__(self):
        if self.labels is None:
            self.labels = ["None", "Mild", "Moderate", "Strong", "Extreme"]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "min": self.min_value,
            "max": self.max_value,
            "step": self.step,
            "labels": self.labels,
            "default_value": self.default_value
        }


@dataclass
class ComposedResponse:
    """A fully composed response ready for the user."""
    content: str
    response_type: ResponseType
    choices: Optional[List[str]] = None
    slider_config: Optional[SliderConfig] = None
    actions: Optional[List[Dict[str, Any]]] = None
    follow_up_intent: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "response_type": self.response_type.value,
            "choices": self.choices,
            "slider_config": self.slider_config.to_dict() if self.slider_config else None,
            "actions": self.actions,
            "follow_up_intent": self.follow_up_intent,
            "metadata": self.metadata
        }


class ResponseComposer:
    """
    Composes intelligent, contextual responses.
    
    This class ensures every response:
    - Has the right UI elements
    - Offers relevant choices
    - Feels natural and helpful
    """
    
    # Context-aware choice templates
    CONTEXTUAL_CHOICES = {
        "care_plan_modal": {
            "general": ["Show my plan", "Mark something done", "I need to skip something"],
            "completed_task": ["What's next?", "I'm done for now", "How am I doing?"],
            "wants_to_skip": ["Suggest alternative", "Skip for today", "Reschedule for later"],
            "overwhelmed": ["Just show one thing", "I need a break", "What's most important?"],
            "motivated": ["What else can I do?", "Show my progress", "Challenge me!"],
            "question_asked": ["Yes", "No", "Tell me more"]
        },
        "symptom_checkin": {
            "general": ["Log a symptom", "Check my trends", "I'm feeling good today"],
            "logging_symptom": ["Log another", "That's all", "What does this mean?"],
            "high_severity": ["What can help?", "Should I worry?", "It's manageable"],
            "pattern_mentioned": ["Tell me more", "What can I do?", "Got it"],
            "feeling_good": ["Track energy level", "Note what's working", "Just checking in"]
        },
        "personalise": {
            "general": ["Update my preferences", "See my profile", "Change notifications"],
            "making_change": ["Save changes", "Actually, nevermind", "What else can I change?"]
        },
        "know_body": {
            "general": ["Ask a question", "Explain my cycle", "Hormone basics"],
            "answered_question": ["I have another question", "That makes sense", "Can you explain more?"],
            "learning": ["What else should I know?", "How does this affect me?", "Got it, thanks!"]
        }
    }
    
    # Slider detection patterns
    SLIDER_TRIGGERS = [
        r"how severe",
        r"scale of",
        r"rate your",
        r"1 to (9|10)",
        r"on a scale",
        r"how bad",
        r"how intense",
        r"how strong",
        r"how would you rate"
    ]
    
    # Symptom types for slider labeling
    SYMPTOM_LABELS = {
        "bloating": ["None", "Barely there", "Mild", "Moderate", "Extreme"],
        "cramps": ["None", "Slight", "Mild", "Moderate", "Severe"],
        "headache": ["None", "Slight", "Mild", "Moderate", "Severe"],
        "fatigue": ["Energized", "Good", "Okay", "Tired", "Exhausted"],
        "mood": ["Great", "Good", "Okay", "Low", "Very low"],
        "anxiety": ["Calm", "Slight unease", "Mild", "Moderate", "Severe"],
        "pain": ["None", "Minimal", "Mild", "Moderate", "Severe"]
    }
    
    def __init__(self):
        pass
    
    def compose_response(
        self,
        raw_content: str,
        conversation_context: str,
        emotional_reading: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None
    ) -> ComposedResponse:
        """
        Compose a complete, polished response.
        """
        # 1. Detect response type
        response_type, slider_config = self._detect_response_type(raw_content)
        
        # 2. Generate appropriate choices
        choices = self._generate_choices(
            raw_content, 
            conversation_context,
            emotional_reading,
            user_message
        )
        
        # 3. Post-process content
        polished_content = self._polish_content(raw_content, emotional_reading)
        
        # 4. Generate any actions
        actions = self._generate_actions(raw_content, conversation_context)
        
        # 5. Determine follow-up intent
        follow_up_intent = self._determine_follow_up_intent(raw_content, conversation_context)
        
        return ComposedResponse(
            content=polished_content,
            response_type=response_type,
            choices=choices,
            slider_config=slider_config,
            actions=actions,
            follow_up_intent=follow_up_intent
        )
    
    def _detect_response_type(
        self, 
        content: str
    ) -> Tuple[ResponseType, Optional[SliderConfig]]:
        """Detect whether this should be text, slider, or choice-based."""
        content_lower = content.lower()
        
        # Check for slider triggers
        for pattern in self.SLIDER_TRIGGERS:
            if re.search(pattern, content_lower):
                # Determine appropriate slider config
                slider_config = self._build_slider_config(content_lower)
                return ResponseType.SLIDER, slider_config
        
        # Check for confirmation patterns
        confirmation_patterns = [
            "would you like me to",
            "shall i",
            "do you want me to",
            "should i go ahead"
        ]
        if any(p in content_lower for p in confirmation_patterns):
            return ResponseType.CONFIRMATION, None
        
        # Default to text
        return ResponseType.TEXT, None
    
    def _build_slider_config(self, content: str) -> SliderConfig:
        """Build appropriate slider config based on context."""
        # Try to detect symptom type from content
        for symptom, labels in self.SYMPTOM_LABELS.items():
            if symptom in content:
                return SliderConfig(
                    min_value=1,
                    max_value=9,
                    labels=labels
                )
        
        # Default severity slider
        return SliderConfig(
            min_value=1,
            max_value=9,
            labels=["None", "Mild", "Moderate", "Strong", "Extreme"]
        )
    
    def _generate_choices(
        self,
        content: str,
        conversation_context: str,
        emotional_reading: Optional[Dict[str, Any]],
        user_message: Optional[str]
    ) -> Optional[List[str]]:
        """Generate smart, contextual choice buttons."""
        content_lower = content.lower()
        
        # Get context-specific choices
        context_choices = self.CONTEXTUAL_CHOICES.get(conversation_context, {})
        
        # Check for specific situations
        
        # Question asked pattern
        if "would you like" in content_lower or content.rstrip().endswith("?"):
            if "yes" in content_lower or "no" in content_lower:
                return ["Yes, please", "No, thanks", "Tell me more"]
        
        # Celebration/completion
        if emotional_reading and emotional_reading.get("ready_to_celebrate"):
            if conversation_context == "care_plan_modal":
                return context_choices.get("completed_task", ["What's next?", "I'm done for now"])
        
        # User seems overwhelmed
        if emotional_reading and emotional_reading.get("needs_support"):
            if conversation_context == "care_plan_modal":
                return context_choices.get("overwhelmed", ["Just show one thing", "I need a break"])
        
        # High severity symptom mentioned
        if conversation_context == "symptom_checkin":
            if any(word in content_lower for word in ["severe", "high", "bad", "painful"]):
                return context_choices.get("high_severity", ["What can help?", "Should I worry?"])
        
        # Pattern-based choices
        choice_patterns = {
            "skip": ["Suggest alternative", "Skip for today", "Maybe tomorrow"],
            "done": ["Great! What's next?", "I'm done for now", "How am I doing?"],
            "how are you feeling": ["Great 🌟", "Okay", "Not great", "Need support"],
            "let me know": ["Yes please!", "Not right now", "Tell me more first"],
            "any questions": ["Yes, I have one", "No, I'm good", "Actually..."]
        }
        
        for pattern, choices in choice_patterns.items():
            if pattern in content_lower:
                return choices
        
        # Default choices for context
        return context_choices.get("general")
    
    def _polish_content(
        self, 
        content: str,
        emotional_reading: Optional[Dict[str, Any]]
    ) -> str:
        """Polish the response content for optimal delivery."""
        polished = content.strip()
        
        # Ensure not too many emojis
        emoji_count = sum(1 for c in polished if ord(c) > 127000)
        if emoji_count > 2:
            # Remove excess emojis, keep first two
            emoji_positions = [i for i, c in enumerate(polished) if ord(c) > 127000]
            if len(emoji_positions) > 2:
                # Remove from end
                for pos in reversed(emoji_positions[2:]):
                    polished = polished[:pos] + polished[pos+1:]
        
        # Ensure response isn't too long
        sentences = polished.split('. ')
        if len(sentences) > 5:
            polished = '. '.join(sentences[:4]) + '.'
        
        # Add signature emoji if emotional and none present
        if emotional_reading:
            if emotional_reading.get("needs_support") and '💜' not in polished:
                if not any(e in polished for e in ['😊', '🎉', '❤️', '💪']):
                    polished = polished.rstrip('.!?') + ' 💜'
        
        return polished
    
    def _generate_actions(
        self, 
        content: str,
        conversation_context: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Generate any frontend actions based on response."""
        content_lower = content.lower()
        actions = []
        
        # Check for navigational intents
        if "show" in content_lower and "plan" in content_lower:
            actions.append({
                "type": "navigate",
                "target": "care_plan",
                "params": {}
            })
        
        if "complete" in content_lower or "done" in content_lower:
            if "assignment" in content_lower or "task" in content_lower:
                actions.append({
                    "type": "complete_assignment",
                    "target": "current",
                    "params": {}
                })
        
        return actions if actions else None
    
    def _determine_follow_up_intent(
        self, 
        content: str,
        conversation_context: str
    ) -> Optional[str]:
        """Determine what kind of follow-up the user might want."""
        content_lower = content.lower()
        
        intent_patterns = {
            "awaiting_confirmation": ["would you like", "shall i", "do you want"],
            "awaiting_severity": ["scale of", "how severe", "rate your"],
            "awaiting_selection": ["which one", "what would you prefer", "choose"],
            "open_ended": ["tell me more", "what's on your mind", "how are you feeling"],
            "closed_question": ["is that", "does that", "did you"]
        }
        
        for intent, patterns in intent_patterns.items():
            if any(p in content_lower for p in patterns):
                return intent
        
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SMART GREETING GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_smart_greeting(
    user_name: Optional[str],
    cycle_context: Dict[str, Any],
    time_context: Dict[str, Any],
    streak_context: Dict[str, Any],
    relationship_stage: str
) -> str:
    """Generate a contextual, personalized greeting."""
    greetings = []
    
    name_part = user_name.split()[0] if user_name else "there"
    
    # Time-based opener
    time_of_day = time_context.get("time_of_day", "morning")
    if time_of_day == "early_morning":
        greetings.append(f"Early start, {name_part}!")
    elif time_of_day == "morning":
        greetings.append(f"Good morning, {name_part}!")
    elif time_of_day == "afternoon":
        greetings.append(f"Hey, {name_part}!")
    elif time_of_day == "evening":
        greetings.append(f"Hey, {name_part}! Winding down?")
    elif time_of_day == "night":
        greetings.append(f"Still up, {name_part}?")
    else:
        greetings.append(f"Hey, {name_part}!")
    
    # Add cycle awareness if relevant
    phase = cycle_context.get("phase", "").lower()
    if phase:
        energy = cycle_context.get("energy_expectation", "")
        if energy == "peak":
            greetings.append("You're in your power phase! 💪")
        elif energy == "low" and phase == "menstrual":
            greetings.append("Day 1 energy - go easy on yourself 💜")
        elif cycle_context.get("approaching_period"):
            greetings.append("Period's coming - how are you holding up?")
    
    # Add streak celebration if applicable
    streak = streak_context.get("current_streak", 0)
    if streak == 3:
        greetings.append("3 days in a row! You're building momentum 🔥")
    elif streak == 7:
        greetings.append("One week streak! That's amazing! 🎉")
    elif streak >= 14:
        greetings.append(f"{streak} day streak - you're crushing it! 💜")
    elif streak_context.get("risk_alert"):
        greetings.append("Let's keep your streak going today!")
    
    # For new users
    if relationship_stage == "new_acquaintance":
        greetings.append("I'm AUVRA, your wellness companion. How can I help today?")
    else:
        greetings.append("How can I help today?")
    
    # Combine intelligently
    if len(greetings) > 2:
        return f"{greetings[0]} {greetings[1]} {greetings[-1]}"
    return " ".join(greetings)


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE ENHANCEMENT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def add_empathetic_opener(content: str, emotional_state: str) -> str:
    """Add an empathetic opener based on emotional state."""
    openers = {
        "anxious": "I hear you - ",
        "stressed": "That sounds tough. ",
        "sad": "I'm sorry you're feeling this way. ",
        "overwhelmed": "That's a lot. ",
        "frustrated": "I get why that's frustrating. "
    }
    
    if emotional_state in openers:
        return openers[emotional_state] + content[0].lower() + content[1:]
    return content


def add_validation(content: str) -> str:
    """Add validation before advice."""
    validations = [
        "That makes total sense. ",
        "I completely understand. ",
        "That's totally valid. "
    ]
    import random
    return random.choice(validations) + content


def soften_advice(content: str) -> str:
    """Soften directive language."""
    replacements = {
        "You should": "You might want to",
        "You need to": "It could help to",
        "You have to": "Consider",
        "You must": "You might consider"
    }
    
    result = content
    for original, replacement in replacements.items():
        result = result.replace(original, replacement)
        result = result.replace(original.lower(), replacement.lower())
    
    return result
