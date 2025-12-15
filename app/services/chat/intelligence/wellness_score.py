"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA WELLNESS SCORE - Holistic Health Quantification
═══════════════════════════════════════════════════════════════════════════════
Calculate a comprehensive wellness score from multiple health dimensions.

SCORING DIMENSIONS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. SLEEP QUALITY (20%) - Duration, consistency, restfulness
2. MOOD & ENERGY (25%) - Emotional state, energy levels, stress
3. SYMPTOM BURDEN (20%) - Number and severity of symptoms
4. HABIT COMPLETION (15%) - Daily tasks, self-care activities
5. CYCLE ALIGNMENT (10%) - How well activities match cycle phase
6. SOCIAL CONNECTION (10%) - Engagement, support seeking
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class WellnessDimension(Enum):
    """Dimensions contributing to overall wellness."""
    SLEEP = "sleep"
    MOOD = "mood"
    SYMPTOMS = "symptoms"
    HABITS = "habits"
    CYCLE_ALIGNMENT = "cycle_alignment"
    SOCIAL = "social"


class WellnessScoreCalculator:
    """
    Calculate holistic wellness scores.
    
    Provides a single number (0-100) that represents overall wellness,
    plus breakdown by dimension for detailed insights.
    """
    
    # Weights for each dimension (must sum to 1.0)
    DIMENSION_WEIGHTS = {
        WellnessDimension.SLEEP: 0.20,
        WellnessDimension.MOOD: 0.25,
        WellnessDimension.SYMPTOMS: 0.20,
        WellnessDimension.HABITS: 0.15,
        WellnessDimension.CYCLE_ALIGNMENT: 0.10,
        WellnessDimension.SOCIAL: 0.10
    }
    
    def calculate_daily_score(
        self,
        sleep_data: Optional[Dict[str, Any]] = None,
        mood_data: Optional[Dict[str, Any]] = None,
        symptom_data: Optional[List[Dict[str, Any]]] = None,
        habit_data: Optional[Dict[str, Any]] = None,
        cycle_data: Optional[Dict[str, Any]] = None,
        social_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Calculate wellness score for today.
        
        Returns:
            {
                "overall_score": 75,  # 0-100
                "dimension_scores": {...},
                "insights": [...],
                "trend": "improving" / "stable" / "declining",
                "recommendations": [...]
            }
        """
        dimension_scores = {}
        
        # Calculate each dimension
        dimension_scores[WellnessDimension.SLEEP.value] = self._score_sleep(sleep_data)
        dimension_scores[WellnessDimension.MOOD.value] = self._score_mood(mood_data)
        dimension_scores[WellnessDimension.SYMPTOMS.value] = self._score_symptoms(symptom_data)
        dimension_scores[WellnessDimension.HABITS.value] = self._score_habits(habit_data)
        dimension_scores[WellnessDimension.CYCLE_ALIGNMENT.value] = self._score_cycle_alignment(cycle_data)
        dimension_scores[WellnessDimension.SOCIAL.value] = self._score_social(social_data)
        
        # Calculate weighted overall score
        overall_score = 0
        for dimension, weight in self.DIMENSION_WEIGHTS.items():
            score = dimension_scores.get(dimension.value, 50)  # Default to neutral
            overall_score += score * weight
        
        overall_score = round(overall_score, 1)
        
        # Generate insights
        insights = self._generate_insights(dimension_scores, overall_score)
        
        # Determine trend (would need historical data)
        trend = "stable"
        
        # Generate recommendations
        recommendations = self._generate_recommendations(dimension_scores)
        
        return {
            "overall_score": overall_score,
            "dimension_scores": dimension_scores,
            "insights": insights,
            "trend": trend,
            "recommendations": recommendations,
            "emoji": self._get_score_emoji(overall_score),
            "message": self._get_score_message(overall_score)
        }
    
    def _score_sleep(self, sleep_data: Optional[Dict[str, Any]]) -> float:
        """Score sleep quality (0-100)."""
        if not sleep_data:
            return 50  # Neutral if no data
        
        score = 50
        
        # Duration (optimal: 7-9 hours)
        hours = sleep_data.get("hours", 7)
        if 7 <= hours <= 9:
            score += 25
        elif 6 <= hours < 7 or 9 < hours <= 10:
            score += 15
        else:
            score += 5
        
        # Quality rating
        quality = sleep_data.get("quality", 5)  # 1-10 scale
        score += (quality / 10) * 25
        
        return min(100, max(0, score))
    
    def _score_mood(self, mood_data: Optional[Dict[str, Any]]) -> float:
        """Score mood & energy (0-100)."""
        if not mood_data:
            return 50
        
        # Mood rating (1-10)
        mood = mood_data.get("mood", 5)
        mood_score = (mood / 10) * 50
        
        # Energy level (1-10)
        energy = mood_data.get("energy", 5)
        energy_score = (energy / 10) * 30
        
        # Stress level (1-10, lower is better)
        stress = mood_data.get("stress", 5)
        stress_score = ((10 - stress) / 10) * 20
        
        return mood_score + energy_score + stress_score
    
    def _score_symptoms(self, symptom_data: Optional[List[Dict[str, Any]]]) -> float:
        """Score symptom burden (0-100, higher = fewer/milder symptoms)."""
        if not symptom_data:
            return 100  # No symptoms = perfect score
        
        if len(symptom_data) == 0:
            return 100
        
        # Start high, deduct for symptoms
        score = 100
        
        for symptom in symptom_data:
            severity = symptom.get("severity", 5)  # 1-9
            # Deduct based on severity
            score -= (severity / 9) * 15
        
        return max(0, score)
    
    def _score_habits(self, habit_data: Optional[Dict[str, Any]]) -> float:
        """Score habit completion (0-100)."""
        if not habit_data:
            return 50
        
        completed = habit_data.get("completed", 0)
        total = habit_data.get("total", 1)
        
        if total == 0:
            return 50
        
        completion_rate = completed / total
        return completion_rate * 100
    
    def _score_cycle_alignment(self, cycle_data: Optional[Dict[str, Any]]) -> float:
        """Score how well activities align with cycle phase (0-100)."""
        if not cycle_data:
            return 50
        
        # This is more conceptual - checks if user is doing phase-appropriate activities
        phase = cycle_data.get("phase", "follicular")
        activities = cycle_data.get("activities", [])
        
        # Placeholder logic
        return 70  # Default to good alignment
    
    def _score_social(self, social_data: Optional[Dict[str, Any]]) -> float:
        """Score social connection (0-100)."""
        if not social_data:
            return 50
        
        # Check for conversations, support seeking, etc.
        engagement = social_data.get("engagement_level", 5)  # 1-10
        return (engagement / 10) * 100
    
    def _generate_insights(self, dimension_scores: Dict[str, float], overall: float) -> List[str]:
        """Generate insights from scores."""
        insights = []
        
        # Overall assessment
        if overall >= 80:
            insights.append("You're doing exceptionally well! 🌟")
        elif overall >= 60:
            insights.append("Solid wellness - room to grow! 💪")
        elif overall >= 40:
            insights.append("Some challenges today - be gentle with yourself 💜")
        else:
            insights.append("Tough day - focus on basics and self-care 🤗")
        
        # Dimension-specific insights
        for dimension, score in dimension_scores.items():
            if score < 40:
                insights.append(f"Your {dimension} needs attention")
            elif score >= 80:
                insights.append(f"Your {dimension} is excellent!")
        
        return insights
    
    def _generate_recommendations(self, dimension_scores: Dict[str, float]) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []
        
        # Find lowest scoring dimension
        lowest = min(dimension_scores.items(), key=lambda x: x[1])
        dimension, score = lowest
        
        if score < 50:
            recommendations.append(f"Focus on improving your {dimension} today")
        
        # Specific recommendations
        if dimension_scores.get("sleep", 50) < 60:
            recommendations.append("Try to get to bed 30 minutes earlier tonight")
        
        if dimension_scores.get("mood", 50) < 60:
            recommendations.append("Take a 10-minute break to do something you enjoy")
        
        if dimension_scores.get("symptoms", 50) < 60:
            recommendations.append("Track what helps reduce your symptoms")
        
        return recommendations
    
    def _get_score_emoji(self, score: float) -> str:
        """Get emoji for score."""
        if score >= 85:
            return "🌟"
        elif score >= 70:
            return "😊"
        elif score >= 50:
            return "😌"
        elif score >= 30:
            return "😔"
        else:
            return "💙"
    
    def _get_score_message(self, score: float) -> str:
        """Get message for score."""
        if score >= 85:
            return "Exceptional wellness day!"
        elif score >= 70:
            return "Feeling good overall"
        elif score >= 50:
            return "Managing well"
        elif score >= 30:
            return "Facing some challenges"
        else:
            return "Tough day - be kind to yourself"
