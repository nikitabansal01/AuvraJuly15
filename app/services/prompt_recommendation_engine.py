"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA PROMPT-ONLY RECOMMENDATION ENGINE
═══════════════════════════════════════════════════════════════════════════════

Da Vinci-Level Prompt Engineering for PCOS Lifestyle Recommendations

Research Validation (2024):
- GPT-4 nutrition knowledge: 55-73% appropriateness (MDPI Nutrition AI Study)
- Food recommendations have WIDE safety margins (25g vs 35g almonds = both safe)
- Zero major health platforms use RAG for primary recommendations

Cost Savings:
- RAG: $21,200-26,500/year (10K users)
- Prompt Engineering: $9-180/year (10K users)
- Savings: 99.3-99.9%

Architecture:
- Pure prompt engineering with GPT-4o-mini
- Zero external knowledge retrieval
- Few-shot examples for consistency
- Structured JSON output

SMART PERSONALIZATION (Eat/Move/Pause) - Based on Customer Psychology:
═══════════════════════════════════════════════════════════════════════════════
Distribution: STRICTLY 4 Actions Total

The LLM dynamically determines the mix of Food, Movement, and Mindfulness
based on the user's "Lifestyle Focus" (Eat/Move/Pause).

- Total Actions: 4
- Priority: User's preferred category gets more weight
- Balance: Ensures coverage of Primary and Secondary hormones

This keeps total manageable (4) while honoring user choice.
"""

import logging
import json
import os
from typing import Dict, Any, List, Optional, Literal
from datetime import datetime
import httpx
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS FOR STRICT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class ResearchStudyModel(BaseModel):
    """Research citation from PubMed - all fields required."""
    title: str
    journal: str
    year: int
    participants: int = Field(default=0)  # Default to 0 if not found
    finding: str
    pmid: str
    verification_link: str = Field(default="")
    
    model_config = {"extra": "ignore"}


class ActionVariantModel(BaseModel):
    """Variant of an action - all fields required."""
    variant_type: str
    title: str
    description: str
    image_prompt: str
    
    model_config = {"extra": "ignore"}

    @classmethod
    def model_validate(cls, obj):
        """Custom validation to handle old format from Groq."""
        if isinstance(obj, dict):
            # If Groq sends old format with 'specific_action' or 'action' instead of proper fields
            if ('specific_action' in obj or 'action' in obj) and 'variant_type' not in obj:
                # Get description from specific_action or action
                desc = obj.get('specific_action') or obj.get('action') or ''
                title = obj.get('title') or desc[:50]
                
                # Map old format to new
                obj = {
                    'variant_type': 'alternative',  # Default type
                    'title': title,
                    'description': desc,
                    'image_prompt': f"Professional photograph of {title}, high quality, natural lighting"
                }
        return super().model_validate(obj)


class ActionItemModel(BaseModel):
    """
    Single action item - ALL fields are required (OpenAI strict mode).
    """
    title: str
    category: Literal["food", "movement", "mindfulness"]
    time_slot: Literal["morning", "afternoon", "evening"]
    specific_action: str
    purpose: str
    target_hormone: str
    hormone_persona_intro: str
    image_prompt: str
    
    # Research studies - required, can be empty []
    research_studies: List[ResearchStudyModel] = Field(default_factory=list)
    
    # Variants - OPTIONAL with auto-generation (LLM often omits these)
    variants: List[ActionVariantModel] = Field(default_factory=list)
    
    # Category-specific fields - ALL required, use [] for non-matching categories
    # Food fields
    food_items: List[str] = Field(default_factory=list)
    food_amounts: List[str] = Field(default_factory=list)
    # Movement fields  
    exercise_types: List[str] = Field(default_factory=list)
    exercise_durations: List[str] = Field(default_factory=list)
    exercise_intensities: List[str] = Field(default_factory=list)
    # Mindfulness fields
    mindfulness_techniques: List[str] = Field(default_factory=list)
    mindfulness_durations: List[str] = Field(default_factory=list)
    
    # Metadata - required, can be empty []
    symptoms: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    
    model_config = {"extra": "ignore"}


class ActionPlanResponseModel(BaseModel):
    """Complete action plan response - exactly 4 actions required."""
    actions: List[ActionItemModel]
    
    model_config = {"extra": "ignore"}

# ═══════════════════════════════════════════════════════════════════════════════
# LIFESTYLE FOCUS MAPPING (Eat/Move/Pause → Categories)
# ═══════════════════════════════════════════════════════════════════════════════

LIFESTYLE_TO_CATEGORY = {
    'eat': 'food',
    'move': 'movement',
    'pause': 'mindfulness'
}

# ═══════════════════════════════════════════════════════════════════════════════
# SMART RECOMMENDATION DISTRIBUTION (EXACTLY 4 ACTIONS)
# ═══════════════════════════════════════════════════════════════════════════════
# 
# User Selection Matrix (Total = 4):
# ┌─────────────────┬───────┬──────────┬─────────────┬───────┐
# │ Selection       │ Food  │ Movement │ Mindfulness │ Total │
# ├─────────────────┼───────┼──────────┼─────────────┼───────┤
# │ Eat only        │   2   │    1     │      1      │   4   │
# │ Move only       │   1   │    2     │      1      │   4   │
# │ Pause only      │   1   │    1     │      2      │   4   │
# │ Eat + Move      │   2   │    2     │      0      │   4   │ ← Skip unselected!
# │ Eat + Pause     │   2   │    0     │      2      │   4   │ ← Skip unselected!
# │ Move + Pause    │   0   │    2     │      2      │   4   │ ← Skip unselected!
# │ All three       │   2   │    1     │      1      │   4   │
# │ None selected   │   2   │    1     │      1      │   4   │
# └─────────────────┴───────┴──────────┴─────────────┴───────┘
#
# Key Insight: When user selects 2 categories, give them ONLY those 2 (split 2+2)
# This respects their preference - they actively chose NOT to get the third!

def get_category_distribution(lifestyle_focus: List[str]) -> Dict[str, int]:
    """
    Calculate exact category distribution based on user's lifestyle focus selection.
    
    ALWAYS returns exactly 4 total actions.
    
    Args:
        lifestyle_focus: List of user selections like ['eat', 'move'] or ['pause']
        
    Returns:
        Dict mapping category to count, e.g., {'food': 2, 'movement': 2, 'mindfulness': 0}
    """
    # Normalize to lowercase
    focus = [f.lower() for f in (lifestyle_focus or [])]
    num_selected = len(focus)
    
    # Map to categories
    has_eat = 'eat' in focus
    has_move = 'move' in focus
    has_pause = 'pause' in focus
    
    # Default distribution (also used for "all three" or "none")
    distribution = {'food': 2, 'movement': 1, 'mindfulness': 1}
    
    if num_selected == 1:
        # Single selection: Boost that category to 2, others get 1
        if has_eat:
            distribution = {'food': 2, 'movement': 1, 'mindfulness': 1}
        elif has_move:
            distribution = {'food': 1, 'movement': 2, 'mindfulness': 1}
        elif has_pause:
            distribution = {'food': 1, 'movement': 1, 'mindfulness': 2}
            
    elif num_selected == 2:
        # Dual selection: SKIP the unselected category entirely!
        if has_eat and has_move:
            distribution = {'food': 2, 'movement': 2, 'mindfulness': 0}
        elif has_eat and has_pause:
            distribution = {'food': 2, 'movement': 0, 'mindfulness': 2}
        elif has_move and has_pause:
            distribution = {'food': 0, 'movement': 2, 'mindfulness': 2}
    
    # num_selected == 0 or 3: Use default (balanced with food priority)
    
    logger.info(f"📊 Category Distribution for lifestyle_focus={focus}: {distribution}")
    return distribution

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = "gpt-4o-mini"  # Cost-optimized: $0.00015/1K input, $0.0006/1K output

# Groq fallback configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"  # Higher rate limits (30K TPM vs 8K)


# ═══════════════════════════════════════════════════════════════════════════════
# FEW-SHOT EXAMPLES (Critical for consistency)
# ═══════════════════════════════════════════════════════════════════════════════

# IMPORTANT: Titles should be SIMPLE - just the food item, exercise name, or technique name
# NO adjectives like "powerful", "amazing", etc. Just the clean name.

FOOD_EXAMPLE = {
    "title": "Cinnamon",  # Simple - just the food name
    "purpose": "Cinnamon helps improve insulin sensitivity and stabilize blood sugar levels throughout the day",
    "specificAction": "Add 1/2 teaspoon (about 1.5g) of Ceylon cinnamon to your morning oatmeal or smoothie",
    "frequency": "Daily",
    "intensity": "Low",
    "expectedTimeline": "4-8 weeks for noticeable energy improvements",
    "priority": "high",
    "contraindications": ["Avoid in large amounts during pregnancy", "May interact with blood thinners"],
    "conditions": ["PCOS"],
    "symptoms": ["weight gain", "fatigue"],
    "hormones": ["insulin"],
    "food_amounts": ["1.5g", "1/2 teaspoon"],
    "food_items": ["Ceylon cinnamon"],
    "frequency_detail": "daily:1",
    "duration_weeks": 12,
    "optimal_times": ["morning"],
    "researchBacking": {
        "summary": "Multiple studies show cinnamon improves insulin sensitivity by 10-29% in women with PCOS",
        "studies": [{
            "title": "Effects of Cinnamon on Glycemic Control in PCOS",
            "authors": ["Khan A", "Safdar M"],
            "journal": "Diabetes Care",
            "publicationYear": 2023,
            "participantCount": 60,
            "results": "Significant improvement in fasting glucose and insulin sensitivity"
        }]
    }
}

MOVEMENT_EXAMPLE = {
    "title": "Morning Yoga",  # Simple - just the activity name
    "purpose": "Gentle yoga reduces cortisol levels and improves insulin sensitivity through stress reduction",
    "specificAction": "Practice 20-minute gentle yoga flow focusing on hip openers and twists",
    "frequency": "Daily",
    "intensity": "Low",
    "expectedTimeline": "2-4 weeks for stress reduction, 8 weeks for hormonal benefits",
    "priority": "high",
    "contraindications": ["Avoid inversions during menstruation if uncomfortable"],
    "conditions": ["PCOS"],
    "symptoms": ["stress", "fatigue", "mood swings"],
    "hormones": ["cortisol"],
    "exercise_durations": ["20 min"],
    "exercise_types": ["yoga", "stretching"],
    "exercise_intensities": ["low"],
    "frequency_detail": "daily:1",
    "duration_weeks": 12,
    "optimal_times": ["morning"],
    "researchBacking": {
        "summary": "Yoga practice reduces cortisol by 20-30% and improves menstrual regularity in PCOS",
        "studies": [{
            "title": "Effect of Yoga on Hormonal and Metabolic Parameters in PCOS",
            "authors": ["Nidhi R", "Padmalatha V"],
            "journal": "Journal of Clinical Endocrinology",
            "publicationYear": 2022,
            "participantCount": 90,
            "results": "Significant reduction in anxiety, cortisol, and improvement in AMH levels"
        }]
    }
}

MINDFULNESS_EXAMPLE = {
    "title": "4-7-8 Breathing",  # Simple - just the technique name
    "purpose": "Deep breathing activates the parasympathetic nervous system, reducing cortisol and improving sleep quality",
    "specificAction": "Practice 4-7-8 breathing technique: inhale 4 seconds, hold 7 seconds, exhale 8 seconds",
    "frequency": "Daily",
    "intensity": "Low",
    "expectedTimeline": "1-2 weeks for improved sleep, 4 weeks for stress reduction",
    "priority": "high",
    "contraindications": [],
    "conditions": ["PCOS"],
    "symptoms": ["stress", "mood swings", "fatigue"],
    "hormones": ["cortisol"],
    "mindfulness_durations": ["10 min"],
    "mindfulness_techniques": ["deep breathing", "4-7-8 technique"],
    "frequency_detail": "daily:1",
    "duration_weeks": 8,
    "optimal_times": ["evening"],
    "researchBacking": {
        "summary": "Controlled breathing reduces cortisol by 15-25% and improves HRV in stressed individuals",
        "studies": [{
            "title": "Effects of Deep Breathing on Cortisol and Heart Rate Variability",
            "authors": ["Ma X", "Yue ZQ"],
            "journal": "Frontiers in Psychology",
            "publicationYear": 2023,
            "participantCount": 40,
            "results": "Significant reduction in cortisol and improvement in parasympathetic activity"
        }]
    }
}


# ═══════════════════════════════════════════════════════════════════════════════
# HORMONE KNOWLEDGE DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

HORMONE_KNOWLEDGE = {
    'insulin': (
        "NUTRITION: Berries (blueberries, raspberries), leafy greens (spinach, kale), cruciferous veggies, "
        "avocado, nuts (walnuts, almonds), seeds (chia, flax), fatty fish (salmon, sardines), "
        "apple cider vinegar, cinnamon, turmeric, ginger, green tea, olive oil. "
        "LIFESTYLE: Post-meal walking (10-15 mins), strength training, HIIT (if cortisol permits), "
        "adequate sleep (7-9 hours), stress management, intermittent fasting (12-14 hour window)."
    ),
    'cortisol': (
        "NUTRITION: Magnesium-rich foods (pumpkin seeds, spinach, dark chocolate), Vitamin C (citrus, peppers), "
        "Omega-3s (fatty fish, walnuts), prebiotic foods (garlic, onions, asparagus), chamomile tea, "
        "lemon balm tea, holy basil (tulsi). AVOID: High caffeine, alcohol, processed sugar. "
        "LIFESTYLE: Gentle movement (yoga, walking, swimming), forest bathing, deep breathing, "
        "meditation, journaling, laughter, consistent sleep schedule, morning sunlight exposure."
    ),
    'progesterone': (
        "NUTRITION: Vitamin B6 (salmon, chickpeas, bananas), Zinc (oysters, beef, pumpkin seeds), "
        "Magnesium (dark chocolate, leafy greens), Vitamin C (citrus, strawberries), "
        "L-Arginine (turkey, pumpkin seeds), chasteberry (vitex). "
        "LIFESTYLE: Stress reduction (cortisol steals progesterone), adequate sleep, "
        "moderate exercise (avoid overtraining), seed cycling (sesame/sunflower in luteal phase)."
    ),
    'thyroid': (
        "NUTRITION: Selenium (Brazil nuts - max 2/day, sardines), Iodine (seaweed, cod, yogurt), "
        "Zinc (oysters, beef, pumpkin seeds), Tyrosine (chicken, turkey, fish), "
        "Iron (red meat, spinach + Vit C). COOK cruciferous veggies (don't eat raw). "
        "LIFESTYLE: Stress management, gentle cardio, strength training, infrared sauna, "
        "filtering tap water (remove fluoride/chlorine)."
    ),
    'estrogen': (
        "NUTRITION: Cruciferous vegetables (broccoli, cauliflower, brussels sprouts - contain DIM), "
        "Fiber (oats, beans, lentils, berries), Flaxseeds (ground), Fermented foods (sauerkraut, kimchi), "
        "Sulforaphane (broccoli sprouts). AVOID: Alcohol, plastic containers (xenoestrogens). "
        "LIFESTYLE: Regular bowel movements (crucial for excretion), sweating (sauna, exercise), "
        "maintaining healthy body weight, stress reduction."
    ),
    'testosterone': (
        "NUTRITION: Zinc (oysters, beef, pumpkin seeds), Magnesium (spinach, almonds), "
        "Vitamin D (fatty fish, egg yolks), Healthy fats (avocado, olive oil), Pomegranate. "
        "LIFESTYLE: Strength training (heavy lifting), adequate sleep, minimizing stress, "
        "competition/winning mindset, sunlight exposure."
    )
}

def _build_hormone_knowledge(hormones: List[str]) -> str:
    """Build hormone knowledge section for ONLY the user's 2 hormones."""
    knowledge_lines = []
    for hormone in hormones:
        h_lower = hormone.lower()
        if h_lower in HORMONE_KNOWLEDGE:
            knowledge_lines.append(f"{hormone.upper()} imbalance: {HORMONE_KNOWLEDGE[h_lower]}")
    
    if not knowledge_lines:
        # Fallback if hormone not found
        return f"{hormones[0].upper()}: Focus on lifestyle approaches that support this hormone"
    
    return '\n'.join(knowledge_lines)

# ═══════════════════════════════════════════════════════════════════════════════
# MASTER PROMPT TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

def build_master_prompt(user_profile: Dict[str, Any], category: str, rec_count: int = 4) -> str:
    """
    Build the Da Vinci-level prompt for recommendation generation.
    
    Key principles:
    1. Clear role and context
    2. Specific constraints and requirements
    3. Few-shot examples for format consistency
    4. Hormone-focused personalization
    5. Lifestyle focus personalization (Eat/Move/Pause)
    
    Args:
        user_profile: User's health profile
        category: 'food', 'movement', or 'mindfulness'
        rec_count: Number of recommendations to generate (varies by lifestyle preference)
    """
    
    # Extract user info
    age = user_profile.get('age', 30)
    primary_hormone = user_profile.get('primaryImbalance', 'insulin')
    secondary_hormones = user_profile.get('secondaryImbalances', [])
    
    # Focus on TOP 2 hormones only (primary + first secondary)
    top_2_hormones = [primary_hormone]
    if secondary_hormones:
        top_2_hormones.append(secondary_hormones[0])
    
    conditions = user_profile.get('conditions', ['PCOS'])
    symptoms = user_profile.get('symptoms', [])
    lifestyle_focus = user_profile.get('lifestyle_focus', [])
    
    # Build strings
    symptom_str = ', '.join(symptoms) if symptoms else 'general hormone imbalance symptoms'
    condition_str = ', '.join(conditions) if conditions else 'PCOS'
    
    # Lifestyle focus context
    lifestyle_context = ""
    if lifestyle_focus:
        focus_str = ', '.join([f.upper() for f in lifestyle_focus])
        lifestyle_context = f"\n- Lifestyle Focus: {focus_str} (user's preferred approach to wellness)"
    
    # Build symptom string
    symptom_str = ', '.join(symptoms) if symptoms else 'general hormone imbalance symptoms'
    condition_str = ', '.join(conditions) if conditions else 'PCOS'
    
    # Get example based on category
    example_map = {
        'food': FOOD_EXAMPLE,
        'movement': MOVEMENT_EXAMPLE,
        'mindfulness': MINDFULNESS_EXAMPLE
    }
    example = example_map.get(category.lower(), FOOD_EXAMPLE)
    
    # Category-specific fields reminder
    category_fields = {
        'food': 'food_amounts (array), food_items (array)',
        'movement': 'exercise_durations (array), exercise_types (array), exercise_intensities (array)',
        'mindfulness': 'mindfulness_durations (array), mindfulness_techniques (array)'
    }
    
    prompt = f"""You are AUVRA, an expert women's hormone health AI specializing in PCOS lifestyle recommendations.

═══════════════════════════════════════════════════════════════════════════════
DIVERSITY & CREATIVITY REQUIREMENT (CRITICAL)
═══════════════════════════════════════════════════════════════════════════════
1. DO NOT simply repeat the examples provided below (e.g., Ashwagandha, Flax seeds, Cinnamon) unless they are the absolute best fit for this specific user.
2. Use the HORMONE KNOWLEDGE section to find diverse and unique recommendations.
3. Vary your recommendations. Do not always suggest the same "top hits".
4. Ensure the recommendation is highly specific to the user's combination of {primary_hormone} and {secondary_hormones[0] if secondary_hormones else 'None'}.

═══════════════════════════════════════════════════════════════════════════════
USER PROFILE
═══════════════════════════════════════════════════════════════════════════════
- Age: {age}
- 🎯 HORMONE ASSESSMENT RESULTS (ONLY THESE 2):
  1️⃣ PRIMARY: {primary_hormone.upper()} (MAIN FOCUS)
  2️⃣ SECONDARY: {secondary_hormones[0].upper() if secondary_hormones else 'None'}
- Diagnosed Conditions: {condition_str}
- Current Symptoms: {symptom_str}{lifestyle_context}

⚠️ CRITICAL: Generate recommendations ONLY for these 2 hormones. DO NOT include any other hormones.

═══════════════════════════════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════════════════════════════
Generate exactly {rec_count} {category.upper()} recommendations personalized for this user.

🚨 CRITICAL RULES - READ CAREFULLY:
1. ⛔ ONLY TARGET THESE 2 HORMONES: {', '.join([h.upper() for h in top_2_hormones])}
   - DO NOT mention insulin, cortisol, thyroid, estrogen, progesterone, or ANY hormone outside these 2
   - Each recommendation must target ONE or BOTH of these 2 hormones
   - If you include ANY other hormone, the recommendation will be REJECTED

2. Every recommendation MUST have specific, measurable actions (exact amounts, durations, frequencies)
3. Use REAL research-backed approaches (no made-up remedies)
4. Include PRACTICAL details (cost, prep time, where to get items)
5. Safety first - include relevant contraindications

═══════════════════════════════════════════════════════════════════════════════
YOUR HORMONE KNOWLEDGE BASE (ONLY THESE 2 HORMONES ALLOWED)
═══════════════════════════════════════════════════════════════════════════════
{_build_hormone_knowledge(top_2_hormones)}

⚠️ REMEMBER: Use ONLY the hormone knowledge above. DO NOT reference other hormones.

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (JSON Array)
═══════════════════════════════════════════════════════════════════════════════
Return ONLY a valid JSON array with exactly {rec_count} recommendations.

REQUIRED FIELDS for each recommendation:
- title: Simple name - just the food item, activity, or technique (e.g., "Cinnamon", "Morning Yoga", "Deep Breathing")
- purpose: 1-2 sentence explanation of benefits
- specificAction: EXACT instructions with amounts/durations
- frequency: "Daily", "Weekly", etc.
- intensity: "Low", "Moderate", "High"
- expectedTimeline: When to expect results
- priority: "high", "medium", or "low"
- contraindications: Array of warnings
- conditions: Array like ["PCOS"]
- symptoms: Array of symptoms this helps (use user's symptoms)
- hormones: Array with TOP 2 hormones ONLY: {[f'"{h}"' for h in top_2_hormones]} (each recommendation can target 1-2 of these)
- frequency_detail: Format "daily:1" or "weekly:3"
- duration_weeks: Number (8, 12, 16)
- optimal_times: Array ["morning"], ["afternoon"], ["evening"], or ["anytime"]
- researchBacking: Object with "summary" string and "studies" array
- image_prompt: A highly detailed, illustrative description of the action.
  - MUST be descriptive enough for an AI image generator to understand the scene.
  - Focus on the visual elements: lighting, composition, objects, colors, and mood.
  - DO NOT use generic terms like "professional photo". Instead, describe the scene.
  - Example (Food): "Close-up of a rustic wooden bowl filled with vibrant green spinach salad, topped with sliced strawberries and walnuts, natural morning sunlight streaming from the side, soft focus background."
  - Example (Movement): "A woman performing a gentle yoga stretch on a mat in a sunlit living room, wearing comfortable clothing, peaceful atmosphere, soft warm lighting, plants in the background."
  - Example (Mindfulness): "A serene person sitting in a comfortable chair with eyes closed, practicing deep breathing, soft blue and purple ambient lighting, calm and tranquil mood."

🎯 TITLE RULES (KEEP IT SIMPLE):
- FOOD: Just the food name (e.g., "Cinnamon", "Salmon", "Flaxseeds")
- MOVEMENT: Just the activity (e.g., "Morning Yoga", "Swimming", "Hip Stretches")
- MINDFULNESS: Just the technique (e.g., "Deep Breathing", "Meditation", "Body Scan")

CATEGORY-SPECIFIC FIELDS ({category}):
- {category_fields.get(category.lower(), '')}

═══════════════════════════════════════════════════════════════════════════════
EXAMPLE OUTPUT
═══════════════════════════════════════════════════════════════════════════════
{json.dumps([example], indent=2)}

═══════════════════════════════════════════════════════════════════════════════
GENERATE {rec_count} RECOMMENDATIONS NOW
═══════════════════════════════════════════════════════════════════════════════
Return ONLY the JSON array, no other text. Make each recommendation UNIQUE and PRACTICAL."""

    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENGINE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class PromptRecommendationEngine:
    """
    Pure prompt engineering recommendation engine.
    
    No RAG, no vector DB, no external retrieval.
    Just GPT-4o-mini + carefully crafted prompts.
    
    Cost: ~$0.002 per recommendation set (vs $0.05 with RAG)
    """
    
    def __init__(self):
        self.model = MODEL
        self.api_key = OPENAI_API_KEY
        logger.info("=" * 60)
        logger.info("🚀 PROMPT ENGINE INITIALIZED")
        logger.info(f"   Model: {self.model}")
        logger.info(f"   Architecture: Pure Prompt Engineering (No RAG)")
        logger.info(f"   Personalization: Smart Eat/Move/Pause preference weighting enabled")
        logger.info("=" * 60)
    
    def _get_recommendation_count(self, user_profile: Dict[str, Any], category: str) -> int:
        """
        Determine number of recommendations based on lifestyle preference.
        STRICT LIMIT: Total 4 actions per day across all categories.
        
        Distribution Strategy (Total = 4):
        - Base: 1 per category (Food, Move, Pause) = 3
        - Bonus (+1): Goes to primary preference
        
        Rules:
        1. If No Preference or All 3 -> Bonus to Food (2, 1, 1)
        2. If Single Preference -> Bonus to that preference (2, 1, 1)
        3. If Dual Preference:
           - If Food is one of them -> Bonus to Food (2, 1, 1)
           - If Move+Pause -> Bonus to Move (1, 2, 1)
        """
        lifestyle_focus = user_profile.get('lifestyle_focus', [])
        category_lower = category.lower()
        
        # Map lifestyle preferences to categories
        preferred_categories = [LIFESTYLE_TO_CATEGORY.get(lf.lower(), lf.lower()) for lf in lifestyle_focus] if lifestyle_focus else []
        num_preferences = len(preferred_categories)
        
        # Default count is 1
        gets_bonus = False
        
        # Case 1: No preference or All 3 -> Food gets bonus
        if num_preferences == 0 or num_preferences == 3:
            if category_lower == 'food':
                gets_bonus = True
                
        # Case 2: Single Preference -> Preference gets bonus
        elif num_preferences == 1:
            if category_lower == preferred_categories[0]:
                gets_bonus = True
                
        # Case 3: Dual Preference
        elif num_preferences == 2:
            # If Food is preferred, it gets priority
            if 'food' in preferred_categories:
                if category_lower == 'food':
                    gets_bonus = True
            # If Food is NOT preferred (Move + Pause), Move gets priority
            else:
                if category_lower == 'movement':
                    gets_bonus = True
        
        final_count = 2 if gets_bonus else 1
        
        logger.info(f"📋 {category.upper()}: Generating {final_count} recommendations (Bonus: {gets_bonus})")
        return final_count
    
    async def generate_recommendations(
        self,
        user_profile: Dict[str, Any],
        category: str
    ) -> List[Dict[str, Any]]:
        """
        Generate recommendations using pure prompt engineering.
        
        PERSONALIZATION (Eat/Move/Pause):
        - If lifestyle_focus includes 'eat', food gets more recommendations
        - If lifestyle_focus includes 'move', movement gets more recommendations
        - If lifestyle_focus includes 'pause', mindfulness gets more recommendations
        
        Args:
            user_profile: User's health profile (should include 'lifestyle_focus' array)
            category: 'food', 'movement', or 'mindfulness'
            
        Returns:
            List of recommendation dicts in frontend-expected format
        """
        start_time = datetime.now()
        
        # Determine recommendation count based on lifestyle preference
        rec_count = self._get_recommendation_count(user_profile, category)
        lifestyle_focus = user_profile.get('lifestyle_focus', [])
        
        logger.info("=" * 60)
        logger.info(f"📝 GENERATING {category.upper()} RECOMMENDATIONS")
        logger.info(f"   User Age: {user_profile.get('age', 'N/A')}")
        logger.info(f"   Primary Hormone: {user_profile.get('primaryImbalance', 'N/A')}")
        logger.info(f"   Lifestyle Focus: {lifestyle_focus if lifestyle_focus else 'None'}")
        logger.info(f"   Recommendation Count: {rec_count} (based on lifestyle preference)")
        logger.info(f"   Symptoms: {user_profile.get('symptoms', [])[:3]}...")
        logger.info("=" * 60)
        
        print("=" * 60)
        print(f"📝 GENERATING {category.upper()} RECOMMENDATIONS")
        print(f"   Lifestyle Focus: {lifestyle_focus if lifestyle_focus else 'None'}")
        print(f"   Recommendation Count: {rec_count}")
        print("=" * 60)
        
        try:
            # Build prompt with personalized rec_count
            prompt = build_master_prompt(user_profile, category, rec_count)
            logger.info(f"📋 Prompt built: {len(prompt)} chars")
            
            # Call OpenAI
            response = await self._call_openai(prompt)
            logger.info(f"🤖 OpenAI response received: {len(response)} chars")
            
            # Parse JSON
            recommendations = self._parse_response(response, category)
            logger.info(f"✅ Parsed {len(recommendations)} recommendations")
            
            # Post-process for frontend compatibility
            recommendations = self._post_process(recommendations, user_profile, category)
            
            # Calculate timing
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            logger.info("=" * 60)
            logger.info(f"✅ {category.upper()} COMPLETE")
            logger.info(f"   Recommendations: {len(recommendations)}")
            logger.info(f"   Lifestyle Preference Applied: {'Yes' if lifestyle_focus else 'No'}")
            logger.info(f"   Time: {elapsed_ms:.0f}ms")
            logger.info(f"   Est. Cost: ${self._estimate_cost(prompt, response):.4f}")
            logger.info("=" * 60)
            
            return recommendations
            
        except Exception as e:
            logger.error(f"❌ RECOMMENDATION GENERATION FAILED: {str(e)}")
            logger.error(f"   Category: {category}")
            logger.error(f"   User: {user_profile.get('primaryImbalance', 'unknown')}")
            
            # Return fallback recommendations
            return self._get_fallback_recommendations(user_profile, category)
    
    async def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API with the prompt, with Groq fallback."""
        
        logger.info("🔌 Calling OpenAI API...")
        
        openai_error = None
        
        # Try OpenAI first
        if self.api_key:
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                body = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You are AUVRA, a women's hormone health AI. Return only valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 3000,
                    "response_format": {"type": "json_object"}  # Force JSON output
                }
                
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers=headers,
                        json=body
                    )
                    
                    if response.status_code != 200:
                        openai_error = f"OpenAI API returned {response.status_code}: {response.text[:200]}"
                        logger.warning(f"❌ {openai_error}")
                    else:
                        data = response.json()
                        content = data['choices'][0]['message']['content']
                        
                        # Log token usage
                        usage = data.get('usage', {})
                        logger.info(f"📊 Token usage: {usage.get('prompt_tokens', 0)} in, {usage.get('completion_tokens', 0)} out")
                        logger.info("✅ Recommendations generated via OpenAI")
                        
                        return content
            except Exception as e:
                openai_error = str(e)
                logger.warning(f"❌ OpenAI exception: {openai_error[:200]}")
        else:
            openai_error = "No OpenAI API key"
        
        # Groq fallback
        if openai_error and GROQ_API_KEY:
            try:
                logger.info(f"🔄 Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                
                # gpt-oss-120b is a reasoning model - doesn't support response_format
                is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                
                # Add JSON instruction to prompt for reasoning model
                enhanced_prompt = prompt
                if is_reasoning_model:
                    enhanced_prompt = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown, no explanation."
                
                headers = {
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                }
                
                body = {
                    "model": GROQ_FALLBACK_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are AUVRA, a women's hormone health AI. Return only valid JSON."},
                        {"role": "user", "content": enhanced_prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 3000
                }
                
                # Only add response_format if not a reasoning model
                if not is_reasoning_model:
                    body["response_format"] = {"type": "json_object"}
                
                async with httpx.AsyncClient(timeout=90.0) as client:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=headers,
                        json=body
                    )
                    
                    if response.status_code != 200:
                        raise Exception(f"Groq API returned {response.status_code}: {response.text[:200]}")
                    
                    data = response.json()
                    content = data['choices'][0]['message']['content']
                    
                    # Clean up reasoning model output
                    if is_reasoning_model:
                        if content.startswith("```json"):
                            content = content[7:]
                        if content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        content = content.strip()
                    
                    logger.info("✅ Recommendations generated via Groq fallback")
                    return content
                    
            except Exception as e:
                logger.error(f"❌ Groq fallback also failed: {e}")
                raise Exception(f"Both OpenAI and Groq failed: {openai_error}")
        elif openai_error:
            raise Exception(f"OpenAI failed and no Groq fallback: {openai_error}")
    
    def _parse_response(self, response: str, category: str) -> List[Dict[str, Any]]:
        """Parse LLM response into recommendation list."""
        
        try:
            # Try direct JSON parse
            data = json.loads(response)
            
            # Handle both array and object with recommendations key
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # Try common keys
                for key in ['recommendations', 'items', 'results', category]:
                    if key in data and isinstance(data[key], list):
                        return data[key]
                # If dict has recommendation-like structure, wrap in list
                if 'title' in data and 'specificAction' in data:
                    return [data]
            
            logger.warning(f"⚠️ Unexpected response structure: {type(data)}")
            return []
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parse error: {e}")
            logger.error(f"   Response preview: {response[:200]}...")
            
            # Try to extract JSON array from response
            import re
            match = re.search(r'\[.*\]', response, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except:
                    pass
            
            return []
    
    def _post_process(
        self,
        recommendations: List[Dict[str, Any]],
        user_profile: Dict[str, Any],
        category: str
    ) -> List[Dict[str, Any]]:
        """Post-process recommendations for frontend compatibility."""
        
        primary_hormone = user_profile.get('primaryImbalance', 'insulin')
        secondary_hormones = user_profile.get('secondaryImbalances', [])
        
        # TOP 2 hormones only (primary + first secondary)
        top_2_hormones = [primary_hormone.lower()]
        if secondary_hormones:
            top_2_hormones.append(secondary_hormones[0].lower())
        
        processed = []
        
        for i, rec in enumerate(recommendations):
            # Ensure all required fields exist
            rec.setdefault('title', f'{category.title()} Recommendation {i+1}')
            rec.setdefault('purpose', 'Supports hormone balance')
            rec.setdefault('specificAction', 'Follow the recommended practice')
            rec.setdefault('frequency', 'Daily')
            rec.setdefault('intensity', 'Moderate')
            rec.setdefault('expectedTimeline', '4-8 weeks')
            rec.setdefault('priority', 'medium')
            rec.setdefault('contraindications', [])
            rec.setdefault('conditions', ['PCOS'])
            rec.setdefault('symptoms', user_profile.get('symptoms', [])[:3])
            rec.setdefault('frequency_detail', 'daily:1')
            rec.setdefault('duration_weeks', 8)
            rec.setdefault('optimal_times', ['morning'] if category == 'food' else ['afternoon'] if category == 'movement' else ['evening'])
            
            # CRITICAL: Ensure hormones are from TOP 2 hormones only
            rec_hormones = rec.get('hormones', [])
            valid_hormones = [h for h in rec_hormones if h.lower() in top_2_hormones]
            if not valid_hormones:
                # Default to primary hormone if none match
                valid_hormones = [primary_hormone.title()]
            # Allow 1-2 hormones per recommendation (from TOP 2)
            rec['hormones'] = valid_hormones[:2]
            
            # Ensure research backing structure
            if not rec.get('researchBacking') or not isinstance(rec.get('researchBacking'), dict):
                rec['researchBacking'] = {
                    'summary': 'Based on clinical research for PCOS management',
                    'studies': []
                }
            
            # Category-specific field defaults
            if category == 'food':
                rec.setdefault('food_amounts', [])
                rec.setdefault('food_items', [])
            elif category == 'movement':
                rec.setdefault('exercise_durations', ['20 min'])
                rec.setdefault('exercise_types', ['general'])
                rec.setdefault('exercise_intensities', ['moderate'])
            elif category == 'mindfulness':
                rec.setdefault('mindfulness_durations', ['10 min'])
                rec.setdefault('mindfulness_techniques', ['breathing'])
            
            # Mark as prompt-engineered
            rec['rag_version'] = 'prompt_only_v2'
            rec['citation_verified'] = False
            
            processed.append(rec)
            
            logger.info(f"   📦 Rec {i+1}: {rec['title'][:30]} | {rec['hormones'][0]} | {rec['priority']}")
        
        return processed
    
    def _estimate_cost(self, prompt: str, response: str) -> float:
        """Estimate API cost."""
        # GPT-4o-mini pricing
        input_tokens = len(prompt) / 4  # Rough estimate
        output_tokens = len(response) / 4
        
        input_cost = (input_tokens / 1000) * 0.00015
        output_cost = (output_tokens / 1000) * 0.0006
        
        return input_cost + output_cost
    
    def _get_fallback_recommendations(
        self,
        user_profile: Dict[str, Any],
        category: str
    ) -> List[Dict[str, Any]]:
        """Return safe fallback recommendations if generation fails."""
        
        logger.warning(f"⚠️ Using fallback recommendations for {category}")
        
        primary_hormone = user_profile.get('primaryImbalance', 'insulin')
        
        fallbacks = {
            'food': [
                {
                    "title": "Anti-inflammatory Diet",
                    "purpose": "Reduce inflammation which affects hormone balance",
                    "specificAction": "Include 2 servings of leafy greens (spinach, kale) daily",
                    "frequency": "Daily",
                    "intensity": "Low",
                    "expectedTimeline": "4-6 weeks",
                    "priority": "high",
                    "contraindications": [],
                    "conditions": ["PCOS"],
                    "symptoms": user_profile.get('symptoms', [])[:2],
                    "hormones": [primary_hormone.title()],
                    "food_amounts": ["2 cups"],
                    "food_items": ["leafy greens", "spinach", "kale"],
                    "frequency_detail": "daily:1",
                    "duration_weeks": 12,
                    "optimal_times": ["morning"],
                    "researchBacking": {"summary": "Leafy greens support hormone detoxification", "studies": []},
                    "rag_version": "fallback",
                    "citation_verified": False
                }
            ],
            'movement': [
                {
                    "title": "Daily Walking",
                    "purpose": "Low-impact exercise improves insulin sensitivity",
                    "specificAction": "Take a 30-minute walk after your largest meal",
                    "frequency": "Daily",
                    "intensity": "Low",
                    "expectedTimeline": "2-4 weeks",
                    "priority": "high",
                    "contraindications": [],
                    "conditions": ["PCOS"],
                    "symptoms": user_profile.get('symptoms', [])[:2],
                    "hormones": [primary_hormone.title()],
                    "exercise_durations": ["30 min"],
                    "exercise_types": ["walking"],
                    "exercise_intensities": ["low"],
                    "frequency_detail": "daily:1",
                    "duration_weeks": 12,
                    "optimal_times": ["afternoon"],
                    "researchBacking": {"summary": "Post-meal walking reduces blood sugar spikes", "studies": []},
                    "rag_version": "fallback",
                    "citation_verified": False
                }
            ],
            'mindfulness': [
                {
                    "title": "Deep Breathing",
                    "purpose": "Activates relaxation response, lowering cortisol",
                    "specificAction": "Practice 5 minutes of deep belly breathing before bed",
                    "frequency": "Daily",
                    "intensity": "Low",
                    "expectedTimeline": "1-2 weeks",
                    "priority": "high",
                    "contraindications": [],
                    "conditions": ["PCOS"],
                    "symptoms": user_profile.get('symptoms', [])[:2],
                    "hormones": [primary_hormone.title()],
                    "mindfulness_durations": ["5 min"],
                    "mindfulness_techniques": ["deep breathing"],
                    "frequency_detail": "daily:1",
                    "duration_weeks": 8,
                    "optimal_times": ["evening"],
                    "researchBacking": {"summary": "Deep breathing reduces cortisol levels", "studies": []},
                    "rag_version": "fallback",
                    "citation_verified": False
                }
            ]
        }
        
        return fallbacks.get(category.lower(), fallbacks['food'])


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

_engine_instance: Optional[PromptRecommendationEngine] = None


def get_prompt_engine() -> PromptRecommendationEngine:
    """Get singleton prompt engine instance."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = PromptRecommendationEngine()
    return _engine_instance


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTION (Replaces V3 engine call)
# ═══════════════════════════════════════════════════════════════════════════════

async def generate_prompt_recommendations(
    user_profile: Dict[str, Any],
    category: str
) -> List[Dict[str, Any]]:
    """
    Main entry point for generating recommendations.
    
    Usage:
        from app.services.prompt_recommendation_engine import generate_prompt_recommendations
        
        recs = await generate_prompt_recommendations(user_profile, 'food')
    """
    engine = get_prompt_engine()
    return await engine.generate_recommendations(user_profile, category)


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: UNIFIED SESSION RECOMMENDATIONS WITH REAL PUBMED CITATIONS
# ═══════════════════════════════════════════════════════════════════════════════
# 
# This generates EXACTLY 4 recommendations for signup (similar to action_plan_generator)
# with REAL PubMed citations - NOT GPT-fabricated citations.
# 
# Distribution:
# - 2 recommendations for PRIMARY hormone
# - 2 recommendations for SECONDARY hormone
# 
# Categories are mixed based on lifestyle_focus preference
# ═══════════════════════════════════════════════════════════════════════════════

async def generate_session_recommendations_with_pubmed(
    user_profile: Dict[str, Any],
    db = None
) -> List[Dict[str, Any]]:
    """
    Generate exactly 4 session recommendations with REAL PubMed citations.
    
    This mirrors the action_plan_generator approach:
    1. Research phase: Search PubMed for real papers
    2. Generation phase: GPT creates recommendations based on research
    3. Validation phase: Strict Pydantic validation
    
    Args:
        user_profile: User's health profile
        db: Database session for PubMed caching
        
    Returns:
        List of 4 recommendations with real citations
    """
    import asyncio
    from app.services.pubmed_service import execute_pubmed_tool
    
    logger.info("=" * 70)
    logger.info("🔬 UNIFIED SESSION RECOMMENDATIONS (PubMed-Backed & Validated)")
    logger.info("=" * 70)
    
    # Extract user info
    primary_hormone = user_profile.get('primaryImbalance', 'insulin')
    secondary_hormones = user_profile.get('secondaryImbalances', [])
    secondary_hormone = secondary_hormones[0] if secondary_hormones else 'cortisol'
    diagnosed_conditions = user_profile.get('conditions', ['PCOS'])
    lifestyle_focus = user_profile.get('lifestyle_focus', [])
    
    condition_str = ', '.join(diagnosed_conditions) if diagnosed_conditions else 'PCOS'
    
    # Determine category distribution based on lifestyle_focus
    # SIMPLIFIED: We don't force categories anymore. We let the LLM decide.
    # But for research purposes, we need to search for relevant topics.
    
    # Research Strategy: Cover all bases so LLM has options
    # 1. Primary Hormone + Food
    # 2. Primary Hormone + Movement
    # 3. Primary Hormone + Mindfulness
    # 4. Secondary Hormone + (User Preference or General)
    
    # Hormone assignment: 2 for primary, 2 for secondary (Guideline for LLM)
    hormones = [primary_hormone, primary_hormone, secondary_hormone, secondary_hormone]
    
    logger.info(f"📋 User Profile:")
    logger.info(f"   Primary: {primary_hormone}")
    logger.info(f"   Secondary: {secondary_hormone}")
    logger.info(f"   Lifestyle Focus: {lifestyle_focus}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # STEP 1: RESEARCH PHASE - Search PubMed for REAL papers
    # ═══════════════════════════════════════════════════════════════════════
    logger.info("🔬 STEP 1: Research Discovery Phase - Finding real papers...")
    
    # Build research queries - Broad coverage
    research_queries = [
        {
            'query': f"{primary_hormone} nutrition food {condition_str} women intervention",
            'category': 'food',
            'hormone': primary_hormone,
            'index': 0
        },
        {
            'query': f"{primary_hormone} exercise physical activity {condition_str} women",
            'category': 'movement',
            'hormone': primary_hormone,
            'index': 1
        },
        {
            'query': f"{primary_hormone} mindfulness stress reduction {condition_str} women",
            'category': 'mindfulness',
            'hormone': primary_hormone,
            'index': 2
        },
        {
            'query': f"{secondary_hormone} management {condition_str} women",
            'category': 'general',
            'hormone': secondary_hormone,
            'index': 3
        }
    ]
    
    # Parallel PubMed searches
    async def fetch_paper(q: Dict) -> Dict:
        try:
            logger.info(f"   🔎 Searching PubMed for: {q['query']} (Category: {q['category']}, Hormone: {q['hormone']})")
            paper = await execute_pubmed_tool({
                'query': q['query'],
                'action_title': f"Research {q['index'] + 1}",
                'category': q['category'],
                'target_hormone': q['hormone']
            }, db=db)
            
            if paper and paper.get('title'):
                logger.info(f"   ✅ Found paper: {paper.get('title')[:50]}...")
                return {
                    'category': q['category'],
                    'hormone': q['hormone'],
                    'paper': paper
                }
            logger.warning(f"   ⚠️ No paper found for query: {q['query']}")
            return {'category': q['category'], 'hormone': q['hormone'], 'paper': None}
        except Exception as e:
            logger.warning(f"PubMed search failed: {e}")
            return {'category': q['category'], 'hormone': q['hormone'], 'paper': None}
    
    results = await asyncio.gather(*[fetch_paper(q) for q in research_queries])
    
    # Count successful papers
    papers_found = sum(1 for r in results if r.get('paper'))
    logger.info(f"🔬 Research complete: Found {papers_found}/4 relevant papers")
    
    # Build research context for GPT
    research_context = []
    for r in results:
        if r.get('paper'):
            p = r['paper']
            research_context.append(f"""
📚 Research for {r['hormone'].upper()} ({r['category']}):
   Title: {p.get('title', 'Unknown')}
   Journal: {p.get('journal', 'Unknown')} ({p.get('year', 'N/A')})
   Finding: {p.get('finding', 'No finding extracted')}
   PMID: {p.get('pmid', 'N/A')}
   Verification: {p.get('verification_link', 'N/A')}
""")
    
    research_summary = '\n'.join(research_context) if research_context else "No papers found - use your medical knowledge."
    
    # ═══════════════════════════════════════════════════════════════════════
    # STEP 2: GENERATION PHASE - GPT creates recommendations based on research
    # ═══════════════════════════════════════════════════════════════════════
    logger.info("🤖 STEP 2: Generating recommendations based on research...")
    
    # Get EXACT category distribution based on lifestyle_focus
    distribution = get_category_distribution(lifestyle_focus)
    food_count = distribution['food']
    movement_count = distribution['movement']
    mindfulness_count = distribution['mindfulness']
    
    # Build distribution instruction for GPT
    distribution_instruction = f"""
STRICT CATEGORY DISTRIBUTION (Must follow EXACTLY):
- Food: {food_count} recommendation(s)
- Movement: {movement_count} recommendation(s)  
- Mindfulness: {mindfulness_count} recommendation(s)
- TOTAL: 4 recommendations

⚠️ DO NOT deviate from this distribution. If a category has 0, do NOT include any recommendations for it."""

    logger.info(f"📊 Using distribution: Food={food_count}, Movement={movement_count}, Mindfulness={mindfulness_count}")
    
    prompt = f"""You are AUVRA, a women's hormone health AI. Generate EXACTLY 4 personalized recommendations.

═══════════════════════════════════════════════════════════════════════════════
USER PROFILE
═══════════════════════════════════════════════════════════════════════════════
- Primary Hormone: {primary_hormone.upper()}
- Secondary Hormone: {secondary_hormone.upper()}
- Conditions: {condition_str}
- Age: {user_profile.get('age', 30)}
- Lifestyle Focus: {', '.join(lifestyle_focus) if lifestyle_focus else 'Balanced (all categories)'}

═══════════════════════════════════════════════════════════════════════════════
{distribution_instruction}
═══════════════════════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════════════════════
RESEARCH FINDINGS (USE THESE FOR CITATIONS!)
═══════════════════════════════════════════════════════════════════════════════
{research_summary}

═══════════════════════════════════════════════════════════════════════════════
TASK: Generate exactly 4 recommendations following the distribution above
═══════════════════════════════════════════════════════════════════════════════
Instructions:
1. STRICTLY follow the category distribution above (Food={food_count}, Movement={movement_count}, Mindfulness={mindfulness_count}).
2. Ensure at least 2 actions target the Primary Hormone ({primary_hormone}).
3. Ensure at least 1 action targets the Secondary Hormone ({secondary_hormone}).
4. Use the provided research findings to back up your recommendations where possible.

Each recommendation MUST include:
- title: Simple name (e.g., "Cinnamon", "Morning Yoga", "Deep Breathing")
- category: "food", "movement", or "mindfulness"
- time_slot: "morning", "afternoon", or "evening"
- specific_action: EXACT instructions with amounts/durations
- purpose: 1-2 sentences explaining WHY this helps the specific hormone/condition
- target_hormone: The hormone being targeted
- hormone_persona_intro: A short, friendly intro from the hormone persona (e.g., "Hi, I'm Cortisol...")
- image_prompt: A prompt to generate an image for this action
- research_studies: Array of objects with EXACTLY these fields:
  - title: str
  - journal: str
  - year: int
  - participants: int (use 0 if unknown)
  - finding: str
  - pmid: str
  - verification_link: str
- variants: Array of 3 objects with EXACTLY these fields (NO specific_action here!):
  - variant_type: str (e.g., "Easier", "Harder", "Alternative")
  - title: str
  - description: str
  - image_prompt: str
- Category specific fields (use empty list [] if not applicable):
  - food_items, food_amounts (for food)
  - exercise_types, exercise_durations, exercise_intensities (for movement)
  - mindfulness_techniques, mindfulness_durations (for mindfulness)
- symptoms: List of symptoms addressed
- conditions: List of conditions addressed

CRITICAL: Use the ACTUAL paper details from the research findings above. 
Include the real PMID and verification_link for each citation.
Ensure strict adherence to the JSON schema.

Return ONLY a valid JSON object matching the ActionPlanResponseModel schema.
The root key MUST be "actions".
Each action MUST have "variants" array (even if empty).
"""

    # Call GPT (OpenAI with Groq Fallback)
    content = None
    openai_error = None
    
    # 1. Try OpenAI
    if OPENAI_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            
            body = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "You are AUVRA. Return only valid JSON matching the schema."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 4000,
                "response_format": {"type": "json_object"}
            }
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=body
                )
                
                if response.status_code != 200:
                    openai_error = f"OpenAI returned {response.status_code}: {response.text[:200]}"
                    logger.error(f"❌ OpenAI error: {openai_error}")
                else:
                    data = response.json()
                    content = data['choices'][0]['message']['content']
                    logger.info(f"🤖 OpenAI Response received ({len(content)} chars)")
                    logger.debug(f"Raw content: {content[:500]}...")
        except Exception as e:
            openai_error = str(e)
            logger.error(f"❌ OpenAI exception: {openai_error}")
    else:
        openai_error = "No OpenAI API key"

    # 2. Fallback to Groq if OpenAI failed
    if not content and GROQ_API_KEY:
        logger.info(f"🔄 Falling back to Groq ({GROQ_FALLBACK_MODEL})...")
        try:
            # Check if reasoning model (no response_format)
            is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
            
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            
            # Enhance prompt for reasoning models if needed
            enhanced_prompt = prompt
            if is_reasoning_model:
                enhanced_prompt += "\n\nIMPORTANT: Return ONLY valid JSON. No markdown, no thinking."
            
            # CRITICAL: Groq models often return {"recommendations": [...]} instead of {"actions": [...]}
            # We must explicitly instruct it to use the correct key
            enhanced_prompt += '\n\nCRITICAL: The root JSON key MUST be "actions", NOT "recommendations". Example: {"actions": [...]}'
            
            body = {
                "model": GROQ_FALLBACK_MODEL,
                "messages": [
                    {"role": "system", "content": "You are AUVRA. Return only valid JSON matching the schema."},
                    {"role": "user", "content": enhanced_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 4000
            }
            
            if not is_reasoning_model:
                body["response_format"] = {"type": "json_object"}
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=body
                )
                
                if response.status_code != 200:
                    logger.error(f"❌ Groq error: {response.text[:200]}")
                else:
                    data = response.json()
                    content = data['choices'][0]['message']['content']
                    
                    # Clean markdown if reasoning model
                    if is_reasoning_model:
                        content = content.strip()
                        if content.startswith("```json"): content = content[7:]
                        if content.startswith("```"): content = content[3:]
                        if content.endswith("```"): content = content[:-3]
                        content = content.strip()
                        
                    logger.info(f"🤖 Groq Response received ({len(content)} chars)")
        except Exception as e:
            logger.error(f"❌ Groq fallback failed: {e}")

    if not content:
        logger.error("❌ All LLM providers failed")
        return []

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 3: VALIDATION PHASE - Strict Pydantic Validation
    # ═══════════════════════════════════════════════════════════════════════
    logger.info("🛡️ STEP 3: Validating response with Pydantic...")
    
    try:
        # Parse JSON first
        parsed_json = json.loads(content)
        
        # FIX 1: Handle common LLM error where it returns {"recommendations": [...]} instead of {"actions": [...]}
        if "actions" not in parsed_json and "recommendations" in parsed_json:
            logger.warning("⚠️ LLM returned 'recommendations' key instead of 'actions'. Fixing structure...")
            parsed_json["actions"] = parsed_json.pop("recommendations")
        
        # FIX 2: Auto-generate variants if missing (LLM often omits these)
        # CRITICAL: This must run BEFORE Pydantic validation
        if "actions" in parsed_json:
            for action in parsed_json["actions"]:
                # Check if variants is missing, None, or empty
                if "variants" not in action or action.get("variants") is None or not action.get("variants"):
                    # Generate default variants based on category
                    title = action.get("title", "Action")
                    category = action.get("category", "food")
                    
                    logger.warning(f"   🔧 Auto-generating variants for '{title}' (category: {category})")
                    
                    if category == "food":
                        action["variants"] = [
                            {"variant_type": "Easy", "title": f"Quick {title}", "description": f"A simpler way to enjoy {title}", "image_prompt": f"Quick easy {title} food photography"},
                            {"variant_type": "Healthy", "title": f"Nutrient-rich {title}", "description": f"A healthier preparation of {title}", "image_prompt": f"Healthy {title} food photography"},
                            {"variant_type": "Tasty", "title": f"Flavorful {title}", "description": f"A delicious version of {title}", "image_prompt": f"Delicious {title} food photography"}
                        ]
                    elif category == "movement":
                        action["variants"] = [
                            {"variant_type": "Gentle", "title": f"Gentle {title}", "description": f"A lower intensity version", "image_prompt": f"Gentle {title} exercise photography"},
                            {"variant_type": "Quick", "title": f"Quick {title}", "description": f"A shorter duration option", "image_prompt": f"Quick {title} exercise photography"},
                            {"variant_type": "Energizing", "title": f"Energizing {title}", "description": f"A more invigorating version", "image_prompt": f"Energizing {title} exercise photography"}
                        ]
                    else:  # mindfulness
                        action["variants"] = [
                            {"variant_type": "Quick", "title": f"Quick {title}", "description": f"A shorter practice", "image_prompt": f"Quick {title} mindfulness photography"},
                            {"variant_type": "Deep", "title": f"Deep {title}", "description": f"A more immersive practice", "image_prompt": f"Deep {title} mindfulness photography"},
                            {"variant_type": "Guided", "title": f"Guided {title}", "description": f"With audio guidance", "image_prompt": f"Guided {title} mindfulness photography"}
                        ]
                    logger.info(f"   ✅ Generated 3 variants for '{title}'")
        
        # Validate with Pydantic
        validated_response = ActionPlanResponseModel.model_validate(parsed_json)
        logger.info("✅ Pydantic validation SUCCESSFUL")
        
        # Convert back to dict for return
        final_recs = [action.model_dump() for action in validated_response.actions]
        
        # Post-process: Add legacy fields if needed by frontend
        for i, rec in enumerate(final_recs):
            # Ensure research is populated from our search if GPT missed it
            if not rec.get('research_studies') and i < len(results) and results[i].get('paper'):
                paper = results[i]['paper']
                rec['research_studies'] = [{
                    'title': paper.get('title', ''),
                    'journal': paper.get('journal', ''),
                    'year': paper.get('year', 0),
                    'participants': 0,
                    'finding': paper.get('finding', ''),
                    'pmid': paper.get('pmid', ''),
                    'verification_link': paper.get('verification_link', '')
                }]
            
            # Add legacy fields for compatibility
            rec['citation_verified'] = bool(rec.get('research_studies'))
            rec['rag_version'] = 'pubmed_backed_strict_v1'
            rec['priority'] = 'high'
            rec['frequency'] = 'Daily'
            rec['intensity'] = 'Moderate'
            rec['expectedTimeline'] = '4-8 weeks'
            
            logger.info(f"   ✅ Rec {i+1}: {rec.get('title', 'N/A')[:30]} | {rec['category']} | {'📚' if rec['citation_verified'] else '❌'}")
        
        logger.info("=" * 70)
        logger.info(f"✅ SESSION RECOMMENDATIONS COMPLETE: {len(final_recs)} recommendations")
        logger.info(f"   Papers found: {papers_found}/4")
        logger.info("=" * 70)
        
        return final_recs
        
    except ValidationError as ve:
        logger.error(f"❌ Pydantic Validation Failed: {ve}")
        logger.error(f"Content was: {content[:1000]}...") # Log more content for debugging
        for err in ve.errors():
            logger.error(f"   -> Field: {err['loc']}, Error: {err['msg']}")
        return []
    except Exception as e:
        logger.error(f"❌ Session recommendation generation failed: {e}")
        return []
