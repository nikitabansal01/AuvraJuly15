"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA SESSION SUMMARIZER - Conversation Intelligence
═══════════════════════════════════════════════════════════════════════════════
Auto-generate meaningful summaries of conversations with actionable insights.

SUMMARY COMPONENTS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. KEY TOPICS - What was discussed
2. EMOTIONAL JOURNEY - How user felt throughout
3. ACTION ITEMS - What user committed to doing
4. INSIGHTS GAINED - What we learned about user
5. NEXT STEPS - Suggestions for next conversation
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class SessionSummarizer:
    """
    Generate intelligent conversation summaries.
    
    Helps users remember what they discussed and track progress over time.
    """
    
    def summarize_session(
        self,
        messages: List[Dict[str, Any]],
        emotional_states: List[str],
        session_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate session summary from conversation.
        
        Args:
            messages: List of message dicts with role, content, timestamp
            emotional_states: List of detected emotional states
            session_metadata: Additional session info
        
        Returns:
            {
                "summary": "High-level overview",
                "key_topics": ["topic1", "topic2", ...],
                "emotional_journey": {...},
                "action_items": [...],
                "insights": [...],
                "next_steps": [...],
                "duration_minutes": 15,
                "message_count": 20
            }
        """
        # Calculate basic metrics
        duration = self._calculate_duration(messages)
        message_count = len(messages)
        
        # Extract key topics
        key_topics = self._extract_key_topics(messages)
        
        # Analyze emotional journey
        emotional_journey = self._analyze_emotional_journey(emotional_states)
        
        # Extract action items
        action_items = self._extract_action_items(messages)
        
        # Generate insights
        insights = self._generate_insights(messages, emotional_states)
        
        # Suggest next steps
        next_steps = self._suggest_next_steps(messages, action_items)
        
        # Create high-level summary
        summary_text = self._create_summary_text(
            key_topics,
            emotional_journey,
            action_items
        )
        
        return {
            "summary": summary_text,
            "key_topics": key_topics,
            "emotional_journey": emotional_journey,
            "action_items": action_items,
            "insights": insights,
            "next_steps": next_steps,
            "metrics": {
                "duration_minutes": duration,
                "message_count": message_count,
                "user_messages": len([m for m in messages if m.get("role") == "user"]),
                "bot_messages": len([m for m in messages if m.get("role") == "assistant"])
            },
            "session_date": datetime.now().strftime("%Y-%m-%d"),
            "summary_version": "1.0"
        }
    
    def _calculate_duration(self, messages: List[Dict[str, Any]]) -> int:
        """Calculate session duration in minutes."""
        if len(messages) < 2:
            return 0
        
        try:
            first_time = messages[0].get("timestamp")
            last_time = messages[-1].get("timestamp")
            
            if isinstance(first_time, datetime) and isinstance(last_time, datetime):
                delta = last_time - first_time
                return round(delta.total_seconds() / 60)
        except:
            pass
        
        # Estimate: ~1 minute per message exchange
        return len(messages) // 2
    
    def _extract_key_topics(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Extract key topics discussed."""
        topics = []
        
        # Analyze user messages for topics
        user_messages = [m.get("content", "").lower() for m in messages if m.get("role") == "user"]
        all_text = " ".join(user_messages)
        
        topic_keywords = {
            "sleep": ["sleep", "tired", "exhausted", "insomnia", "rest"],
            "mood": ["mood", "stressed", "anxious", "overwhelmed", "sad", "happy"],
            "symptoms": ["symptom", "pain", "cramps", "headache", "bloating"],
            "cycle": ["period", "cycle", "menstrual", "ovulation"],
            "habits": ["exercise", "yoga", "workout", "meditation", "habit"],
            "nutrition": ["eat", "food", "diet", "nutrition", "meal"],
            "energy": ["energy", "fatigue", "tired", "exhausted"]
        }
        
        for topic, keywords in topic_keywords.items():
            if any(keyword in all_text for keyword in keywords):
                topics.append(topic)
        
        return topics[:5]  # Max 5 topics
    
    def _analyze_emotional_journey(self, emotional_states: List[str]) -> Dict[str, Any]:
        """Analyze how emotions changed during session."""
        if not emotional_states:
            return {"start": "neutral", "end": "neutral", "trend": "stable"}
        
        start_emotion = emotional_states[0] if emotional_states else "neutral"
        end_emotion = emotional_states[-1] if emotional_states else "neutral"
        
        # Determine trend
        positive_emotions = ["joy", "grateful", "hopeful", "confident"]
        negative_emotions = ["sad", "anxious", "stressed", "frustrated"]
        
        start_is_positive = start_emotion in positive_emotions
        end_is_positive = end_emotion in positive_emotions
        
        if not start_is_positive and end_is_positive:
            trend = "improving"
        elif start_is_positive and not end_is_positive:
            trend = "declining"
        else:
            trend = "stable"
        
        return {
            "start": start_emotion,
            "end": end_emotion,
            "trend": trend,
            "predominant": max(set(emotional_states), key=emotional_states.count) if emotional_states else "neutral"
        }
    
    def _extract_action_items(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Extract committed actions from conversation."""
        action_items = []
        
        user_messages = [m.get("content", "").lower() for m in messages if m.get("role") == "user"]
        
        # Look for commitment phrases
        commitment_patterns = [
            "i will", "i'll", "going to", "plan to", "want to", 
            "need to", "should", "trying to"
        ]
        
        for msg in user_messages:
            for pattern in commitment_patterns:
                if pattern in msg:
                    # Extract the action (simplified)
                    action = msg.split(pattern)[1].strip().split(".")[0]
                    if action and len(action) < 100:
                        action_items.append(action)
        
        return action_items[:5]  # Max 5 action items
    
    def _generate_insights(
        self,
        messages: List[Dict[str, Any]],
        emotional_states: List[str]
    ) -> List[str]:
        """Generate insights about the user."""
        insights = []
        
        # Analyze message patterns
        user_messages = [m.get("content", "") for m in messages if m.get("role") == "user"]
        
        if len(user_messages) > 5:
            insights.append("Engaged in deep conversation - showing commitment to health")
        
        # Emotional insights
        if "stressed" in emotional_states or "anxious" in emotional_states:
            insights.append("Currently managing stress - needs extra support")
        
        if "hopeful" in emotional_states or "grateful" in emotional_states:
            insights.append("Showing positive mindset and gratitude")
        
        return insights
    
    def _suggest_next_steps(
        self,
        messages: List[Dict[str, Any]],
        action_items: List[str]
    ) -> List[str]:
        """Suggest next steps for follow-up."""
        next_steps = []
        
        if action_items:
            next_steps.append("Follow up on committed actions")
        
        # Generic helpful next steps
        next_steps.append("Track any new symptoms or patterns")
        next_steps.append("Check in about mood and energy levels")
        
        return next_steps[:3]
    
    def _create_summary_text(
        self,
        topics: List[str],
        emotional_journey: Dict[str, Any],
        action_items: List[str]
    ) -> str:
        """Create human-readable summary text."""
        parts = []
        
        # Topics
        if topics:
            topic_str = ", ".join(topics)
            parts.append(f"We discussed {topic_str}")
        
        # Emotional journey
        trend = emotional_journey.get("trend", "stable")
        if trend == "improving":
            parts.append("Your mood improved during our chat 💜")
        elif trend == "declining":
            parts.append("You shared some challenges today")
        
        # Actions
        if action_items:
            parts.append(f"You committed to {len(action_items)} action(s)")
        
        return ". ".join(parts) + "." if parts else "We had a meaningful conversation."
    
    def create_shareable_summary(self, summary: Dict[str, Any]) -> str:
        """Create formatted summary for sharing/export."""
        lines = [
            "═" * 60,
            "CONVERSATION SUMMARY",
            f"Date: {summary.get('session_date', 'Unknown')}",
            f"Duration: {summary.get('metrics', {}).get('duration_minutes', 0)} minutes",
            "═" * 60,
            "",
            "OVERVIEW:",
            summary.get("summary", ""),
            "",
            "KEY TOPICS:",
        ]
        
        for topic in summary.get("key_topics", []):
            lines.append(f"  • {topic}")
        
        lines.append("")
        lines.append("ACTION ITEMS:")
        for action in summary.get("action_items", []):
            lines.append(f"  ☐ {action}")
        
        lines.append("")
        lines.append("NEXT STEPS:")
        for step in summary.get("next_steps", []):
            lines.append(f"  → {step}")
        
        return "\n".join(lines)
