"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA SYMPTOM PREDICTOR - Anticipatory Health Intelligence
═══════════════════════════════════════════════════════════════════════════════
Predict upcoming symptoms based on cycle phase + historical patterns.

PREDICTION APPROACH:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Historical Pattern Analysis - What symptoms occur in which phases
2. Severity Trends - How severe symptoms typically are
3. Timing Prediction - When symptoms are likely to appear (2-3 days ahead)
4. Confidence Scoring - How confident we are in the prediction
5. Proactive Advice - What to do NOW to prevent/minimize
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class SymptomPredictor:
    """
    Predict symptoms before they occur.
    
    Uses cycle phase + historical data to anticipate symptoms 2-3 days ahead,
    allowing proactive intervention.
    """
    
    # Common symptoms by cycle phase
    PHASE_TYPICAL_SYMPTOMS = {
        "menstrual": [
            {"name": "cramps", "likelihood": 0.8, "typical_severity": 6},
            {"name": "fatigue", "likelihood": 0.7, "typical_severity": 5},
            {"name": "headache", "likelihood": 0.5, "typical_severity": 5},
            {"name": "bloating", "likelihood": 0.6, "typical_severity": 4}
        ],
        "follicular": [
            {"name": "energy_surge", "likelihood": 0.7, "typical_severity": 0},
            {"name": "mood_improvement", "likelihood": 0.8, "typical_severity": 0}
        ],
        "ovulatory": [
            {"name": "increased_energy", "likelihood": 0.8, "typical_severity": 0},
            {"name": "mild_cramping", "likelihood": 0.4, "typical_severity": 3}
        ],
        "luteal": [
            {"name": "mood_changes", "likelihood": 0.7, "typical_severity": 6},
            {"name": "bloating", "likelihood": 0.8, "typical_severity": 6},
            {"name": "breast_tenderness", "likelihood": 0.7, "typical_severity": 5},
            {"name": "fatigue", "likelihood": 0.6, "typical_severity": 5},
            {"name": "food_cravings", "likelihood": 0.7, "typical_severity": 4},
            {"name": "irritability", "likelihood": 0.6, "typical_severity": 5}
        ]
    }
    
    def predict_upcoming_symptoms(
        self,
        user_id: str,
        current_phase: str,
        days_until_period: int,
        historical_symptoms: List[Dict[str, Any]],
        db_session: Any = None
    ) -> Dict[str, Any]:
        """
        Predict symptoms for the next 2-3 days.
        
        Returns:
            {
                "predictions": [
                    {
                        "symptom": "cramps",
                        "likelihood": 0.85,  # 0-1
                        "expected_severity": 6,  # 1-9
                        "expected_date": "2025-12-18",
                        "confidence": "high",
                        "proactive_advice": [...]
                    }
                ],
                "phase_transition": {
                    "current": "luteal",
                    "next": "menstrual",
                    "transition_date": "2025-12-18"
                },
                "overall_outlook": "Challenging few days ahead - plan accordingly"
            }
        """
        predictions = []
        
        # Analyze historical patterns
        user_patterns = self._analyze_user_patterns(historical_symptoms, current_phase)
        
        # Combine generic patterns with user-specific patterns
        phase_symptoms = self.PHASE_TYPICAL_SYMPTOMS.get(current_phase, [])
        
        for symptom_info in phase_symptoms:
            # Adjust based on user history
            user_history = user_patterns.get(symptom_info["name"], {})
            
            likelihood = symptom_info["likelihood"]
            expected_severity = symptom_info["typical_severity"]
            
            # Personalize based on user history
            if user_history:
                # User has had this symptom before
                likelihood = min(1.0, likelihood * 1.2)  # Increase likelihood
                expected_severity = user_history.get("avg_severity", expected_severity)
            
            # Determine confidence
            confidence = self._calculate_confidence(user_history, likelihood)
            
            # Generate proactive advice
            proactive_advice = self._generate_proactive_advice(
                symptom_info["name"],
                expected_severity,
                days_until_period
            )
            
            # Expected date (2-3 days before phase transition for luteal symptoms)
            if current_phase == "luteal" and days_until_period <= 5:
                expected_date = (datetime.now() + timedelta(days=max(0, days_until_period - 2))).strftime("%Y-%m-%d")
            else:
                expected_date = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
            
            predictions.append({
                "symptom": symptom_info["name"],
                "likelihood": round(likelihood, 2),
                "expected_severity": expected_severity,
                "expected_date": expected_date,
                "confidence": confidence,
                "proactive_advice": proactive_advice,
                "user_specific": bool(user_history)
            })
        
        # Sort by likelihood (highest first)
        predictions.sort(key=lambda x: x["likelihood"], reverse=True)
        
        # Determine phase transition
        phase_transition = self._determine_phase_transition(current_phase, days_until_period)
        
        # Overall outlook
        overall_outlook = self._generate_overall_outlook(predictions, current_phase)
        
        return {
            "predictions": predictions[:5],  # Top 5 most likely
            "phase_transition": phase_transition,
            "overall_outlook": overall_outlook,
            "prediction_date": datetime.now().strftime("%Y-%m-%d"),
            "horizon_days": 3
        }
    
    def _analyze_user_patterns(
        self,
        historical_symptoms: List[Dict[str, Any]],
        current_phase: str
    ) -> Dict[str, Dict[str, Any]]:
        """Analyze user's historical symptom patterns."""
        patterns = defaultdict(lambda: {"count": 0, "severities": []})
        
        for symptom in historical_symptoms:
            symptom_name = symptom.get("type", "")
            phase = symptom.get("cycle_phase", "")
            
            if phase == current_phase:
                patterns[symptom_name]["count"] += 1
                patterns[symptom_name]["severities"].append(symptom.get("severity", 5))
        
        # Calculate averages
        result = {}
        for symptom, data in patterns.items():
            if data["count"] > 0:
                result[symptom] = {
                    "occurrences": data["count"],
                    "avg_severity": sum(data["severities"]) / len(data["severities"]),
                    "frequency": data["count"] / max(1, len(historical_symptoms))
                }
        
        return result
    
    def _calculate_confidence(self, user_history: Dict, likelihood: float) -> str:
        """Calculate confidence level in prediction."""
        if not user_history:
            return "low"  # No user data
        
        occurrences = user_history.get("occurrences", 0)
        
        if occurrences >= 3 and likelihood >= 0.7:
            return "high"
        elif occurrences >= 2 or likelihood >= 0.6:
            return "medium"
        else:
            return "low"
    
    def _generate_proactive_advice(
        self,
        symptom: str,
        severity: int,
        days_until: int
    ) -> List[str]:
        """Generate proactive advice to prevent/minimize symptom."""
        advice_map = {
            "cramps": [
                "Start taking magnesium supplements now",
                "Use a heating pad in the evening",
                "Gentle yoga or stretching daily",
                "Avoid inflammatory foods (sugar, caffeine)"
            ],
            "bloating": [
                "Reduce sodium intake starting today",
                "Drink extra water (helps paradoxically)",
                "Avoid carbonated drinks",
                "Eat smaller, more frequent meals"
            ],
            "mood_changes": [
                "Prioritize sleep - aim for 8 hours",
                "Exercise daily (even a 10-min walk)",
                "Limit alcohol and caffeine",
                "Plan lighter schedule for challenging days"
            ],
            "fatigue": [
                "Go to bed 30 minutes earlier",
                "Eat iron-rich foods",
                "Take short breaks throughout day",
                "Limit intense exercise"
            ],
            "headache": [
                "Stay well-hydrated (8+ glasses water)",
                "Manage stress proactively",
                "Avoid triggering foods",
                "Get consistent sleep schedule"
            ],
            "breast_tenderness": [
                "Wear supportive bra",
                "Reduce caffeine intake",
                "Apply cold or warm compress",
                "Take evening primrose oil supplement"
            ]
        }
        
        return advice_map.get(symptom, ["Track this symptom to understand your patterns better"])
    
    def _determine_phase_transition(self, current_phase: str, days_until_period: int) -> Dict[str, Any]:
        """Determine upcoming phase transition."""
        phase_order = ["menstrual", "follicular", "ovulatory", "luteal"]
        current_idx = phase_order.index(current_phase) if current_phase in phase_order else 0
        next_phase = phase_order[(current_idx + 1) % len(phase_order)]
        
        # Estimate transition date
        if current_phase == "luteal":
            transition_date = (datetime.now() + timedelta(days=days_until_period)).strftime("%Y-%m-%d")
        else:
            transition_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        
        return {
            "current": current_phase,
            "next": next_phase,
            "transition_date": transition_date,
            "days_until_transition": days_until_period if current_phase == "luteal" else 7
        }
    
    def _generate_overall_outlook(self, predictions: List[Dict], phase: str) -> str:
        """Generate overall outlook message."""
        high_likelihood = [p for p in predictions if p["likelihood"] >= 0.7]
        
        if len(high_likelihood) == 0:
            return "Smooth few days ahead! Enjoy this phase 💜"
        elif len(high_likelihood) <= 2:
            return "Mild symptoms possible - you've got this! 💪"
        elif phase == "luteal":
            return "PMS symptoms likely - plan self-care and lighter schedule 🌙"
        elif phase == "menstrual":
            return "Period symptoms expected - rest, warmth, gentle movement 🤗"
        else:
            return "Some symptoms possible - listen to your body 💜"
