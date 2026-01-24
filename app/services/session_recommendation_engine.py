"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA SESSION RECOMMENDATION ENGINE (Signup Flow)
═══════════════════════════════════════════════════════════════════════════════

Generates exactly 4 recommendations in ONE API call based on user's lifestyle_focus.

Distribution based on user preference:
┌─────────────────────────┬──────┬──────────┬─────────────┬───────┐
│ User Preference         │ Food │ Movement │ Mindfulness │ Total │
├─────────────────────────┼──────┼──────────┼─────────────┼───────┤
│ None (default)          │  2   │    1     │      1      │   4   │
│ "Eat" only              │  2   │    1     │      1      │   4   │
│ "Move" only             │  1   │    2     │      1      │   4   │
│ "Pause" only            │  1   │    1     │      2      │   4   │
│ "Eat" + "Move"          │  2   │    2     │      0      │   4   │
│ "Eat" + "Pause"         │  2   │    0     │      2      │   4   │
│ "Move" + "Pause"        │  0   │    2     │      2      │   4   │
│ All three               │  2   │    1     │      1      │   4   │
└─────────────────────────┴──────┴──────────┴─────────────┴───────┘
"""

import logging
import json
import os
from typing import Dict, Any, List
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = "gpt-5-mini"

# Mapping
LIFESTYLE_TO_CATEGORY = {
    'eat': 'food',
    'move': 'movement', 
    'pause': 'mindfulness'
}


def get_category_distribution(lifestyle_focus: List[str]) -> Dict[str, int]:
    """
    Get how many recommendations per category based on user's lifestyle_focus.
    Total is ALWAYS 4.
    """
    focus = [f.lower() for f in lifestyle_focus] if lifestyle_focus else []
    
    # Default: 2 food, 1 movement, 1 mindfulness
    if not focus or len(focus) == 3:
        return {"food": 2, "movement": 1, "mindfulness": 1}
    
    # Single preference
    if len(focus) == 1:
        if 'eat' in focus:
            return {"food": 2, "movement": 1, "mindfulness": 1}
        elif 'move' in focus:
            return {"food": 1, "movement": 2, "mindfulness": 1}
        elif 'pause' in focus:
            return {"food": 1, "movement": 1, "mindfulness": 2}
    
    # Two preferences - split evenly
    if len(focus) == 2:
        dist = {"food": 0, "movement": 0, "mindfulness": 0}
        for f in focus:
            cat = LIFESTYLE_TO_CATEGORY.get(f, f)
            dist[cat] = 2
        return dist
    
    return {"food": 2, "movement": 1, "mindfulness": 1}


def build_unified_prompt(user_profile: Dict[str, Any]) -> str:
    """
    Build a single prompt that generates ALL 4 recommendations at once.
    """
    age = user_profile.get('age', 30)
    primary_hormone = user_profile.get('primaryImbalance', 'insulin')
    secondary_hormones = user_profile.get('secondaryImbalances', [])
    lifestyle_focus = user_profile.get('lifestyle_focus', [])
    symptoms = user_profile.get('symptoms', ['hormone imbalance'])
    conditions = user_profile.get('conditions', ['PCOS'])
    
    # Get distribution
    distribution = get_category_distribution(lifestyle_focus)
    
    # Build preference text
    if lifestyle_focus:
        preference_text = f"User PREFERS: {', '.join([f.upper() for f in lifestyle_focus])}"
    else:
        preference_text = "User has no specific preference (balanced approach)"
    
    symptom_str = ', '.join(symptoms[:5]) if symptoms else 'hormone imbalance'
    
    prompt = f"""You are AUVRA, an expert women's hormone health AI.

═══════════════════════════════════════════════════════════════════════════════
USER PROFILE
═══════════════════════════════════════════════════════════════════════════════
- Age: {age}
- Primary Hormone Imbalance: {primary_hormone.upper()}
- Secondary Hormone: {secondary_hormones[0].upper() if secondary_hormones else 'None'}
- Conditions: {', '.join(conditions)}
- Symptoms: {symptom_str}
- {preference_text}

═══════════════════════════════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════════════════════════════
Generate exactly 4 daily action recommendations:
- {distribution['food']} FOOD recommendation(s)
- {distribution['movement']} MOVEMENT recommendation(s)  
- {distribution['mindfulness']} MINDFULNESS recommendation(s)

Each recommendation should:
1. Target the user's PRIMARY hormone ({primary_hormone})
2. Be specific and actionable (exact amounts, times, durations)
3. Be safe for someone with {', '.join(conditions)}

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (JSON)
═══════════════════════════════════════════════════════════════════════════════
Return ONLY valid JSON in this exact format:
{{
  "recommendations": [
    {{
      "title": "Simple title (just food item / exercise / technique name)",
      "category": "food" | "movement" | "mindfulness",
      "purpose": "One sentence explaining why this helps their hormone",
      "specificAction": "Exact what to do with amounts/duration",
      "frequency": "Daily",
      "intensity": "Low" | "Medium" | "High",
      "priority": "high" | "medium",
      "hormones": ["{primary_hormone}"],
      "optimal_times": ["morning"] | ["afternoon"] | ["evening"],
      "frequency_detail": "daily:1",
      "duration_weeks": 8,
      "contraindications": [],
      "conditions": {json.dumps(conditions)},
      "symptoms": {json.dumps(symptoms[:3] if symptoms else ['hormone imbalance'])},
      "food_amounts": ["amount"] (for food only),
      "food_items": ["item"] (for food only),
      "exercise_durations": ["20 min"] (for movement only),
      "exercise_types": ["type"] (for movement only),
      "exercise_intensities": ["low"] (for movement only),
      "mindfulness_durations": ["10 min"] (for mindfulness only),
      "mindfulness_techniques": ["technique"] (for mindfulness only),
      "researchBacking": {{
        "summary": "Brief research summary",
        "studies": []
      }}
    }}
  ]
}}

IMPORTANT:
- Generate EXACTLY 4 recommendations total
- Match the category distribution above
- Keep titles simple (just the item/activity name, no adjectives)
- Be specific with amounts (e.g., "30g almonds", "20 minutes", "5 deep breaths")
- Use "{primary_hormone}" as the hormone for ALL recommendations
"""
    
    return prompt


async def generate_session_recommendations_unified(
    user_profile: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Generate all 4 recommendations in ONE API call.
    
    This replaces the old approach of 3 separate API calls.
    """
    start_time = datetime.now()
    lifestyle_focus = user_profile.get('lifestyle_focus', [])
    
    logger.info("=" * 60)
    logger.info("🚀 SESSION RECOMMENDATION ENGINE")
    logger.info(f"   Lifestyle Focus: {lifestyle_focus if lifestyle_focus else 'None'}")
    logger.info(f"   Distribution: {get_category_distribution(lifestyle_focus)}")
    logger.info("=" * 60)
    
    try:
        # Build unified prompt
        prompt = build_unified_prompt(user_profile)
        
        # Call OpenAI
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        body = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "You are AUVRA, a women's hormone health AI. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"}
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body
            )
            
            if response.status_code != 200:
                logger.error(f"OpenAI error: {response.status_code}")
                return get_fallback_recommendations(user_profile)
            
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            # Parse JSON
            parsed = json.loads(content)
            recommendations = parsed.get("recommendations", [])
            
            # Post-process
            for i, rec in enumerate(recommendations):
                rec['id'] = f"session_rec_{i+1}"
                rec['priority'] = 'high' if i == 0 else 'medium'
                
                # Ensure hormones is a list
                if 'hormones' not in rec or not rec['hormones']:
                    rec['hormones'] = [user_profile.get('primaryImbalance', 'insulin')]
            
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            logger.info(f"✅ Generated {len(recommendations)} recommendations in {elapsed_ms:.0f}ms")
            for rec in recommendations:
                logger.info(f"   - [{rec.get('category', '?')}] {rec.get('title', '?')}")
            
            return recommendations
            
    except Exception as e:
        logger.error(f"❌ Session recommendation generation failed: {e}")
        return get_fallback_recommendations(user_profile)


def get_fallback_recommendations(user_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fallback recommendations if API fails - with full DB-compatible format."""
    primary = user_profile.get('primaryImbalance', 'insulin')
    conditions = user_profile.get('conditions', ['PCOS'])
    symptoms = user_profile.get('symptoms', ['hormone imbalance'])
    
    return [
        {
            "title": "Cinnamon",
            "category": "food",
            "purpose": f"Helps improve {primary} sensitivity and blood sugar balance",
            "specificAction": "Add 1/2 teaspoon Ceylon cinnamon to your morning oatmeal or smoothie",
            "frequency": "Daily",
            "intensity": "Low",
            "priority": "high",
            "hormones": [primary],
            "optimal_times": ["morning"],
            "frequency_detail": "daily:1",
            "duration_weeks": 8,
            "contraindications": [],
            "conditions": conditions,
            "symptoms": symptoms[:3],
            "food_amounts": ["1/2 teaspoon", "1.5g"],
            "food_items": ["Ceylon cinnamon"],
            "researchBacking": {
                "summary": f"Studies show cinnamon improves {primary} sensitivity in women with PCOS",
                "studies": []
            }
        },
        {
            "title": "Leafy Greens",
            "category": "food",
            "purpose": f"Supports {primary} balance with fiber, magnesium and antioxidants",
            "specificAction": "Eat 2 cups of spinach or kale with lunch as a salad or side",
            "frequency": "Daily",
            "intensity": "Low",
            "priority": "medium",
            "hormones": [primary],
            "optimal_times": ["afternoon"],
            "frequency_detail": "daily:1",
            "duration_weeks": 8,
            "contraindications": [],
            "conditions": conditions,
            "symptoms": symptoms[:3],
            "food_amounts": ["2 cups"],
            "food_items": ["spinach", "kale"],
            "researchBacking": {
                "summary": f"Leafy greens provide nutrients that support {primary} regulation",
                "studies": []
            }
        },
        {
            "title": "Brisk Walking",
            "category": "movement",
            "purpose": f"Improves {primary} sensitivity through gentle cardiovascular exercise",
            "specificAction": "Walk briskly for 20 minutes after dinner at moderate pace",
            "frequency": "Daily",
            "intensity": "Low",
            "priority": "medium",
            "hormones": [primary],
            "optimal_times": ["evening"],
            "frequency_detail": "daily:1",
            "duration_weeks": 8,
            "contraindications": [],
            "conditions": conditions,
            "symptoms": symptoms[:3],
            "exercise_durations": ["20 minutes"],
            "exercise_types": ["walking", "cardio"],
            "exercise_intensities": ["low"],
            "researchBacking": {
                "summary": f"Light cardio after meals helps {primary} regulation",
                "studies": []
            }
        },
        {
            "title": "4-7-8 Breathing",
            "category": "mindfulness",
            "purpose": f"Reduces stress hormones that affect {primary} balance",
            "specificAction": "Practice 5 cycles of 4-7-8 breathing: inhale 4s, hold 7s, exhale 8s",
            "frequency": "Daily",
            "intensity": "Low",
            "priority": "medium",
            "hormones": [primary],
            "optimal_times": ["evening"],
            "frequency_detail": "daily:1",
            "duration_weeks": 8,
            "contraindications": [],
            "conditions": conditions,
            "symptoms": symptoms[:3],
            "mindfulness_durations": ["5 minutes"],
            "mindfulness_techniques": ["deep breathing", "4-7-8 technique"],
            "researchBacking": {
                "summary": f"Breathing exercises reduce cortisol and support {primary}",
                "studies": []
            }
        }
    ]
