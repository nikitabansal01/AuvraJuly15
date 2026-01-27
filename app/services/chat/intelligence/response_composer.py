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
            self.labels = ["None 😊", "Mild", "Moderate", "Strong", "Intense 💪"]
    
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
    
    # Context-aware choice templates (engaging and friendly)
    CONTEXTUAL_CHOICES = {
        "care_plan_modal": {
            "general": ["✨ Show my plan", "✅ Mark something done", "🔄 I need to skip something"],
            "completed_task": ["🎯 What's next?", "😌 I'm done for now", "📊 How am I doing?"],
            "wants_to_skip": ["💡 Suggest alternative", "⏭️ Skip for today", "📅 Reschedule for later"],
            "overwhelmed": ["🎯 Just show one thing", "☕ I need a break", "⭐ What's most important?"],
            "motivated": ["💪 What else can I do?", "📈 Show my progress", "🚀 Challenge me!"],
            "question_asked": ["Yes please! 💜", "No thanks", "Tell me more 🤔"]
        },
        "symptom_checkin": {
            "general": ["📝 Log a symptom", "📊 Check my trends", "✨ I'm feeling good today!"],
            "logging_symptom": ["➕ Log another", "✅ That's all", "🤔 What does this mean?"],
            "high_severity": ["💊 What can help?", "🤔 Should I worry?", "👍 It's manageable"],
            "pattern_mentioned": ["📖 Tell me more", "💪 What can I do?", "👍 Got it!"],
            "feeling_good": ["⚡ Track energy level", "📝 Note what's working", "👋 Just checking in"]
        },
        "personalise": {
            "general": ["🥗 Personalize my diet", "💪 Personalize my exercise", "😴 Personalize my sleep", "🎯 Personalize my goals"],
            "question_asked": ["🥗 Personalize my diet", "💪 Personalize my exercise", "😴 Personalize my sleep", "🎯 Personalize my goals"],
            "making_change": ["💾 Save changes", "↩️ Actually, nevermind", "🔧 What else can I change?"],
            "diet": ["🥬 I'm vegetarian", "🥩 High protein", "🥗 No restrictions", "🤔 Not sure"],
            "exercise": ["🏃 Moderate exercise", "🚶 Sedentary", "💪 Very active", "🧘 Yoga/Pilates"],
            "sleep": ["😴 7-8 hours", "🌙 5-6 hours", "☕ Less than 5h", "🛌 8+ hours"]
        },
        "know_body": {
            "general": ["❓ Ask a question", "🌙 Explain my cycle", "🧬 Hormone basics"],
            "answered_question": ["🙋 I have another question", "💡 That makes sense", "📚 Can you explain more?"],
            "learning": ["📖 What else should I know?", "🎯 How does this affect me?", "👍 Got it, thanks!"]
        },
        "weekly_checkin": {
             "general": ["📝 Log a symptom", "📊 Check my trends", "✨ I'm feeling good today!"],
             "protein_update": ["👍 Sounds good", "🥩 I'll eat more protein", "🤔 Why tomorrow?"]
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
    
    # Symptom types for slider labeling (friendly labels)
    SYMPTOM_LABELS = {
        "bloating": ["None 😊", "Barely there", "Mild", "Noticeable", "Very bloated 😔"],
        "cramps": ["None 😊", "Slight", "Mild", "Moderate", "Intense 😣"],
        "headache": ["None 😊", "Slight", "Mild", "Moderate", "Severe 😔"],
        "fatigue": ["Energized ⚡", "Good", "Okay", "Tired", "Exhausted 😴"],
        "mood": ["Great 😊", "Good", "Okay", "Low 😔", "Very low 💙"],
        "anxiety": ["Calm ☮️", "Slight unease", "Mild", "Moderate", "Overwhelming 😰"],
        "pain": ["None 😊", "Minimal", "Mild", "Moderate", "Severe 😣"]
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
        polished_content = self._polish_content(raw_content, emotional_reading, conversation_context)
        
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
        """Generate smart, contextual, engaging choice buttons."""
        content_lower = content.lower()
        
        # 1. Check for dynamic [OPTIONS: ...] tag first
        import re
        options_match = re.search(r'\[OPTIONS:\s*(.*?)\]', content, re.IGNORECASE)
        if options_match:
            options_text = options_match.group(1)
            # Split by | and clean up
            dynamic_choices = [opt.strip() for opt in options_text.split('|') if opt.strip()]
            if dynamic_choices:
                return dynamic_choices
        
        # 2. Get context-specific choices
        context_choices = self.CONTEXTUAL_CHOICES.get(conversation_context, {})
        
        # Personalize deep-dives (diet, exercise, etc)
        if conversation_context == "personalise":
            # If AI asks about specific lifestyle aspects
            if any(w in content_lower for w in ["diet", "food", "eat"]):
                return context_choices.get("diet")
            if any(w in content_lower for w in ["exercise", "workout", "active", "routine"]):
                return context_choices.get("exercise")
            if any(w in content_lower for w in ["sleep", "rest", "night"]):
                return context_choices.get("sleep")
            
            # If AI asks a general "how to personalize" question
            if "?" in content_lower or "let me know" in content_lower or "aspects" in content_lower:
                return context_choices.get("question_asked")

        # Check for specific situations
        
        # Question asked pattern
        if "would you like" in content_lower or content.rstrip().endswith("?"):
            if "yes" in content_lower or "no" in content_lower:
                return ["Yes please! 💜", "No thanks", "Tell me more 🤔"]
        
        # Celebration/completion
        if emotional_reading and emotional_reading.get("ready_to_celebrate"):
            if conversation_context == "care_plan_modal":
                return context_choices.get("completed_task", ["🎯 What's next?", "😌 I'm done for now"])
        
        # User seems overwhelmed
        if emotional_reading and emotional_reading.get("needs_support"):
            if conversation_context == "care_plan_modal":
                return context_choices.get("overwhelmed", ["🎯 Just show one thing", "☕ I need a break"])
        
        # High severity symptom mentioned
        if conversation_context == "symptom_checkin":
            if any(word in content_lower for word in ["severe", "high", "bad", "painful"]):
                return context_choices.get("high_severity", ["💊 What can help?", "🤔 Should I worry?"])
        
        # Pattern-based choices (engaging with emojis)
        choice_patterns = {
            "skip": ["💡 Suggest alternative", "⏭️ Skip for today", "📅 Maybe tomorrow"],
            "done": ["🎉 Great! What's next?", "😌 I'm done for now", "📊 How am I doing?"],
            "how are you feeling": ["🌟 Great!", "👍 Okay", "😔 Not great", "💙 Need support"],
            "let me know": ["Yes please! 💜", "⏸️ Not right now", "🤔 Tell me more first"],
            "any questions": ["🙋 Yes, I have one", "👍 No, I'm good", "🤔 Actually..."],
            "what can i help": ["📝 Track a symptom", "📋 Check my plan", "❓ Ask a question"],
            "anything else": ["✨ Yes, one more thing", "👍 That's all", "💭 Let me think"]
        }
        
        for pattern, choices in choice_patterns.items():
            if pattern in content_lower:
                return choices
        
        # Default choices for context (with fallback for unknown contexts)
        default_choices = context_choices.get("general")
        if default_choices:
            return default_choices
        
        # Ultimate fallback for any unknown context - NEVER return None
        return ["Yes please! 💜", "No thanks", "Tell me more 🤔"]
    
    def _polish_content(
        self, 
        content: str,
        emotional_reading: Optional[Dict[str, Any]],
        conversation_context: str = ""
    ) -> str:
        """Polish the response content for optimal delivery."""
        polished = content.strip()
        
        # Remove [OPTIONS: ...] tag
        import re
        polished = re.sub(r'\[OPTIONS:.*?\]', '', polished, flags=re.IGNORECASE).strip()
        
        # Strip markdown formatting (mobile doesn't render it, shows literally)
        # Strip markdown formatting (mobile doesn't render it, shows literally)
        # Robustly remove ** and __ and * wrappers
        # Replace **text** with text (DOTALL to handle newlines)
        polished = re.sub(r'\*\*(.*?)\*\*', r'\1', polished, flags=re.DOTALL)
        polished = re.sub(r'__(.*?)__', r'\1', polished, flags=re.DOTALL)
        
        # Handle italics *text* or _text_ but be careful not to break bullet points
        # Only replace if not at start of line (bullets)
        polished = re.sub(r' (?<!^)\*(.*?)\*', r' \1', polished) # *text* inside line
        polished = re.sub(r' _(.*?)_', r' \1', polished)
        
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
        
        # Add "tomorrow onwards" note for protein/diet changes in weekly check-in
        if conversation_context == "weekly_checkin":
             if any(w in polished.lower() for w in ["protein", "diet", "eating", "food"]) and "tomorrow" not in polished.lower():
                 polished += " (Your action plan will update from tomorrow onwards! 📅)"

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


def generate_smart_follow_up(user_message: str, context: Dict[str, Any]) -> Optional[str]:
    """
    Generate intelligent follow-up questions that show curiosity and care.
    
    Goes deeper - if user says "tired", explore why, when, patterns.
    """
    message_lower = user_message.lower()
    
    # Tired/Exhausted → Explore deeper
    if any(word in message_lower for word in ["tired", "exhausted", "drained", "low energy"]):
        follow_ups = [
            "When did you start feeling this way? Was it sudden or gradual?",
            "How's your sleep been lately? Getting enough rest?",
            "Is this a new feeling or have you noticed this pattern before?",
            "What do you think might be contributing to this?"
        ]
        import random
        return random.choice(follow_ups)
    
    # Stressed/Anxious → Explore triggers
    if any(word in message_lower for word in ["stressed", "anxious", "overwhelmed", "worried"]):
        follow_ups = [
            "What's weighing on you most right now?",
            "When do you notice this feeling most - morning, evening, specific situations?",
            "Is this related to something specific or more of a general feeling?",
            "Have you noticed what makes it better or worse?"
        ]
        import random
        return random.choice(follow_ups)
    
    # Pain → Get specifics
    if any(word in message_lower for word in ["pain", "hurts", "cramps", "ache"]):
        follow_ups = [
            "On a scale of 1-9, how intense is it right now?",
            "Where exactly do you feel it? Does it radiate anywhere?",
            "When did it start? Has it been constant or coming and going?",
            "Does anything make it better or worse?"
        ]
        import random
        return random.choice(follow_ups)
    
    # Sleep issues → Pattern exploration
    if any(word in message_lower for word in ["can't sleep", "insomnia", "waking up", "sleep badly"]):
        follow_ups = [
            "Tell me more - is it trouble falling asleep or staying asleep?",
            "How many nights this week has this happened?",
            "Do you notice any patterns with your cycle?",
            "What have you tried that's helped, even a little?"
        ]
        import random
        return random.choice(follow_ups)
    
    # Mood changes → Timing and patterns
    if any(word in message_lower for word in ["mood", "emotional", "crying", "irritable", "angry"]):
        follow_ups = [
            "When did you start noticing this shift?",
            "Do you see any connection to your cycle timing?",
            "Is this familiar or does it feel different than usual?",
            "What usually helps when you feel this way?"
        ]
        import random
        return random.choice(follow_ups)
    
    # Accomplishment → Build on success
    if any(word in message_lower for word in ["completed", "finished", "did it", "accomplished", "done"]):
        follow_ups = [
            "That's amazing! What made you decide to tackle it today?",
            "I'm proud of you! How do you feel now that it's done?",
            "Awesome! What was the hardest part?",
            "What helped you get it done?"
        ]
        import random
        return random.choice(follow_ups)
    
    # Change mentioned → Understand better
    if any(word in message_lower for word in ["different", "changed", "new", "unusual"]):
        follow_ups = [
            "Interesting - tell me more about what's different.",
            "When did you first notice this change?",
            "How does it compare to how things usually are for you?",
            "Do you have any sense of what might have triggered it?"
        ]
        import random
        return random.choice(follow_ups)
    
    return None
