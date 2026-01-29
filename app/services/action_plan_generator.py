"""
AUVRA Action Plan Generator Service

Generates 4 personalized daily actions using GPT-5-mini:
- 2 actions targeting PRIMARY hormone
- 2 actions targeting SECONDARY hormone  
- Categories based on users lifestyle_focus (eat/move/pause)
- Each action has 4 images (hero + 3 variants)

Features:
- Hormone-aware persona introductions
- Real research citations (journal, year, participants)
- Consistent prompt style for semantic image matching
- Integration with ImageLibraryService for image generation
"""
 
import os
import json
import logging
import time
import random
import asyncio
import traceback
import hashlib
from typing import Optional, List, Dict, Any, Tuple, Literal
from datetime import datetime, timezone, date, timedelta

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update, text, or_

from app.services.image_library_service import get_image_library_service
from app.services.pubmed_service import PUBMED_SEARCH_TOOL, execute_pubmed_tool, execute_pubmed_tool_multiple
from app.core.config import settings

# NEW: Import unified memory for cross-chatbot context
from app.langgraph.memory import get_unified_context, format_context_for_prompt

# Get API keys from environment
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or getattr(settings, "GROQ_API_KEY", None)

# Fallback model: llama-3.3-70b-versatile has higher rate limits (30K TPM vs 8K)
GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"

logger = logging.getLogger(__name__)


# ============================================================================
# PYDANTIC MODELS FOR STRUCTURED OUTPUTS
# OpenAI Structured Outputs requires ALL fields to be required (no Optional)
# For strict: true mode, we must have additionalProperties: false
# ============================================================================

class ResearchStudyModel(BaseModel):
    """Research citation from PubMed - all fields required."""
    title: str
    journal: str
    year: int
    participants: int = Field(default=0)  # Default to 0 if LLM doesn't provide
    finding: str
    pmid: str
    verification_link: str = Field(default="")  # Default empty if LLM doesn't provide
    
    model_config = {"extra": "forbid"}  # additionalProperties: false


class ActionVariantModel(BaseModel):
    """Variant of an action - all fields required."""
    variant_type: str
    title: str
    description: str
    image_prompt: str
    
    model_config = {"extra": "ignore"}  # Changed from "forbid" to handle Groqs extra fields
    
    @classmethod
    def model_validate(cls, obj):
        """Custom validation to handle old format from Groq."""
        if isinstance(obj, dict):
            # If Groq sends old format with 'action' instead of proper fields
            if 'action' in obj and 'variant_type' not in obj:
                # Map old format to new
                obj = {
                    'variant_type': 'alternative',  # Default type
                    'title': obj.get('action', ''),
                    'description': obj.get('action', ''),
                    'image_prompt': f"Professional photograph of {obj.get('action', 'healthy food')}, appetizing presentation, natural lighting, 4K quality"
                }
        return super().model_validate(obj)


class ActionItemModel(BaseModel):
    """
    Single action item - ALL fields are required (OpenAI strict mode).
    For category-specific fields, GPT fills with [] for non-matching categories.
    Example: food action has food_items=["oats"], exercise_types=[]
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
    
    # Variants - exactly 3 required
    variants: List[ActionVariantModel]
    
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
    
    model_config = {"extra": "forbid"}

    @field_validator(
        'food_items', 'food_amounts', 
        'exercise_types', 'exercise_durations', 'exercise_intensities',
        'mindfulness_techniques', 'mindfulness_durations',
        'symptoms', 'conditions',
        mode='before'
    )
    @classmethod
    def convert_list_items_to_strings(cls, v):
        """Handle cases where LLM returns numbers instead of strings for list items."""
        if v is None:
            return []
        if isinstance(v, list):
            return [str(item) for item in v]
        return [str(v)]


class ActionPlanResponseModel(BaseModel):
    """Complete action plan response - exactly 4 actions required."""
    actions: List[ActionItemModel]
    
    model_config = {"extra": "forbid"}


async def _create_async_session(engine_maker=None) -> AsyncSession:
    """Create an isolated async database session for concurrent operations."""
    if engine_maker:
        return engine_maker()
        
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import sessionmaker
    
    db_url = os.getenv("DATABASE_URL", "")
    
    # Convert to async URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    return async_session()


# ============================================================================
# ACTION DEDUPLICATION HELPERS - ENHANCED VERSION
# Multi-layer approach: title normalization, SEMANTIC grouping, similarity scoring
# ============================================================================

import re
import random
from difflib import SequenceMatcher

# ═══════════════════════════════════════════════════════════════════════════════
# SEMANTIC FOOD GROUPS - Items in same group are considered DUPLICATES
# This prevents recommending "salmon" and "sardines" in the same plan (both fatty fish)
# ═══════════════════════════════════════════════════════════════════════════════

SEMANTIC_FOOD_GROUPS = {
    "fatty_fish": ["salmon", "sardines", "mackerel", "herring", "anchovies", "trout", "tuna"],
    "leafy_greens": ["spinach", "kale", "swiss chard", "arugula", "collard greens", "bok choy", "lettuce", "watercress"],
    "nuts_walnuts_family": ["walnuts", "pecans"],  # Same omega-3 profile
    "nuts_almonds_family": ["almonds", "cashews", "pistachios", "macadamia"],
    "seeds_omega3": ["chia seeds", "flaxseed", "flax seeds", "hemp seeds"],
    "seeds_zinc": ["pumpkin seeds", "sunflower seeds", "sesame seeds"],
    "berries": ["blueberries", "strawberries", "raspberries", "blackberries", "acai", "goji berries", "cranberries"],
    "cruciferous": ["broccoli", "cauliflower", "brussels sprouts", "cabbage"],
    "whole_grains": ["quinoa", "oats", "oatmeal", "brown rice", "farro", "barley", "millet", "buckwheat"],
    "legumes": ["lentils", "chickpeas", "black beans", "kidney beans", "edamame", "mung beans"],
    "fermented": ["yogurt", "kefir", "sauerkraut", "kimchi", "miso", "tempeh", "kombucha"],
    "eggs_dairy": ["eggs", "egg", "cheese", "milk"],
    "root_vegetables": ["sweet potato", "beets", "carrots", "turnips", "parsnips"],
    "avocado_healthy_fats": ["avocado", "olive oil", "coconut oil"],
    "citrus": ["orange", "lemon", "lime", "grapefruit"],
    "tropical": ["banana", "mango", "pineapple", "papaya"],
}

SEMANTIC_MOVEMENT_GROUPS = {
    "yoga_family": ["yoga", "morning yoga", "evening yoga", "gentle yoga", "restorative yoga", "vinyasa", "yin yoga"],
    "stretching": ["stretching", "gentle stretching", "hip stretches", "morning stretch", "evening stretch"],
    "walking": ["walking", "morning walk", "evening walk", "post-meal walk", "nature walk", "hiking"],
    "cardio_high": ["hiit", "running", "jogging", "sprinting", "jumping rope", "burpees"],
    "cardio_moderate": ["cycling", "swimming", "rowing", "elliptical", "dancing", "zumba"],
    "strength": ["strength training", "weight training", "resistance training", "weightlifting", "bodyweight"],
    "pilates_barre": ["pilates", "barre", "core workout"],
    "mind_body": ["tai chi", "qigong"],
}

SEMANTIC_MINDFULNESS_GROUPS = {
    "breathing": ["deep breathing", "box breathing", "4-7-8 breathing", "belly breathing", "breath work", "pranayama"],
    "meditation": ["meditation", "guided meditation", "silent meditation", "mindfulness meditation", "loving kindness"],
    "body_awareness": ["body scan", "progressive relaxation", "progressive muscle relaxation"],
    "journaling": ["journaling", "gratitude journal", "gratitude journaling", "reflection", "morning pages"],
    "visualization": ["visualization", "guided imagery", "mental rehearsal"],
}


def get_semantic_group(title: str, category: str) -> Optional[str]:
    """
    Get the semantic group for an action title.
    Returns None if not in any group.
    """
    if not title:
        return None
    
    title_lower = title.lower().strip()
    
    if category == "food":
        groups = SEMANTIC_FOOD_GROUPS
    elif category == "movement":
        groups = SEMANTIC_MOVEMENT_GROUPS
    elif category == "mindfulness":
        groups = SEMANTIC_MINDFULNESS_GROUPS
    else:
        return None
    
    for group_name, items in groups.items():
        for item in items:
            if item in title_lower or title_lower in item:
                return group_name
    
    return None


def is_semantic_duplicate(new_action: Dict[str, Any], existing_actions: List[Dict[str, Any]]) -> bool:
    """
    Check if new action is semantically a duplicate (same food group, etc.).
    E.g., "salmon" and "sardines" are both fatty_fish → duplicate.
    """
    new_title = new_action.get("title", "")
    new_category = new_action.get("category", "")
    new_group = get_semantic_group(new_title, new_category)
    
    if not new_group:
        return False  # Not in a known group, can't be semantic duplicate
    
    for existing in existing_actions:
        existing_title = existing.get("title", "")
        existing_category = existing.get("category", "")
        existing_group = get_semantic_group(existing_title, existing_category)
        
        if existing_group and existing_group == new_group:
            logger.debug(f"Semantic duplicate: '{new_title}' and '{existing_title}' both in group '{new_group}'")
            return True
    
    return False


def normalize_title(title: str) -> str:
    """
    Normalize action title for duplicate comparison.
    Removes punctuation, extra spaces, and lowercases.
    """
    if not title:
        return ""
    # Remove all non-alphanumeric characters except spaces
    normalized = re.sub(r'[^a-z0-9\s]+', '', title.lower().strip())
    # Collapse multiple spaces to single space
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def calculate_similarity(action1: Dict[str, Any], action2: Dict[str, Any]) -> float:
    """
    Calculate similarity score between two actions.
    
    Uses weighted combination of:
    - Title similarity (40%)
    - Content/specific_action similarity (40%)
    - Category match (10%)
    - Target hormone match (10%)
    
    Returns: float between 0.0 and 1.0
    """
    # Title similarity using SequenceMatcher (built-in, no fuzzywuzzy needed)
    title1 = normalize_title(action1.get('title', ''))
    title2 = normalize_title(action2.get('title', ''))
    title_sim = SequenceMatcher(None, title1, title2).ratio()
    
    # Content similarity
    content1 = (action1.get('specific_action', '') or '').lower().strip()
    content2 = (action2.get('specific_action', '') or '').lower().strip()
    content_sim = SequenceMatcher(None, content1, content2).ratio() if content1 and content2 else 0.0
    
    # Category match (boolean -> 1.0 or 0.0)
    cat1 = (action1.get('category', '') or '').lower()
    cat2 = (action2.get('category', '') or '').lower()
    category_match = 1.0 if cat1 and cat2 and cat1 == cat2 else 0.0
    
    # Hormone match (boolean -> 1.0 or 0.0)
    hormone1 = (action1.get('target_hormone', '') or '').lower()
    hormone2 = (action2.get('target_hormone', '') or '').lower()
    hormone_match = 1.0 if hormone1 and hormone2 and hormone1 == hormone2 else 0.0
    
    # Weighted score
    score = (title_sim * 0.4) + (content_sim * 0.4) + (category_match * 0.1) + (hormone_match * 0.1)
    return score


def is_duplicate(
    new_action: Dict[str, Any], 
    existing_actions: List[Dict[str, Any]], 
    threshold: float = 0.85  # INCREASED from 0.70 to 0.85 for stricter matching
) -> bool:
    """
    Check if new_action is a duplicate of any existing action.
    Uses BOTH string similarity AND semantic grouping.
    
    Args:
        new_action: The action to check
        existing_actions: List of existing actions to compare against
        threshold: Similarity threshold (0.0-1.0). Default 0.85 = 85% similar
        
    Returns: True if duplicate found, False otherwise
    """
    if not existing_actions:
        return False
    
    new_title_normalized = normalize_title(new_action.get('title', ''))
    
    # Check 1: SEMANTIC DUPLICATE (same food group, etc.)
    if is_semantic_duplicate(new_action, existing_actions):
        return True
    
    for existing in existing_actions:
        # Check 2: Exact title match (normalized)
        existing_title_normalized = normalize_title(existing.get('title', ''))
        if new_title_normalized and existing_title_normalized:
            if new_title_normalized == existing_title_normalized:
                logger.debug(f"Duplicate: exact title match '{new_action.get('title')}' == '{existing.get('title')}'")
                return True
        
        # Check 3: Full similarity check
        similarity = calculate_similarity(new_action, existing)
        if similarity >= threshold:
            logger.debug(f"Duplicate: similarity {similarity:.2f} >= {threshold} for '{new_action.get('title')}' vs '{existing.get('title')}'")
            return True
    
    return False


def is_title_in_exclusion_set(title: str, exclusion_set: set) -> bool:
    """Check if normalized title is in the exclusion set."""
    if not title or not exclusion_set:
        return False
    normalized = normalize_title(title)
    return normalized in exclusion_set


def get_alternative_from_group(banned_item: str, category: str) -> Optional[str]:
    """
    Get a random alternative from a different semantic group.
    Used to suggest variety when an item is banned.
    """
    banned_group = get_semantic_group(banned_item, category)
    
    if category == "food":
        all_groups = SEMANTIC_FOOD_GROUPS
    elif category == "movement":
        all_groups = SEMANTIC_MOVEMENT_GROUPS
    elif category == "mindfulness":
        all_groups = SEMANTIC_MINDFULNESS_GROUPS
    else:
        return None
    
    # Get items from DIFFERENT groups
    alternatives = []
    for group_name, items in all_groups.items():
        if group_name != banned_group:
            alternatives.extend(items)
    
    return random.choice(alternatives) if alternatives else None


# ============================================================================
# HORMONE PERSONAS - Used for personalized introductions
# ============================================================================

HORMONE_PERSONAS = {
    "cortisol": {
        "name": "Cortisol",
        "emoji": "",
        "personality": "your calming companion",
        "phase_behavior": {
            "menstrual": "I tend to spike during your period, which can make you feel more stressed or anxious",
            "follicular": "I am usually balanced in your follicular phase, but stress can still throw me off",
            "ovulation": "I can rise during ovulation, affecting your mood and energy",
            "luteal": "I tend to spike in your luteal phase, which can cause anxiety or tension"
        },
        "focus": "stress reduction and adrenal support",
        "benefit": "calmer and more relaxed",
        "supportive_foods": ["magnesium-rich foods", "adaptogens", "omega-3s", "vitamin C foods"],
        "supportive_movement": ["yoga", "gentle stretching", "walking in nature", "tai chi"],
        "supportive_mindfulness": ["meditation", "deep breathing", "journaling", "body scan"]
    },
    "progesterone": {
        "name": "Progesterone",
        "emoji": "",
        "personality": "your peaceful guide",
        "phase_behavior": {
            "menstrual": "I am at my lowest during your period, which can affect your mood and sleep",
            "follicular": "I am starting to build up in your follicular phase, preparing your body",
            "ovulation": "I begin to rise after ovulation to support potential pregnancy",
            "luteal": "I tend to dip in your luteal phase, causing mood swings or cramps"
        },
        "focus": "hormonal balance and calm",
        "benefit": "calmer and more balanced",
        "supportive_foods": ["zinc-rich foods", "vitamin B6 foods", "healthy fats", "fiber-rich foods"],
        "supportive_movement": ["gentle yoga", "swimming", "pilates", "restorative movement"],
        "supportive_mindfulness": ["sleep hygiene", "relaxation techniques", "gratitude practice", "evening rituals"]
    },
    "estrogen": {
        "name": "Estrogen",
        "emoji": "",
        "personality": "your radiant friend",
        "phase_behavior": {
            "menstrual": "I am at my lowest during your period, which can cause fatigue and low mood",
            "follicular": "I am rising in your follicular phase, boosting your energy and mood",
            "ovulation": "I peak during ovulation, making you feel confident and vibrant",
            "luteal": "I start to drop in your luteal phase, which can affect your skin and mood"
        },
        "focus": "estrogen balance and vitality",
        "benefit": "glowing and energized",
        "supportive_foods": ["phytoestrogen-rich foods", "cruciferous vegetables", "omega-3 rich seeds", "antioxidant-rich fruits"],
        "supportive_movement": ["strength training", "high-intensity intervals", "dancing", "cardio"],
        "supportive_mindfulness": ["confidence building", "self-care rituals", "social connection", "creative expression"]
    },
    "testosterone": {
        "name": "Testosterone",
        "emoji": "",
        "personality": "your energizing coach",
        "phase_behavior": {
            "menstrual": "I am lower during your period, which can reduce your drive and energy",
            "follicular": "I am rising in your follicular phase, boosting your motivation",
            "ovulation": "I peak around ovulation, giving you extra confidence and energy",
            "luteal": "I tend to drop in your luteal phase, reducing your drive and strength"
        },
        "focus": "energy and vitality",
        "benefit": "stronger and more energized",
        "supportive_foods": ["protein-rich foods", "zinc-rich foods", "vitamin D sources", "healthy fat sources"],
        "supportive_movement": ["weight training", "high-intensity intervals", "sprint-based exercise", "power yoga"],
        "supportive_mindfulness": ["goal setting", "affirmations", "cold exposure", "achievement tracking"]
    },
    "insulin": {
        "name": "Insulin",
        "emoji": "",
        "personality": "your balance keeper",
        "phase_behavior": {
            "menstrual": "I can be less sensitive during your period, causing blood sugar fluctuations",
            "follicular": "I work more efficiently in your follicular phase, keeping energy stable",
            "ovulation": "I am balanced around ovulation, helping maintain steady energy",
            "luteal": "I become less sensitive in your luteal phase, causing cravings and energy crashes"
        },
        "focus": "blood sugar stability",
        "benefit": "steady and balanced",
        "supportive_foods": ["low glycemic foods", "protein with carbs", "fiber-rich foods", "cinnamon"],
        "supportive_movement": ["walking after meals", "resistance training", "steady-state cardio", "dancing"],
        "supportive_mindfulness": ["mindful eating", "stress management", "regular meal timing", "sleep optimization"]
    },
    "thyroid": {
        "name": "Thyroid",
        "emoji": "",
        "personality": "your metabolism friend",
        "phase_behavior": {
            "menstrual": "I can slow down during your period, affecting your energy and metabolism",
            "follicular": "I am more active in your follicular phase, boosting your metabolism",
            "ovulation": "I work efficiently around ovulation, keeping your energy high",
            "luteal": "I can slow down in your luteal phase, causing fatigue and sluggishness"
        },
        "focus": "metabolic support",
        "benefit": "energized and balanced",
        "supportive_foods": ["selenium foods", "iodine foods", "zinc foods", "anti-inflammatory foods"],
        "supportive_movement": ["moderate cardio", "strength training", "yoga", "swimming"],
        "supportive_mindfulness": ["stress reduction", "sleep quality", "cold exposure", "energy management"]
    }
}

# Default persona for unknown hormones
DEFAULT_PERSONA = {
    "name": "Hormone",
    "emoji": "",
    "personality": "your wellness guide",
    "phase_behavior": {
        "menstrual": "I can fluctuate during your period, affecting your overall wellness",
        "follicular": "I am adjusting in your follicular phase as your body prepares",
        "ovulation": "I am active around ovulation, supporting your bodys natural rhythm",
        "luteal": "I tend to shift in your luteal phase, which can affect how you feel"
    },
    "focus": "overall wellness",
    "benefit": "balanced and well",
    "supportive_foods": ["whole foods", "vegetables", "lean proteins", "healthy fats"],
    "supportive_movement": ["varied exercise", "walking", "yoga", "strength training"],
    "supportive_mindfulness": ["meditation", "breathing", "journaling", "self-care"]
}




# ============================================================================
# GPT PROMPT TEMPLATES
# ============================================================================

SYSTEM_PROMPT = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║  ⚠️  STOP! READ THIS USER'S PROFILE FIRST - BEFORE ANYTHING ELSE  ⚠️           ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  👤 USER NAME: {user_name}                                                    ║
║  🩺 DIAGNOSED CONDITIONS: {diagnosed_conditions_summary}                      ║
║  🎯 TOP CONCERN: {top_concern}                                                ║
║  📅 CYCLE: Day {cycle_day}, {cycle_phase} Phase                              ║
║  💊 TARGET HORMONES: {primary_hormone} (primary), {secondary_hormone} (secondary) ║
║                                                                               ║
║  ❌ ALLERGIES: {food_allergies}                                               ║
║  🥗 DIET: {diet_preference}                                                   ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝

YOU ARE CREATING A PLAN FOR {user_name} WHO HAS {diagnosed_conditions_summary}.

Every single recommendation you make MUST:
1. Be SPECIFIC to {diagnosed_conditions_summary} - not generic wellness
2. Name their condition in the 'purpose' field
3. Explain WHY this helps THEIR specific condition

═══════════════════════════════════════════════════════════════════════════════
YOUR ROLE
═══════════════════════════════════════════════════════════════════════════════
You are AUVRA, creating personalized daily actions for {user_name}.

This user has {diagnosed_conditions_summary}. 
Your recommendations must TARGET this condition specifically.

═══════════════════════════════════════════════════════════════════════════════
PERSONALIZATION ENFORCEMENT
═══════════════════════════════════════════════════════════════════════════════

The 'hormone_persona_intro' field MUST be MAX 2 SENTENCES (25-30 words):
"Hey {user_name}! I'm [Hormone] 💜 - I picked [this action] for your {diagnosed_conditions_summary} because it [one key mechanism]."

⚠️ CRITICAL: hormone_persona_intro must be SHORT - it appears as first paragraph on "Why?" page.
The 'purpose' field provides the detailed explanation separately.

The 'purpose' field MUST follow this pattern:
"For your {diagnosed_conditions_summary}: [food/action] contains [compound] which 
[mechanism]. Studies show this specifically helps women with [their condition] by [benefit]."

REJECTION CRITERIA - Your output will be REJECTED if:
- Any 'purpose' field doesn't mention their diagnosed condition by name
- Any 'hormone_persona_intro' doesn't address them by name or condition
- Recommendations are generic wellness that could apply to anyone

═══════════════════════════════════════════════════════════════════════════════
CONDITION-SPECIFIC RECOMMENDATIONS
═══════════════════════════════════════════════════════════════════════════════
WRONG: Pick salmon because "omega-3s are healthy"
RIGHT: Pick spearmint for PCOS because "it reduces androgens"

For {diagnosed_conditions_summary}, research what SPECIFICALLY helps:
- PCOS → insulin sensitizers (cinnamon, inositol), anti-androgens (spearmint, saw palmetto)
- Endometriosis → anti-inflammatory (turmeric, omega-3s), avoid inflammatory foods
- Thyroid → selenium (brazil nuts), iodine (seaweed), avoid goitrogens if needed
- High Cortisol → adaptogens (ashwagandha), stress reducers (magnesium)
- Estrogen Dominance → cruciferous vegetables (broccoli, cauliflower), fiber
- Low Progesterone → vitamin B6, zinc, vitex/chasteberry

===============================================================================
 CRITICAL - CATEGORY-SPECIFIC REQUIRED FIELDS (READ THIS FIRST!) 
===============================================================================
For EVERY action, you MUST include the category-specific fields based on the category.
FAILURE TO INCLUDE THESE FIELDS WILL CAUSE VALIDATION ERRORS.

 For "food" category, ALWAYS include:
   - food_items: [...] 
   - food_amounts: [...] 
   
    FOOD AMOUNTS RULES (CRITICAL):
   - These are DAILY action plans, so use IMMEDIATE, TODAY language
   - Use SMALL, UI-FRIENDLY portions ("1 cup", "1 scoop", "handful", "2 tbsp")
   -  NEVER say "2 servings per week" or "3x per week" - this is a daily plan!
   -  GOOD: "1 cup", "1 scoop", "handful", "4 oz", "2 pieces", "1 tbsp"
   -  BAD: "2 servings per week", "consume 3x weekly", "large portions"
   - Think: What fits in the users hand or on a small plate TODAY 

 For "movement" category, ALWAYS include:
   - exercise_types: [...] 
   - exercise_durations: [...] 
   - exercise_intensities: [...] 

 For "mindfulness" category, ALWAYS include:
   - mindfulness_techniques: [...]
   - mindfulness_durations: [...]

===============================================================================
 VARIETY IS MANDATORY - ANTI-REPETITION RULES
===============================================================================
- NEVER suggest the same foods/exercises across multiple days
- Draw from a WIDE variety of options for each category
- EVERY recommendation MUST directly address the users diagnosed conditions
- Research evidence-based interventions specific to THEIR conditions and hormones
- Generic "healthy eating" is NOT acceptable - be condition-specific
- Match movement intensity to users stated workout_intensity preference
- Consider their stress_level when suggesting mindfulness duration

HOW TO ACHIEVE TRUE PERSONALIZATION (RESEARCH-FIRST APPROACH):

IMPORTANT: We show users REAL research papers as proof. Your recommendations MUST be grounded in this research.

Follow this order:
1. SEARCH FIRST: Use the search_research_paper tool to find what studies say about [users condition] + [target hormone]
2. READ THE EVIDENCE: What interventions (foods, exercises, mindfulness) does the research show works?
3. RECOMMEND BASED ON EVIDENCE: Only recommend what has scientific backing
4. EXPLAIN THE MECHANISM: The 'purpose' field should cite WHY this works based on the research

Example thinking process:
- User has Cushings Syndrome + high cortisol
- Search: "cortisol reduction interventions women"
- Research shows: dark chocolate polyphenols reduce cortisol, yoga lowers HPA axis activation
- Recommend: dark chocolate, yoga (with research backing)
- Purpose: "Dark chocolates flavonoids have been shown in studies to reduce cortisol levels by inhibiting HPA axis activation"

QUALITY CHECK - Before finalizing each action, ask:
1. Did I base this on actual research findings, not just common knowledge?
2. Can I explain the specific mechanism with scientific terminology?
3. Would this recommendation hold up if a user googles it?

===============================================================================

IMPORTANT GUIDELINES:
1. Each action must target EXACTLY ONE hormone - the specified target hormone
2. Actions should be specific, actionable, and achievable in one day
3. Use the 'search_research_paper' tool to get REAL citations - NEVER fabricate citations
4. Time slots should be appropriate: morning (6-11am), afternoon (12-5pm), evening (6-10pm)
5. Image prompts should follow a consistent photography style for better semantic matching
6.  DAILY ACTION LANGUAGE: Use immediate, today-focused language ("add to your breakfast today", "try this evening"), NOT weekly frequencies ("consume 2x per week"). These are daily action plans!

CATEGORY DEFINITIONS:
- "food" (eat): Specific meals, recipes, or food recommendations
- "movement" (move): Exercise, stretching, physical activities
- "mindfulness" (pause): Meditation, breathing, relaxation, mental wellness

RESEARCH CITATION FORMAT (from search_research_paper tool):
{{
    "title": "Study title from PubMed/OpenAlex",
    "journal": "Journal name from tool result",
    "year": 2020,
    "participants": 156,
    "finding": "Key finding from paper abstract",
    "pmid": "12345678"
}}

IMAGE PROMPT STYLE (for consistent semantic matching):
All prompts should follow this pattern:
"[Subject/food/activity], centered composition, subject fills 60-70% of the frame (important for circular crops), natural lighting, clean minimalist background, warm inviting tones, wellness aesthetic, no text, no watermark, no logo"

HORMONE PERSONA INTRO STYLE:
The hormone speaks in first person, identifying itself and explaining whats happening in the users current cycle phase (1 sentence). 
CRITICAL: Do NOT explain how the action helps here. That goes in the 'purpose' field. Write naturally and warmly.

EXAMPLE INTROS (Persona part only):
- "I am Progesterone  in your luteal phase, I tend to dip, causing mood swings or cramps."
- "I am Estrogen  in your menstrual phase, I am at my lowest which can cause fatigue and low mood."
- "I am Insulin  in your luteal phase, I become less sensitive, causing cravings and energy crashes."
- "I am Cortisol  when stress is high, I spike and can disrupt your bodys natural rhythm."
"""

ACTION_GENERATION_PROMPT = """Generate {num_actions} personalized daily wellness actions for this user.

======================================================================
HEALTH PROFILE
======================================================================
- Age: {age}
- Cycle Day: {cycle_day}
- Cycle Phase: {cycle_phase}
- Primary Hormone to Support: {primary_hormone}
- Secondary Hormone to Support: {secondary_hormone}

HEALTH CONCERNS:
- Top Concern: {top_concern}
- Diagnosed Conditions: {diagnosed_conditions}
- Period Concerns: {period_concerns}
- Body Concerns: {body_concerns}
- Skin/Hair Concerns: {skin_hair_concerns}
- Mental Health Concerns: {mental_health_concerns}
- Family History: {family_history}

======================================================================
PERSONALIZATION FACTORS
======================================================================
- Lifestyle Focus: {lifestyle_focus}
- Diet Preference: {diet_preference}
- Food Allergies/Restrictions: {food_allergies}
- Cuisine Preference: {cuisine_preference}
- Cultural Background: {cultural_background}
- Dine Out Frequency: {dine_out_frequency}
- Body Metrics: {body_metrics}
- Common Cravings: {cravings}
- Stress Level: {stress_level}
- Sleep Duration: {sleep_duration}
- Workout Intensity: {workout_intensity}
- Birth Control: {birth_control}
- Current Streak: {current_streak} days
- Longest Streak: {longest_streak} days

======================================================================
HORMONE CONTEXT FOR {cycle_phase} PHASE
======================================================================
{hormone_phase_context}

======================================================================
⭐ UNIFIED CROSS-CHATBOT MEMORY (MOST IMPORTANT PERSONALIZATION DATA) ⭐
======================================================================
This is EVERYTHING we know about this user from ALL their interactions across
ALL chatbots. Use this to create truly personalized recommendations:

{unified_memory_context}

USE THIS DATA TO:
- Reference specific things the user said in past conversations
- Avoid items they've complained about or disliked
- Build on foods/exercises they've explicitly enjoyed
- Address symptoms they've recently reported
- Consider their learned preferences and past feedback
- Make each recommendation feel like it was made FOR THIS SPECIFIC USER

======================================================================
FEEDBACK MEMORY (Critical - avoid disliked patterns, repeat liked patterns)
======================================================================
HISTORICAL SUMMARY (learned patterns over time):
{feedback_summary}

RECENT FEEDBACK (last 20-50 actions):
{feedback_memory}

======================================================================
CHATBOT CONVERSATION CONTEXT
======================================================================
{chatbot_context}

======================================================================
WEEKLY CHECK-IN INSIGHTS (Recent symptom reports from user)
======================================================================
{weekly_checkin_insights}
Use these insights to:
- Target actions that address the users recent symptom triggers
- Avoid recommending things that made symptoms worse
- Build on what helped the user feel better

======================================================================
DAILY REVIEW INSIGHTS (Feedback from yesterdays plan)
======================================================================
{daily_review_insights}
Use these insights to:
- If user skipped items, understand why and suggest easier alternatives
- If user replaced items, learn from their substitutions
- If user completed items, reinforce those habits

======================================================================
CARE PLAN CHECK-IN INSIGHTS (Daily chat about todays plan)
======================================================================
{care_plan_checkin_insights}
Use these insights to:
- Respect explicit requests to change/skip/replace actions
- Make alternatives easier if user reports barriers (time, cravings, fatigue)
- Reinforce what the user said is working well

======================================================================
SYMPTOM CHECK-IN INSIGHTS (Daily symptom progress)
======================================================================
{symptom_checkin_insights}
Use these insights to:
- Reduce triggers and double-down on what helped today
- Keep actions realistic if user reports low energy / high symptoms
- Reinforce wins and remove friction from difficult items

======================================================================
🚨 CRITICAL: EVERY ACTION MUST BE COMPLETELY UNIQUE 🚨
======================================================================
Even if "Strength Training" is great for BOTH cortisol AND testosterone,
you can ONLY use it for ONE hormone. Pick something DIFFERENT for the other.

Duplicate detection runs AFTER you respond - if you generate duplicates,
the entire response will be REJECTED and you'll have to regenerate.

EXAMPLES OF WHAT WILL BE REJECTED:
❌ "Salmon" for cortisol + "Salmon" for progesterone (same food)
❌ "Yoga" for estrogen + "Morning Yoga" for testosterone (same activity)
❌ "Meditation" for cortisol + "Mindful Meditation" for progesterone (same practice)

======================================================================
REQUIREMENTS (READ CAREFULLY)
======================================================================
1. Generate exactly {num_actions} actions total
2. Actions targeting PRIMARY hormone ({primary_hormone}): {primary_count}
3. Actions targeting SECONDARY hormone ({secondary_hormone}): {secondary_count}
4. Category distribution based on lifestyle_focus: {category_guidance}
5. 🔴 EACH ACTION TITLE MUST BE COMPLETELY DIFFERENT - NO SIMILAR FOODS/EXERCISES
6. Time slots should be varied (mix of morning, afternoon, evening)
7. RESPECT food allergies - NEVER recommend foods the user is allergic to
8. RESPECT diet preferences - if vegetarian, no meat; if vegan, no animal products
9. RESPECT cuisine preferences - prioritize foods from preferred cuisines when possible
10. RESPECT cultural background - include culturally appropriate and familiar foods/practices
11. ADAPT to body metrics - if BMI indicates overweight, focus on portion control and lighter meals
12. ADDRESS cravings - include healthy alternatives that satisfy users common cravings
13. CONSIDER dining habits - if user dines out often, suggest restaurant-friendly options
14. CONDITION-SPECIFIC PERSONALIZATION (CRITICAL):
   - Analyze the users EXACT diagnosed conditions listed above
   - Research evidence-based interventions for THEIR specific conditions
   - Each food/exercise MUST have a clear mechanism for helping THEIR hormone + condition combo
   - The 'purpose' field must explain HOW this specific action helps THIS users condition
   - NO generic recommendations - every action should feel designed for THIS user
15. Learn from FEEDBACK MEMORY above:
    - If user LIKED something: create similar types
    - If user DISLIKED something: NEVER suggest similar patterns
16. Match intensity to users stated workout_intensity level
17. Recommend longer mindfulness for high stress users, shorter for low stress

======================================================================
⚠️ ANTI-REPETITION & PERSONALIZATION RULES (CRITICAL) ⚠️
======================================================================
1. ⛔ CONDITION-FIRST APPROACH: Your recommendations MUST be driven by the user's SPECIFIC diagnosed conditions above.
   - If user has PCOS: Research and recommend foods that improve insulin sensitivity, reduce androgens
   - If user has endometriosis: Research anti-inflammatory foods, avoid inflammatory triggers
   - If user has thyroid issues: Research iodine-rich or goitrogenic foods as appropriate
   - Each recommendation should have a CLEAR biochemical reason for THIS user
   
2. ⛔ NEVER RECOMMEND RECENTLY BANNED ITEMS (see list below in ABSOLUTE BAN section)
   The system tracks what this user received before - check and avoid repeating

3. ⛔ NO GENERIC WELLNESS FOODS: Do NOT default to "safe" wellness foods that work for everyone.
   Examples of GENERIC defaults to AVOID:
   - Fatty fish (too common - be specific: mackerel, sardines, herring are different)
   - "Seeds" (too vague - pick ONE specific seed with a reason)
   - "Leafy greens" (too vague - pick ONE specific green with a mechanism)
   
4. ✅ VARIETY THROUGH SPECIFICITY: Instead of generic categories, recommend:
   - SPECIFIC foods from the user's cuisine_preference
   - SPECIFIC exercises matching workout_intensity
   - SPECIFIC mindfulness techniques for their stress_level
   
5. ✅ RESEARCH-DRIVEN: Use the PubMed research findings above to find LESS COMMON interventions
   that are equally or more effective for this user's specific condition.

6. STRICT SYMPTOM WHITELIST: In the 'symptoms' output array, you may ONLY use symptoms from this exact list:
   {allowed_symptoms}
   If a symptom is not in this list, DO NOT include it.

7. STRICT CONDITION WHITELIST: In the 'conditions' output array, you may ONLY use conditions from this exact list:
   {allowed_conditions}
   If no conditions are listed, this array MUST be empty [].

======================================================================
⚠️ RECENTLY RECOMMENDED (ABSOLUTE BAN - DO NOT REPEAT) ⚠️
======================================================================
{recently_recommended}
You MUST choose DIFFERENT items. If you repeat anything from this list, the generation will fail.

======================================================================
⭐ CORE PRINCIPLE: TITLE vs SPECIFIC_ACTION
======================================================================
+---------------------------------------------------------------------+
|  TITLE = WHAT it is (the thing itself - noun)                       |
|  SPECIFIC_ACTION = HOW to use it (3 different methods - verbs)      |
+---------------------------------------------------------------------+

FOOD:
   Title: Raw ingredient name ONLY (no preparation method)
   specific_action: 3 consumption methods - grilled, baked, in smoothie, etc.

MOVEMENT:
   Title: Activity type name ONLY
   specific_action: 3 ways to do it - gentle flow, hip openers, sun salutations, etc.

MINDFULNESS:
   Title: Specific technique name (NOT just "meditation")
   specific_action: 3 practice methods - 4-7-8 technique, box breathing, belly breathing, etc.

======================================================================
OUTPUT FORMAT (for each action)
======================================================================
1. title: SIMPLE, CLEAN NAME ONLY (see TITLE RULES below)
2. category: "food", "movement", or "mindfulness"
3. time_slot: "morning", "afternoon", or "evening"
4. specific_action: MUST include 3 DIFFERENT WAYS to consume/do this action! (80-120 words)
   FORMAT: Start with scientific benefit for THIS user's condition, then list 3 methods:
   "[Food/Exercise] provides [specific benefit for user's hormone/condition]. Try it as: (1) [method 1 with details], (2) [method 2 with details], or (3) [method 3 with details]."
   
   STRUCTURE:
   - Sentence 1: Why this helps THIS user's specific hormone/condition
   - Sentence 2-3: Three numbered methods with specific instructions
   
   ❌ BAD (no consumption methods): "This food helps reduce stress. Consume it daily."
   
5. purpose: CRITICAL - The purpose field is WHERE YOU PROVE PERSONALIZATION.
   
   MANDATORY STRUCTURE (follow exactly):
   a) START by naming their EXACT condition: "With your [diagnosed_condition from profile]..."
   b) EXPLAIN the mechanism in simple terms: "...this helps because [biochemical reason]"
   c) CONNECT to their symptoms: "...which addresses your [symptom from their profile]"
   d) CITE the evidence briefly: "Research shows [finding] for women with [their condition]"
   
   ✅ GOOD PURPOSE EXAMPLES:
   "With your PCOS, insulin sensitivity is a key challenge. Cinnamon contains cinnamaldehyde which mimics insulin action—studies show it improves glucose uptake by up to 20% in women with PCOS, directly helping those afternoon energy crashes."
   
   "Since you have endometriosis and inflammation is a major driver of your symptoms, turmeric's curcumin acts as a natural COX-2 inhibitor. A 2019 study found it reduced pelvic pain by 45% in women with endo."
   
   ❌ BAD PURPOSE EXAMPLES (rejected - too generic):
   "This supports hormonal balance." → WHERE IS THEIR CONDITION?
   "Good for women's health." → WHICH WOMAN? WHAT CONDITION?
   "Helps reduce inflammation." → HOW? FOR WHAT CONDITION?
6. target_hormone: CRITICAL - You MUST set this exactly as follows:
   - Action 1 and 2: MUST be "{primary_hormone}" (the PRIMARY hormone)
   - Action 3 and 4: MUST be "{secondary_hormone}" (the SECONDARY hormone)
   DO NOT deviate from this. The mascot image shown depends on this field matching correctly.
7. hormone_persona_intro: MAX 2 SENTENCES (25-30 words)! Keep it SHORT.
   
   MANDATORY PATTERN (SHORT!):
   "Hey [name]! I'm [Hormone] 💜 - I picked [action] for your [condition] because it [one key mechanism]."
   
   ✅ GOOD EXAMPLES (short!):
   "Hey Sarah! I'm Progesterone 💜 - I picked spearmint for your PCOS because it reduces androgens."
   "Hey Maya! I'm Cortisol 💜 - I chose yoga for your high stress because it calms my activity."
   
   ❌ BAD EXAMPLES:
   - TOO LONG: "I know your PCOS can make things challenging, especially during your luteal phase..." (3+ sentences)
   - TOO GENERIC: "I am Progesterone - this food is healthy." (no condition mentioned)
   
8. image_prompt: FLUX.1 Schnell optimized prompt (see IMAGE PROMPT REQUIREMENTS below)
9. research_studies: Array with 2-4 REAL research citations focused on WOMEN/FEMALES.
   PRIORITY ORDER (include highest quality sources first):
   - Meta-analysis (combines multiple studies - HIGHEST quality)
   - Systematic review (rigorous literature review)
   - Randomized controlled trial (RCT - gold standard experiment)
   - Clinical trial (experimental study)
   - Review paper (narrative review)
   
   EACH CITATION MUST HAVE:
   {{
     "title": "Full paper title",
     "journal": "Journal name",
     "year": 2023,
     "participants": 150,
     "finding": "Key finding for women",
     "pmid": "12345678",
     "verification_link": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
     "study_type": "meta_analysis" | "systematic_review" | "rct" | "clinical_trial" | "review",
     "study_type_label": "Meta-Analysis" | "Systematic Review" | "Randomized Controlled Trial" | etc.
   }}
   
   Use the research papers provided in the RESEARCH FINDINGS section above!
10. variants: Array of 3 variants showing DIFFERENT WAYS to consume/do this action. CRITICAL: Do NOT include 'specific_action' in variants. Only: variant_type, title, description, image_prompt.
11. symptoms: Array of strings - specific user symptoms this action addresses (e.g., taken from user's logged symptoms)
12. conditions: Array of strings - specific conditions this action is beneficial for (taken from user's diagnosed_conditions)

======================================================================
⭐ TITLE RULES (CRITICAL - INGREDIENT/ACTIVITY NAME ONLY!)
======================================================================
⚠️ IMPORTANT: Generate UNIQUE recommendations based on the user's conditions. 
Do NOT default to generic wellness foods - be specific to THIS user's health situation.

Titles MUST be the RAW INGREDIENT or ACTIVITY NAME ONLY.
❌ NO preparation methods (latte, tea, smoothie, porridge, etc.)
❌ NO adjectives (powerful, amazing, gentle, etc.)
❌ NO brand names

FORMAT RULES:
✅ FOOD: Single ingredient name (e.g., the raw food item)
✅ MOVEMENT: Simple activity name (e.g., type of exercise + optional time)
✅ MINDFULNESS: Specific technique name (NOT just "Meditation")

❌ BAD FOOD TITLES (includes preparation method):
- "[Food] Tea" → should be just "[Food]"
- "[Food] Smoothie" → should be just "[Food]"

❌ BAD MOVEMENT TITLES (too descriptive):
- "Gentle Morning Yoga Flow" → should be "Morning Yoga"
- "Relaxing Evening Stretch" → should be "Evening Stretching"

❌ BAD MINDFULNESS TITLES (too generic):
- Just "Meditation" → should be specific like "Body Scan" or "Loving Kindness"

 BAD MINDFULNESS TITLES (too wordy):
- "Evening Calm Breathing Practice"  should be "Deep Breathing"
- "Peaceful Meditation Session"  should be "Meditation"

CATEGORY-SPECIFIC REQUIRED FIELDS:
**ALL category fields are REQUIRED in every action.** Fill with actual values for matching category, use empty array [] for non-matching categories.

For FOOD actions:
- food_items: Array like ["salmon", "quinoa"] (REQUIRED)
- food_amounts: Array like ["4 oz", "1 cup cooked"] (REQUIRED)
   CRITICAL: Use SMALL, UI-FRIENDLY portions for TODAY ("1 scoop", "handful", "1 cup", "2 tbsp")
   NEVER use weekly frequencies ("2 servings per week") - this is a DAILY action plan!
   Think: What the user can consume in ONE sitting or ONE day
- exercise_types: [] (empty array - not a food action)
- exercise_durations: [] (empty array)
- exercise_intensities: [] (empty array)
- mindfulness_techniques: [] (empty array)
- mindfulness_durations: [] (empty array)

For MOVEMENT actions:
- exercise_types: Array like ["yoga", "walking"] (REQUIRED)
- exercise_durations: Array like ["15 min", "20 minutes"] (REQUIRED)
- exercise_intensities: Array like ["low", "moderate"] (REQUIRED)
- food_items: [] (empty array - not a movement action)
- food_amounts: [] (empty array)
- mindfulness_techniques: [] (empty array)
- mindfulness_durations: [] (empty array)

For MINDFULNESS actions:
- mindfulness_techniques: Array like ["deep breathing", "meditation"] (REQUIRED)
- mindfulness_durations: Array like ["5 min", "10 minutes"] (REQUIRED)
- food_items: [] (empty array - not a mindfulness action)
- food_amounts: [] (empty array)
- exercise_types: [] (empty array)
- exercise_durations: [] (empty array)
- exercise_intensities: [] (empty array)

======================================================================
JSON STRUCTURE TEMPLATE (NO COPYING - GENERATE UNIQUE)
======================================================================

⚠️ CRITICAL: The templates below show STRUCTURE ONLY. All placeholders marked 
with {{GENERATE}} MUST be replaced with YOUR OWN unique recommendations based on:
1. The user's SPECIFIC diagnosed conditions
2. The user's EXACT hormone imbalances
3. Research findings from PubMed for THEIR conditions
4. Foods/exercises they have NOT received recently

DO NOT use any food, exercise, or technique mentioned in these templates!
These are FORMAT examples only - generate completely different items.

FOOD ACTION TEMPLATE (structure only - generate unique food):
{{
  "title": "{{GENERATE: Raw ingredient name based on users conditions}}",
  "category": "food",
  "time_slot": "{{GENERATE: morning/afternoon/evening}}",
  "specific_action": "{{GENERATE: Explain why THIS food helps THIS users hormone. Then: Try it today as: (1) [method 1], (2) [method 2], or (3) [method 3].}}",
  "purpose": "{{GENERATE: Scientific mechanism - how this food affects the users SPECIFIC hormone and condition}}",
  "target_hormone": "{{FROM REQUIREMENTS: primary or secondary hormone}}",
  "hormone_persona_intro": "{{GENERATE: Personalized greeting from the hormone persona}}",
  "image_prompt": "{{GENERATE: FLUX.1 prompt showing THIS specific food clearly}}",
  "food_items": ["{{GENERATE: forms of the food}}"],
  "food_amounts": ["{{GENERATE: daily portions}}"],
  "research_studies": [{{GENERATE: Real study from PubMed}}],
  "variants": [
    {{"variant_type": "tasty", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}},
    {{"variant_type": "easy", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}},
    {{"variant_type": "healthy", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}}
  ],
  "symptoms": ["{{FROM USER: symptoms this addresses}}"],
  "conditions": ["{{FROM USER: conditions this helps}}"]
}}

MOVEMENT ACTION TEMPLATE (structure only - generate unique exercise):
{{
  "title": "{{GENERATE: Activity name based on users workout_intensity and conditions}}",
  "category": "movement",
  "time_slot": "{{GENERATE: morning/afternoon/evening}}",
  "specific_action": "{{GENERATE: Explain benefit. Then: Try it today as: (1) [variation 1], (2) [variation 2], or (3) [variation 3].}}",
  "purpose": "{{GENERATE: How this movement helps THIS users hormone balance}}",
  "target_hormone": "{{FROM REQUIREMENTS}}",
  "hormone_persona_intro": "{{GENERATE}}",
  "image_prompt": "{{GENERATE: FLUX.1 prompt showing woman doing THIS exercise}}",
  "exercise_types": ["{{GENERATE}}"],
  "exercise_durations": ["{{GENERATE}}"],
  "exercise_intensities": ["{{GENERATE: match users workout_intensity}}"],
  "research_studies": [{{GENERATE: Real study}}],
  "variants": [
    {{"variant_type": "gentle", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}},
    {{"variant_type": "energizing", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}},
    {{"variant_type": "quick", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}}
  ],
  "symptoms": ["{{FROM USER}}"],
  "conditions": ["{{FROM USER}}"]
}}

MINDFULNESS ACTION TEMPLATE (structure only - generate unique technique):
{{
  "title": "{{GENERATE: Specific technique name - NOT generic meditation}}",
  "category": "mindfulness",
  "time_slot": "{{GENERATE}}",
  "specific_action": "{{GENERATE: Explain how technique helps. Then: Try it as: (1) [method 1], (2) [method 2], or (3) [method 3].}}",
  "purpose": "{{GENERATE: Mechanism for stress/hormone regulation}}",
  "target_hormone": "{{FROM REQUIREMENTS}}",
  "hormone_persona_intro": "{{GENERATE}}",
  "image_prompt": "{{GENERATE: FLUX.1 prompt showing woman practicing THIS technique}}",
  "mindfulness_techniques": ["{{GENERATE}}"],
  "mindfulness_durations": ["{{GENERATE: adjust for users stress_level}}"],
  "research_studies": [{{GENERATE: Real study}}],
  "variants": [
    {{"variant_type": "guided", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}},
    {{"variant_type": "solo", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}},
    {{"variant_type": "brief", "title": "{{GENERATE}}", "description": "{{GENERATE}}", "image_prompt": "{{GENERATE}}"}}
  ],
  "symptoms": ["{{FROM USER}}"],
  "conditions": ["{{FROM USER}}"]
}}

CRITICAL: Every action MUST include ALL of its category-specific fields (food_items/food_amounts for food, exercise_types/exercise_durations/exercise_intensities for movement, mindfulness_techniques/mindfulness_durations for mindfulness).

IMAGE PROMPT REQUIREMENTS (for FLUX.1 Schnell):

 GOAL: User should INSTANTLY UNDERSTAND what the action is just by seeing the image!


The image MUST be SELF-EXPLANATORY and ILLUSTRATIVE. Think of it like a visual instruction:
- For FOOD: Show the EXACT food item as the HERO of the image, clearly visible and recognizable
- For MOVEMENT: Show a WOMAN doing the EXACT pose/exercise so user knows what to do
- For MINDFULNESS: Show the TECHNIQUE being practiced (hands position, posture, setup)

 COMPOSITION RULES:
1. Make the subject (food/pose/technique) the CLEAR FOCAL POINT - fill 60-70% of the frame
2. Use CLOSE-UP or MEDIUM shots - don't zoom out too much
3. Show TEXTURE and DETAIL of the food item so its instantly recognizable
4. For exercises, show the FULL POSE clearly from a good angle
5. Keep backgrounds simple but contextual (kitchen for food, living room for yoga)
6. IMPORTANT FOR APP UI: Center the subject (these images are often displayed in small circular crops)
7. STRICT: No text, no captions, no watermarks, no logos/branding

=======================================================================
FOR FOOD - Make the FOOD ITEM the HERO (clearly visible, close-up):
=======================================================================
Template: "Professional close-up food photography of [EXACT FOOD ITEM in detail], [texture/color description], [simple serving context], natural lighting, shallow depth of field, the [food item] is clearly the main subject filling most of the frame, 4K quality"

STRUCTURE (apply to YOUR chosen food):
- Describe the food's distinctive visual features (color, texture, shape)
- Show it in a simple, elegant presentation (small bowl, cutting board, etc.)
- Use natural lighting that highlights the food's qualities
- Fill 60-70% of frame with the food
- Make the food instantly recognizable

❌ BAD EXAMPLES (food not clear, too generic, or too zoomed out):
- "Professional food photography of healthy food" (What food?!)
- "Bowl of food on a table" (Can't tell whats in it!)
- "Overhead shot of breakfast spread" (Too much, can't focus on any item)

=======================================================================
FOR MOVEMENT - Show a WOMAN DOING the exact exercise/pose:
=======================================================================
Template: "Serene photograph of woman [EXACT POSE/MOVEMENT DESCRIPTION], [setting], soft natural lighting, wellness aesthetic, warm earth tones, 4K quality"

STRUCTURE (apply to YOUR chosen exercise):
- Show the EXACT pose/position clearly from a good viewing angle
- Woman should be wearing appropriate athletic wear
- Background should match the activity (home for yoga, outdoors for walking, etc.)
- The pose/movement should be immediately recognizable

❌ BAD EXAMPLES (too generic):
- "Woman exercising" (What exercise?!)
- "Yoga pose" (Which one?!)

=======================================================================
FOR MINDFULNESS - Show the TECHNIQUE/SETUP clearly:
=======================================================================
Template: "Peaceful photograph of [EXACT MINDFULNESS SETUP/TECHNIQUE visualization], [calming elements], soft diffused lighting, minimalist aesthetic, calming colors, 4K quality"

STRUCTURE (apply to YOUR chosen technique):
- Show the specific hand position, posture, or setup for THIS technique
- Include calming elements appropriate to the time of day
- Peaceful facial expression if face is visible
- The technique should be clearly identifiable

❌ BAD EXAMPLES (too generic):
- "Peaceful zen scene" (What technique?!)
- "Meditation" (Show what KIND!)

VARIANT FORMAT (REQUIRED structure):
Each variant MUST be an object with these exact fields:
- variant_type: MUST be one of the allowed types (see below)
- title: Specific name of this variant (e.g., "Teriyaki Glazed Salmon", "Grilled Salmon with Lemon")
- description: How to prepare or do this variant (1-2 sentences)
- image_prompt: FLUX.1 Schnell optimized prompt for this specific variant

VARIANT TYPES by category (use exact string values):
- food: "tasty" (indulgent version), "easy" (quick/simple), "healthy" (most nutritious)
- movement: "gentle" (low intensity), "energizing" (higher intensity), "quick" (time-efficient)
- mindfulness: "guided" (with instruction), "solo" (self-directed), "brief" (5-min version)

RESEARCH STUDIES - CRITICAL REQUIREMENTS:
- Use 'search_research_paper' tool to find papers that EXPLAIN WHY this action helps the target hormone
- The paper must support the recommendation (e.g., "cinnamon reduces blood sugar" not just "cinnamon is used in cooking")
- Search query should include: [specific food/exercise] + [mechanism/benefit] + [hormone] + women
- Include the PMID for verification - users can click "See details in PubMed"
- research_studies format: [{{"title": "...", "journal": "...", "year": 2023, "participants": 150, "finding": "...", "pmid": "12345678", "verification_link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"}}]
- participants should be the NUMBER of women in the study (integer, e.g., 150)
- finding should explain the BENEFIT/RESULT discovered (e.g., "Cinnamon supplementation significantly reduced fasting blood glucose in women with PCOS")
- If the tool returns no results, set research_studies to an empty array []

Respond with valid JSON array only, no markdown formatting."""

# ============================================================================
# JSON SCHEMA FOR STRUCTURED OUTPUTS
# ============================================================================

# Generate JSON Schema automatically from Pydantic model
# This ensures schema and model are always in sync
_raw_schema = ActionPlanResponseModel.model_json_schema()

# Fix for OpenAI strict mode: 
# 1. ALL fields MUST be in 'required' (Pydantic excludes default_factory fields)
# 2. 'additionalProperties' MUST be false at every object level
def _fix_required_fields(schema: dict) -> dict:
    """Recursively fix schema for OpenAI strict mode compliance."""
    if isinstance(schema, dict):
        if "properties" in schema:
            # Add ALL property keys to required
            schema["required"] = list(schema["properties"].keys())
            # OpenAI strict mode requires additionalProperties: false
            schema["additionalProperties"] = False
            # Recurse into nested properties
            for prop_value in schema["properties"].values():
                _fix_required_fields(prop_value)
        # Handle $defs (nested type definitions)
        if "$defs" in schema:
            for def_value in schema["$defs"].values():
                _fix_required_fields(def_value)
        # Handle items (for arrays)
        if "items" in schema:
            _fix_required_fields(schema["items"])
    return schema

ACTION_PLAN_SCHEMA = _fix_required_fields(_raw_schema)

VARIANT_PROMPT_TEMPLATE = """For the {category} action "{title}", create 3 variants:

Original action: {specific_action}

Create variants based on these types:
{variant_types}

For each variant provide:
1. variant_type: The type name
2. title: Short variant title
3. description: How this variant differs (1-2 sentences)
4. image_prompt: Specific image prompt following the photography style

Respond with valid JSON array only."""


# ============================================================================
# ACTION PLAN GENERATOR SERVICE
# ============================================================================

class ActionPlanGenerator:
    """
    Main orchestrator for generating personalized daily action plans.
    
    Flow:
    1. Check if todays plan exists
    2. Get user context (hormones, cycle, preferences, feedback)
    3. Generate 4 actions via GPT-5-mini
    4. Generate images for each action (16 total)
    5. Store plan in database
    """
    
    GPT_MODEL = "gpt-5-mini"
    GPT_TEMPERATURE = 0.7
    MAX_RETRIES = 3
    
    # Models that don't support temperature parameter (reasoning models)
    NO_TEMPERATURE_MODELS = ["o1", "o1-mini", "o1-preview", "o3-mini", "gpt-5-mini"]
    
    @classmethod
    def model_supports_temperature(cls, model_name: str) -> bool:
        """Check if a model supports the temperature parameter."""
        model_lower = model_name.lower()
        for no_temp_model in cls.NO_TEMPERATURE_MODELS:
            if no_temp_model.lower() in model_lower:
                return False
        return True
    
    def __init__(self):
        """Initialize the generator."""
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.client = httpx.AsyncClient(timeout=120.0)
        self.image_service = get_image_library_service()
        
        # Shared database engine for pooled sessions
        # Use NullPool for Supabase Session Pooler compatibility - 
        # Supabase handles all pooling externally, so local pooling causes MaxClientsInSessionMode errors
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        
        db_url = os.getenv("DATABASE_URL", "")
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
        self.engine = create_async_engine(
            db_url, 
            echo=False,
            poolclass=NullPool  # No local pooling - Supabase Session Pooler handles it
        )
        self.async_session_maker = sessionmaker(
            self.engine, 
            class_=AsyncSession, 
            expire_on_commit=False
        )
        
        # Semaphore to limit concurrent DB writes/ops to 32 at a time (allows all images in parallel)
        self.db_semaphore = asyncio.Semaphore(32)
        
        logger.info(f"ActionPlanGenerator initialized with shared engine")
        logger.info(f"  OpenAI configured: {bool(self.openai_api_key)}")
    
    def build_openai_payload(
        self,
        model: str,
        messages: list,
        max_tokens: int = 4000,
        response_format: dict = None,
        temperature: float = None,
        tools: list = None
    ) -> dict:
        """
        Build OpenAI API payload, conditionally including temperature.
        Some models (o1, o3-mini, gpt-5-mini) don't support temperature.
        """
        payload = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        
        # Only add temperature if model supports it
        if temperature is not None and self.model_supports_temperature(model):
            payload["temperature"] = temperature
        
        if response_format:
            payload["response_format"] = response_format
            
        if tools:
            payload["tools"] = tools
            
        return payload

    async def get_or_generate_today_plan(
        self,
        user_id: str,
        user_timezone: str,
        db: AsyncSession,
        image_mode: Literal["full", "hero_only", "none"] = "full",
        skip_quality_check: bool = False,
        carryforward_items: Optional[List[Dict[str, Any]]] = None,  # Items passed from daily review carry-forward
    ) -> Dict[str, Any]:
        """
        Get todays action plan or generate a new one.
        
        This is the main entry point called on app open.
        
        NEW: If yesterday was frozen and had incomplete items, those items
        carry forward to todays plan instead of generating new ones.
        
        Args:
            skip_quality_check: If True, skip model quality evaluation (faster for first-time users)
            carryforward_items: Optional list of items from daily review to carry forward
                                (source_item, source_variants, original_id)
        """
        from app.core.database import ActionPlan
        from datetime import timedelta
        
        # Get todays date in users timezone
        today = self._get_user_today(user_timezone)
        
        # Check if plan exists for today - ONE plan per day
        existing_plan = await self._get_existing_plan(user_id, today, db)
        
        # If plan already exists for today, return it (never replace)
        if existing_plan:
            logger.info(f"Found existing plan for user {user_id} on {today}")
            
            # Check if images are missing and generate them BEFORE returning response
            # This ensures frontend always receives valid image URLs
            has_missing_images = False
            if image_mode != "none":
                has_missing_images = await self._check_missing_images(existing_plan, db)
                
                if has_missing_images:
                    # BLOCKING IMAGE GENERATION - wait for images before returning response
                    # This fixes the blank/fallback image issue on frontend
                    logger.info(f" [IMAGE-WAIT] Generating missing images for existing plan {existing_plan.id} (blocking)")
                    try:
                        await self._ensure_plan_has_images(existing_plan, user_id, db, image_mode)
                        logger.info(f" [IMAGE-WAIT] Completed image generation for plan {existing_plan.id}")
                    except Exception as e:
                        logger.error(f" [IMAGE-WAIT] Failed to generate images: {e}")
                        # Continue anyway - plan will load with fallback icons
            
            resp = await self._format_plan_response(existing_plan, db)
            if isinstance(resp, dict) and resp.get("success"):
                resp["plan_source"] = "existing_today"
                # Images are now generated synchronously, no need for frontend polling
                # resp["images_generating"] = has_missing_images
            return resp
        
        # No plan for today - check if we should carryforward from frozen day
        # This only runs when generating a NEW plan for today
        carryforward_result = await self._check_and_carryforward_frozen_plan(
            user_id, today, user_timezone, db
        )
        
        if carryforward_result and carryforward_result.get("success"):
            logger.info(f"Carried forward incomplete items from frozen day for {user_id}")
            carryforward_result["plan_source"] = "carryforward"
            return carryforward_result
        
        # NEW: Check for items skipped via care plan check-in yesterday
        # These should be included in today's plan
        skipped_items = await self._get_chat_skipped_items_from_yesterday(user_id, today, db)
        
        # Combine all carry-forward sources:
        # 1. Items passed from daily review carry-forward (carryforward_items parameter)
        # 2. Items skipped via care plan check-in yesterday (skipped_items)
        all_carryforward = []
        
        # Add items from daily review (already in correct format)
        if carryforward_items:
            all_carryforward.extend(carryforward_items)
            logger.info(f"📦 Including {len(carryforward_items)} daily-review carry-forward items")
        
        # Add items from chat skips (already in correct format)
        if skipped_items:
            all_carryforward.extend(skipped_items)
            logger.info(f"📦 Including {len(skipped_items)} chat-skipped items from yesterday")
        
        # Limit total carry-forward to 4 items
        all_carryforward = all_carryforward[:4]
        
        # Generate new plan, passing all carry-forward items
        logger.info(f"Generating new plan for user {user_id} on {today}")
        if all_carryforward:
            logger.info(f"📦 Total carry-forward items: {len(all_carryforward)} (will generate {4 - len(all_carryforward)} new actions)")
        gen_result = await self.generate_new_plan(
            user_id=user_id,
            plan_date=today,
            user_timezone=user_timezone,
            db=db,
            image_mode=image_mode,
            skip_quality_check=skip_quality_check,
            carryforward_items=all_carryforward,  # Pass combined carry-forward items
        )
        if isinstance(gen_result, dict) and gen_result.get("success"):
            gen_result.setdefault("plan_source", "generated_new")
            if all_carryforward:
                gen_result["plan_source"] = "generated_with_carryforward"
        return gen_result
    
    async def generate_new_plan(
        self,
        user_id: Optional[str],
        plan_date: date,
        user_timezone: str,
        db: AsyncSession,
        image_mode: Literal["full", "hero_only", "none"] = "full",
        skip_quality_check: bool = False,
        session_id: Optional[str] = None,  # For guest users
        carryforward_items: Optional[List[Dict[str, Any]]] = None  # Items to carry forward from yesterday
    ) -> Dict[str, Any]:
        """
        Generate a completely new action plan.
        
        Uses PostgreSQL advisory lock to prevent race conditions.
        
        Steps:
        1. Acquire advisory lock for user+date (or session+date)
        2. Check for existing plan (double-check after lock)
        3. Load user context
        4. If carryforward_items provided, include them in plan (reduces new generation slots)
        """
        start_time = time.time()
        
        # Determine identifier
        if user_id:
            identifier = user_id
            log_prefix = f"[ACTION_PLAN:{user_id[:8]}]"
        elif session_id:
            identifier = f"session:{session_id}"
            log_prefix = f"[ACTION_PLAN:GUEST:{session_id[:8]}]"
        else:
            return {"success": False, "error": "Either user_id or session_id is required"}

        logger.info(f"{log_prefix}  Starting generation for {plan_date}")

        logger.info(f"{log_prefix} ==========================================================================")
        logger.info(f"{log_prefix}  STARTING NEW PLAN GENERATION for user: {user_id or session_id}, date: {plan_date}")
        logger.info(f"{log_prefix} Timezone: {user_timezone}")
        logger.info(f"{log_prefix} ==========================================================================")
        
        start_time = time.time()
        total_cost = 0.0
        
        # Lock key based on user_id or session_id
        identity_key = user_id if user_id else f"session:{session_id}"
        lock_key = hash(f"{identity_key}:{plan_date}") % 2147483647  # int32 range for PostgreSQL
        got_lock = False
        
        try:
            # Step 0: Acquire advisory lock to prevent race conditions
            # Two requests for the same user+date will serialize here
            logger.info(f"{log_prefix} Step 0: Acquiring advisory lock (key: {lock_key})")
            lock_result = await db.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": lock_key}
            )
            got_lock = lock_result.scalar()
            
            if not got_lock:
                # Another request is already generating - wait and poll for result
                # OPTIMIZED: Reduced wait times since we're faster now (~15s total instead of 45s)
                logger.info(f"{log_prefix}  Another request is generating plan, polling for result...")
                
                # Poll for existing plan with shorter backoff (2s, 4s, 9s = ~15s total)
                wait_times = [2, 4, 9]
                for wait_time in wait_times:
                    await asyncio.sleep(wait_time)
                    
                    # Check if plan was created by the other request
                    from app.core.database import ActionPlan
                    query = select(ActionPlan).where(ActionPlan.plan_date == plan_date)
                    if user_id:
                        query = query.where(ActionPlan.uid == user_id)
                    elif session_id:
                        query = query.where(ActionPlan.session_id == session_id)
                    
                    result = await db.execute(query)
                    existing_plan = result.scalar_one_or_none()

                    if existing_plan:
                        logger.info(f"{log_prefix}  Found plan created by concurrent request after {wait_time}s wait")
                        resp = await self._format_plan_response(existing_plan, db)
                        if isinstance(resp, dict) and resp.get("success"):
                            resp["plan_source"] = "concurrent_wait_existing"
                        return resp
                    
                    logger.info(f"{log_prefix}  Still waiting for plan... (total wait: {sum(wait_times[:wait_times.index(wait_time)+1])}s)")
                
                # After ~45s of waiting, try to acquire blocking lock
                logger.info(f"{log_prefix}  Timed out waiting for concurrent request, acquiring blocking lock...")
                await db.execute(
                    text("SELECT pg_advisory_lock(:key)"),
                    {"key": lock_key}
                )
                got_lock = True
            
            # Double-check for existing plan after acquiring lock
            existing_plan = await self._get_existing_plan(user_id, plan_date, db, session_id=session_id)
            if existing_plan:
                logger.info(f"[GENERATE] Plan already exists for {user_id} on {plan_date}")
                resp = await self._format_plan_response(existing_plan, db)
                if isinstance(resp, dict) and resp.get("success"):
                    resp["plan_source"] = "existing_after_lock"
                return resp
            
            logger.info(f"[GENERATE]  Lock acquired, proceeding with plan generation")
            
            # Determine how many new actions to generate based on carryforward items
            num_carryforward = len(carryforward_items) if carryforward_items else 0
            num_to_generate = max(0, 4 - num_carryforward)  # Standard plan is 4 items
            
            # OPTIMIZATION: For pure carryforward (all 4 items), skip heavy GPT context loading
            # But we still need to load MINIMAL context for storing the plan
            if num_to_generate == 0 and num_carryforward == 4:
                logger.info(f"[GENERATE] ⚡ FAST PATH: All 4 items from carryforward - loading minimal context only")
                # Load MINIMAL context required for _store_plan (primary_hormone, cycle info, etc.)
                user_context = await self._load_minimal_context_for_carryforward(user_id, db, user_timezone)
                if not user_context:
                    logger.error(f"[GENERATE]  Could not load minimal context for {user_id}")
                    return {"success": False, "error": "User profile not found"}
            else:
                # Step 1: Load user context (needed for GPT generation)
                logger.info(f"[GENERATE] Step 1: Loading user context...")
                user_context = await self._load_user_context(user_id, db, session_id=session_id)
                
                if not user_context:
                    logger.error(f"[GENERATE]  Could not load user context for {user_id}")
                    return {"success": False, "error": "User profile not found"}
                logger.info(f"[GENERATE]  User context loaded successfully")
            
            if num_carryforward > 0:
                logger.info(f"[GENERATE]  Have {num_carryforward} carryforward items, will generate {num_to_generate} new actions")
            
            # Build carryforward actions list FIRST (used in all cases with carryforward)
            carryforward_actions = []
            if carryforward_items:
                for cf_item in carryforward_items[:4]:
                    carryforward_actions.append({
                        "title": cf_item.get("title", "Action"),
                        "category": cf_item.get("category", "general"),
                        "specific_action": cf_item.get("specific_action", ""),
                        "purpose": cf_item.get("purpose", ""),
                        "target_hormone": cf_item.get("target_hormone", "cortisol"),
                        "time_slot": cf_item.get("time_slot", "morning"),
                        "carried_forward_from": cf_item.get("carried_forward_from") or cf_item.get("id"),
                        "hormone_persona_intro": cf_item.get("hormone_persona_intro", ""),
                        "symptoms": cf_item.get("symptoms", []),
                        "conditions": cf_item.get("conditions", []),
                        "food_items": cf_item.get("food_items", []),
                        "food_amounts": cf_item.get("food_amounts", []),
                        "exercise_types": cf_item.get("exercise_types", []),
                        "exercise_durations": cf_item.get("exercise_durations", []),
                        "exercise_intensities": cf_item.get("exercise_intensities", []),
                        "mindfulness_techniques": cf_item.get("mindfulness_techniques", []),
                        "mindfulness_durations": cf_item.get("mindfulness_durations", []),
                        "variants": cf_item.get("variants", []),
                        "hero_image_url": cf_item.get("hero_image_url"),
                        "research_studies": cf_item.get("research_studies", []),
                    })
            
            # CASE 1: All 4 items from carryforward - no GPT needed
            if num_to_generate == 0:
                logger.info(f"[GENERATE]  All 4 slots filled by carryforward items - skipping GPT generation")
                actions = carryforward_actions
                gpt_cost = 0.0
                used_model = "carryforward_only"
                model_switch_reason = "All items carried forward from yesterday"
                logger.info(f"[GENERATE]  Carryforward plan ready with {len(actions)} items (no GPT cost)")
            
            # CASE 2: Partial carryforward (1-3 items) - generate only what's needed
            elif num_to_generate < 4 and num_carryforward > 0:
                logger.info(f"[GENERATE] Step 2: Generating {num_to_generate} NEW actions via GPT (partial)...")
                gpt_cost = 0.0
                used_model = self.GPT_MODEL
                model_switch_reason = f"Partial generation: {num_carryforward} carried + {num_to_generate} new"
                
                # Calculate hormone requirements for new actions
                # Check carryforward hormone distribution first
                primary_hormone = user_context.get("primary_hormone", "cortisol").lower()
                cf_primary = sum(1 for a in carryforward_actions if (a.get("target_hormone") or "").lower() == primary_hormone)
                cf_secondary = num_carryforward - cf_primary
                
                # We want 2 primary + 2 secondary total
                new_primary_needed = max(0, 2 - cf_primary)
                new_secondary_needed = max(0, 2 - cf_secondary)
                
                # Ensure we generate exactly num_to_generate
                if new_primary_needed + new_secondary_needed != num_to_generate:
                    # Adjust to match num_to_generate
                    if new_primary_needed + new_secondary_needed < num_to_generate:
                        # Need more - add to whichever is less
                        diff = num_to_generate - (new_primary_needed + new_secondary_needed)
                        if new_primary_needed <= new_secondary_needed:
                            new_primary_needed += diff
                        else:
                            new_secondary_needed += diff
                    else:
                        # Too many - reduce
                        diff = (new_primary_needed + new_secondary_needed) - num_to_generate
                        if new_secondary_needed >= diff:
                            new_secondary_needed -= diff
                        else:
                            new_primary_needed -= (diff - new_secondary_needed)
                            new_secondary_needed = 0
                
                hormone_requirements = {"primary": new_primary_needed, "secondary": new_secondary_needed}
                logger.info(f"[GENERATE]  Hormone balance: need {new_primary_needed} primary + {new_secondary_needed} secondary new actions")
                
                # Generate ONLY the needed actions using partial generation
                from app.services.evaluation_service import get_action_plan_evaluator
                evaluator = get_action_plan_evaluator()
                
                for attempt in range(1, self.MAX_RETRIES + 1):
                    logger.info(f" Partial generation attempt {attempt}/{self.MAX_RETRIES} for {num_to_generate} actions")
                    
                    new_actions, attempt_cost = await self._generate_partial_actions(
                        user_context=user_context,
                        num_actions=num_to_generate,
                        existing_actions=carryforward_actions,
                        db=db,
                        hormone_requirements=hormone_requirements
                    )
                    gpt_cost += attempt_cost
                    
                    if new_actions:
                        # Accept ANY valid actions we got, even if fewer than requested
                        actual_count = len(new_actions)
                        if actual_count >= num_to_generate:
                            logger.info(f" Partial generation successful: got {actual_count} new actions (requested: {num_to_generate})")
                            actions = carryforward_actions + new_actions[:num_to_generate]
                        else:
                            # Got fewer than requested, but still use what we have
                            logger.warning(f" Partial generation got {actual_count}/{num_to_generate} actions - using all available")
                            actions = carryforward_actions + new_actions
                        break
                    else:
                        logger.warning(f" Partial generation attempt {attempt} failed - no valid actions returned")
                        if attempt < self.MAX_RETRIES:
                            delay = attempt + random.uniform(0, 1)
                            await asyncio.sleep(delay)
                        else:
                            # Fallback: use carryforward only
                            logger.error(f" Partial generation failed after {self.MAX_RETRIES} attempts, using carryforward only")
                            actions = carryforward_actions
                            model_switch_reason += " | Partial gen failed, carryforward only"
            
            # CASE 3: No carryforward - generate all 4 actions
            else:
                # Step 2: Generate actions via GPT-5-mini with retry logic
                # Pydantic validation ensures complete data - no fallbacks
                logger.info(f"[GENERATE] Step 2: Generating all 4 actions via GPT...")
                actions = None
                gpt_cost = 0.0
                used_model = self.GPT_MODEL
                model_switch_reason = None  # Track why we switched models
            
                from app.services.evaluation_service import get_action_plan_evaluator
                evaluator = get_action_plan_evaluator()
                
                for attempt in range(1, self.MAX_RETRIES + 1):
                    logger.info(f" Generation attempt {attempt}/{self.MAX_RETRIES}")
                
                    # Generate actions with real citations from PubMed
                    # Pydantic validation happens inside _generate_actions_via_gpt
                    attempt_actions, attempt_cost = await self._generate_actions_via_gpt(user_context, db)
                    gpt_cost += attempt_cost
                    
                    if attempt_actions:
                        # Pydantic validated successfully
                        logger.info(f" Attempt {attempt}: All {len(attempt_actions)} actions validated by Pydantic")
                        
                        # ---------------------------------------------------------
                        # FAST QUALITY CHECK & MODEL SWITCHING
                        # Only checks condition_appropriateness (safety-critical)
                        # Full evaluation runs async after plan delivery
                        # ---------------------------------------------------------
                        try:
                            # Fast check: only evaluate condition safety (not full 5 metrics)
                            condition_score = await self._fast_condition_check(
                                attempt_actions, user_context
                            )
                            
                            # If medical safety is low, switch to fallback model
                            if condition_score is not None and condition_score < 70:
                                model_switch_reason = f"Low condition_appropriateness: {condition_score}/100 (threshold: 70)"
                                logger.warning(f" {model_switch_reason}. Switching to fallback model for better quality.")
                                
                                # Use Groq fallback model for better medical accuracy
                                fallback_model = GROQ_FALLBACK_MODEL
                                
                                try:
                                    fallback_actions, fallback_cost = await self._generate_actions_via_gpt(
                                        user_context, db, model_override=fallback_model
                                    )
                                    gpt_cost += fallback_cost
                                    
                                    if fallback_actions:
                                        logger.info(f" Fallback generation successful. Using {fallback_model} results.")
                                        attempt_actions = fallback_actions
                                        used_model = fallback_model
                                    else:
                                        logger.error(" Fallback generation failed. Using original OpenAI results.")
                                        model_switch_reason += " | Fallback returned None, using original"
                                except Exception as fallback_err:
                                    logger.error(f" Fallback API error: {fallback_err}. Using original OpenAI results.")
                                    model_switch_reason += f" | Fallback error: {str(fallback_err)[:100]}"
                            else:
                                logger.info(f" Fast quality check passed (Condition: {condition_score})")
                                
                        except Exception as e:
                            logger.warning(f" Fast quality check failed: {e}. Proceeding with OpenAI results.")
                            model_switch_reason = f"Quality check error: {str(e)[:100]}"
                        
                        actions = attempt_actions
                        break
                    else:
                        logger.warning(f" Attempt {attempt}: Generation or validation failed")
                        if attempt < self.MAX_RETRIES:
                            # OPTIMIZED: Shorter delays (1-3s) instead of exponential (2-9s)
                            # Attempt 1: ~1-2s
                            # Attempt 2: ~2-3s
                            delay = attempt + random.uniform(0, 1)
                            logger.info(f" Retrying generation in {delay:.2f}s...")
                            await asyncio.sleep(delay)
                        else:
                            # Max retries exceeded - FAIL CLEANLY, no fallbacks
                            logger.error(f" Max retries ({self.MAX_RETRIES}) exceeded. Failing without fallbacks.")
            
            total_cost += gpt_cost
            
            if not actions:
                logger.error("[GENERATE]  Failed to generate valid actions via GPT after all retries")
                return {"success": False, "error": "Failed to generate actions. Please try again."}
            
            # NOTE: Carryforward combining is now handled in CASE 2 (partial generation)
            # No need to combine here - actions already contains the right mix
            
            # Log the generated actions for debugging
            logger.info(f"[GENERATE] ==========================================================================")
            logger.info(f"[GENERATE]  GENERATED ACTIONS SUMMARY ({len(actions)} actions):")
            for i, action in enumerate(actions):
                is_carried = "carried_forward_from" in action and action.get("carried_forward_from")
                label = " [CARRYFORWARD]" if is_carried else ""
                logger.info(f"[GENERATE]   Action {i+1}: '{action['title']}' | Category: {action['category']} | Hormone: {action['target_hormone']}{label}")
                logger.info(f"[GENERATE]     Symptoms: {action['symptoms']}")
                logger.info(f"[GENERATE]     Conditions: {action['conditions']}")
            logger.info(f"[GENERATE] ==========================================================================")
            
            # Step 3: Generate images
            # - full: hero + 3 variants per action (16 total) - ALL IN PARALLEL NOW
            # - hero_only: only 1 hero image per action (4 total)
            # - none: skip image generation entirely
            
            if image_mode == "none":
                logger.info("[GENERATE] Step 3: Skipping image generation (image_mode=none)")
                actions_with_images, image_cost = actions, 0.0
            else:
                logger.info(
                    f"[GENERATE] Step 3: Generating images for {len(actions)} actions (image_mode={image_mode})..."
                )
                actions_with_images, image_cost = await self._generate_all_images(
                    actions=actions,
                    user_id=user_id,
                    db=db,
                    image_mode=image_mode,  # Use actual mode - no override
                )
                total_cost += image_cost
                logger.info(f"[GENERATE]  Images generated. Cost: ${image_cost:.4f}")
            
            # Step 4: Store plan in database
            logger.info(f"{log_prefix} Step 4: Storing plan in database...")
            plan = await self._store_plan(
                user_id=user_id,
                plan_date=plan_date,
                user_context=user_context,
                actions=actions_with_images,
                total_cost=total_cost,
                generation_time_ms=int((time.time() - start_time) * 1000),
                db=db,
                session_id=session_id
            )
            logger.info(f"{log_prefix}  Plan stored with ID: {plan.id}")
            
            # Step 4.5: Log AI Model Usage (Admin Tracking)
            try:
                from app.core.database import AIModelUsageLog
                
                # Determine if a switch happened
                fallback_model = None
                if used_model != self.GPT_MODEL:
                    fallback_model = used_model

                usage_log = AIModelUsageLog(
                    plan_id=plan.id,
                    user_id=user_id or (f"guest_{session_id}" if session_id else "guest_unknown"),
                    primary_model=self.GPT_MODEL,
                    fallback_model=fallback_model,
                    switch_reason=model_switch_reason,  # Now captures actual score
                    final_model_used=used_model
                )
                db.add(usage_log)
                await db.commit()
                logger.info(f" AI model usage logged for plan {plan.id}")
            except Exception as log_err:
                logger.error(f"Failed to log AI model usage: {log_err}")
            
            # Step 5: Fire-and-forget quality evaluation (async, non-blocking)
            # This stores metrics for trend monitoring without impacting UX
            # OPTIMIZATION: Skip evaluation for pure carryforward plans (already evaluated)
            if used_model == "carryforward_only":
                logger.info(f"⚡ Skipping evaluation for carryforward-only plan {plan.id}")
            else:
                try:
                    from app.services.evaluation_service import get_action_plan_evaluator
                    evaluator = get_action_plan_evaluator()
                    asyncio.create_task(
                        evaluator.evaluate_plan(
                            plan_id=plan.id,
                            user_id=user_id,
                            actions=actions_with_images,
                            user_context=user_context,
                            structure_valid=True,  # Pydantic validated already
                            db=self.async_session_maker(),  # New session for async task
                            session_id=session_id
                        )
                    )
                    logger.info(f" Evaluation task queued for plan {plan.id}")
                except Exception as eval_err:
                    logger.warning(f"Failed to queue evaluation: {eval_err}")
            
            elapsed = time.time() - start_time
            logger.info(f"[GENERATE] ==========================================================================")
            logger.info(f"[GENERATE]  PLAN GENERATION COMPLETE!")
            logger.info(f"[GENERATE]   Plan ID: {plan.id}")
            logger.info(f"[GENERATE]   User: {user_id}")
            logger.info(f"[GENERATE]   Date: {plan_date}")
            logger.info(f"[GENERATE]   Actions: {len(actions_with_images)}")
            logger.info(f"[GENERATE]   Time: {elapsed:.2f}s")
            logger.info(f"[GENERATE]   Cost: ${total_cost:.4f}")
            logger.info(f"[GENERATE]   Model: {used_model}")
            logger.info(f"[GENERATE] ==========================================================================")
            
            return await self._format_plan_response(plan, db)
            
        except Exception as e:
            logger.error(f"[GENERATE]  Error generating plan: {e}")
            logger.error(f"[GENERATE] Full traceback: {traceback.format_exc()}")
            return {"success": False, "error": "Failed to generate plan. Please try again."}
        finally:
            # Release advisory lock if we acquired it
            if got_lock:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": lock_key}
                    )
                    logger.info(f"[GENERATE]  Released advisory lock for {user_id}")
                except Exception as unlock_err:
                    logger.warning(f"[GENERATE] Failed to release advisory lock: {unlock_err}")
    
    def _get_user_today(self, timezone_str: str) -> date:
        """Get todays date in users timezone."""
        from zoneinfo import ZoneInfo
        
        try:
            tz = ZoneInfo(timezone_str)
            return datetime.now(tz).date()
        except Exception:
            # Fallback to UTC
            return datetime.utcnow().date()
    
    async def _get_existing_plan(
        self,
        user_id: Optional[str],
        plan_date: date,
        db: AsyncSession,
        session_id: Optional[str] = None
    ) -> Optional[Any]:
        """Check if a plan already exists for this user/date."""
        from app.core.database import ActionPlan
        
        try:
            query = select(ActionPlan).where(ActionPlan.plan_date == plan_date)
            
            if user_id:
                query = query.where(ActionPlan.uid == user_id)
            elif session_id:
                query = query.where(ActionPlan.session_id == session_id)
            else:
                return None
                
            result = await db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error checking existing plan: {e}")
            return None
    
    async def _check_and_carryforward_frozen_plan(
        self,
        user_id: str,
        today: date,
        user_timezone: str,
        db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """Check if yesterday was frozen and carry forward items."""
        from datetime import timedelta
        from sqlalchemy import text
        from app.core.database import ActionPlan, ActionPlanItem, ActionPlanItemVariant, UserStreakData
        
        TARGET_ACTIONS = 4  # Standard action plan size
        lock_key = hash(f"carryforward:{user_id}:{today}") % 2147483647  # int32 range
        got_lock = False
        
        try:
            # Acquire advisory lock to prevent race conditions
            lock_result = await db.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": lock_key}
            )
            got_lock = lock_result.scalar()
            
            if not got_lock:
                logger.info(f" CARRYFORWARD: Another request already processing for {user_id}, checking for existing plan")
                # Another request is handling this, wait a bit and check for existing plan
                import asyncio
                await asyncio.sleep(0.5)
                existing = await self._get_existing_plan(user_id, today, db)
                if existing:
                    resp = await self._format_plan_response(existing, db)
                    if isinstance(resp, dict) and resp.get("success"):
                        resp["plan_source"] = "carryforward_concurrent_existing"
                    return resp
                return None
            
            # Double-check: plan might have been created while we waited for lock
            existing_plan = await self._get_existing_plan(user_id, today, db)
            if existing_plan:
                logger.info(f" CARRYFORWARD: Plan already exists after acquiring lock, returning existing")
                resp = await self._format_plan_response(existing_plan, db)
                if isinstance(resp, dict) and resp.get("success"):
                    resp["plan_source"] = "carryforward_existing_after_lock"
                return resp
            
            logger.info(f" CARRYFORWARD CHECK: user={user_id}, today={today}")
            
            # Get users streak data to check frozen dates
            streak_result = await db.execute(
                select(UserStreakData).where(UserStreakData.uid == user_id)
            )
            streak_data = streak_result.scalar_one_or_none()
            
            if not streak_data:
                logger.info(f" CARRYFORWARD: No streak data found for {user_id}")
                return None
            
            logger.info(f" CARRYFORWARD: freeze_used_dates = {streak_data.freeze_used_dates}")
            
            if not streak_data.freeze_used_dates:
                logger.info(f" CARRYFORWARD: No frozen dates for {user_id}")
                return None  # No frozen dates
            
            # Parse frozen dates
            frozen_dates = []
            try:
                frozen_dates = [date.fromisoformat(d) for d in streak_data.freeze_used_dates if d]
                logger.info(f" CARRYFORWARD: Parsed frozen dates = {frozen_dates}")
            except (ValueError, TypeError) as e:
                logger.error(f" CARRYFORWARD: Error parsing dates: {e}")
                return None
            
            # Check if yesterday (or recent days) were frozen
            yesterday = today - timedelta(days=1)
            
            logger.info(f" CARRYFORWARD: yesterday={yesterday}, in frozen_dates? {yesterday in frozen_dates}")
            
            if yesterday not in frozen_dates:
                logger.info(f" CARRYFORWARD: Yesterday {yesterday} was NOT frozen, skipping carryforward")
                return None  # Yesterday wasn't frozen
            
            logger.info(f"Yesterday ({yesterday}) was frozen for user {user_id}, checking for incomplete items")
            
            # Get yesterdays plan
            yesterday_plan_result = await db.execute(
                select(ActionPlan).where(
                    and_(
                        ActionPlan.uid == user_id,
                        ActionPlan.plan_date == yesterday
                    )
                )
            )
            yesterday_plan = yesterday_plan_result.scalar_one_or_none()
            
            if not yesterday_plan:
                logger.info(f"No plan found for frozen day {yesterday}")
                return None
            
            # Get incomplete items from yesterdays plan
            incomplete_items_result = await db.execute(
                select(ActionPlanItem).where(
                    and_(
                        ActionPlanItem.plan_id == yesterday_plan.id,
                        ActionPlanItem.is_completed == False,
                        ActionPlanItem.is_replaced.isnot(True)
                    )
                )
            )
            incomplete_items = incomplete_items_result.scalars().all()
            
            if not incomplete_items:
                logger.info(f"No incomplete items in frozen day plan - user completed all yesterday")
                return None
            
            num_incomplete = len(incomplete_items)
            num_to_generate = TARGET_ACTIONS - num_incomplete
            
            # Calculate hormone distribution of carried items
            primary_hormone = user_context.get("primary_hormone", "cortisol").lower() if 'user_context' in dir() else "cortisol"
            secondary_hormone = user_context.get("secondary_hormone", "progesterone").lower() if 'user_context' in dir() else "progesterone"
            
            # We need to load user context first to get hormone info
            user_context = await self._load_user_context(user_id, db)
            if user_context:
                primary_hormone = user_context.get("primary_hormone", "cortisol").lower()
                secondary_hormone = user_context.get("secondary_hormone", "progesterone").lower()
            
            # Count hormones in carried items
            carried_primary = sum(1 for item in incomplete_items if (item.target_hormone or "").lower() == primary_hormone)
            carried_secondary = num_incomplete - carried_primary
            
            # Calculate what we need to generate to maintain 2+2 balance
            TARGET_PER_HORMONE = 2
            needed_primary = max(0, TARGET_PER_HORMONE - carried_primary)
            needed_secondary = max(0, TARGET_PER_HORMONE - carried_secondary)
            
            # Adjust if we need fewer than calculated (e.g., carried 3 items = only generate 1)
            if needed_primary + needed_secondary > num_to_generate:
                # Prioritize balance - reduce whichever we have more of in carried
                if carried_primary >= carried_secondary:
                    needed_primary = min(needed_primary, num_to_generate)
                    needed_secondary = num_to_generate - needed_primary
                else:
                    needed_secondary = min(needed_secondary, num_to_generate)
                    needed_primary = num_to_generate - needed_secondary
            
            hormone_requirements = {
                "primary": needed_primary,
                "secondary": needed_secondary
            }
            
            logger.info(f"Carryforward: {num_incomplete} incomplete items from {yesterday} ({carried_primary} primary, {carried_secondary} secondary)")
            logger.info(f"Will generate {num_to_generate} new items with hormone requirements: {hormone_requirements}")
            
            # Create todays plan
            new_plan = ActionPlan(
                uid=user_id,
                plan_date=today,
                created_at=datetime.utcnow(),
                feedback_collected=False
            )
            db.add(new_plan)
            await db.flush()  # Get new plan ID
            
            # Track carried forward items for excluding from new generation
            carried_items = []
            
            # Copy incomplete items to new plan
            for slot_idx, old_item in enumerate(incomplete_items):
                new_item = ActionPlanItem(
                    uid=user_id,
                    plan_id=new_plan.id,
                    slot=slot_idx,  # Re-assign slots starting from 0
                    time_slot=old_item.time_slot,
                    category=old_item.category,
                    title=old_item.title,
                    specific_action=old_item.specific_action,
                    purpose=old_item.purpose,
                    target_hormone=old_item.target_hormone,
                    hormone_persona_intro=old_item.hormone_persona_intro,
                    hero_image_url=old_item.hero_image_url,
                    research_studies=old_item.research_studies,
                    conditions=old_item.conditions,
                    symptoms=old_item.symptoms,
                    food_items=old_item.food_items,
                    food_amounts=old_item.food_amounts,
                    exercise_types=old_item.exercise_types,
                    exercise_durations=old_item.exercise_durations,
                    exercise_intensities=old_item.exercise_intensities,
                    mindfulness_techniques=old_item.mindfulness_techniques,
                    mindfulness_durations=old_item.mindfulness_durations,
                    is_completed=False,  # Reset completion status
                    is_replaced=False,
                    created_at=datetime.utcnow()
                )
                db.add(new_item)
                await db.flush()
                
                carried_items.append({
                    "title": old_item.title,
                    "category": old_item.category,
                    "target_hormone": old_item.target_hormone
                })
                
                # Copy variants too
                variants_result = await db.execute(
                    select(ActionPlanItemVariant).where(
                        ActionPlanItemVariant.item_id == old_item.id
                    )
                )
                variants = variants_result.scalars().all()
                
                for old_variant in variants:
                    new_variant = ActionPlanItemVariant(
                        item_id=new_item.id,
                        variant_type=old_variant.variant_type,
                        title=old_variant.title,
                        description=old_variant.description,
                        image_url=old_variant.image_url
                    )
                    db.add(new_variant)
            
            await db.commit()
            
            # If we need more items to reach 4, generate them
            if num_to_generate > 0:
                logger.info(f"Generating {num_to_generate} new actions to complete the plan")
                
                try:
                    # user_context already loaded above for hormone calculation
                    if user_context:
                        # Generate new actions for the remaining slots with hormone balance
                        new_actions, gen_cost = await self._generate_partial_actions(
                            user_context=user_context,
                            num_actions=num_to_generate,
                            existing_actions=carried_items,
                            db=db,
                            hormone_requirements=hormone_requirements
                        )
                        
                        if new_actions:
                            # Generate images for new actions
                            actions_with_images, img_cost = await self._generate_all_images(
                                new_actions, user_id, db
                            )
                            
                            # Store the new actions
                            start_slot = num_incomplete
                            for i, action_data in enumerate(actions_with_images):
                                new_item = ActionPlanItem(
                                    uid=user_id,
                                    plan_id=new_plan.id,
                                    slot=start_slot + i,
                                    time_slot=action_data.get("time_slot", "anytime"),
                                    category=action_data.get("category", "food"),
                                    title=action_data.get("title", ""),
                                    specific_action=action_data.get("specific_action", ""),
                                    purpose=action_data.get("purpose", ""),
                                    target_hormone=action_data.get("target_hormone", ""),
                                    hormone_persona_intro=action_data.get("hormone_persona_intro", ""),
                                    hero_image_url=action_data.get("hero_image_url", ""),
                                    research_studies=action_data.get("research_studies", []),
                                    conditions=action_data.get("conditions", []),
                                    symptoms=action_data.get("symptoms", []),
                                    food_items=action_data.get("food_items", []),
                                    food_amounts=action_data.get("food_amounts", []),
                                    exercise_types=action_data.get("exercise_types", []),
                                    exercise_durations=action_data.get("exercise_durations", []),
                                    exercise_intensities=action_data.get("exercise_intensities", []),
                                    mindfulness_techniques=action_data.get("mindfulness_techniques", []),
                                    mindfulness_durations=action_data.get("mindfulness_durations", []),
                                    is_completed=False,
                                    is_replaced=False,
                                    created_at=datetime.utcnow()
                                )
                                db.add(new_item)
                                await db.flush()
                                
                                # Store variants
                                for variant in action_data.get("variants", []):
                                    var = ActionPlanItemVariant(
                                        item_id=new_item.id,
                                        variant_type=variant.get("variant_type", ""),
                                        title=variant.get("title", ""),
                                        description=variant.get("description", ""),
                                        image_url=variant.get("image_url", "")
                                    )
                                    db.add(var)
                            
                            await db.commit()
                            logger.info(f"Added {len(actions_with_images)} new actions to carryforward plan")
                except Exception as gen_err:
                    logger.error(f"Failed to generate additional actions: {gen_err}")
                    # Continue with partial plan - better than nothing
            
            await db.refresh(new_plan)
            
            logger.info(f"Created carryforward plan {new_plan.id} with {num_incomplete} carried + {num_to_generate} new items")
            
            # Return formatted response
            resp = await self._format_plan_response(new_plan, db)
            if isinstance(resp, dict) and resp.get("success"):
                resp["plan_source"] = "carryforward"
            return resp
            
        except Exception as e:
            error_str = str(e)
            logger.error(f"Error checking for carryforward plan: {e}")
            
            # Handle race condition - if plan already exists, rollback and fetch it
            if "duplicate key" in error_str.lower() or "unique" in error_str.lower():
                logger.info(f"Race condition detected - plan already exists, rolling back and fetching")
                try:
                    await db.rollback()
                    # Fetch the existing plan that was created by the other request
                    existing = await self._get_existing_plan(user_id, today, db)
                    if existing:
                        resp = await self._format_plan_response(existing, db)
                        if isinstance(resp, dict) and resp.get("success"):
                            resp["plan_source"] = "carryforward_race_existing"
                        return resp
                except Exception as fetch_err:
                    logger.error(f"Failed to fetch existing plan after race condition: {fetch_err}")
            
            return None
        finally:
            # Release advisory lock if we acquired it
            if got_lock:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": lock_key}
                    )
                except Exception as unlock_err:
                    logger.warning(f"Failed to release carryforward lock: {unlock_err}")
    
    async def _get_chat_skipped_items_from_yesterday(
        self,
        user_id: str,
        today: date,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Get items that were skipped via care plan check-in yesterday.
        
        These items should be carried forward to today's plan generation.
        Returns list of action item data dicts that can be used as seed for today's plan.
        """
        from datetime import timedelta
        from sqlalchemy import func
        from app.core.database import ActionPlanFeedback, ActionPlanItem, ActionPlanItemVariant
        
        yesterday = today - timedelta(days=1)
        
        try:
            # Find feedback records for skipped items from yesterday's care plan check-in
            skipped_feedback_result = await db.execute(
                select(ActionPlanFeedback).where(
                    and_(
                        ActionPlanFeedback.uid == user_id,
                        ActionPlanFeedback.feedback_type == "skipped",
                        ActionPlanFeedback.feedback_source == "care_plan_checkin",
                        func.date(ActionPlanFeedback.created_at) == yesterday
                    )
                )
            )
            skipped_feedbacks = skipped_feedback_result.scalars().all()
            
            if not skipped_feedbacks:
                return []
            
            # Get the actual item details for each skipped feedback
            skipped_items = []
            for feedback in skipped_feedbacks:
                if not feedback.item_id:
                    continue
                    
                # Get the original item
                item_result = await db.execute(
                    select(ActionPlanItem).where(ActionPlanItem.id == feedback.item_id)
                )
                item = item_result.scalar_one_or_none()
                
                if item and not item.is_completed:
                    # Item was skipped and not completed - carry forward
                    # CRITICAL: Include ALL fields so generator can use them directly
                    skipped_items.append({
                        # Core identification
                        "id": item.id,
                        "original_id": item.id,
                        "carried_forward_from": item.id,
                        
                        # Core content - MUST have these
                        "title": item.title,
                        "category": item.category,
                        "specific_action": item.specific_action,
                        "purpose": item.purpose,
                        "time_slot": item.time_slot,
                        
                        # Hormone targeting - CRITICAL for hormone balance
                        "target_hormone": item.target_hormone,
                        "hormone_persona_intro": item.hormone_persona_intro,
                        
                        # Symptoms and conditions
                        "symptoms": item.symptoms or [],
                        "conditions": item.conditions or [],
                        
                        # Category-specific fields
                        "food_items": item.food_items or [],
                        "food_amounts": item.food_amounts or [],
                        "exercise_types": item.exercise_types or [],
                        "exercise_durations": item.exercise_durations or [],
                        "exercise_intensities": item.exercise_intensities or [],
                        "mindfulness_techniques": item.mindfulness_techniques or [],
                        "mindfulness_durations": item.mindfulness_durations or [],
                        
                        # Images - preserve original images
                        "hero_image_url": item.hero_image_url,
                        "hero_image_prompt": item.hero_image_prompt,
                        
                        # Research
                        "research_studies": item.research_studies or [],
                        
                        # Variants - will be loaded below
                        "variants": [],
                    })
                    
                    # CRITICAL: Load variants with their images to avoid regeneration
                    variant_result = await db.execute(
                        select(ActionPlanItemVariant).where(
                            ActionPlanItemVariant.item_id == item.id
                        )
                    )
                    variants = variant_result.scalars().all()
                    
                    if variants:
                        skipped_items[-1]["variants"] = [
                            {
                                "variant_type": v.variant_type,
                                "title": v.title,
                                "description": v.description,
                                "image_url": v.image_url,  # Preserve existing image!
                                "image_prompt": v.image_prompt,
                            }
                            for v in variants
                        ]
            
            if skipped_items:
                logger.info(f"Found {len(skipped_items)} chat-skipped items from yesterday to carry forward")
            
            return skipped_items
            
        except Exception as e:
            logger.error(f"Error getting chat-skipped items: {e}")
            return []
    
    async def _generate_partial_actions(
        self,
        user_context: Dict[str, Any],
        num_actions: int,
        existing_actions: List[Dict],
        db: Optional[AsyncSession] = None,
        hormone_requirements: Optional[Dict[str, int]] = None
    ) -> Tuple[Optional[List[Dict]], float]:
        """
        Generate a specific number of actions, avoiding duplicates with existing actions.
        
        Used for carryforward plans where we need to fill remaining slots.
        
        Args:
            user_context: User profile and preferences
            num_actions: Number of NEW actions to generate
            existing_actions: Actions already in plan (to avoid duplicates)
            db: Database session
            hormone_requirements: Dict with {"primary": N, "secondary": M} specifying
                                 how many of each hormone type to generate
        """
        if num_actions <= 0:
            return ([], 0.0)
        
        if not self.openai_api_key:
            logger.error("OpenAI API key not configured")
            return (None, 0.0)
        
        # Get cycle phase for hormone context
        cycle_phase = user_context.get("cycle_phase", "follicular").lower()
        primary_hormone = user_context.get("primary_hormone", "cortisol").lower()
        secondary_hormone = user_context.get("secondary_hormone", "progesterone").lower()
        
        # Calculate hormone distribution if not specified
        if hormone_requirements:
            primary_count = hormone_requirements.get("primary", num_actions // 2)
            secondary_count = hormone_requirements.get("secondary", num_actions - primary_count)
        else:
            # Default: split evenly
            primary_count = num_actions // 2
            secondary_count = num_actions - primary_count
        
        # Build hormone instruction
        hormone_instruction = f"""
HORMONE BALANCE REQUIREMENT (CRITICAL):
- Generate exactly {primary_count} action(s) targeting PRIMARY hormone ({primary_hormone})
- Generate exactly {secondary_count} action(s) targeting SECONDARY hormone ({secondary_hormone})
Total: {num_actions} actions
"""
        
        # Build prompt for partial generation
        existing_summary = json.dumps(existing_actions, indent=2) if existing_actions else "None"
        user_conditions = user_context.get('diagnosed_conditions', [])
        top_concern = user_context.get('top_concern', 'general wellness')
        condition_str = user_conditions[0] if user_conditions else "womens health"
        
        # ========================================================
        # RESEARCH-FIRST: Fetch PubMed research BEFORE generating actions
        # This ensures real citations, not hallucinated ones
        # ========================================================
        research_context = ""
        if db:
            try:
                import asyncio
                import random
                
                # Build research queries based on num_actions needed
                food_varieties = ["omega-3", "antioxidant", "fiber", "protein", "vitamin", "mineral"]
                movement_varieties = ["yoga", "walking", "stretching", "strength", "cardio"]
                mindfulness_varieties = ["breathing", "meditation", "relaxation", "mindfulness"]
                
                random.shuffle(food_varieties)
                random.shuffle(movement_varieties)
                random.shuffle(mindfulness_varieties)
                
                # Create queries for the number of actions needed
                research_queries = []
                categories_needed = []
                hormones_needed = []
                
                for i in range(num_actions):
                    hormone = primary_hormone if i < primary_count else secondary_hormone
                    # Rotate through categories
                    if i % 3 == 0:
                        query = f"{food_varieties[i % len(food_varieties)]} {hormone} {condition_str} women"
                        categories_needed.append("food")
                    elif i % 3 == 1:
                        query = f"{movement_varieties[i % len(movement_varieties)]} {hormone} {condition_str} women"
                        categories_needed.append("movement")
                    else:
                        query = f"{mindfulness_varieties[i % len(mindfulness_varieties)]} stress {hormone} women"
                        categories_needed.append("mindfulness")
                    research_queries.append(query)
                    hormones_needed.append(hormone)
                
                # Fetch research in parallel
                async def fetch_paper(idx: int, query: str):
                    try:
                        paper = await execute_pubmed_tool({
                            "query": query,
                            "action_title": f"Partial action {idx + 1}",
                            "category": categories_needed[idx],
                            "target_hormone": hormones_needed[idx]
                        }, db=db)
                        if paper and paper.get("title"):
                            return {"paper": paper, "category": categories_needed[idx], "hormone": hormones_needed[idx]}
                    except Exception as e:
                        logger.warning(f"Partial gen research query failed: {e}")
                    return None
                
                logger.info(f"[PARTIAL] Fetching {len(research_queries)} research papers in parallel...")
                results = await asyncio.gather(*[fetch_paper(i, q) for i, q in enumerate(research_queries)], return_exceptions=True)
                
                research_findings = [r for r in results if r and not isinstance(r, Exception)]
                logger.info(f"[PARTIAL] Found {len(research_findings)} research papers")
                
                if research_findings:
                    research_context = """
======================================================================
RESEARCH FINDINGS - USE THESE TO INFORM YOUR RECOMMENDATIONS
======================================================================
"""
                    for finding in research_findings:
                        paper = finding["paper"]
                        research_context += f"""
📚 Research for {finding['hormone'].upper()} ({finding['category']}):
   Title: {paper.get('title', 'Unknown')}
   Journal: {paper.get('journal', 'Unknown')} ({paper.get('year', 'N/A')})
   Finding: {paper.get('finding', 'Evidence-based intervention')}
   PMID: {paper.get('pmid', 'N/A')}
   
   → Use this to inform your {finding['category']} recommendation for {finding['hormone']}
"""
                    research_context += """
IMPORTANT: Base your recommendations on the research above. Include paper details in research_studies field.
"""
            except Exception as e:
                logger.warning(f"[PARTIAL] Research fetch failed: {e}, proceeding without")
        
        # Format existing actions more clearly so GPT understands what NOT to generate
        existing_titles = [a.get('title', 'Unknown') for a in existing_actions] if existing_actions else []
        existing_categories = [a.get('category', 'unknown') for a in existing_actions] if existing_actions else []
        existing_hormones = [a.get('target_hormone', 'unknown') for a in existing_actions] if existing_actions else []
        
        prompt = f"""Generate exactly {num_actions} personalized wellness action(s) for this user.

======================================================================
🚨 ABSOLUTE BAN - DO NOT GENERATE ANYTHING SIMILAR TO THESE 🚨
======================================================================
The user already has these actions in their plan today. Your new actions
MUST be COMPLETELY DIFFERENT. If you generate anything similar, the entire
response will be REJECTED.

EXISTING ACTION TITLES (BANNED): {existing_titles}
EXISTING CATEGORIES: {existing_categories}
EXISTING HORMONES: {existing_hormones}

FULL DETAILS OF EXISTING ACTIONS:
{existing_summary}

❌ Do NOT suggest similar foods (e.g., if they have "Salmon", don't suggest "Mackerel" - both are fatty fish)
❌ Do NOT suggest similar exercises (e.g., if they have "Yoga", don't suggest "Stretching")
❌ Do NOT suggest similar mindfulness (e.g., if they have "Meditation", don't suggest "Mindful Breathing")

======================================================================
USER PROFILE
======================================================================
- Age: {user_context.get('age', 'Not specified')}
- Cycle Day: {user_context.get('cycle_day', 'Unknown')}
- Cycle Phase: {cycle_phase}
- Primary Hormone: {primary_hormone}
- Secondary Hormone: {secondary_hormone}
- Top Concern: {top_concern}
- Diagnosed Conditions: {', '.join(user_conditions) if user_conditions else 'none'}
- Lifestyle Focus: {user_context.get('lifestyle_focus', ['eat', 'move', 'pause'])}
- Diet Preference: {user_context.get('diet_preference', 'none')}
- Food Allergies: {user_context.get('food_allergies', 'none')}
- Stress Level: {user_context.get('stress_level', 'moderate')}
- Workout Intensity: {user_context.get('workout_intensity', 'moderate')}
- Current Streak: {user_context.get('current_streak', 0)} days
- Longest Streak: {user_context.get('longest_streak', 0)} days

======================================================================
RECENT INSIGHTS
======================================================================
Weekly Check-ins: {user_context.get('weekly_checkin_insights', 'None')}
Daily Reviews: {user_context.get('daily_review_insights', 'None')}
Feedback Memory: {user_context.get('feedback_memory', 'None')}

{research_context}

======================================================================
{hormone_instruction}
======================================================================

======================================================================
⭐ TITLE RULES (CRITICAL - 1-3 WORDS ONLY!)
======================================================================
✅ FOOD: Raw ingredient name ONLY (e.g., "Salmon", "Quinoa", "Spearmint")
✅ MOVEMENT: Simple activity name (e.g., "Morning Yoga", "Brisk Walking")
✅ MINDFULNESS: Technique name (e.g., "Deep Breathing", "Body Scan")

❌ NO preparation methods ("tea", "smoothie", "salad")
❌ NO adjectives ("powerful", "amazing", "gentle")
❌ NO long phrases

======================================================================
OUTPUT STRUCTURE FOR EACH ACTION
======================================================================

1. title: SHORT 1-3 word noun (see TITLE RULES above)

2. category: "food" or "movement" or "mindfulness"

3. time_slot: "morning" or "afternoon" or "evening"

4. specific_action: MUST include 3 DIFFERENT WAYS to consume/do this action (80-120 words)
   FORMAT: Start with scientific benefit, then list 3 methods:
   "[Food/Exercise] provides [benefit for user's {top_concern}]. Try it as: (1) [method with details], (2) [method with details], or (3) [method with details]."

5. purpose: CONDITION-SPECIFIC explanation (2-3 sentences)
   MANDATORY: Start with "With your [condition]..." and explain the mechanism.
   Example: "With your PCOS, insulin sensitivity is key. Cinnamon contains cinnamaldehyde which mimics insulin action, helping reduce those afternoon energy crashes."

6. target_hormone: MUST be exactly "{primary_hormone}" or "{secondary_hormone}" (follow hormone counts above)

7. hormone_persona_intro: MAX 2 SENTENCES (25-30 words)
   Pattern: "Hey! I'm [Hormone] 💜 - I picked [action] for your [condition] because it [mechanism]."

8. image_prompt: FLUX.1 optimized prompt for the action
   - FOOD: "Professional close-up food photography of [EXACT FOOD], [texture/color], on [surface], natural lighting, 4K quality"
   - MOVEMENT: "Serene photograph of woman doing [EXACT POSE], [setting], soft natural lighting, wellness aesthetic"
   - MINDFULNESS: "Calm photograph of woman practicing [TECHNIQUE], peaceful setting, soft lighting"

9. research_studies: Array with 2-4 research citations (use papers from RESEARCH FINDINGS above if available):
   PRIORITY: Meta-analysis > Systematic Review > RCT > Clinical Trial > Review
   [
     {{
       "title": "[Study title]",
       "journal": "[Journal name]",
       "year": [year],
       "participants": [integer],
       "finding": "[Key finding]",
       "pmid": "[PMID if available]",
       "verification_link": "[PubMed URL if available]",
       "study_type": "meta_analysis" | "systematic_review" | "rct" | "clinical_trial" | "review",
       "study_type_label": "Meta-Analysis" | "Systematic Review" | "RCT" | etc.
     }},
     ... (2-4 total)
   ]

10. variants: Array of EXACTLY 3 variant objects showing DIFFERENT WAYS to do the action:

    FOR FOOD ACTIONS:
    [
      {{"variant_type": "tasty", "title": "[Delicious version]", "description": "[How to make it tasty - 2 sentences]", "image_prompt": "[FLUX prompt for this variant]"}},
      {{"variant_type": "easy", "title": "[Quick/simple version]", "description": "[Easy preparation - 2 sentences]", "image_prompt": "[FLUX prompt]"}},
      {{"variant_type": "healthy", "title": "[Healthiest version]", "description": "[Maximum nutrition - 2 sentences]", "image_prompt": "[FLUX prompt]"}}
    ]
    
    FOR MOVEMENT ACTIONS:
    [
      {{"variant_type": "gentle", "title": "[Gentle version]", "description": "[Low intensity option - 2 sentences]", "image_prompt": "[FLUX prompt]"}},
      {{"variant_type": "energizing", "title": "[Active version]", "description": "[Higher energy option - 2 sentences]", "image_prompt": "[FLUX prompt]"}},
      {{"variant_type": "quick", "title": "[Short version]", "description": "[5-10 min version - 2 sentences]", "image_prompt": "[FLUX prompt]"}}
    ]
    
    FOR MINDFULNESS ACTIONS:
    [
      {{"variant_type": "guided", "title": "[Guided version]", "description": "[With audio/app guidance - 2 sentences]", "image_prompt": "[FLUX prompt]"}},
      {{"variant_type": "solo", "title": "[Self-guided version]", "description": "[Independent practice - 2 sentences]", "image_prompt": "[FLUX prompt]"}},
      {{"variant_type": "brief", "title": "[Quick version]", "description": "[3-5 min version - 2 sentences]", "image_prompt": "[FLUX prompt]"}}
    ]

11. symptoms: Array of 1-3 symptoms this addresses (from user's concerns)

12. conditions: Array of conditions this helps (from user's diagnosed_conditions) or []

======================================================================
⚠️ CATEGORY-SPECIFIC FIELDS (MANDATORY - DO NOT SKIP!)
======================================================================

FOR FOOD ACTIONS (MUST include both):
- food_items: ["ingredient1", "ingredient2"] (the actual food items)
- food_amounts: ["portion1", "portion2"] (TODAY's portions like "4 oz", "1 cup", "2 tbsp")
- exercise_types: []
- exercise_durations: []
- exercise_intensities: []
- mindfulness_techniques: []
- mindfulness_durations: []

FOR MOVEMENT ACTIONS (MUST include all three):
- exercise_types: ["yoga", "walking", etc.]
- exercise_durations: ["15 min", "20 min"]
- exercise_intensities: ["low", "moderate", "gentle"]
- food_items: []
- food_amounts: []
- mindfulness_techniques: []
- mindfulness_durations: []

FOR MINDFULNESS ACTIONS (MUST include both):
- mindfulness_techniques: ["deep breathing", "body scan", etc.]
- mindfulness_durations: ["5 min", "10 min"]
- food_items: []
- food_amounts: []
- exercise_types: []
- exercise_durations: []
- exercise_intensities: []

======================================================================
FINAL REQUIREMENTS
======================================================================
1. Generate exactly {num_actions} NEW action(s) - DIFFERENT from existing ones
2. Mix categories to complement existing actions
3. STRICTLY follow hormone count: {primary_count} for {primary_hormone}, {secondary_count} for {secondary_hormone}
4. Each action MUST have ALL fields listed above
5. Variants MUST be meaningful alternatives, not just renamed copies

Return as JSON: {{"actions": [array of {num_actions} action objects]}}
"""
        
        try:
            import openai
            from groq import AsyncGroq
            
            openai_error = None
            content = None
            cost = 0.0
            
            # Try OpenAI first
            if self.openai_api_key:
                try:
                    client = openai.AsyncOpenAI(api_key=self.openai_api_key)
                    
                    # Build payload with conditional temperature
                    create_kwargs = {
                        "model": "gpt-5-mini",
                        "messages": [
                            {"role": "system", "content": "You are a womens wellness expert. Generate personalized health actions. Follow hormone balance requirements EXACTLY."},
                            {"role": "user", "content": prompt}
                        ],
                        "max_completion_tokens": 16000,  # GPT-5-mini has 128K context - allow proper output
                        "response_format": {"type": "json_object"}
                    }
                    
                    # Only add temperature if model supports it
                    if self.model_supports_temperature("gpt-5-mini"):
                        create_kwargs["temperature"] = 0.7
                    
                    response = await client.chat.completions.create(**create_kwargs)
                    
                    content = response.choices[0].message.content
                    cost = (response.usage.prompt_tokens * 0.00015 + response.usage.completion_tokens * 0.0006) / 1000
                    logger.info(f" Partial actions generated via OpenAI (tokens: prompt={response.usage.prompt_tokens}, completion={response.usage.completion_tokens})")
                    logger.info(f" DEBUG: Raw OpenAI response content length: {len(content) if content else 0}")
                    if content:
                        logger.info(f" DEBUG: Raw OpenAI content (first 500 chars): {content[:500]}")
                except Exception as e:
                    openai_error = str(e)
                    logger.warning(f" OpenAI exception: {openai_error[:200]}")
            else:
                openai_error = "No OpenAI API key"
            
            # Groq fallback
            if openai_error and GROQ_API_KEY:
                try:
                    logger.info(f" Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
                    
                    # gpt-oss-120b is a reasoning model - doesn't support response_format
                    is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                    enhanced_prompt = prompt + "\n\nCRITICAL: Return ONLY valid JSON in this exact format: {\"actions\": [{...}]}. No markdown, no explanation, just JSON." if is_reasoning_model else prompt
                    
                    response = await groq_client.chat.completions.create(
                        model=GROQ_FALLBACK_MODEL,
                        messages=[
                            {"role": "system", "content": "You are a womens wellness expert. Generate personalized health actions. Follow hormone balance requirements EXACTLY. Return ONLY valid JSON."},
                            {"role": "user", "content": enhanced_prompt}
                        ],
                        temperature=0.7,
                        max_tokens=16000  # Groq llama-3.3-70b supports large outputs
                    )
                    
                    content = response.choices[0].message.content
                    
                    # Clean Groq output - more robust cleaning
                    if content:
                        logger.info(f" DEBUG: Raw Groq content (first 500 chars): {content[:500]}")
                        # Remove markdown code blocks
                        if "```json" in content:
                            content = content.split("```json", 1)[1]
                            if "```" in content:
                                content = content.split("```", 1)[0]
                        elif "```" in content:
                            content = content.split("```", 1)[1]
                            if "```" in content:
                                content = content.split("```", 1)[0]
                        content = content.strip()
                        logger.info(f" DEBUG: Cleaned Groq content (first 500 chars): {content[:500]}")
                    
                    logger.info(" Partial actions generated via Groq fallback")
                except Exception as e:
                    logger.error(f" Groq fallback also failed: {e}")
                    return (None, 0.0)
            elif openai_error:
                logger.error(f" OpenAI failed and no Groq fallback: {openai_error}")
                return (None, 0.0)
            
            if not content:
                logger.error(" No content returned from LLM")
                return (None, 0.0)
            
            # Parse response
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f" JSON parse error: {e}. Content received: {content[:500]}")
                return (None, 0.0)
            
            # DEBUG: Log parsed JSON structure
            logger.info(f" DEBUG: Parsed JSON keys: {list(parsed.keys()) if isinstance(parsed, dict) else f'type={type(parsed).__name__}'}")
            if isinstance(parsed, dict):
                logger.info(f" DEBUG: Full parsed dict: {json.dumps(parsed, indent=2)[:1000]}")
            
            actions = parsed.get("actions", parsed if isinstance(parsed, list) else [parsed])
            
            # Ensure we have a list
            if not isinstance(actions, list):
                logger.warning(f" DEBUG: actions is not a list, converting from {type(actions).__name__}")
                actions = [actions] if actions else []
            
            logger.info(f" DEBUG: actions list length = {len(actions)}, content: {json.dumps(actions, indent=2)[:500] if actions else 'EMPTY'}")
            
            # Just take the actions GPT returned - the prompt already told it what to avoid
            # No deduplication needed since prompt includes carryforward items to avoid
            validated_actions = actions[:num_actions]
            
            if not validated_actions:
                logger.warning(f" ⚠️ EMPTY RESULT: GPT returned no actions. actions={actions}, num_actions={num_actions}, validated_actions={validated_actions}")
                logger.warning(f" ⚠️ FULL PARSED CONTENT: {json.dumps(parsed, indent=2)[:2000] if parsed else 'NULL'}")
                return (None, cost)  # Return None for empty result to trigger retry
            
            logger.info(f"✅ Generated {len(validated_actions)} partial actions for carryforward plan (requested: {num_actions})")
            return (validated_actions, cost)
            
        except Exception as e:
            logger.error(f"Failed to generate partial actions: {e}")
            return (None, 0.0)
    
    async def _load_minimal_context_for_carryforward(
        self,
        user_id: str,
        db: AsyncSession,
        user_timezone: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Load MINIMAL context needed for storing carryforward-only plans.
        This is much faster than _load_user_context since we skip:
        - Feedback history
        - Weekly check-ins
        - Daily reviews
        - Anti-repetition data
        - Chatbot context
        
        Only loads: profile, primary/secondary hormones, cycle info, conditions
        """
        from app.core.database import UserProfile, UserResponse
        from app.services.cycle_service import get_cycle_service
        from sqlalchemy.future import select
        
        try:
            # Get user profile
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.uid == user_id)
            )
            profile = profile_result.scalar_one_or_none()
            
            if not profile:
                logger.warning(f"[MINIMAL_CONTEXT] No UserProfile found for user {user_id}")
                return None
            
            # Get user responses (for diagnosed conditions)
            response_result = await db.execute(
                select(UserResponse)
                .where(UserResponse.uid == user_id)
                .order_by(UserResponse.created_at.desc())
                .limit(1)
            )
            user_response = response_result.scalar_one_or_none()
            
            # Get cycle info
            cycle_service = get_cycle_service()
            cycle_info = await cycle_service.get_cycle_phase_info_async(user_id, db)
            
            cycle_day = cycle_info.get("cycle_day", 1) if cycle_info else 1
            cycle_phase = cycle_info.get("phase", "follicular") if cycle_info else "follicular"
            
            # Determine primary/secondary hormones from conditions/phase
            diagnosed_conditions = []
            if user_response and user_response.response_data:
                diagnosed_conditions = user_response.response_data.get("diagnosed_conditions", [])
            
            primary_hormone, secondary_hormone = self._determine_hormones_from_conditions(
                diagnosed_conditions, cycle_phase
            )
            
            # Build minimal context
            context = {
                "user_id": user_id,
                "user_timezone": user_timezone or profile.current_timezone or "UTC",
                "primary_hormone": primary_hormone,
                "secondary_hormone": secondary_hormone,
                "cycle_day": cycle_day,
                "cycle_phase": cycle_phase,
                "diagnosed_conditions": diagnosed_conditions,
                "lifestyle_focus": profile.lifestyle_focus or ["eat", "move", "pause"],
                "top_concern": user_response.response_data.get("top_concern", "General Wellness") if user_response and user_response.response_data else "General Wellness",
            }
            
            logger.info(f"[MINIMAL_CONTEXT] Loaded for {user_id}: primary={primary_hormone}, phase={cycle_phase}")
            return context
            
        except Exception as e:
            logger.error(f"[MINIMAL_CONTEXT] Failed to load: {e}")
            return None
    
    def _determine_hormones_from_conditions(
        self, 
        conditions: List[str], 
        cycle_phase: str
    ) -> tuple:
        """Determine primary/secondary hormones based on conditions and cycle phase."""
        # Condition-to-hormone mapping
        condition_hormone_map = {
            "pcos": ("androgens", "insulin"),
            "polycystic ovary syndrome": ("androgens", "insulin"),
            "endometriosis": ("estrogen", "progesterone"),
            "thyroid": ("thyroid", "cortisol"),
            "hypothyroidism": ("thyroid", "cortisol"),
            "hyperthyroidism": ("thyroid", "cortisol"),
            "diabetes": ("insulin", "cortisol"),
            "cushing": ("cortisol", "androgens"),
            "adrenal": ("cortisol", "androgens"),
            "menopause": ("estrogen", "progesterone"),
            "perimenopause": ("estrogen", "progesterone"),
        }
        
        # Check conditions first
        for condition in conditions:
            condition_lower = condition.lower()
            for key, hormones in condition_hormone_map.items():
                if key in condition_lower:
                    return hormones
        
        # Default based on cycle phase
        phase_lower = cycle_phase.lower() if cycle_phase else "follicular"
        if "luteal" in phase_lower:
            return ("progesterone", "cortisol")
        elif "ovula" in phase_lower:
            return ("estrogen", "LH")
        elif "menstr" in phase_lower:
            return ("estrogen", "prostaglandins")
        else:  # follicular
            return ("estrogen", "FSH")

    async def _load_user_context(
        self,
        user_id: Optional[str],
        db: AsyncSession,
        session_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Load all user context needed for action generation.

        NOTE: SQLAlchemy AsyncSession does not allow concurrent use; keep DB I/O
        sequential on this session. We still optimize the slowest piece
        (anti-repetition) by using a single JOIN query instead of an N+1 loop.
        """
        from app.core.database import UserProfile, UserResponse, ActionPlanFeedback, UserStreakData, WeeklyCheckIn, ActionPlanDailyReview, ActionPlan, ActionPlanItem, CarePlanCheckInThread, SymptomCheckInThread, QuestionSession
        
        logger.info(f"[CONTEXT] ==========================================================================")
        logger.info(f"[CONTEXT] Starting _load_user_context for user: {user_id} (session: {session_id})")
        logger.info(f"[CONTEXT] ==========================================================================")
        
        try:
            # GUEST FLOW
            if not user_id and session_id:
                logger.info(f"[CONTEXT] Loading GUEST context from session: {session_id}")
                session_result = await db.execute(
                    select(QuestionSession).where(QuestionSession.session_id == session_id)
                )
                session = session_result.scalar_one_or_none()
                
                if not session:
                    logger.warning(f"[CONTEXT] Session {session_id} not found")
                    return None
                    
                # Construct context from session data
                return {
                    "age": session.age,
                    "cycle_day": 1, 
                    "cycle_phase": "follicular", # Default or infer from period_description?
                    "primary_hormone": "cortisol", 
                    "secondary_hormone": "progesterone",
                    "top_concern": session.top_concern,
                    "diagnosed_conditions": session.diagnosed_conditions or [],
                    "period_concerns": [], # Map from session?
                    "body_concerns": [],
                    "skin_hair_concerns": [],
                    "mental_health_concerns": [],
                    "family_history": [],
                    "lifestyle_focus": session.lifestyle_focus or ["eat", "move", "pause"],
                    "diet_preference": "none",
                    "food_allergies": [],
                    "stress_level": "moderate",
                    "sleep_duration": "7-8 hours",
                    "workout_intensity": "moderate",
                    "birth_control": session.birth_control,
                    "current_streak": 0,
                    "longest_streak": 0,
                    "feedback_summary": "New guest user",
                    "feedback_memory": "",
                    "chatbot_context": "",
                    "weekly_checkin_insights": "",
                    "timezone": "UTC"
                }

            # STEP 1: Get user profile FIRST (required to continue)
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.uid == user_id)
            )
            profile = profile_result.scalar_one_or_none()
            
            if not profile:
                logger.warning(f"[CONTEXT] No UserProfile found for user {user_id}")
                return None
            logger.info(f"[CONTEXT] Found UserProfile for user {user_id}")
            
            fourteen_days_ago = date.today() - timedelta(days=14)

            # Get user responses (assessment data)
            response_result = await db.execute(
                select(UserResponse)
                .where(UserResponse.uid == user_id)
                .order_by(UserResponse.created_at.desc())
                .limit(1)
            )
            user_response = response_result.scalar_one_or_none()

            # Get streak data
            streak_result = await db.execute(
                select(UserStreakData).where(UserStreakData.uid == user_id)
            )
            streak_data = streak_result.scalar_one_or_none()

            # Get recent weekly check-ins
            checkin_result = await db.execute(
                select(WeeklyCheckIn)
                .where(
                    WeeklyCheckIn.uid == user_id,
                    WeeklyCheckIn.is_complete == True,
                )
                .order_by(WeeklyCheckIn.completed_at.desc())
                .limit(4)
            )
            recent_checkins = checkin_result.scalars().all()

            # Get recent daily reviews
            review_result = await db.execute(
                select(ActionPlanDailyReview)
                .where(ActionPlanDailyReview.uid == user_id)
                .order_by(ActionPlanDailyReview.review_date.desc())
                .limit(7)
            )
            recent_reviews = review_result.scalars().all()

            # Get recent care plan check-ins (daily threads)
            care_plan_result = await db.execute(
                select(CarePlanCheckInThread)
                .where(CarePlanCheckInThread.uid == user_id)
                .order_by(CarePlanCheckInThread.local_date.desc(), CarePlanCheckInThread.updated_at.desc())
                .limit(7)
            )
            recent_care_plan_threads = care_plan_result.scalars().all()

            # Get recent symptom check-ins
            symptom_checkin_result = await db.execute(
                select(SymptomCheckInThread)
                .where(SymptomCheckInThread.uid == user_id)
                .order_by(SymptomCheckInThread.local_date.desc(), SymptomCheckInThread.updated_at.desc())
                .limit(7)
            )
            recent_symptom_threads = symptom_checkin_result.scalars().all()

            # Anti-repetition (FIXED N+1): Fetch plan items with a single JOIN query
            recent_plan_items_result = await db.execute(
                select(ActionPlanItem.title)
                .join(ActionPlan, ActionPlanItem.plan_id == ActionPlan.id)
                .where(
                    and_(
                        ActionPlan.uid == user_id,
                        ActionPlan.plan_date >= fourteen_days_ago,
                    )
                )
                .order_by(ActionPlan.plan_date.desc())
            )
            titles = [t for (t,) in recent_plan_items_result.all() if t]
            seen_titles = set()
            recently_recommended = []
            for t in titles:
                if t not in seen_titles:
                    seen_titles.add(t)
                    recently_recommended.append(t)

            # Get recent feedback for memory (last 30 days)
            feedback_result = await db.execute(
                select(ActionPlanFeedback)
                .where(ActionPlanFeedback.uid == user_id)
                .order_by(ActionPlanFeedback.created_at.desc())
                .limit(50)
            )
            recent_feedback = feedback_result.scalars().all()
            
            # Extract streak info
            current_streak = streak_data.current_streak if streak_data else 0
            longest_streak = streak_data.longest_streak if streak_data else 0
            
            logger.info(f"[CONTEXT] UserResponse found: {user_response is not None}")
            logger.info(f"[CONTEXT] Streak data: current={current_streak}, longest={longest_streak}")
            logger.info(f"[CONTEXT] Found {len(recent_checkins)} weekly check-ins")
            logger.info(f"[CONTEXT] Found {len(recent_reviews)} daily reviews")
            logger.info(f"[CONTEXT] Found {len(recent_care_plan_threads)} care plan threads")
            logger.info(f"[CONTEXT] Found {len(recent_symptom_threads)} symptom threads")
            logger.info(f"[ANTI-REPETITION] Found {len(recently_recommended)} items to avoid")
            
            # Format insights
            weekly_checkin_insights = self._format_weekly_checkin_insights(recent_checkins)
            daily_review_insights = self._format_daily_reviews(recent_reviews)
            care_plan_checkin_insights = self._format_care_plan_checkin_insights(recent_care_plan_threads)
            symptom_checkin_insights = self._format_symptom_checkin_insights(recent_symptom_threads)
            
            # Format as string for prompt
            recently_recommended_str = ", ".join(recently_recommended[:30]) if recently_recommended else "None (this is the users first plan)"
            
            # Extract user's name from profile for personalization
            user_name = profile.name if profile and profile.name else None
            # Try to get first name only (more personal)
            if user_name:
                user_name = user_name.split()[0] if user_name else None
            
            # Load base context with defaults
            context = {
                "user_id": user_id,
                "user_name": user_name or "there",  # "Hey there" if no name
                "primary_hormone": "cortisol",
                "secondary_hormone": "progesterone",
                "cycle_day": 1,
                "cycle_phase": "follicular",
                "age": "not specified",
                "top_concern": "general wellness",
                "diagnosed_conditions": [],
                "period_concerns": "none specified",
                "body_concerns": "none specified",
                "skin_hair_concerns": "none specified",
                "mental_health_concerns": "none specified",
                "family_history": "none specified",
                "birth_control": "none",
                "lifestyle_focus": ["eat", "move", "pause"],
                "diet_preference": "no preference specified",
                "food_allergies": "none specified",
                "stress_level": "moderate",
                "sleep_duration": "7-8 hours",
                "workout_intensity": "moderate",
                "feedback_summary": "No summary yet",
                "feedback_memory": "No previous feedback",
                "chatbot_memory": {},
                "chatbot_context": "No additional context",
                "weekly_checkin_insights": weekly_checkin_insights,
                "daily_review_insights": daily_review_insights,
                "care_plan_checkin_insights": care_plan_checkin_insights,
                "symptom_checkin_insights": symptom_checkin_insights,
                "current_streak": current_streak,
                "longest_streak": longest_streak,
                "recently_recommended": recently_recommended_str,
                "allowed_symptoms": "general wellness support",
                "allowed_conditions": "None diagnosed"
            }

            if not user_response:
                logger.info(f"No UserResponse for {user_id}, using defaults")
                # Update focus if available in profile
                if profile.lifestyle_focus:
                    context["lifestyle_focus"] = profile.lifestyle_focus
                return context
            
            # Calculate cycle day and phase
            cycle_day, cycle_phase = self._calculate_cycle_info(
                user_response.last_period_date_utc,
                user_response.cycle_length
            )
            
            # Get lifestyle focus (what user prefers: eat/move/pause)
            lifestyle_focus = profile.lifestyle_focus or user_response.lifestyle_focus or ["eat", "move", "pause"]
            
            # Get primary and secondary hormones
            primary_hormone = user_response.primary_hormone or "cortisol"
            secondary_hormones = user_response.secondary_hormones or []
            secondary_hormone = secondary_hormones[0] if secondary_hormones else "progesterone"
            
            # Automatically summarize feedback if needed (>100 records)
            feedback_summary = await self._summarize_feedback_if_needed(user_id, profile, db)
            
            # Format feedback memory for GPT (enhanced with patterns)
            feedback_memory = self._format_feedback_memory(recent_feedback)
            
            # Extract chatbot memory preferences
            chatbot_memory = profile.chatbot_memory or {}
            chatbot_context = self._format_chatbot_context(chatbot_memory)
            
            # ===================================================================
            # EXTRACT ALL PREFERENCES FROM CHATBOT MEMORY (Reward-gated features)
            # ===================================================================
            
            # Diet preference (7-day reward)
            diet_preference = chatbot_memory.get("diet_preference", "no preference specified")
            
            # Food allergies (8-day reward)
            food_allergies = chatbot_memory.get("food_allergies", [])
            if isinstance(food_allergies, list):
                food_allergies = ", ".join(food_allergies) if food_allergies else "none specified"
            
            # Cuisine preference (12-day reward)
            cuisine_preference = chatbot_memory.get("cuisine_preference", [])
            if isinstance(cuisine_preference, list):
                cuisine_preference = ", ".join(cuisine_preference) if cuisine_preference else "no preference"
            
            # Dine out frequency (14-day reward)
            dine_out_frequency = chatbot_memory.get("dine_out_frequency", "not specified")
            
            # Cultural background (18-day reward)
            cultural_background = chatbot_memory.get("cultural_background", "not specified")
            
            # Body metrics (18-day reward)
            body_metrics = chatbot_memory.get("body_metrics", {})
            bmi_info = "not specified"
            if body_metrics:
                if body_metrics.get("bmi"):
                    bmi_info = f"BMI: {body_metrics.get('bmi')} ({body_metrics.get('bmi_category', 'N/A')})"
                if body_metrics.get("waist_height_ratio"):
                    bmi_info += f", Waist-to-Height: {body_metrics.get('waist_height_ratio')}"
            
            # Cravings (18-day reward)
            cravings = chatbot_memory.get("cravings", [])
            if isinstance(cravings, list):
                cravings = ", ".join(cravings) if cravings else "none specified"
            
            # Update context with real data
            context.update({
                "primary_hormone": primary_hormone,
                "secondary_hormone": secondary_hormone,
                "cycle_day": cycle_day,
                "cycle_phase": cycle_phase,
                "age": user_response.age or "not specified",
                "top_concern": user_response.top_concern or "general wellness",
                "diagnosed_conditions": (
                    [user_response.diagnosed_conditions] if isinstance(user_response.diagnosed_conditions, str)
                    else (user_response.diagnosed_conditions or [])
                ),
                "period_concerns": self._format_concerns(user_response.period_concerns),
                "body_concerns": self._format_concerns(user_response.body_concerns),
                "skin_hair_concerns": self._format_concerns(user_response.skin_hair_concerns),
                "mental_health_concerns": self._format_concerns(user_response.mental_health_concerns),
                "family_history": ", ".join(user_response.family_history) if user_response.family_history else "none specified",
                "birth_control": ", ".join(user_response.birth_control) if user_response.birth_control else "none",
                "lifestyle_focus": lifestyle_focus,
                # Core preferences
                "diet_preference": diet_preference,
                "food_allergies": food_allergies,
                # Enhanced personalization (reward-gated)
                "cuisine_preference": cuisine_preference,
                "dine_out_frequency": dine_out_frequency,
                "cultural_background": cultural_background,
                "body_metrics": bmi_info,
                "cravings": cravings,
                # Existing fields
                "stress_level": user_response.stress_level or "moderate",
                "sleep_duration": user_response.sleep_duration or "7-8 hours",
                "workout_intensity": user_response.workout_intensity or "moderate",
                "feedback_summary": feedback_summary or "No summary yet",
                "feedback_memory": feedback_memory,
                "chatbot_memory": chatbot_memory,
                "chatbot_context": chatbot_context,
                # Weekly check-in insights for personalization
                "weekly_checkin_insights": weekly_checkin_insights,
                # Streak data
                "current_streak": current_streak,
                "longest_streak": longest_streak,
                # Anti-repetition: Recently recommended items (last 14 days)
                "recently_recommended": recently_recommended_str
            })
            
            # ===================================================================
            # BUILD ALLOWED SYMPTOMS/CONDITIONS WHITELIST (Anti-hallucination)
            # ===================================================================
            logger.info(f"[WHITELIST] Building allowed symptoms/conditions whitelist for user {user_id}")
            # Extract all symptoms user actually has from their health profile
            allowed_symptoms_set = set()
            
            # Add top concern
            if user_response.top_concern and user_response.top_concern != "general wellness":
                allowed_symptoms_set.add(user_response.top_concern)
                logger.debug(f"[WHITELIST] Added top_concern: '{user_response.top_concern}'")
            
            # Extract from period concerns
            if user_response.period_concerns:
                if isinstance(user_response.period_concerns, dict):
                    period_symptoms = [k for k, v in user_response.period_concerns.items() if v]
                    allowed_symptoms_set.update(period_symptoms)
                    logger.debug(f"[WHITELIST] Added period_concerns (dict): {period_symptoms}")
                elif isinstance(user_response.period_concerns, list):
                    allowed_symptoms_set.update(user_response.period_concerns)
                    logger.debug(f"[WHITELIST] Added period_concerns (list): {user_response.period_concerns}")
            
            # Extract from body concerns
            if user_response.body_concerns:
                if isinstance(user_response.body_concerns, dict):
                    body_symptoms = [k for k, v in user_response.body_concerns.items() if v]
                    allowed_symptoms_set.update(body_symptoms)
                    logger.debug(f"[WHITELIST] Added body_concerns (dict): {body_symptoms}")
                elif isinstance(user_response.body_concerns, list):
                    allowed_symptoms_set.update(user_response.body_concerns)
                    logger.debug(f"[WHITELIST] Added body_concerns (list): {user_response.body_concerns}")
            
            # Extract from skin/hair concerns
            if user_response.skin_hair_concerns:
                if isinstance(user_response.skin_hair_concerns, dict):
                    skin_symptoms = [k for k, v in user_response.skin_hair_concerns.items() if v]
                    allowed_symptoms_set.update(skin_symptoms)
                    logger.debug(f"[WHITELIST] Added skin_hair_concerns (dict): {skin_symptoms}")
                elif isinstance(user_response.skin_hair_concerns, list):
                    allowed_symptoms_set.update(user_response.skin_hair_concerns)
                    logger.debug(f"[WHITELIST] Added skin_hair_concerns (list): {user_response.skin_hair_concerns}")
            
            # Extract from mental health concerns
            if user_response.mental_health_concerns:
                if isinstance(user_response.mental_health_concerns, dict):
                    mental_symptoms = [k for k, v in user_response.mental_health_concerns.items() if v]
                    allowed_symptoms_set.update(mental_symptoms)
                    logger.debug(f"[WHITELIST] Added mental_health_concerns (dict): {mental_symptoms}")
                elif isinstance(user_response.mental_health_concerns, list):
                    allowed_symptoms_set.update(user_response.mental_health_concerns)
                    logger.debug(f"[WHITELIST] Added mental_health_concerns (list): {user_response.mental_health_concerns}")
            
            # Extract from weekly check-in symptoms (most recent actual symptoms)
            if recent_checkins:
                latest_checkin = recent_checkins[0]  # Most recent
                logger.debug(f"[WHITELIST] Checking latest weekly check-in (completed: {latest_checkin.completed_at})")
                # Safely access symptoms_this_week as it might not exist on the model
                symptoms_this_week = getattr(latest_checkin, 'symptoms_this_week', None)
                if symptoms_this_week:
                    if isinstance(symptoms_this_week, list):
                        allowed_symptoms_set.update(symptoms_this_week)
                        logger.debug(f"[WHITELIST] Added weekly_checkin symptoms: {symptoms_this_week}")
            else:
                logger.debug(f"[WHITELIST] No recent weekly check-ins found")
            
            # Build allowed conditions list
            allowed_conditions_list = []
            if user_response.diagnosed_conditions:
                if isinstance(user_response.diagnosed_conditions, list):
                    allowed_conditions_list = user_response.diagnosed_conditions
                    logger.debug(f"[WHITELIST] Added diagnosed_conditions (list): {allowed_conditions_list}")
                elif isinstance(user_response.diagnosed_conditions, str):
                    allowed_conditions_list = [user_response.diagnosed_conditions]
                    logger.debug(f"[WHITELIST] Added diagnosed_conditions (str): {allowed_conditions_list}")
            else:
                logger.debug(f"[WHITELIST] No diagnosed conditions found")
            
            # Format for prompt
            allowed_symptoms_str = ", ".join(sorted(allowed_symptoms_set)) if allowed_symptoms_set else "general wellness support"
            allowed_conditions_str = ", ".join(allowed_conditions_list) if allowed_conditions_list else "None diagnosed"
            
            # Add to context
            context["allowed_symptoms"] = allowed_symptoms_str
            context["allowed_conditions"] = allowed_conditions_str
            
            # Final summary log
            logger.info(f"[WHITELIST] ===========================================================")
            logger.info(f"[WHITELIST] FINAL ALLOWED SYMPTOMS ({len(allowed_symptoms_set)} total): {allowed_symptoms_str}")
            logger.info(f"[WHITELIST] FINAL ALLOWED CONDITIONS ({len(allowed_conditions_list)} total): {allowed_conditions_str}")
            logger.info(f"[WHITELIST] ===========================================================")
            
            # ===================================================================
            # NEW: LOAD UNIFIED CROSS-CHATBOT MEMORY
            # This gives us access to ALL conversations across ALL chatbots,
            # learned preferences, and episodic memory from past interactions.
            # ===================================================================
            try:
                unified_ctx = await get_unified_context(user_id, "action_plan_generator")
                formatted_unified = format_context_for_prompt(unified_ctx)
                
                context["unified_memory"] = unified_ctx
                context["unified_memory_formatted"] = formatted_unified
                
                logger.info(f"[UNIFIED_MEMORY] Loaded cross-chatbot context for user {user_id}")
                logger.info(f"[UNIFIED_MEMORY] Keys: {list(unified_ctx.keys()) if unified_ctx else 'None'}")
            except Exception as mem_error:
                logger.warning(f"[UNIFIED_MEMORY] Could not load unified memory: {mem_error}")
                context["unified_memory"] = {}
                context["unified_memory_formatted"] = ""
            
            return context
            
        except Exception as e:
            logger.error(f"Error loading user context: {e}")
            return None
    
    def _format_concerns(self, concerns: Any) -> str:
        """Format concern data (JSONB) to string."""
        if not concerns:
            return "none specified"
        if isinstance(concerns, dict):
            return ", ".join([f"{k}: {v}" for k, v in concerns.items() if v])
        if isinstance(concerns, list):
            return ", ".join(concerns)
        return str(concerns)
    
    def _format_chatbot_context(self, chatbot_memory: Dict[str, Any]) -> str:
        """Format chatbot memory into context for GPT."""
        if not chatbot_memory:
            return "No additional context from conversations."
        
        context_parts = []
        
        # Extract relevant preferences discussed in chat
        if chatbot_memory.get("food_preferences"):
            context_parts.append(f"Food preferences discussed: {chatbot_memory['food_preferences']}")
        if chatbot_memory.get("exercise_preferences"):
            context_parts.append(f"Exercise preferences: {chatbot_memory['exercise_preferences']}")
        if chatbot_memory.get("schedule_constraints"):
            context_parts.append(f"Schedule constraints: {chatbot_memory['schedule_constraints']}")
        if chatbot_memory.get("dislikes"):
            context_parts.append(f"Things user dislikes: {chatbot_memory['dislikes']}")
        if chatbot_memory.get("goals"):
            context_parts.append(f"Users goals: {chatbot_memory['goals']}")
        if chatbot_memory.get("notes"):
            context_parts.append(f"Other notes: {chatbot_memory['notes']}")
        
        return "\n".join(context_parts) if context_parts else "No additional context from conversations."
    
    def _format_weekly_checkin_insights(self, recent_checkins: List[Any]) -> str:
        """Format weekly check-in summaries for action plan personalization."""
        if not recent_checkins:
            return "No weekly check-in data yet"
        
        insights = []
        for i, checkin in enumerate(recent_checkins):
            week_label = "This week" if i == 0 else f"{i} week(s) ago"
            parts = []
            
            # Add severity info
            if checkin.concern_severity:
                severity = checkin.concern_severity
                concern = checkin.top_concern or "symptoms"
                if severity <= 3:
                    parts.append(f"{concern}: minimal (severity {severity}/9)")
                elif severity <= 6:
                    parts.append(f"{concern}: moderate (severity {severity}/9)")
                else:
                    parts.append(f"{concern}: significant (severity {severity}/9)")
            
            # Use actionable_insights if available (new structured format)
            if hasattr(checkin, 'actionable_insights') and checkin.actionable_insights:
                ai_insights = checkin.actionable_insights
                
                if ai_insights.get("triggers_identified"):
                    triggers = ", ".join(ai_insights["triggers_identified"][:3])
                    parts.append(f"Triggers: {triggers}")
                
                if ai_insights.get("relief_factors_identified"):
                    helpers = ", ".join(ai_insights["relief_factors_identified"][:3])
                    parts.append(f"Helped: {helpers}")
                
                if ai_insights.get("severity_trend"):
                    parts.append(f"Trend: {ai_insights['severity_trend']}")
                
                if ai_insights.get("suggested_additions"):
                    additions = ", ".join(ai_insights["suggested_additions"][:2])
                    parts.append(f"Suggested: {additions}")
                
                if ai_insights.get("key_insight"):
                    parts.append(f"Key: {ai_insights['key_insight']}")
            else:
                # Fallback to old format
                if checkin.factors_negative:
                    triggers = ", ".join(checkin.factors_negative[:3])
                    parts.append(f"Triggers: {triggers}")
                
                if checkin.factors_positive:
                    helpers = ", ".join(checkin.factors_positive[:3])
                    parts.append(f"Helped: {helpers}")
                
                if checkin.conversation_summary:
                    parts.append(f"Summary: {checkin.conversation_summary}")
            
            if parts:
                insights.append(f"[{week_label}] " + " | ".join(parts))
        
        return "\n".join(insights) if insights else "No weekly check-in data yet"

    def _format_care_plan_checkin_insights(self, threads: List[Any]) -> str:
        """Format daily care plan check-in threads into a compact prompt block."""
        if not threads:
            return "No care plan check-in data yet"

        lines: List[str] = []
        for thread in threads:
            try:
                day = thread.local_date.strftime("%Y-%m-%d") if getattr(thread, "local_date", None) else "unknown-date"
                parts: List[str] = []

                if getattr(thread, "actionable_insights", None):
                    ai = thread.actionable_insights or {}
                    if ai.get("wins"):
                        parts.append(f"Wins: {', '.join(ai['wins'][:3])}")
                    if ai.get("blockers"):
                        parts.append(f"Blockers: {', '.join(ai['blockers'][:3])}")
                    if ai.get("actions_to_skip"):
                        parts.append(f"Skip: {', '.join(ai['actions_to_skip'][:3])}")
                    if ai.get("plan_changes_requested"):
                        parts.append(f"Change: {', '.join(ai['plan_changes_requested'][:2])}")
                    if ai.get("alternate_suggestions_requested") is True:
                        parts.append("Asked for alternates")
                    if ai.get("key_takeaway"):
                        parts.append(f"Key: {ai['key_takeaway']}")

                # If we have a rolling summary, prefer it as the main signal.
                summary = (getattr(thread, "rolling_summary", None) or "").strip()
                if summary:
                    parts.append(f"Summary: {summary}")
                else:
                    # Fallback: include a couple recent user messages
                    raw = getattr(thread, "raw_messages", None) or []
                    recent_user = [m.get("content") for m in raw[::-1] if m.get("role") == "user" and m.get("content")][:2]
                    if recent_user:
                        parts.append("Recent: " + " | ".join(recent_user[::-1]))

                if parts:
                    lines.append(f"[{day}] " + " | ".join(parts))
            except Exception:
                continue

        return "\n".join(lines) if lines else "No care plan check-in data yet"

    def _format_symptom_checkin_insights(self, threads: List[Any]) -> str:
        """Format daily symptom check-in threads into a compact prompt block."""
        if not threads:
            return "No symptom check-in data yet"

        lines: List[str] = []
        for thread in threads:
            try:
                day = thread.local_date.strftime("%Y-%m-%d") if getattr(thread, "local_date", None) else "unknown-date"
                parts: List[str] = []

                ai = getattr(thread, "actionable_insights", None) or {}
                if ai.get("progress"):
                    parts.append(f"Progress: {ai['progress']}")
                if ai.get("symptoms_mentioned"):
                    parts.append(f"Symptoms: {', '.join(ai['symptoms_mentioned'][:3])}")
                if ai.get("severity_rating"):
                    parts.append(f"Severity: {ai['severity_rating']}/9")
                if ai.get("wins"):
                    parts.append(f"Wins: {', '.join(ai['wins'][:3])}")
                if ai.get("difficulties"):
                    parts.append(f"Difficulties: {', '.join(ai['difficulties'][:3])}")
                if ai.get("triggers_identified"):
                    parts.append(f"Triggers: {', '.join(ai['triggers_identified'][:3])}")
                if ai.get("relief_factors_identified"):
                    parts.append(f"Helped: {', '.join(ai['relief_factors_identified'][:3])}")
                if ai.get("key_takeaway"):
                    parts.append(f"Key: {ai['key_takeaway']}")

                summary = (getattr(thread, "rolling_summary", None) or "").strip()
                if summary:
                    parts.append(f"Summary: {summary}")

                if parts:
                    lines.append(f"[{day}] " + " | ".join(parts))
            except Exception:
                continue

        return "\n".join(lines) if lines else "No symptom check-in data yet"

    def _format_daily_reviews(self, recent_reviews: List[Any]) -> str:
        """Format daily review data for action plan personalization."""
        if not recent_reviews:
            return "No daily review data yet"
        
        insights = []
        for review in recent_reviews:
            date_str = review.review_date.strftime("%Y-%m-%d")
            parts = []
            
            # Check items review data
            if review.items_review_data:
                skipped_count = 0
                replaced_items = []
                completed_count = 0
                
                for item in review.items_review_data:
                    status = item.get("status")
                    if status == "skipped":
                        skipped_count += 1
                    elif status == "replaced":
                        replacement = item.get("replacement_text")
                        if replacement:
                            replaced_items.append(replacement)
                    elif status == "was_completed":
                        completed_count += 1
                
                if skipped_count > 0:
                    parts.append(f"Skipped {skipped_count} items")
                if replaced_items:
                    parts.append(f"Replaced items with: {', '.join(replaced_items)}")
                if completed_count > 0:
                    parts.append(f"Completed {completed_count} items")
            
            # Check streak action
            if review.streak_action:
                parts.append(f"Streak: {review.streak_action}")
                
            if parts:
                insights.append(f"[{date_str}] " + " | ".join(parts))
                
        return "\n".join(insights) if insights else "No daily review data yet"
    
    def _calculate_cycle_info(
        self,
        last_period_date: Optional[datetime],
        cycle_length_str: Optional[str]
    ) -> Tuple[Optional[int], Optional[str]]:
        """Calculate current cycle day and phase."""
        if not last_period_date:
            return (None, None)
        
        # Parse cycle length
        try:
            if cycle_length_str and cycle_length_str.isdigit():
                cycle_length = int(cycle_length_str)
            elif cycle_length_str and "-" in cycle_length_str:
                # Handle ranges like "26-30"
                parts = cycle_length_str.split("-")
                cycle_length = (int(parts[0]) + int(parts[1])) // 2
            else:
                cycle_length = 28  # Default
        except (ValueError, TypeError, IndexError):
            cycle_length = 28
        
        # Calculate days since last period
        now = datetime.utcnow()
        if last_period_date.tzinfo is not None:
            last_period_date = last_period_date.replace(tzinfo=None)
            
        days_since = (now - last_period_date).days
        cycle_day = (days_since % cycle_length) + 1
        
        # Determine phase
        if cycle_day <= 5:
            phase = "menstrual"
        elif cycle_day <= 13:
            phase = "follicular"
        elif cycle_day <= 16:
            phase = "ovulation"
        else:
            phase = "luteal"
        
        return (cycle_day, phase)
    
    def _format_feedback_memory(self, feedback_list: List[Any]) -> str:
        """Format recent feedback for GPT context with pattern analysis and text insights."""
        if not feedback_list:
            return "No previous feedback available - this is likely a new user."
        
        liked = []
        disliked = []
        skipped = []
        completed = []
        loved = []  # NEW: From ActionDetailScreen
        not_for_me = []  # NEW: From ActionDetailScreen
        text_insights = []  # NEW: Users written feedback
        
        # Analyze patterns
        liked_categories = {}
        disliked_categories = {}
        liked_hormones = {}
        disliked_hormones = {}
        
        for fb in feedback_list:
            category = fb.action_category or "unknown"
            hormone = fb.target_hormone or "unknown"
            
            # Collect text feedback separately
            if hasattr(fb, 'feedback_text') and fb.feedback_text:
                source = getattr(fb, 'feedback_source', 'unknown')
                text_insights.append(f"- {fb.action_title}: \"{fb.feedback_text}\" (from {source})")
            
            if fb.feedback_type == "like":
                liked.append(f"- {category}: {fb.action_title}")
                liked_categories[category] = liked_categories.get(category, 0) + 1
                liked_hormones[hormone] = liked_hormones.get(hormone, 0) + 1
            elif fb.feedback_type == "loved":  # NEW
                loved.append(f"- {category}: {fb.action_title}")
                liked_categories[category] = liked_categories.get(category, 0) + 2  # Weight loved more
                liked_hormones[hormone] = liked_hormones.get(hormone, 0) + 2
            elif fb.feedback_type == "dislike":
                # Include both replacement_reason (what they did instead) and replacement_category (why)
                reason = fb.replacement_reason or "unspecified"
                category_reason = getattr(fb, 'replacement_category', None)
                if category_reason:
                    disliked.append(f"- {category}: {fb.action_title} (why: {category_reason}, did instead: {reason})")
                else:
                    disliked.append(f"- {category}: {fb.action_title} (did instead: {reason})")
                disliked_categories[category] = disliked_categories.get(category, 0) + 1
                disliked_hormones[hormone] = disliked_hormones.get(hormone, 0) + 1
            elif fb.feedback_type == "not_for_me":  # From daily review "replaced" status
                # Include BOTH: why they replaced (category) AND what they did instead (reason)
                replaced_with = getattr(fb, 'replacement_reason', None) or "unspecified activity"
                why_replaced = getattr(fb, 'replacement_category', None) or "unspecified reason"
                not_for_me.append(f"- {category}: {fb.action_title} (why replaced: {why_replaced}, did instead: {replaced_with})")
                disliked_categories[category] = disliked_categories.get(category, 0) + 2  # Weight stronger
                disliked_hormones[hormone] = disliked_hormones.get(hormone, 0) + 2
            elif fb.feedback_type in ["skip", "skipped"]:
                skipped.append(f"- {category}: {fb.action_title}")
            
            if fb.feedback_type == "completed":
                completed.append(f"- {category}: {fb.action_title}")
        
        memory_parts = []
        
        # Summary patterns
        if liked_categories or disliked_categories:
            patterns = []
            if liked_categories:
                top_liked = max(liked_categories.items(), key=lambda x: x[1])
                patterns.append(f"User tends to LIKE {top_liked[0]} actions ({top_liked[1]} positive reactions)")
            if disliked_categories:
                top_disliked = max(disliked_categories.items(), key=lambda x: x[1])
                patterns.append(f"User tends to DISLIKE {top_disliked[0]} actions ({top_disliked[1]} negative reactions)")
            memory_parts.append("PATTERNS DETECTED:\n" + "\n".join(patterns))
        
        # NEW: Text insights from users written feedback
        if text_insights:
            memory_parts.append(f"USERS WRITTEN FEEDBACK (VERY IMPORTANT):\n" + "\n".join(text_insights[:5]))
        
        if loved:
            memory_parts.append(f"LOVED actions (PRIORITIZE similar ones):\n" + "\n".join(loved[:5]))
        
        if liked:
            memory_parts.append(f"LIKED actions (create SIMILAR ones):\n" + "\n".join(liked[:7]))
        
        if not_for_me:
            memory_parts.append(f"STRONGLY DISLIKED actions (NEVER suggest similar):\n" + "\n".join(not_for_me[:5]))
        
        if disliked:
            memory_parts.append(f"DISLIKED actions (AVOID similar types):\n" + "\n".join(disliked[:7]))
        
        if skipped:
            memory_parts.append(f"SKIPPED actions (user didn't engage):\n" + "\n".join(skipped[:5]))
        
        if completed:
            memory_parts.append(f"COMPLETED actions (user followed through):\n" + "\n".join(completed[:5]))
        
        return "\n\n".join(memory_parts) if memory_parts else "No previous feedback available."

    async def _summarize_feedback_if_needed(
        self,
        user_id: str,
        profile: Any,
        db: AsyncSession
    ) -> Optional[str]:
        """
        Automatically summarize feedback when count exceeds threshold (100).
        Returns summary if exists or just created, None otherwise.
        """
        from app.core.database import ActionPlanFeedback, UserProfile
        from sqlalchemy import select, func, delete
        
        try:
            # Count current feedback
            count_result = await db.execute(
                select(func.count()).select_from(ActionPlanFeedback).where(
                    ActionPlanFeedback.uid == user_id
                )
            )
            current_count = count_result.scalar() or 0
            
            logger.info(f" Feedback count for user {user_id}: {current_count}, threshold: 100")
            
            # Return existing summary if count hasn't grown much
            # Use safe default for feedback_last_count to avoid None + 20 error
            last_count = getattr(profile, 'feedback_last_count', 0) or 0
            if getattr(profile, 'feedback_summary', None) and current_count < (last_count + 20):
                logger.info(f" Using existing feedback summary (last updated: {getattr(profile, 'feedback_summary_updated_at', 'unknown')})")
                return getattr(profile, 'feedback_summary', None)
            
            # If count > 100, summarize
            if current_count >= 100:
                logger.info(f" Generating feedback summary with GPT for {current_count} feedback records")
                
                # Fetch ALL feedback
                all_feedback_result = await db.execute(
                    select(ActionPlanFeedback).where(
                        ActionPlanFeedback.uid == user_id
                    ).order_by(ActionPlanFeedback.created_at.desc())
                )
                all_feedback = all_feedback_result.scalars().all()
                
                # Format for summarization
                feedback_text = self._format_feedback_memory(all_feedback)
                
                # Ask GPT to summarize
                summary_prompt = f"""Analyze this users action plan feedback history and create a concise summary of their preferences.

FEEDBACK HISTORY:
{feedback_text}

Create a summary focusing on:
1. Category preferences (food/movement/mindfulness) - what they tend to LIKE vs DISLIKE
2. Specific patterns to AVOID (e.g., "User dislikes high-intensity workouts", "Avoids raw vegetables")
3. Specific patterns to CREATE MORE (e.g., "Loves seed-based foods", "Prefers morning mindfulness")
4. Hormone-specific preferences if any patterns emerge

Keep it concise (max 200 words) and actionable for generating future action plans.
Format as bullet points."""

                # Try OpenAI first, fallback to Groq
                openai_error = None
                summary = None
                
                if self.openai_api_key:
                    try:
                        payload = self.build_openai_payload(
                            model="gpt-5-mini",
                            messages=[
                                {"role": "system", "content": "You are a wellness AI analyzing user feedback patterns."},
                                {"role": "user", "content": summary_prompt}
                            ],
                            max_tokens=500,
                            temperature=0.3
                        )
                        response = await self.client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.openai_api_key}",
                                "Content-Type": "application/json"
                            },
                            json=payload
                        )
                        
                        if response.status_code != 200:
                            openai_error = f"OpenAI returned {response.status_code}"
                            logger.warning(f" {openai_error}")
                        else:
                            data = response.json()
                            summary = data["choices"][0]["message"]["content"].strip()
                            logger.info(f" Feedback summary generated via OpenAI, length: {len(summary)} chars")
                    except Exception as e:
                        openai_error = str(e)
                        logger.warning(f" OpenAI exception: {openai_error[:200]}")
                else:
                    openai_error = "No OpenAI API key"
                
                # Groq fallback
                if openai_error and GROQ_API_KEY:
                    try:
                        logger.info(f" Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                        response = await self.client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {GROQ_API_KEY}",
                                "Content-Type": "application/json"
                            },
                            json={
                                "model": GROQ_FALLBACK_MODEL,
                                "messages": [
                                    {"role": "system", "content": "You are a wellness AI analyzing user feedback patterns."},
                                    {"role": "user", "content": summary_prompt}
                                ],
                                "temperature": 0.3,
                                "max_tokens": 500
                            },
                            timeout=90.0
                        )
                        
                        if response.status_code != 200:
                            raise Exception(f"Groq returned {response.status_code}")
                        
                        data = response.json()
                        summary = data["choices"][0]["message"]["content"].strip()
                        logger.info(f" Feedback summary generated via Groq fallback, length: {len(summary)} chars")
                    except Exception as e:
                        logger.error(f" Groq fallback also failed: {e}")
                        return None  # Return None, don't crash
                elif openai_error:
                    logger.error(f" OpenAI failed and no Groq fallback: {openai_error}")
                    return None
                
                if summary:
                    
                    # Save summary to profile
                    profile.feedback_summary = summary
                    profile.feedback_summary_updated_at = datetime.utcnow()
                    profile.feedback_last_count = current_count
                    await db.commit()
                    
                    logger.info(f" Feedback summary saved to profile")
                    
                    # Delete old feedback (keep last 20)
                    if current_count > 20:
                        # Get IDs of items to keep
                        keep_result = await db.execute(
                            select(ActionPlanFeedback.id).where(
                                ActionPlanFeedback.uid == user_id
                            ).order_by(ActionPlanFeedback.created_at.desc()).limit(20)
                        )
                        keep_ids = [row[0] for row in keep_result.all()]
                        
                        # Delete everything except those (Fix: use and_() for multiple conditions)
                        delete_result = await db.execute(
                            delete(ActionPlanFeedback).where(
                                and_(
                                    ActionPlanFeedback.uid == user_id,
                                    ActionPlanFeedback.id.notin_(keep_ids)
                                )
                            )
                        )
                        await db.commit()
                        
                        deleted_count = delete_result.rowcount
                        logger.info(f"  Deleted {deleted_count} old feedback records, kept last 20")
                    
                    return summary
            
            # Less than 100 feedback - no summary needed yet
            return getattr(profile, 'feedback_summary', None)
            
        except Exception as e:
            logger.error(f" Error in feedback summarization: {e}")
            return None

    
    def _get_category_guidance(self, lifestyle_focus: List[str]) -> str:
        """
        Generate STRICT category distribution guidance based on lifestyle focus.
        
        Distribution Matrix (Total = 4):
        +-----------------+-------+----------+-------------+
        | Selection       | Food  | Movement | Mindfulness |
        +-----------------+-------+----------+-------------+
        | Eat only        |   2   |    1     |      1      |
        | Move only       |   1   |    2     |      1      |
        | Pause only      |   1   |    1     |      2      |
        | Eat + Move      |   2   |    2     |      0      |
        | Eat + Pause     |   2   |    0     |      2      |
        | Move + Pause    |   0   |    2     |      2      |
        | All three/None  |   2   |    1     |      1      |
        +-----------------+-------+----------+-------------+
        """
        focus = [f.lower() for f in (lifestyle_focus or [])]
        num_selected = len(focus)
        
        has_eat = 'eat' in focus
        has_move = 'move' in focus
        has_pause = 'pause' in focus
        
        if num_selected == 1:
            if has_eat:
                return "Food focus (STRICT): Generate 2 Food + 1 Movement + 1 Mindfulness = 4 total"
            elif has_move:
                return "Movement focus (STRICT): Generate 1 Food + 2 Movement + 1 Mindfulness = 4 total"
            elif has_pause:
                return "Mindfulness focus (STRICT): Generate 1 Food + 1 Movement + 2 Mindfulness = 4 total"
        elif num_selected == 2:
            if has_eat and has_move:
                return "Food and movement focus (STRICT): Generate 2 Food + 2 Movement + 0 Mindfulness = 4 total (NO mindfulness!)"
            elif has_eat and has_pause:
                return "Food and mindfulness focus (STRICT): Generate 2 Food + 0 Movement + 2 Mindfulness = 4 total (NO movement!)"
            elif has_move and has_pause:
                return "Movement and mindfulness focus (STRICT): Generate 0 Food + 2 Movement + 2 Mindfulness = 4 total (NO food!)"
        
        # Default: All three or none selected
        return "Balanced (STRICT): Generate 2 Food + 1 Movement + 1 Mindfulness = 4 total"
    
    async def _generate_actions_via_gpt(
        self,
        user_context: Dict[str, Any],
        db: Optional[AsyncSession] = None,
        model_override: Optional[str] = None
    ) -> Tuple[Optional[List[Dict]], float]:
        """Generate actions using GPT with tool calling."""
        logger.info(f"[GPT] ==========================================================================")
        logger.info(f"[GPT] Starting _generate_actions_via_gpt")
        logger.info(f"[GPT]   model_override: {model_override or 'None (using default)'}")
        logger.info(f"[GPT]   user_id: {user_context.get('user_id')}")
        logger.info(f"[GPT] ==========================================================================")
        
        if not self.openai_api_key and not GROQ_API_KEY:
            logger.error("[GPT]  No API keys configured")
            return (None, 0.0)
        
        # Track if we're using Groq fallback
        is_groq = False
        
        # Get cycle phase for hormone context
        cycle_phase = user_context.get("cycle_phase", "follicular").lower()
        primary_hormone = user_context.get("primary_hormone", "cortisol").lower()
        secondary_hormone = user_context.get("secondary_hormone", "progesterone").lower()
        logger.info(f"[GPT] Hormone context: primary={primary_hormone}, secondary={secondary_hormone}, phase={cycle_phase}")
        
        # Get hormone personas
        primary_persona = HORMONE_PERSONAS.get(primary_hormone, DEFAULT_PERSONA)
        secondary_persona = HORMONE_PERSONAS.get(secondary_hormone, DEFAULT_PERSONA)
        
        # Build hormone phase context for the prompt
        primary_behavior = primary_persona.get("phase_behavior", {}).get(cycle_phase, "I fluctuate during this phase")
        secondary_behavior = secondary_persona.get("phase_behavior", {}).get(cycle_phase, "I fluctuate during this phase")
        
        hormone_phase_context = f"""
For {primary_persona.get('name', 'Hormone')} ({primary_hormone}):
- Phase behavior: "{primary_behavior}"
- User benefit: "{primary_persona.get('benefit', 'balanced')}"
- Focus: {primary_persona.get('focus', 'overall wellness')}

For {secondary_persona.get('name', 'Hormone')} ({secondary_hormone}):
- Phase behavior: "{secondary_behavior}"
- User benefit: "{secondary_persona.get('benefit', 'balanced')}"
- Focus: {secondary_persona.get('focus', 'overall wellness')}
"""
        
        # Build the prompt with ALL user context
        logger.info(f"[GPT] Building prompt with user context...")
        prompt = ACTION_GENERATION_PROMPT.format(
            num_actions=4,
            # Cycle info
            cycle_day=user_context.get("cycle_day", "unknown"),
            cycle_phase=user_context.get("cycle_phase", "unknown"),
            # Hormones
            primary_hormone=primary_hormone,
            secondary_hormone=secondary_hormone,
            # Health profile
            age=user_context.get("age", "not specified"),
            top_concern=user_context.get("top_concern", "general wellness"),
            diagnosed_conditions=", ".join(user_context.get("diagnosed_conditions", [])) or "none",
            period_concerns=user_context.get("period_concerns", "none specified"),
            body_concerns=user_context.get("body_concerns", "none specified"),
            skin_hair_concerns=user_context.get("skin_hair_concerns", "none specified"),
            mental_health_concerns=user_context.get("mental_health_concerns", "none specified"),
            family_history=user_context.get("family_history", "none specified"),
            birth_control=user_context.get("birth_control", "none"),
            # Personalization
            lifestyle_focus=", ".join(user_context.get("lifestyle_focus", ["eat", "move", "pause"])),
            diet_preference=user_context.get("diet_preference", "no preference specified"),
            food_allergies=user_context.get("food_allergies", "none specified"),
            cuisine_preference=user_context.get("cuisine_preference", "no preference specified"),
            cultural_background=user_context.get("cultural_background", "not specified"),
            dine_out_frequency=user_context.get("dine_out_frequency", "occasionally"),
            body_metrics=user_context.get("body_metrics", "not provided"),
            cravings=user_context.get("cravings", "none specified"),
            stress_level=user_context.get("stress_level", "moderate"),
            sleep_duration=user_context.get("sleep_duration", "7-8 hours"),
            workout_intensity=user_context.get("workout_intensity", "moderate"),
            current_streak=user_context.get("current_streak", 0),
            longest_streak=user_context.get("longest_streak", 0),
            # Feedback and context
            feedback_memory=user_context.get("feedback_memory", "No previous feedback"),
            chatbot_context=user_context.get("chatbot_context", "No additional context"),
            feedback_summary=user_context.get("feedback_summary", "No summary yet"),
            weekly_checkin_insights=user_context.get("weekly_checkin_insights", "No weekly check-in data yet"),
            daily_review_insights=user_context.get("daily_review_insights", "No daily review data yet"),
            care_plan_checkin_insights=user_context.get("care_plan_checkin_insights", "No care plan check-in data yet"),
            symptom_checkin_insights=user_context.get("symptom_checkin_insights", "No symptom check-in data yet"),
            # NEW: Unified cross-chatbot memory
            unified_memory_context=user_context.get("unified_memory_formatted", "No unified memory available yet"),
            # Anti-repetition and hallucination prevention
            recently_recommended=user_context.get("recently_recommended", "None (this is the users first plan)"),
            allowed_symptoms=user_context.get("allowed_symptoms", "general wellness support"),
            allowed_conditions=user_context.get("allowed_conditions", "None diagnosed"),
            # Generation params
            primary_count=2,
            secondary_count=2,
            category_guidance=self._get_category_guidance(user_context.get("lifestyle_focus", [])),
            hormone_phase_context=hormone_phase_context
        )
        
        # Log the anti-repetition and whitelist values being sent to GPT
        logger.info(f"[PROMPT] ==========================================================================")
        logger.info(f"[PROMPT] ANTI-REPETITION DATA SENT TO GPT:")
        logger.info(f"[PROMPT]   recently_recommended: {user_context.get('recently_recommended', 'None')[:200]}...")
        logger.info(f"[PROMPT] WHITELIST DATA SENT TO GPT:")
        logger.info(f"[PROMPT]   allowed_symptoms: {user_context.get('allowed_symptoms', 'None')}")
        logger.info(f"[PROMPT]   allowed_conditions: {user_context.get('allowed_conditions', 'None')}")
        logger.info(f"[PROMPT] PERSONALIZATION DATA:")
        logger.info(f"[PROMPT]   primary_hormone: {primary_hormone}, secondary_hormone: {secondary_hormone}")
        logger.info(f"[PROMPT]   cycle_phase: {cycle_phase}, cycle_day: {user_context.get('cycle_day')}")
        logger.info(f"[PROMPT]   diet_preference: {user_context.get('diet_preference')}")
        logger.info(f"[PROMPT]   food_allergies: {user_context.get('food_allergies')}")
        logger.info(f"[PROMPT]   current_streak: {user_context.get('current_streak')}, longest_streak: {user_context.get('longest_streak')}")
        logger.info(f"[PROMPT] ==========================================================================")
        
        # Get user name from profile or fallback
        user_name = user_context.get("user_name", "there")  # "Hey there" if no name
        
        # Get diagnosed conditions for the personalized system prompt
        conditions_list_for_prompt = user_context.get("diagnosed_conditions", [])
        diagnosed_conditions_summary = ", ".join(conditions_list_for_prompt) if conditions_list_for_prompt else user_context.get("top_concern", "hormone imbalance")
        
        # Format SYSTEM_PROMPT with user's specific data AT THE TOP
        personalized_system = SYSTEM_PROMPT.format(
            user_name=user_name,
            diagnosed_conditions_summary=diagnosed_conditions_summary,
            top_concern=user_context.get("top_concern", "general wellness"),
            cycle_day=user_context.get("cycle_day", "?"),
            cycle_phase=cycle_phase,
            primary_hormone=primary_hormone,
            secondary_hormone=secondary_hormone,
            food_allergies=user_context.get("food_allergies", "none"),
            diet_preference=user_context.get("diet_preference", "no preference")
        )
        
        # Enhanced system prompt with tool calling instructions
        enhanced_system = personalized_system + f"""

CURRENT USER'S HORMONE CONTEXT:
- Cycle Phase: {cycle_phase}
- Primary Hormone: {user_context["primary_hormone"]} - {primary_behavior}
- Secondary Hormone: {user_context["secondary_hormone"]} - {secondary_behavior}

Write the hormone_persona_intro to be PERSONAL:
1. Address user by name: "Hey {user_name}!"
2. Acknowledge their condition: "I know your {diagnosed_conditions_summary} can be challenging..."
3. Explain what's happening in this cycle phase
4. Connect the action to how it helps THEIR specific situation

CRITICAL - RESEARCH CITATIONS:
You MUST use the 'search_research_paper' tool for EACH action to get a REAL citation.
The tool searches PubMed, OpenAlex, and Semantic Scholar for real papers.
Include the paper details (title, journal, year, pmid, finding) in research_studies.
NEVER fabricate citations - always use the tool results.
If the tool returns empty, set research_studies to an empty array.
"""

        # ---------------------------------------------------------------------
        # REASONING ENHANCEMENT FOR FALLBACK MODEL
        # ---------------------------------------------------------------------
        # Note: openai/gpt-oss-120b has native reasoning, so we don't need manual CoT prompt
        # But we keep it for other models just in case
        if model_override and "llama" in model_override.lower():
            enhanced_system += """
            
===============================================================================
 DEEP REASONING INSTRUCTIONS (CHAIN OF THOUGHT)
===============================================================================
You are a highly advanced medical reasoning engine. Before generating the final JSON, 
you must think step-by-step to ensure maximum medical accuracy for this specific user.

1. ANALYZE CONDITIONS: Look at the users diagnosed conditions ({diagnosed_conditions}).
   - What are the contraindications?
   - What are the gold-standard lifestyle interventions?

2. CONNECT TO HORMONES: How do these conditions interact with {primary_hormone} and {secondary_hormone}?
   - Example: If PCOS + High Cortisol -> Avoid high intensity cardio that spikes cortisol.

3. VERIFY SAFETY: Ensure no recommended food conflicts with allergies ({food_allergies}) 
   or diet preferences ({diet_preference}).

4. SYNTHESIZE: Select actions that hit the "sweet spot" of helping the condition AND the hormone.

Think deeply. Be precise. Prioritize clinical efficacy over generic wellness advice.
"""
        
        total_cost = 0.0
        
        # Get users conditions for research queries - ENHANCED with fallbacks
        conditions_list = user_context.get("diagnosed_conditions", [])
        diagnosed_conditions = ", ".join(conditions_list) if conditions_list else ""
        
        # If no conditions, use top_concern or symptoms as fallback
        if not diagnosed_conditions:
            top_concern = user_context.get("top_concern", "")
            if top_concern:
                diagnosed_conditions = top_concern
            else:
                # Use period concerns as last resort
                period_concerns = user_context.get("period_concerns", {})
                if period_concerns:
                    diagnosed_conditions = ", ".join(list(period_concerns.keys())[:3])
                else:
                    diagnosed_conditions = "hormone balance menstrual cycle"
        
        try:
            # =======================================================================
            # STEP 1: RESEARCH DISCOVERY PHASE (ENHANCED)
            # Search for evidence-based interventions with VARIETY
            # =======================================================================
            logger.info(" STEP 1: Research Discovery Phase - Finding what works for this user...")
            
            # ═══════════════════════════════════════════════════════════════════════
            # ENHANCED RESEARCH QUERIES - Add variety to avoid repetitive results
            # ═══════════════════════════════════════════════════════════════════════
            
            import random
            
            # Food variety modifiers - pick random ones each day for variety
            food_varieties = [
                "whole grain", "fermented food", "fatty fish", "legume", "leafy green",
                "root vegetable", "seed", "nut", "cruciferous vegetable", "citrus",
                "berry", "probiotic", "prebiotic", "anti-inflammatory", "antioxidant"
            ]
            
            # Movement variety modifiers
            movement_varieties = [
                "yoga", "walking", "pilates", "swimming", "dancing", "stretching",
                "tai chi", "cycling", "resistance training", "gentle exercise"
            ]
            
            # Mindfulness variety modifiers
            mindfulness_varieties = [
                "breathing exercise", "meditation", "body scan", "relaxation technique",
                "mindful eating", "gratitude practice", "journaling", "visualization"
            ]
            
            # Randomly select variety modifiers for TODAY (ensures different results each day)
            random.shuffle(food_varieties)
            random.shuffle(movement_varieties)
            random.shuffle(mindfulness_varieties)
            
            today_str = date.today().isoformat()  # Add date to cache key for variety
            
            # Build SPECIFIC research queries with variety
            research_queries = [
                # Food 1: Primary hormone with specific food type
                f"{food_varieties[0]} {primary_hormone} {diagnosed_conditions} women RCT",
                # Food 2: Secondary hormone with different food type
                f"{food_varieties[1]} {secondary_hormone} {diagnosed_conditions} women intervention",
                # Movement: Specific type for hormone support
                f"{movement_varieties[0]} {primary_hormone} {diagnosed_conditions} women effect",
                # Mindfulness: Specific technique
                f"{mindfulness_varieties[0]} stress {primary_hormone} women clinical trial"
            ]
            
            categories = ["food", "food", "movement", "mindfulness"]
            hormones = [primary_hormone, secondary_hormone, primary_hormone, primary_hormone]
            
            # =======================================================================
            # PARALLEL EXECUTION: Run all PubMed searches for MULTIPLE papers per action
            # Priority: Meta-analysis > Systematic Review > RCT > Clinical Trial > Review
            # Returns 2-4 citations per action for the collapsible references section
            # =======================================================================
            async def fetch_multiple_research_papers(index: int, query: str) -> Dict[str, Any]:
                """Fetch 2-4 research papers for a query, prioritized by study type."""
                try:
                    papers = await execute_pubmed_tool_multiple({
                        "query": query,
                        "action_title": f"Research {index + 1}",
                        "category": categories[index],
                        "target_hormone": hormones[index]
                    }, db=db, max_citations=4)
                    
                    if papers and len(papers) > 0:
                        return {
                            "query": query,
                            "category": categories[index],
                            "hormone": hormones[index],
                            "papers": papers  # List of 2-4 papers
                        }
                    return None
                except Exception as e:
                    logger.warning(f"Research query failed: {query[:40]}... Error: {e}")
                    return None
            
            # Execute all searches in parallel
            logger.info(f"   Searching {len(research_queries)} queries for multiple citations each...")
            results = await asyncio.gather(
                *[fetch_multiple_research_papers(i, q) for i, q in enumerate(research_queries)],
                return_exceptions=True
            )
            
            # Filter out None results and exceptions
            research_findings = []
            for i, result in enumerate(results):
                if result is None:
                    continue
                if isinstance(result, Exception):
                    logger.warning(f"Research query {i} exception: {result}")
                    continue
                research_findings.append(result)
                papers = result.get("papers", [])
                logger.info(f"   Found {len(papers)} papers for action {i+1}: {[p.get('study_type_label', 'Unknown') for p in papers]}")
            
            total_papers = sum(len(r.get("papers", [])) for r in research_findings)
            logger.info(f" Research complete: Found {total_papers} total papers across {len(research_findings)} actions")
            
            # Build research summary for GPT - now with multiple papers per action
            research_summary = "\\n\\n======================================================================\\n"
            research_summary += "RESEARCH FINDINGS - USE THESE TO INFORM YOUR RECOMMENDATIONS\\n"
            research_summary += "(Priority: Meta-analysis > Systematic Review > RCT > Clinical Trial > Review)\\n"
            research_summary += "======================================================================\\n"
            
            for finding in research_findings:
                papers = finding.get("papers", [])
                research_summary += f"""
📚 Research for {finding['hormone'].upper()} ({finding['category']}) - {len(papers)} sources:
"""
                for idx, paper in enumerate(papers, 1):
                    study_type_label = paper.get('study_type_label', 'Research Study')
                    research_summary += f"""
   [{idx}] {study_type_label}
       Title: {paper.get('title', 'Unknown')}
       Journal: {paper.get('journal', 'Unknown')} ({paper.get('year', 'N/A')})
       Finding: {paper.get('finding', 'No finding extracted')}
       PMID: {paper.get('pmid', 'N/A')}
"""
                research_summary += f"""
   → Use these {len(papers)} sources in research_studies for your {finding['category']} recommendation for {finding['hormone']}
"""
            
            research_summary += "\nIMPORTANT: Your recommendations MUST be based on the research findings above.\n"
            research_summary += "Include 2-4 papers in research_studies field for each action (with study_type field).\n"
            
            # =======================================================================
            # STEP 2: RECOMMENDATION GENERATION - Based on research findings
            # =======================================================================
            logger.info(" STEP 2: Generating recommendations based on research findings...")
            
            # Enhanced system prompt with research findings
            enhanced_system_with_research = SYSTEM_PROMPT + f"""

CURRENT USERS HORMONE CONTEXT:
- Cycle Phase: {cycle_phase}
- Primary Hormone: {user_context["primary_hormone"]} - {primary_behavior}
- Secondary Hormone: {user_context["secondary_hormone"]} - {secondary_behavior}

Write the hormone_persona_intro naturally, following the example style above. The hormone should:
1. Introduce itself by name ("I am Progesterone...")
2. Explain whats happening in this cycle phase
3. Connect the recommended action to how it helps the hormone and the user

CRITICAL - RESEARCH-BASED RECOMMENDATIONS:
The research has ALREADY been done for you (see findings below).
You MUST base your recommendations on WHAT THE RESEARCH FOUND.
Include the paper details (title, journal, year, pmid, finding) in research_studies.

{research_summary}
"""
            
            # API CALL: OpenAI PRIMARY, Groq FALLBACK on ANY error
            # ================================================================
            use_groq = False
            openai_error = None
            response = None  # Fix #19: Prevent UnboundLocalError
            
            # Build OpenAI payload with Structured Outputs
            # NOTE: Some models (o1, o3-mini, gpt-5-mini) don't support temperature
            openai_payload = {
                "model": self.GPT_MODEL,
                "messages": [
                    {"role": "system", "content": enhanced_system_with_research},
                    {"role": "user", "content": prompt}
                ],
                "max_completion_tokens": 4000,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "action_plan",
                        "strict": True,
                        "schema": ACTION_PLAN_SCHEMA
                    }
                }
            }
            
            # Only add temperature if model supports it
            if self.model_supports_temperature(self.GPT_MODEL):
                openai_payload["temperature"] = self.GPT_TEMPERATURE
            
            # Try OpenAI first
            logger.info(f" Trying OpenAI with model: {self.GPT_MODEL}")
            try:
                response = await self.client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openai_api_key}",
                        "Content-Type": "application/json"
                    },
                    json=openai_payload,
                    timeout=60.0
                )
                
                if response.status_code != 200:
                    openai_error = f"OpenAI returned {response.status_code}"
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", {}).get("message", "")
                        error_type = error_data.get("error", {}).get("type", "")
                        openai_error = f"{error_type}: {error_msg[:200]}"
                    except:
                        pass
                    logger.warning(f" OpenAI failed: {openai_error}")
                    
            except Exception as e:
                openai_error = str(e) or "Unknown OpenAI Error" # Ensure not empty string
                logger.warning(f" OpenAI exception: {openai_error[:200]}")
            
            # Fallback to Groq if OpenAI failed for ANY reason
            if openai_error is not None and GROQ_API_KEY:
                logger.info(f" Falling back to Groq with model: {GROQ_FALLBACK_MODEL}")
                use_groq = True
                is_groq = True
                
                # Build Groq payload
                # openai/gpt-oss-120b is a reasoning model - doesn't support response_format
                is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                
                groq_system = enhanced_system_with_research + """

 CRITICAL SCHEMA ENFORCEMENT 
You MUST include ALL fields for EVERY action, even if they are not relevant to the category.
If a field is not relevant, you MUST provide an empty list [].

REQUIRED FIELDS FOR EVERY ACTION:
- "time_slot": MUST be one of "morning", "afternoon", "evening" (NO "lunch", "dinner", etc.)
- "food_items", "food_amounts" (Use [] if not food)
- "exercise_types", "exercise_durations", "exercise_intensities" (Use [] if not movement)
- "mindfulness_techniques", "mindfulness_durations" (Use [] if not mindfulness)
- "research_studies" (Must include "verification_link")

Do not omit ANY field. The system requires a fixed schema.
IMPORTANT: Output ONLY valid JSON. No markdown, no thinking output, no preamble.
"""
                
                groq_payload = {
                    "model": GROQ_FALLBACK_MODEL,
                    "messages": [
                        {"role": "system", "content": groq_system},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 16000  # gpt-oss-120b can handle more tokens
                }
                
                # Only add response_format for non-reasoning models
                if not is_reasoning_model:
                    groq_payload["response_format"] = {"type": "json_object"}
                
                try:
                    response = await self.client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {GROQ_API_KEY}",
                            "Content-Type": "application/json"
                        },
                        json=groq_payload,
                        timeout=90.0
                    )
                    
                    if response.status_code == 200:
                        logger.info(f" Groq fallback successful!")
                    else:
                        logger.error(f" Groq also failed: {response.status_code}")
                        try:
                            groq_error = response.json()
                            logger.error(f"   Groq error: {groq_error}")
                        except:
                            pass
                        return (None, total_cost)
                        
                except Exception as e:
                    logger.error(f" Groq exception: {e}")
                    return (None, total_cost)
                    
            elif openai_error is not None:
                logger.error(f" OpenAI failed and no Groq API key for fallback: {openai_error}")
                return (None, total_cost)
            
            # Final safety check before processing
            if response is None:
                logger.error("Critical Error: response is None after all API attempts")
                return (None, total_cost)
            
            data = response.json()
            
            # Calculate cost
            input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
            output_tokens = data.get("usage", {}).get("completion_tokens", 0)
            total_cost += (input_tokens * 0.00015 / 1000) + (output_tokens * 0.0006 / 1000)
            
            content = data["choices"][0]["message"]["content"]
            logger.info(f" GPT generated recommendations based on {len(research_findings)} research papers")
            
            # Parse response
            try:
                # Clean content for reasoning models that might output markdown
                cleaned_content = content.strip()
                if cleaned_content.startswith("```json"):
                    cleaned_content = cleaned_content[7:]
                if cleaned_content.startswith("```"):
                    cleaned_content = cleaned_content[3:]
                if cleaned_content.endswith("```"):
                    cleaned_content = cleaned_content[:-3]
                cleaned_content = cleaned_content.strip()
                
                response_data = json.loads(cleaned_content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Raw content: {content[:500]}...")
                return (None, total_cost)
            
            # Extract actions array from response
            if isinstance(response_data, dict) and "actions" in response_data:
                raw_actions = response_data["actions"]
            elif isinstance(response_data, list):
                raw_actions = response_data
            else:
                logger.error(f"Unexpected response format: {type(response_data)}")
                return (None, total_cost)
            
            # Normalize categories before validation
            for action in raw_actions:
                if "category" in action:
                    action["category"] = action["category"].lower()
            
            # Sanitize data before Pydantic validation
            # Fix common GPT issues like participants being a string
            for action in raw_actions:
                # Fix research_studies participants field
                for study in action.get("research_studies", []):
                    if isinstance(study.get("participants"), str):
                        # Convert "Women", "50 women", etc. to integer
                        try:
                            import re
                            nums = re.findall(r'\d+', str(study.get("participants", "")))
                            study["participants"] = int(nums[0]) if nums else 0
                        except:
                            study["participants"] = 0
                        logger.debug(f"Sanitized participants: {study.get('participants')}")
            
            # Validate with Pydantic - ensures all required fields are present
            logger.info(f" Validating {len(raw_actions)} raw actions with Pydantic...")
            try:
                # Prepare data for validation - model expects {"actions": [...]}
                validation_payload = {"actions": raw_actions}
                
                # Strict Pydantic Validation
                validated_response = ActionPlanResponseModel.model_validate(validation_payload)
                actions = [action.model_dump() for action in validated_response.actions]
                logger.info(f" Base Pydantic validation passed")
                
                # CRITICAL: Validate and fix target_hormone assignments
                # GPT is told: first 2 actions = primary, last 2 = secondary
                # But GPT sometimes returns wrong values - we MUST fix this
                primary_hormone = user_context.get("primary_hormone", "").lower()
                secondary_hormone = user_context.get("secondary_hormone", "progesterone").lower()
                
                logger.info(f" Validating target_hormone: primary={primary_hormone}, secondary={secondary_hormone}")
                
                for i, action in enumerate(actions):
                    actual_hormone = (action.get("target_hormone") or "").lower()
                    expected_hormone = primary_hormone if i < 2 else secondary_hormone
                    
                    if actual_hormone != expected_hormone:
                        logger.warning(f" Action {i+1} '{action.get('title')}': "
                                      f"target_hormone MISMATCH! Got '{actual_hormone}', expected '{expected_hormone}'. FIXING!")
                        action["target_hormone"] = expected_hormone
                    else:
                        logger.info(f" Action {i+1} '{action.get('title')}': target_hormone={actual_hormone} ")
                
                # Category-specific validation - MUST have these fields, no auto-fill
                validation_errors = []
                for i, action in enumerate(actions):
                    category = action.get("category", "food")
                    title = action.get("title", "Untitled")
                    
                    if category == "food":
                        if not action.get("food_items") or len(action.get("food_items", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [food]: missing food_items")
                        if not action.get("food_amounts") or len(action.get("food_amounts", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [food]: missing food_amounts")
                        else:
                            logger.info(f"   Action {i+1} '{title}' [food]: food_items={action.get('food_items')}, food_amounts={action.get('food_amounts')}")
                    elif category == "movement":
                        if not action.get("exercise_types") or len(action.get("exercise_types", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [movement]: missing exercise_types")
                        if not action.get("exercise_durations") or len(action.get("exercise_durations", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [movement]: missing exercise_durations")
                        else:
                            logger.info(f"   Action {i+1} '{title}' [movement]: exercise_types={action.get('exercise_types')}, exercise_durations={action.get('exercise_durations')}")
                    elif category == "mindfulness":
                        if not action.get("mindfulness_techniques") or len(action.get("mindfulness_techniques", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [mindfulness]: missing mindfulness_techniques")
                        if not action.get("mindfulness_durations") or len(action.get("mindfulness_durations", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [mindfulness]: missing mindfulness_durations")
                        else:
                            logger.info(f"   Action {i+1} '{title}' [mindfulness]: techniques={action.get('mindfulness_techniques')}, durations={action.get('mindfulness_durations')}")
                
                if validation_errors:
                    logger.warning(f" Category-specific validation failed (will retry):")
                    for error in validation_errors:
                        logger.warning(f"    {error}")
                    return (None, total_cost)  # Trigger retry
                    
                logger.info(f" All validations passed for {len(actions)} actions")
                
            except ValidationError as e:
                logger.error(f" Pydantic validation failed: {e}")
                logger.error(f"   This usually means GPT returned incomplete data. Will retry.")
                
                # Detailed error logging
                for err in e.errors():
                    logger.error(f"   -> Field: {err['loc']}, Error: {err['msg']}")
                
                # Log what GPT actually returned for debugging
                for i, action in enumerate(raw_actions):
                    logger.error(f"   Raw Action {i+1}: title={action.get('title')}, category={action.get('category')}, "
                                f"variants_count={len(action.get('variants', []))}")
                return (None, total_cost)
            
            # ================================================================
            # INJECT PRE-FETCHED RESEARCH IF GPT DIDN'T INCLUDE IT
            # Ensures every action has citations (requirement: citations must ALWAYS be present)
            # ================================================================
            for i, action in enumerate(actions):
                existing_research = action.get("research_studies", [])
                if not existing_research or len(existing_research) == 0:
                    # Find matching research from pre-fetched findings
                    action_category = action.get("category", "food").lower()
                    action_hormone = action.get("target_hormone", "").lower()
                    
                    # Try to find research for this category/hormone combo
                    injected = False
                    for finding in research_findings:
                        if finding.get("category") == action_category or finding.get("hormone") == action_hormone:
                            papers = finding.get("papers", [])
                            if papers:
                                action["research_studies"] = papers[:4]  # Max 4 citations
                                logger.info(f"📚 Injected {len(action['research_studies'])} pre-fetched citations for '{action.get('title')}'")
                                injected = True
                                break
                    
                    if not injected and research_findings:
                        # Fallback: use ANY available research
                        for finding in research_findings:
                            papers = finding.get("papers", [])
                            if papers:
                                action["research_studies"] = papers[:2]  # Use 2 as fallback
                                logger.info(f"📚 Fallback: injected {len(action['research_studies'])} citations for '{action.get('title')}'")
                                break
            
            logger.info(f" Generated {len(actions)} actions with REAL citations (cost: ${total_cost:.4f})")
            
            # Log citations for verification
            for i, action in enumerate(actions):
                research = action.get("research_studies", [])
                category = action.get("category", "unknown")
                
                # Log category-specific fields
                if category == "food":
                    logger.info(f"  Action {i+1} '{action.get('title')}' [FOOD]: "
                               f"food_amounts={action.get('food_amounts')}, "
                               f"food_items={action.get('food_items')}")
                elif category == "movement":
                    logger.info(f"  Action {i+1} '{action.get('title')}' [MOVEMENT]: "
                               f"exercise_durations={action.get('exercise_durations')}")
                elif category == "mindfulness":
                    logger.info(f"  Action {i+1} '{action.get('title')}' [MINDFULNESS]: "
                               f"mindfulness_durations={action.get('mindfulness_durations')}")
                
                # Log citation info
                if research and len(research) > 0 and isinstance(research[0], dict):
                    pmid = research[0].get("pmid", "")
                    if pmid:
                        logger.info(f"     REAL citation: PMID {pmid}")
                    else:
                        logger.info(f"     Citation from: {research[0].get('source', 'unknown')}")
                else:
                    logger.warning(f"     No citation for this action")
                
                logger.info(f"     target_hormone={action.get('target_hormone')}, "
                           f"symptoms={action.get('symptoms')}, "
                           f"conditions={action.get('conditions')}, "
                           f"hormone_persona_intro={bool(action.get('hormone_persona_intro'))}")
            
            # Trust the prompt to generate unique actions - no post-generation deduplication
            # The prompt already tells GPT to generate diverse actions across categories
            
            logger.info(f"✅ Generated {len(actions)} actions (cost: ${total_cost:.4f})")
            return (actions, total_cost)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GPT response as JSON: {e}")
            return (None, total_cost)
        except Exception as e:
            logger.error(f"Error calling GPT: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return (None, total_cost)
    
    def _validate_action_fields(
        self,
        action: Dict[str, Any],
        category: str,
        user_conditions: List[str] = None
    ) -> Tuple[bool, List[str]]:
        """
        Validate that all required fields are present for the given category.
        Also validates personalization quality.
        
        Args:
            action: Action dictionary from GPT
            category: Category type (food/movement/mindfulness)
            user_conditions: User's diagnosed conditions for personalization check
            
        Returns:
            Tuple of (is_valid, missing_fields)
        """
        # CRITICAL fields - GPT must return these
        REQUIRED_BASE = [
            "title", "category", "time_slot", "specific_action", 
            "purpose", "target_hormone", "image_prompt"
        ]
        # NON-CRITICAL fields - will be filled by fallback if missing
        # hormone_persona_intro, research_studies, variants, symptoms
        
        REQUIRED_BY_CATEGORY = {
            "food": ["food_items", "food_amounts"],
            "movement": ["exercise_types", "exercise_durations", "exercise_intensities"],
            "mindfulness": ["mindfulness_techniques", "mindfulness_durations"]
        }
        
        missing = []
        
        # Check base fields
        for field in REQUIRED_BASE:
            if not action.get(field):
                missing.append(field)
        
        # Check category-specific fields
        for field in REQUIRED_BY_CATEGORY.get(category, []):
            value = action.get(field)
            if not value or (isinstance(value, list) and len(value) == 0):
                missing.append(field)
        
        # ═══════════════════════════════════════════════════════════════════════
        # PERSONALIZATION QUALITY CHECK
        # STRICT: Purpose MUST mention user's condition or a specific mechanism
        # ═══════════════════════════════════════════════════════════════════════
        purpose = action.get("purpose", "").lower()
        
        # Check for generic phrases that indicate low personalization
        GENERIC_PHRASES = [
            "supports hormonal balance",
            "helps with hormone balance", 
            "good for women's health",
            "promotes wellness",
            "supports overall health",
            "beneficial for hormones",
            "helps regulate hormones",
            "supports your hormonal health",
            "promotes hormonal balance"
        ]
        
        is_generic = any(phrase in purpose for phrase in GENERIC_PHRASES)
        
        # Check if purpose mentions a specific condition
        mentions_condition = False
        if user_conditions:
            for condition in user_conditions:
                if condition.lower() in purpose:
                    mentions_condition = True
                    break
        
        # Also accept if purpose has specific mechanisms (not just generic wellness)
        SPECIFIC_MECHANISMS = [
            "insulin", "glucose", "blood sugar", "cortisol", "progesterone", "estrogen",
            "testosterone", "androgen", "inflammation", "anti-inflammatory", "omega-3",
            "magnesium", "vitamin", "mineral", "serotonin", "dopamine", "melatonin",
            "thyroid", "metabolism", "adrenal", "oxidative stress", "antioxidant"
        ]
        has_mechanism = any(mech in purpose for mech in SPECIFIC_MECHANISMS)
        
        if is_generic and not mentions_condition and not has_mechanism:
            # STRICT: Fail the action - purpose is too generic
            logger.warning(f"🚨 PERSONALIZATION FAILURE: Purpose for '{action.get('title')}' is too generic! "
                          f"Must mention user's condition or specific mechanism. Will retry.")
            missing.append("purpose_personalization")
        
        # research_studies validation - optional, will be filled by fallback
        # (removed strict validation to allow generation to proceed)
        
        # variants validation - optional, will be filled by fallback
        # (removed strict validation to allow generation to proceed)
        
        return (len(missing) == 0, missing)
    
    def _fill_missing_fields(
        self,
        actions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Apply safe defaults to actions with missing fields.
        
        Args:
            actions: List of action dictionaries
            
        Returns:
            Updated actions with defaults applied
        """
        DEFAULTS = {
            "food": {
                "food_items": ["whole grains", "vegetables"],
                "food_amounts": ["1 serving", "1 cup"]
            },
            "movement": {
                "exercise_types": ["walking"],
                "exercise_durations": ["15 min"],
                "exercise_intensities": ["moderate"]
            },
            "mindfulness": {
                "mindfulness_techniques": ["deep breathing"],
                "mindfulness_durations": ["5 min"]
            }
        }
        
        for action in actions:
            category = action.get("category", "food")
            
            # Apply category-specific defaults
            for field, default_value in DEFAULTS.get(category, {}).items():
                if not action.get(field) or (isinstance(action.get(field), list) and len(action.get(field)) == 0):
                    action[field] = default_value.copy() if isinstance(default_value, list) else default_value
                    logger.warning(f" Applied default for {field} in '{action.get('title', 'Untitled')}'")
            
            # Apply base field defaults
            if not action.get("research_studies") or len(action.get("research_studies", [])) == 0:
                # Log as error - research should have been injected earlier
                # Keeping empty array but flagging for investigation
                action["research_studies"] = []
                logger.error(f"🚨 CRITICAL: No research for '{action.get('title', 'Untitled')}' - should have been injected!")
            
            if not action.get("variants") or len(action.get("variants", [])) < 3:
                # Fill up to 3 variants
                existing_variants = action.get("variants", [])
                variant_types = {
                    "food": ["healthy", "easy", "tasty"],
                    "movement": ["gentle", "energizing", "quick"],
                    "mindfulness": ["brief", "guided", "solo"]
                }.get(category, ["alternative", "alternative", "alternative"])
                
                while len(existing_variants) < 3:
                    idx = len(existing_variants)
                    v_type = variant_types[idx] if idx < len(variant_types) else "alternative"
                    existing_variants.append({
                        "variant_type": v_type,
                        # Use descriptive title combining main title + variant type (e.g. 'Water Aerobics (Gentle)')
                        "title": f"{action.get('title', 'Action')} ({v_type.title()})",
                        "description": "Alternative approach",
                        "image_prompt": action.get("image_prompt", "")
                    })
                
                action["variants"] = existing_variants
                logger.warning(f" Filled variants for '{action.get('title', 'Untitled')}' (now {len(existing_variants)})")
            
            # Ensure other base fields have safe defaults
            if not action.get("title"):
                action["title"] = f"{category.title()} Action"
            if not action.get("specific_action"):
                action["specific_action"] = "Follow recommended wellness practices for hormonal balance"
            if not action.get("purpose"):
                action["purpose"] = "Supports hormonal balance and overall wellness"
            if not action.get("hormone_persona_intro"):
                action["hormone_persona_intro"] = "This action supports your hormonal health"
            if not action.get("time_slot"):
                action["time_slot"] = "morning"
        
        return actions
    
    async def _fast_condition_check(
        self,
        actions: List[Dict],
        user_context: Dict[str, Any]
    ) -> Optional[int]:
        """Fast quality evaluation."""
        if not self.openai_api_key:
            return None
        
        # Build compact action summary
        actions_summary = []
        for action in actions:
            actions_summary.append({
                "title": action.get("title", ""),
                "category": action.get("category", ""),
                "specific_action": action.get("specific_action", "")[:150],
                "food_items": action.get("food_items", [])[:5],
                "exercise_types": action.get("exercise_types", [])[:3],
                "research": [s.get("finding", "")[:80] for s in action.get("research_studies", [])[:2]]
            })
        
        # Extract user context for evaluation
        diagnosed_conditions = user_context.get("diagnosed_conditions", [])
        diet_preference = user_context.get("diet_preference", "none")
        food_allergies = user_context.get("food_allergies", [])
        cuisine_preferences = user_context.get("cuisine_preferences", [])
        top_concern = user_context.get("top_concern", "")
        period_concerns = user_context.get("period_concerns", [])
        feedback_summary = user_context.get("feedback_summary", "")
        
        # Build compact prompt with ALL 5 factors
        prompt = f"""Rate this health plan quality (0-100 each):

USER:
- Conditions: {', '.join(diagnosed_conditions) if diagnosed_conditions else 'None'}
- Diet: {diet_preference}
- Allergies: {', '.join(food_allergies) if food_allergies else 'None'}
- Cuisines: {', '.join(cuisine_preferences[:3]) if cuisine_preferences else 'Any'}
- Top Concern: {top_concern or 'General wellness'}
- Period Concerns: {', '.join(period_concerns[:3]) if period_concerns else 'None'}
- Feedback: {feedback_summary[:200] if feedback_summary else 'No prior feedback'}

ACTIONS:
{json.dumps(actions_summary, indent=1)}

RATE 5 FACTORS (0-100):
1. personalization: Are actions specific to users conditions/concerns?
2. condition_safety: Safe for users diagnosed conditions?
3. feedback_alignment: Avoids disliked patterns, repeats liked ones?
4. preference_compliance: Respects diet/allergies/cuisine?
5. evidence_quality: Do research findings support the recommendations?

JSON ONLY:
{{"personalization": <score>, "condition_safety": <score>, "feedback_alignment": <score>, "preference_compliance": <score>, "evidence_quality": <score>}}"""

        try:
            response = await self.client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-5-mini",
                    "messages": [
                        {"role": "system", "content": "You are a health plan quality evaluator. Output ONLY JSON, no explanation."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,
                    "max_completion_tokens": 150,  # Enough for 5 scores
                },
                timeout=15.0  # Slightly longer for 5 factors
            )
            
            if response.status_code != 200:
                logger.warning(f"Fast quality check failed: {response.status_code}")
                return None
            
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            # Parse JSON response
            import re
            json_match = re.search(r'\{[^}]+\}', content)
            if json_match:
                result = json.loads(json_match.group())
                
                # Extract all 5 scores
                personalization = result.get("personalization", 80)
                condition_safety = result.get("condition_safety", 80)
                feedback_alignment = result.get("feedback_alignment", 80)
                preference_compliance = result.get("preference_compliance", 80)
                evidence_quality = result.get("evidence_quality", 80)
                
                # Calculate average
                avg_score = (personalization + condition_safety + feedback_alignment + preference_compliance + evidence_quality) / 5
                
                logger.info(f" Fast quality check - ALL 5 FACTORS:")
                logger.info(f"    Personalization: {personalization}/100")
                logger.info(f"    Condition Safety: {condition_safety}/100")
                logger.info(f"    Feedback Alignment: {feedback_alignment}/100")
                logger.info(f"    Preference Compliance: {preference_compliance}/100")
                logger.info(f"    Evidence Quality: {evidence_quality}/100")
                logger.info(f"    Average: {avg_score:.1f}/100")
                
                # Return condition_safety for model switching decision
                # (this is the critical safety factor)
                return condition_safety
            
            return None
            
        except Exception as e:
            logger.warning(f"Fast quality check error: {e}")
            return None
    
    async def _generate_all_images(
        self,
        actions: List[Dict],
        user_id: str,
        db: AsyncSession,
        image_mode: Literal["full", "hero_only", "variants_only", "none"] = "full",
    ) -> Tuple[List[Dict], float]:
        """
        Generate all images for all actions (16 total) in PARALLEL.
        
        Uses asyncio.gather to generate all images concurrently:
        - 4 hero images + 12 variant images = 16 total
        - Each task uses its OWN database session to avoid concurrency issues
        - Previous: ~2-4 minutes (sequential)
        - Now: ~15-30 seconds (parallel)
        """
        
        async def _generate_single_image(
            prompt: str, 
            category: str, 
            variant_type: str, 
            user_id: str,
            title_embedding: Optional[List[float]] = None
        ):
            """Wrapper that creates its own session from shared pool for each image task."""
            task_session = None
            try:
                # Use semaphore to limit concurrent DB operations
                # The entire image generation (including cache check and store) happens with a valid session
                async with self.db_semaphore:
                    task_session = await _create_async_session(self.async_session_maker)
                    logger.debug(f"[ImageTask] Created session for {category}/{variant_type}")
                    
                    url, was_cached, cost = await self.image_service.get_or_generate_image(
                        prompt=prompt,
                        category=category,
                        variant_type=variant_type,
                        user_id=user_id,
                        db=task_session,
                        title_embedding=title_embedding
                    )
                
                if not url:
                    logger.warning(f"[ImageTask] Empty URL for {category}/{variant_type}")
                else:
                    logger.debug(f"[ImageTask] Got URL for {category}/{variant_type}: {url[:50]}...")
                
                return (url, was_cached, cost)
            except Exception as e:
                logger.error(f"[ImageTask] Error for {category}/{variant_type}: {e}")
                logger.error(f"[ImageTask] Full traceback: {traceback.format_exc()}")
                return ("", False, 0.0)
            finally:
                if task_session:
                    try:
                        await task_session.close()
                    except Exception as close_err:
                        logger.error(f"[ImageTask] Session close error: {close_err}")
        
        if image_mode == "none":
            # Safety: caller should have short-circuited, but keep this defensive.
            return (actions, 0.0)

        # Build list of all image generation task data
        # SKIP actions that already have images (carryforward items)
        task_data_list = []
        skipped_actions = 0
        
        for action_idx, action in enumerate(actions):
            action_title = action.get("title", "Wellness Action")
            action_category = action.get("category", "food")
            
            # Check if this action already has images (carryforward item)
            has_hero = bool(action.get("hero_image_url"))
            variants = action.get("variants", [])
            has_all_variant_images = all(
                isinstance(v, dict) and v.get("image_url")
                for v in variants
            ) if variants else False
            
            # Skip this action entirely if it has all images
            if has_hero and has_all_variant_images:
                skipped_actions += 1
                logger.info(f"[IMAGES] ✅ Action '{action_title}' already has images (carryforward) - skipping")
                continue
            
            # Hero image task data (only if no hero image yet)
            if image_mode != "variants_only" and not has_hero:
                task_data_list.append({
                    "prompt": action_title,
                    "category": action_category,
                    "variant_type": "hero",
                    "meta": {"action_idx": action_idx, "variant_idx": None}
                })
            
            # Variant image tasks data (only for variants without images)
            if image_mode in ["full", "variants_only"]:
                for variant_idx, variant in enumerate(variants):
                    if not isinstance(variant, dict):
                        continue
                    # Skip variants that already have images
                    if variant.get("image_url"):
                        continue
                    variant_title = variant.get("title", f"{variant.get('variant_type', 'variant')} {action_title}")
                    task_data_list.append({
                        "prompt": variant_title,
                        "category": action_category,
                        "variant_type": variant.get("variant_type", f"variant_{variant_idx}"),
                        "meta": {"action_idx": action_idx, "variant_idx": variant_idx}
                    })
        
        if skipped_actions > 0:
            logger.info(f"[IMAGES] 🔄 Skipped {skipped_actions} carryforward actions with existing images")

        if not task_data_list:
            logger.info(f"[IMAGES] ✅ All {len(actions)} actions already have images - NO GENERATION NEEDED ($0.00)")
            return (actions, 0.0)

        # 🚀 STEP 1: Fetch ALL embeddings in ONE batch call
        logger.info(f" [IMAGES] Fetching {len(task_data_list)} embeddings in batch...")
        all_prompts = [t["prompt"] for t in task_data_list]
        all_embeddings = await self.image_service._get_batch_embeddings(all_prompts)
        
        # 🚀 STEP 2: Queue all image generation tasks using pre-fetched embeddings
        image_tasks = []
        task_metadata = []
        for i, task_data in enumerate(task_data_list):
            image_tasks.append(
                _generate_single_image(
                    prompt=task_data["prompt"],
                    category=task_data["category"],
                    variant_type=task_data["variant_type"],
                    user_id=user_id,
                    title_embedding=all_embeddings[i]
                )
            )
            task_metadata.append(task_data["meta"])

        # Execute all image tasks in parallel
        logger.info(f" Generating {len(image_tasks)} images in PARALLEL with isolated sessions... (image_mode={image_mode})")
        start_time = time.time()
        
        results = await asyncio.gather(*image_tasks, return_exceptions=True)
        
        elapsed = time.time() - start_time
        logger.info(f" All {len(image_tasks)} images generated in {elapsed:.2f}s (parallel)")
        
        # Process results and assign back to actions
        total_cost = 0.0
        for i, result in enumerate(results):
            meta = task_metadata[i]
            action_idx = meta["action_idx"]
            variant_idx = meta["variant_idx"]
            
            # Handle exceptions from gather
            if isinstance(result, Exception):
                logger.error(f"Image generation failed for action {action_idx}: {result}")
                url, was_cached, cost = "", False, 0.0
            else:
                url, was_cached, cost = result
            
            total_cost += cost
            
            if variant_idx is None:
                # Hero image
                actions[action_idx]["hero_image_url"] = url
                actions[action_idx]["hero_image_cached"] = was_cached
            else:
                # Variant image
                variants = actions[action_idx].get("variants", [])
                if variant_idx < len(variants) and isinstance(variants[variant_idx], dict):
                    variants[variant_idx]["image_url"] = url
                    variants[variant_idx]["image_cached"] = was_cached
        
        # Filter out invalid variants
        for action in actions:
            valid_variants = [v for v in action.get("variants", []) if isinstance(v, dict)]
            action["variants"] = valid_variants
        
        # ============================================================================
        # RETRY LOOP: Ensure 100% image generation (all 16) before returning
        # If any images failed, retry them up to 3 times
        # ============================================================================
        MAX_IMAGE_RETRIES = 3
        FALLBACK_IMAGE_URLS = {
            "food": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_food_fzjqkl.jpg",
            "movement": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_movement_k8zq3n.jpg",
            "mindfulness": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_mindfulness_pqwz9m.jpg",
        }
        
        for retry_attempt in range(MAX_IMAGE_RETRIES):
            # Find ALL missing images (hero + variants)
            missing_images = []  # List of (action_idx, variant_idx or None for hero)
            
            for action_idx, action in enumerate(actions):
                # Check hero image
                hero_url = action.get("hero_image_url")
                if not hero_url or hero_url == "":
                    missing_images.append((action_idx, None))
                
                # Check variant images
                variants = action.get("variants", [])
                for variant_idx, variant in enumerate(variants):
                    if isinstance(variant, dict):
                        variant_url = variant.get("image_url")
                        if not variant_url or variant_url == "":
                            missing_images.append((action_idx, variant_idx))
            
            if not missing_images:
                total_images = len(actions) + sum(len(a.get("variants", [])) for a in actions)
                logger.info(f"✅ All {total_images} images (hero + variants) generated successfully")
                break
            
            logger.warning(f"⚠️ Image retry {retry_attempt + 1}/{MAX_IMAGE_RETRIES}: {len(missing_images)} images missing")
            
            # Retry failed images
            retry_tasks = []
            retry_metadata = []  # (action_idx, variant_idx or None)
            
            for action_idx, variant_idx in missing_images:
                action = actions[action_idx]
                action_category = action.get("category", "food")
                
                if variant_idx is None:
                    # Hero image
                    prompt = action.get("title", "Wellness Action")
                    variant_type = "hero"
                else:
                    # Variant image
                    variant = action.get("variants", [])[variant_idx]
                    prompt = variant.get("title", f"{variant.get('variant_type', 'variant')} {action.get('title', 'Action')}")
                    variant_type = variant.get("variant_type", f"variant_{variant_idx}")
                
                retry_tasks.append(
                    _generate_single_image(
                        prompt=prompt,
                        category=action_category,
                        variant_type=variant_type,
                        user_id=user_id,
                        title_embedding=None
                    )
                )
                retry_metadata.append((action_idx, variant_idx))
            
            # Exponential backoff before retry
            await asyncio.sleep(1.0 * (retry_attempt + 1))
            
            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
            
            # Update actions with retry results
            for i, result in enumerate(retry_results):
                action_idx, variant_idx = retry_metadata[i]
                
                if isinstance(result, Exception):
                    logger.error(f"Retry failed for action {action_idx}, variant {variant_idx}: {result}")
                    continue
                
                url, was_cached, cost = result
                if url:
                    if variant_idx is None:
                        actions[action_idx]["hero_image_url"] = url
                        actions[action_idx]["hero_image_cached"] = was_cached
                    else:
                        actions[action_idx]["variants"][variant_idx]["image_url"] = url
                        actions[action_idx]["variants"][variant_idx]["image_cached"] = was_cached
                    total_cost += cost
                    img_type = "Hero" if variant_idx is None else f"Variant {variant_idx}"
                    logger.info(f"✅ {img_type} retry succeeded for action {action_idx}")
        
        # Final check: Apply fallback URLs for any still-missing images
        final_missing_hero = 0
        final_missing_variant = 0
        
        for action in actions:
            category = action.get("category", "food").lower()
            fallback_url = FALLBACK_IMAGE_URLS.get(category, FALLBACK_IMAGE_URLS["food"])
            
            # Check hero
            hero_url = action.get("hero_image_url")
            if not hero_url or hero_url == "":
                action["hero_image_url"] = fallback_url
                action["hero_image_cached"] = False
                final_missing_hero += 1
                logger.warning(f"⚠️ Using fallback for hero: '{action.get('title', 'Unknown')}'")
            
            # Check variants
            for variant in action.get("variants", []):
                if isinstance(variant, dict):
                    variant_url = variant.get("image_url")
                    if not variant_url or variant_url == "":
                        variant["image_url"] = fallback_url
                        variant["image_cached"] = False
                        final_missing_variant += 1
                        logger.warning(f"⚠️ Using fallback for variant: '{variant.get('title', 'Unknown')}'")
        
        total_fallbacks = final_missing_hero + final_missing_variant
        if total_fallbacks > 0:
            logger.error(f"❌ {total_fallbacks} images ({final_missing_hero} hero, {final_missing_variant} variant) required fallback after {MAX_IMAGE_RETRIES} retries")
        
        logger.info(f"Generated {len(image_tasks)} images (cost: ${total_cost:.4f})")
        
        return (actions, total_cost)
    
    async def _store_plan(
        self,
        user_id: Optional[str],
        plan_date: date,
        user_context: Dict[str, Any],
        actions: List[Dict],
        total_cost: float,
        generation_time_ms: int,
        db: AsyncSession,
        session_id: Optional[str] = None  # NEW: For guest users
    ) -> Any:
        """Store the complete plan in the database."""
        from app.core.database import ActionPlan, ActionPlanItem, ActionPlanItemVariant
        
        try:
            # Create plan record
            plan = ActionPlan(
                uid=user_id,
                session_id=session_id,  # Store session ID
                plan_date=plan_date,
                primary_hormone=user_context["primary_hormone"],
                secondary_hormones=[user_context["secondary_hormone"]],
                cycle_day=user_context.get("cycle_day"),
                cycle_phase=user_context.get("cycle_phase"),
                lifestyle_focus=user_context.get("lifestyle_focus"),
                generation_cost=str(total_cost),
                generation_time_ms=generation_time_ms,
                gpt_model_used=self.GPT_MODEL,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.add(plan)
            await db.flush()  # Get the plan ID
            
            # Create action items
            for slot, action in enumerate(actions, start=1):
                # Get conditions from action or user context
                action_conditions = action.get("conditions", [])
                if not action_conditions:
                    # Use users diagnosed conditions if action doesn't specify
                    action_conditions = user_context.get("diagnosed_conditions", [])
                
                # Get symptoms from action (if GPT generated them) 
                action_symptoms = action.get("symptoms", [])
                if not action_symptoms:
                    # Fallback to top concern if no specific symptoms generated
                    top_concern = user_context.get("top_concern")
                    if top_concern and top_concern.lower() != "general wellness":
                        action_symptoms = [top_concern]
                
                item = ActionPlanItem(
                    plan_id=plan.id,
                    uid=user_id,
                    session_id=session_id,  # Store session ID
                    slot=slot,
                    time_slot=action.get("time_slot", "morning"),
                    category=action.get("category", "food"),
                    title=action.get("title", "Wellness Action"),
                    specific_action=action.get("specific_action", ""),
                    purpose=action.get("purpose", ""),
                    target_hormone=action.get("target_hormone", user_context.get("primary_hormone", "Hormonal Balance")),
                    hormone_persona_intro=action.get("hormone_persona_intro", ""),
                    hero_image_url=action.get("hero_image_url"),
                    hero_image_prompt=action.get("image_prompt"),
                    research_studies=action.get("research_studies", []),
                    conditions=action_conditions,
                    symptoms=action_symptoms,
                    carried_forward_from=action.get("carried_forward_from"),  # Track if skipped from previous day
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                # Add category-specific fields (case-insensitive)
                cat = action.get("category", "").lower()
                if cat == "food":
                    item.food_items = action.get("food_items", [])
                    item.food_amounts = action.get("food_amounts", [])
                elif cat == "movement":
                    item.exercise_types = action.get("exercise_types", [])
                    item.exercise_durations = action.get("exercise_durations", [])
                    item.exercise_intensities = action.get("exercise_intensities", [])
                elif cat == "mindfulness":
                    item.mindfulness_techniques = action.get("mindfulness_techniques", [])
                    item.mindfulness_durations = action.get("mindfulness_durations", [])
                
                db.add(item)
                await db.flush()
                
                # Create variants
                for variant in action.get("variants", []):
                    # Skip invalid variants
                    if not isinstance(variant, dict):
                        continue
                        
                    v_type = variant.get("variant_type")
                    if not v_type or v_type == "alternative":
                        category = action.get("category", "food")
                        defaults = {
                            "food": ["healthy", "easy", "tasty"],
                            "movement": ["gentle", "quick", "energizing"],
                            "mindfulness": ["brief", "guided", "solo"]
                        }.get(category, ["alternative"])
                        v_type = defaults[action.get("variants", []).index(variant) % len(defaults)]
                        
                    variant_image_url = variant.get("image_url")
                    logger.info(f"[STORE_VARIANT] {v_type}: title='{variant.get('title', '')[:30]}', has_image={bool(variant_image_url)}, url_preview='{str(variant_image_url)[:50] if variant_image_url else 'None'}'")
                    
                    variant_record = ActionPlanItemVariant(
                        item_id=item.id,
                        variant_type=v_type,
                        title=variant.get("title", ""),
                        description=variant.get("description", ""),
                        image_url=variant_image_url,
                        image_prompt=variant.get("image_prompt"),
                        created_at=datetime.utcnow()
                    )
                    db.add(variant_record)
            
            await db.commit()
            await db.refresh(plan)
            
            logger.info(f"Stored plan {plan.id} with {len(actions)} actions")
            
            return plan
            
        except Exception as e:
            await db.rollback()
            
            # Check if this is a duplicate key error (race condition)
            if "UniqueViolationError" in str(e) or "duplicate key" in str(e).lower():
                logger.warning(f"Race condition detected for user {user_id} on {plan_date}, fetching existing plan")
                
                # Fetch the existing plan that was created by the concurrent request
                existing_plan = await self._get_existing_plan(user_id, plan_date, db)
                if existing_plan:
                    logger.info(f"Found existing plan {existing_plan.id} created by concurrent request")
                    return existing_plan
                else:
                    logger.error(f"Could not find existing plan after duplicate key error")
                    raise
            
            logger.error(f"Error storing plan: {e}")
            raise
    
    async def _check_missing_images(self, plan: Any, db: AsyncSession) -> bool:
        """
        Quick check if any items are missing images (without generating them).
        
        Returns True if any hero images are missing.
        """
        from app.core.database import ActionPlanItem
        
        try:
            result = await db.execute(
                select(ActionPlanItem).where(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_replaced.isnot(True),
                        or_(
                            ActionPlanItem.hero_image_url.is_(None),
                            ActionPlanItem.hero_image_url == ""
                        )
                    )
                ).limit(1)  # We only need to know if ANY are missing
            )
            return bool(result.scalars().first())
        except Exception as e:
            logger.warning(f"Error checking missing images: {e}")
            return False
    
    async def _background_ensure_images(
        self,
        plan_id: int,
        user_id: str,
        image_mode: str = "full"
    ) -> None:
        """
        Background task to generate missing images.
        
        Creates its own database session since this runs independently of the request.
        """
        from app.core.database import ActionPlan
        
        session = None
        try:
            session = self.async_session_maker()
            
            # Fetch the plan in this session
            result = await session.execute(
                select(ActionPlan).where(ActionPlan.id == plan_id)
            )
            plan = result.scalar_one_or_none()
            
            if not plan:
                logger.warning(f"[BG-IMAGE] Plan {plan_id} not found for background image generation")
                return
            
            logger.info(f" [BG-IMAGE] Starting image generation for plan {plan_id}")
            await self._ensure_plan_has_images(plan, user_id, session, image_mode)
            logger.info(f" [BG-IMAGE] Completed image generation for plan {plan_id}")
            
        except Exception as e:
            logger.error(f" [BG-IMAGE] Error in background image generation: {e}")
        finally:
            if session:
                await session.close()
    
    async def _ensure_plan_has_images(
        self,
        plan: Any,
        user_id: str,
        db: AsyncSession,
        image_mode: str = "hero_only"
    ) -> None:
        """Check if plan items have missing hero images and generate them."""
        from app.core.database import ActionPlanItem, ActionPlanItemVariant
        
        try:
            # Get items with missing hero images
            items_result = await db.execute(
                select(ActionPlanItem).where(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_replaced.isnot(True),
                        or_(
                            ActionPlanItem.hero_image_url.is_(None),
                            ActionPlanItem.hero_image_url == ""
                        )
                    )
                )
            )
            items_missing_images = items_result.scalars().all()
            
            # Also get variants with missing images
            all_items_result = await db.execute(
                select(ActionPlanItem).where(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_replaced.isnot(True)
                    )
                )
            )
            all_items = all_items_result.scalars().all()
            
            variants_missing_images = []
            if image_mode == "full":
                for item in all_items:
                    variants_result = await db.execute(
                        select(ActionPlanItemVariant).where(
                            and_(
                                ActionPlanItemVariant.item_id == item.id,
                                or_(
                                    ActionPlanItemVariant.image_url.is_(None),
                                    ActionPlanItemVariant.image_url == ""
                                )
                            )
                        )
                    )
                    variants = variants_result.scalars().all()
                    for v in variants:
                        variants_missing_images.append((item, v))
            
            total_missing = len(items_missing_images) + len(variants_missing_images)
            
            if total_missing == 0:
                logger.info(f"[ENSURE_IMAGES] All items in plan {plan.id} already have images")
                return
            
            logger.info(f"[ENSURE_IMAGES] Found {len(items_missing_images)} hero + {len(variants_missing_images)} variant images missing in plan {plan.id}")
            
            # Generate hero images
            async def generate_hero_image(item):
                """Wrapper that creates its own session for each image task."""
                task_session = None
                try:
                    # Use TITLE for image generation - prompt enhancement done by image_library_service
                    if not item.title:
                        logger.warning(f"[ENSURE_IMAGES] Item {item.id} has no title, skipping")
                        return False
                    
                    logger.info(f"[ENSURE_IMAGES] Generating hero: '{item.title[:40]}' ({item.category})")
                    
                    async with self.db_semaphore:
                        task_session = await _create_async_session(self.async_session_maker)
                        url, was_cached, cost = await self.image_service.get_or_generate_image(
                            prompt=item.title,  # Use TITLE for cache matching
                            category=item.category or "food",
                            variant_type="hero",
                            user_id=user_id,
                            db=task_session
                        )
                    
                    if url:
                        item.hero_image_url = url
                        cache_status = "CACHE HIT" if was_cached else "GENERATED"
                        logger.info(f"[ENSURE_IMAGES]  Hero {cache_status}: '{item.title[:25]}...'")
                        return url
                    return None
                except Exception as e:
                    logger.warning(f"[ENSURE_IMAGES] Hero failed for {item.id}: {e}")
                    return None
                finally:
                    if task_session:
                        await task_session.close()
            
            # Generate variant images
            async def generate_variant_image(item, variant):
                """Wrapper for variant image generation."""
                task_session = None
                try:
                    prompt = variant.image_prompt
                    if not prompt:
                        prompt = f"{variant.variant_type.title()} {item.title}, {item.category} lifestyle, professional photography"
                    
                    async with self.db_semaphore:
                        task_session = await _create_async_session(self.async_session_maker)
                        url, was_cached, cost = await self.image_service.get_or_generate_image(
                            prompt=prompt,
                            category=item.category or "food",
                            variant_type=variant.variant_type,
                            user_id=user_id,
                            db=task_session
                        )
                    
                    if url:
                        variant.image_url = url
                        logger.info(f"[ENSURE_IMAGES] Variant {variant.variant_type}: {item.title[:20]}...")
                        return url
                    return None
                except Exception as e:
                    logger.warning(f"[ENSURE_IMAGES] Variant failed for {variant.id}: {e}")
                    return None
                finally:
                    if task_session:
                        await task_session.close()
            
            # Generate all missing images in parallel
            hero_tasks = [generate_hero_image(item) for item in items_missing_images]
            variant_tasks = [generate_variant_image(item, var) for item, var in variants_missing_images]
            
            all_results = await asyncio.gather(
                *hero_tasks, *variant_tasks,
                return_exceptions=True
            )
            
            # Count successes
            success_count = sum(1 for r in all_results if r and not isinstance(r, Exception))
            logger.info(f"[ENSURE_IMAGES] Generated {success_count}/{total_missing} images for plan {plan.id}")
            
            # Commit all changes to the main session
            await db.commit()
            logger.info(f"[ENSURE_IMAGES] Completed image generation for plan {plan.id}")
            
        except Exception as e:
            logger.error(f"[ENSURE_IMAGES] Error ensuring plan has images: {e}")
            # Don't raise - we want the plan to load even if images fail
    
    async def _format_plan_response(
        self,
        plan: Any,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Format plan for API response."""
        from app.core.database import ActionPlanItem, ActionPlanItemVariant
        
        try:
            # Get all items for this plan
            items_result = await db.execute(
                select(ActionPlanItem).where(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_replaced.isnot(True)
                    )
                ).order_by(ActionPlanItem.slot)
            )
            items = items_result.scalars().all()
            
            actions = []
            for item in items:
                # Get variants for this item
                variants_result = await db.execute(
                    select(ActionPlanItemVariant).where(
                        ActionPlanItemVariant.item_id == item.id
                    )
                )
                variants = variants_result.scalars().all()
                
                action_data = {
                    "id": item.id,
                    "slot": item.slot,
                    "time_slot": item.time_slot,  # Added: was missing
                    "category": item.category,
                    "title": item.title,
                    "specific_action": item.specific_action,
                    "purpose": item.purpose,
                    "target_hormone": item.target_hormone,
                    "hormone_persona_intro": item.hormone_persona_intro,
                    "hero_image_url": item.hero_image_url,
                    "research_studies": item.research_studies or [],
                    "conditions": item.conditions or [],
                    "symptoms": item.symptoms or [],
                    "is_completed": item.is_completed,
                    "is_replaced": item.is_replaced,
                    "variants": [
                        {
                            "variant_type": v.variant_type,
                            "title": v.title,
                            "description": v.description,
                            "image_url": v.image_url
                        }
                        for v in variants
                    ]
                }
                
                # Add category-specific data
                if item.category == "food":
                    action_data["food_items"] = item.food_items
                    action_data["food_amounts"] = item.food_amounts
                elif item.category == "movement":
                    action_data["exercise_types"] = item.exercise_types
                    action_data["exercise_durations"] = item.exercise_durations
                    action_data["exercise_intensities"] = item.exercise_intensities
                elif item.category == "mindfulness":
                    action_data["mindfulness_techniques"] = item.mindfulness_techniques
                    action_data["mindfulness_durations"] = item.mindfulness_durations
                
                actions.append(action_data)
            
            return {
                "success": True,
                "plan_id": plan.id,
                "plan_date": plan.plan_date.isoformat(),
                "primary_hormone": plan.primary_hormone,
                "secondary_hormones": plan.secondary_hormones,
                "cycle_day": plan.cycle_day,
                "cycle_phase": plan.cycle_phase,
                "actions": actions,
                "generation_cost": plan.generation_cost,
                "generation_time_ms": plan.generation_time_ms
            }
            
        except Exception as e:
            logger.error(f"Error formatting plan response: {e}")
            return {"success": False, "error": "Failed to load plan. Please try again."}
    
    async def _background_generate_variants(
        self,
        plan_id: int,
        user_id: str,
        actions: List[Dict],
    ):
        """
        Background task to generate variant images and update the database.
        This runs after the plan has been returned to the user to improve latency.
        """
        logger.info(f"[BG-IMAGE] Starting background variant generation for plan {plan_id}")
        start_time = time.time()
        
        # Create a dedicated session for this background task
        session = self.async_session_maker()
        try:
            from app.core.database import ActionPlanItem, ActionPlanItemVariant
            
            # 1. Generate the variant images (this uses its own internal sessions for image gen)
            # We reuse the existing actions list which has the prompts
            updated_actions, cost = await self._generate_all_images(
                actions=actions,
                user_id=user_id,
                db=session, # Passed but not used for the image gen logic itself
                image_mode="variants_only"
            )
            
            # 2. Update the database records
            # Get all items for this plan ordered by slot
            items_result = await session.execute(
                select(ActionPlanItem)
                .where(ActionPlanItem.plan_id == plan_id)
                .order_by(ActionPlanItem.slot)
            )
            items = items_result.scalars().all()
            
            if len(items) != len(updated_actions):
                logger.warning(f"[BG-IMAGE] Mismatch: Plan has {len(items)} items, but generated {len(updated_actions)} actions.")
            
            updates_count = 0
            
            for i, item in enumerate(items):
                if i >= len(updated_actions):
                    break
                    
                action_data = updated_actions[i]
                variants_data = action_data.get("variants", [])
                
                # Get existing variants for this item
                variants_result = await session.execute(
                    select(ActionPlanItemVariant)
                    .where(ActionPlanItemVariant.item_id == item.id)
                )
                db_variants = variants_result.scalars().all()
                
                # Map by variant_type for easy update
                db_variant_map = {v.variant_type: v for v in db_variants}
                
                for v_data in variants_data:
                    if not isinstance(v_data, dict):
                        continue
                        
                    v_type = v_data.get("variant_type")
                    image_url = v_data.get("image_url")
                    
                    if v_type and image_url and v_type in db_variant_map:
                        db_variant = db_variant_map[v_type]
                        db_variant.image_url = image_url
                        updates_count += 1
            
            await session.commit()
            elapsed = time.time() - start_time
            logger.info(f"[BG-IMAGE]  Updated {updates_count} variant images for plan {plan_id} in {elapsed:.2f}s")
            
        except Exception as e:
            await session.rollback()
            logger.error(f"[BG-IMAGE]  Failed to update variant images: {e}")
            logger.error(traceback.format_exc())
        finally:
            await session.close()

    async def replace_action(
        self,
        user_id: str,
        item_id: int,
        reason: Optional[str],
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Replace a disliked action with a new one.
        
        New action must target the SAME hormone but can be different category.
        """
        from app.core.database import ActionPlanItem, ActionPlanFeedback
        
        try:
            # Get the original action
            result = await db.execute(
                select(ActionPlanItem).where(ActionPlanItem.id == item_id)
            )
            original = result.scalar_one_or_none()
            
            if not original:
                return {"success": False, "error": "Action not found"}
            
            if original.uid != user_id:
                return {"success": False, "error": "Unauthorized"}
            
            # Record feedback
            feedback = ActionPlanFeedback(
                uid=user_id,
                plan_id=original.plan_id,
                item_id=item_id,
                feedback_type="dislike",
                action_title=original.title,
                action_category=original.category,
                target_hormone=original.target_hormone,
                replacement_reason=reason,
                was_replaced=True,
                feedback_given_at=datetime.utcnow(),
                created_at=datetime.utcnow()
            )
            db.add(feedback)
            
            # SQL-direct deactivation of original item
            await db.execute(
                update(ActionPlanItem)
                .where(ActionPlanItem.id == item_id)
                .values(
                    is_replaced=True,
                    replaced_at=datetime.utcnow(),
                    replacement_reason=reason
                )
            )
            
            # Load user context for replacement
            user_context = await self._load_user_context(user_id, db)
            
            if not user_context:
                return {"success": False, "error": "Could not load user context"}
            
            # ========================================================
            # FETCH ALL OTHER ACTIVE ACTIONS TODAY - TO TELL GPT NOT TO DUPLICATE
            # ========================================================
            other_active_actions = []
            other_action_titles = []
            try:
                other_items_result = await db.execute(
                    select(ActionPlanItem).where(
                        ActionPlanItem.plan_id == original.plan_id,
                        ActionPlanItem.uid == user_id,
                        ActionPlanItem.id != item_id,  # Exclude the one being replaced
                        ActionPlanItem.is_replaced != True  # Only active items
                    )
                )
                for item in other_items_result.scalars().all():
                    other_active_actions.append({
                        "title": item.title,
                        "category": item.category,
                        "specific_action": item.specific_action[:100] if item.specific_action else "",
                        "target_hormone": item.target_hormone
                    })
                    other_action_titles.append(item.title)
                logger.info(f"[REPLACEMENT] Found {len(other_active_actions)} other active actions: {other_action_titles}")
            except Exception as e:
                logger.warning(f"Could not load other plan items: {e}")
            
            # ========================================================
            # RESEARCH-FIRST APPROACH: Get research BEFORE generating action
            # ========================================================
            
            # Step 1: Search for relevant research based on users condition + target hormone
            from app.services.pubmed_service import execute_pubmed_tool
            
            user_conditions = user_context.get('diagnosed_conditions', [])
            condition_str = user_conditions[0] if user_conditions else "womens health"
            
            # Build search query for the target hormone and users condition
            search_query = f"{original.target_hormone} {condition_str} intervention"
            logger.info(f" RESEARCH-FIRST: Searching for '{search_query}'")
            
            research_paper = await execute_pubmed_tool({
                "action_title": f"Wellness action for {original.target_hormone}",
                "search_query": search_query
            }, db=db)
            
            if research_paper and research_paper.get("title"):
                logger.info(f" Found research: {research_paper.get('title', '')[:60]}...")
                research_context = f"""
RESEARCH EVIDENCE (USE THIS AS BASIS FOR YOUR RECOMMENDATION):

Title: {research_paper.get('title')}
Journal: {research_paper.get('journal', 'Unknown')}
Year: {research_paper.get('year', 'Unknown')}
Participants: {research_paper.get('participants', 'Unknown')} women
Key Finding: {research_paper.get('finding', 'Evidence-based intervention')}
PMID: {research_paper.get('pmid', '')}


 IMPORTANT: Your recommendation MUST be grounded in this research. 
Extract a specific intervention (food, exercise, or mindfulness practice) 
that this study shows is effective, and create your action based on that.
"""
            else:
                logger.warning(" No research found, using general recommendation")
                research_context = ""
                research_paper = {}
            
            # Step 2: Generate replacement action based on research findings
            # Build OTHER ACTIVE ACTIONS context for GPT
            other_actions_context = ""
            if other_active_actions:
                other_actions_json = json.dumps(other_active_actions, indent=2)
                other_actions_context = f"""
======================================================================
🚨 OTHER ACTIVE ACTIONS TODAY - DO NOT DUPLICATE ANY OF THESE 🚨
======================================================================
The user already has these actions in their plan. Your replacement MUST be
COMPLETELY DIFFERENT from all of these. If you suggest anything similar,
the response will be REJECTED.

BANNED TITLES: {other_action_titles}

FULL DETAILS:
{other_actions_json}

❌ Do NOT suggest similar foods (e.g., if they have "Salmon", don't suggest "Mackerel")
❌ Do NOT suggest similar exercises (e.g., if they have "Yoga", don't suggest "Stretching")
❌ Do NOT suggest the same type of thing in a different form
"""
            
            replacement_prompt = f"""Generate 1 replacement wellness action BASED ON THE RESEARCH BELOW.

🛑 CRITICAL WARNING 🛑
You MUST include ALL category-specific fields or your response will be REJECTED and regenerated.
Previous failures happened because you forgot exercise_types, exercise_durations, exercise_intensities for movement.

{other_actions_context}

{research_context}

REQUIREMENTS:
- Must target hormone: {original.target_hormone}
- Should be DIFFERENT from: {original.title} (user disliked this)
- Dislike reason: {reason or 'not specified'}
- If the reason includes a specific requested item (e.g., "replace with cashews"), you MUST use that exact item as the core of the new action.
- AVOID generating same category as disliked ({original.category}) unless users lifestyle_focus only includes that category
- Prefer different category from: {original.category}
- Users lifestyle focus: {user_context.get('lifestyle_focus', ['eat', 'move', 'pause'])}
- 🔴 MUST NOT DUPLICATE any of the other active actions listed above

======================================================================
HEALTH PROFILE
======================================================================
- Age: {user_context.get('age', 'unknown')}
- Cycle Day: {user_context.get('cycle_day', 'unknown')}
- Cycle Phase: {user_context.get('cycle_phase', 'unknown')}
- Target Hormone: {original.target_hormone}

HEALTH CONCERNS:
- Top Concern: {user_context.get('top_concern', 'general wellness')}
- Diagnosed Conditions: {', '.join(user_conditions) if user_conditions else 'none specified'}
- Period Concerns: {user_context.get('period_concerns', 'none specified')}
- Body Concerns: {user_context.get('body_concerns', 'none specified')}
- Skin/Hair Concerns: {user_context.get('skin_hair_concerns', 'none specified')}
- Mental Health Concerns: {user_context.get('mental_health_concerns', 'none specified')}
- Family History: {user_context.get('family_history', 'none specified')}

======================================================================
PERSONALIZATION FACTORS
======================================================================
- Lifestyle Focus: {user_context.get('lifestyle_focus', ['eat', 'move', 'pause'])}
- Diet Preference: {user_context.get('diet_preference', 'none')}
- Food Allergies/Restrictions: {user_context.get('food_allergies', 'none')}
- Stress Level: {user_context.get('stress_level', 'moderate')}
- Sleep Duration: {user_context.get('sleep_duration', '7-8 hours')}
- Workout Intensity: {user_context.get('workout_intensity', 'moderate')}
- Birth Control: {user_context.get('birth_control', 'none')}
- Current Streak: {user_context.get('current_streak', 0)} days
- Longest Streak: {user_context.get('longest_streak', 0)} days

======================================================================
FEEDBACK MEMORY (Critical - avoid disliked patterns, repeat liked patterns)
======================================================================
HISTORICAL SUMMARY (learned patterns over time):
{user_context.get('feedback_summary', 'No summary yet')}

RECENT FEEDBACK (last 20-50 actions):
{user_context.get('feedback_memory', 'No previous feedback')}

======================================================================
CHATBOT CONVERSATION CONTEXT
======================================================================
{user_context.get('chatbot_context', 'No recent chatbot conversations')}

======================================================================
WEEKLY CHECK-IN INSIGHTS (Recent symptom reports)
======================================================================
{user_context.get('weekly_checkin_insights', 'No weekly check-in data yet')}

 MANDATORY CATEGORY-SPECIFIC FIELDS - DO NOT SKIP:

IF category="food":
   MUST have: "food_items": ["salmon", "avocado", "blueberries"]
   MUST have: "food_amounts": ["4 oz", "half avocado", "1 cup"]

IF category="movement":
   MUST have: "exercise_types": ["yoga", "walking", "stretching"]  
   MUST have: "exercise_durations": ["15 min", "20 minutes", "30 min"]
   MUST have: "exercise_intensities": ["low", "moderate", "gentle"]

IF category="mindfulness":
   MUST have: "mindfulness_techniques": ["deep breathing", "meditation", "body scan"]
   MUST have: "mindfulness_durations": ["5 min", "10 minutes", "15 min"]

REQUIRED OUTPUT FIELDS (ALL actions):
1. category: "food", "movement", or "mindfulness"
2. title: Simple, clean name (just the food item, activity, or technique - see TITLE RULES section)
3. time_slot: "morning", "afternoon", or "evening"
4. specific_action: Detailed description GROUNDED IN THE RESEARCH above
5. purpose: Explain WHY this works, citing the research mechanism
6. target_hormone: MUST be "{original.target_hormone}"
7. hormone_persona_intro: First-person intro from hormone perspective
8. image_prompt: FLUX.1 Schnell optimized prompt
9. research_studies: Use the research provided above - format as single-item array with the paper details
10. variants: Array of 3 variant objects - each showing DIFFERENT WAYS to consume/do the action
11. symptoms: Pick 1-3 from users health concerns
12. conditions: Array of conditions this helps

 TITLE RULES (RAW INGREDIENT/ACTIVITY NAME ONLY!):
- FOOD: Just the ingredient ("Salmon" NOT "Grilled Salmon", "Quinoa" NOT "Quinoa Bowl")
- MOVEMENT: Just the activity (e.g., "Morning Yoga", "Post-Meal Walk", "Hip Stretches")
- MINDFULNESS: Just the technique (e.g., "Deep Breathing", "Body Scan", "Meditation")
- NO preparation methods (latte, tea, smoothie) - those go in specific_action!

 BEFORE RESPONDING: Double-check that you included ALL category-specific arrays.

Respond with valid JSON object only."""


            # Generate replacement via GPT (no tool calling - research already fetched)
            MAX_REPLACEMENT_RETRIES = 2
            replacement_action = None
            
            for attempt in range(1, MAX_REPLACEMENT_RETRIES + 1):
                logger.info(f" Replacement generation attempt {attempt}/{MAX_REPLACEMENT_RETRIES}")
                
                # Try OpenAI first, fallback to Groq
                openai_error = None
                content = None
                
                if self.openai_api_key:
                    try:
                        payload = self.build_openai_payload(
                            model=self.GPT_MODEL,
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": replacement_prompt}
                            ],
                            max_tokens=2500,
                            temperature=0.7,
                            response_format={"type": "json_object"}
                        )
                        response = await self.client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.openai_api_key}",
                                "Content-Type": "application/json"
                            },
                            json=payload
                        )
                        
                        if response.status_code != 200:
                            openai_error = f"OpenAI returned {response.status_code}"
                            logger.warning(f" {openai_error}")
                        else:
                            data = response.json()
                            content = data["choices"][0]["message"].get("content", "{}")
                            logger.info(" Replacement action generated via OpenAI")
                    except Exception as e:
                        openai_error = str(e)
                        logger.warning(f" OpenAI exception: {openai_error[:200]}")
                else:
                    openai_error = "No OpenAI API key"
                
                # Groq fallback
                if openai_error and GROQ_API_KEY:
                    try:
                        logger.info(f" Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                        
                        # gpt-oss-120b is a reasoning model - doesn't support response_format
                        is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                        enhanced_prompt = replacement_prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown." if is_reasoning_model else replacement_prompt
                        
                        response = await self.client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {GROQ_API_KEY}",
                                "Content-Type": "application/json"
                            },
                            json={
                                "model": GROQ_FALLBACK_MODEL,
                                "messages": [
                                    {"role": "system", "content": SYSTEM_PROMPT},
                                    {"role": "user", "content": enhanced_prompt}
                                ],
                                "temperature": 0.7,
                                "max_tokens": 2500
                            },
                            timeout=90.0
                        )
                        
                        if response.status_code != 200:
                            raise Exception(f"Groq returned {response.status_code}")
                        
                        data = response.json()
                        content = data["choices"][0]["message"].get("content", "{}")
                        logger.info(" Replacement action generated via Groq fallback")
                    except Exception as e:
                        logger.error(f" Groq fallback also failed: {e}")
                        continue  # Try next attempt
                elif openai_error:
                    logger.error(f" OpenAI failed and no Groq fallback: {openai_error}")
                    continue  # Try next attempt
                
                if not content:
                    continue
                
                # Parse replacement action
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                
                try:
                    parsed_action = json.loads(content.strip())
                    
                    # Handle various response formats
                    if isinstance(parsed_action, list) and len(parsed_action) > 0:
                        parsed_action = parsed_action[0]
                    elif isinstance(parsed_action, dict) and "actions" in parsed_action:
                        parsed_action = parsed_action["actions"][0]
                    
                    # Normalize category
                    if "category" in parsed_action:
                        parsed_action["category"] = parsed_action["category"].lower()
                    
                    # Inject the pre-fetched research if GPT didn't include it
                    if research_paper and research_paper.get("title"):
                        if not parsed_action.get("research_studies") or not isinstance(parsed_action.get("research_studies"), list):
                            parsed_action["research_studies"] = [research_paper]
                            logger.info(" Injected pre-fetched research paper into action")
                        elif isinstance(parsed_action.get("research_studies"), dict):
                            # GPT returned a dict instead of list
                            parsed_action["research_studies"] = [research_paper]
                    
                    # Validate the action
                    category = parsed_action.get("category", "food")
                    valid, missing = self._validate_action_fields(parsed_action, category)
                    
                    if valid:
                        logger.info(f" Replacement action valid")
                        replacement_action = parsed_action
                        break
                    else:

                        logger.warning(f" Attempt {attempt} missing fields: {missing}")
                        if attempt >= MAX_REPLACEMENT_RETRIES:
                            logger.warning(" Applying minimal fallbacks for replacement")
                            replacement_action = self._fill_missing_fields([parsed_action])[0]
                            
                except json.JSONDecodeError as je:
                    logger.error(f"JSON parse error: {je}")
                    continue
            
            # Check if we got a valid replacement action
            if not replacement_action:
                logger.error("Failed to generate valid replacement action after retries")
                await db.rollback()
                return {"success": False, "error": "Failed to generate replacement. Please try again."}
            
            # Normalize research_studies to always be a list
            rs = replacement_action.get("research_studies")
            if rs is None:
                replacement_action["research_studies"] = []
            elif isinstance(rs, dict):
                # GPT sometimes returns a single dict instead of a list of dicts
                replacement_action["research_studies"] = [rs]
            elif not isinstance(rs, list):
                replacement_action["research_studies"] = []

            # ================================================================
            # POST-GENERATION DEDUPLICATION FOR SINGLE REPLACEMENT
            # Check if replacement duplicates any other plan item
            # ================================================================
            other_plan_items = []
            try:
                all_items_result = await db.execute(
                    select(ActionPlanItem).where(
                        ActionPlanItem.plan_id == original.plan_id,
                        ActionPlanItem.uid == user_id,
                        ActionPlanItem.id != item_id,  # Exclude the one being replaced
                        ActionPlanItem.is_replaced != True  # Only active items
                    )
                )
                for item in all_items_result.scalars().all():
                    other_plan_items.append({
                        "title": item.title,
                        "category": item.category,
                        "specific_action": item.specific_action,
                        "target_hormone": item.target_hormone
                    })
            except Exception as e:
                logger.warning(f"Could not load other plan items: {e}")
            
            # Generate images for replacement using TITLE for cache matching with retry
            replacement_title = replacement_action.get("title", "Wellness Action")
            replacement_category = replacement_action.get("category", "food")
            logger.info(f"[REPLACE] Generating image: '{replacement_title[:40]}' ({replacement_category})")
            
            # Retry loop for hero image
            hero_url = None
            was_cached = False
            MAX_HERO_RETRIES = 3
            for retry_attempt in range(MAX_HERO_RETRIES):
                try:
                    hero_url, was_cached, _ = await self.image_service.get_or_generate_image(
                        prompt=replacement_title,  # Use TITLE for cache matching
                        category=replacement_category,
                        variant_type="hero",
                        user_id=user_id,
                        db=db
                    )
                    if hero_url:
                        break
                    else:
                        logger.warning(f"⚠️ Hero retry {retry_attempt + 1}/{MAX_HERO_RETRIES}: empty URL")
                        await asyncio.sleep(1.0 * (retry_attempt + 1))
                except Exception as e:
                    logger.error(f"Hero image error (attempt {retry_attempt + 1}): {e}")
                    await asyncio.sleep(1.0 * (retry_attempt + 1))
            
            # Defensive check: ensure hero_url is never empty (fallback after retries)
            if not hero_url:
                logger.warning(f"[REPLACE] hero_url empty after {MAX_HERO_RETRIES} retries, using fallback for {replacement_category}")
                hero_url = self.image_service.FALLBACK_IMAGE_URLS.get(
                    replacement_category, 
                    self.image_service.FALLBACK_IMAGE_URLS["food"]
                )
            
            cache_status = "CACHE HIT" if was_cached else "GENERATED"
            logger.info(f"[REPLACE]  Image {cache_status}: '{replacement_title[:30]}...'")
            
            # Mark original as replaced
            original.is_replaced = True
            original.replaced_at = datetime.utcnow()
            original.replacement_reason = reason
            
            # Create new action item
            from app.core.database import ActionPlanItemVariant
            
            # Get conditions from user context
            action_conditions = user_context.get("diagnosed_conditions", [])
            action_symptoms = replacement_action.get("symptoms", [])
            category = replacement_action.get("category", "food")
            
            new_item = ActionPlanItem(
                plan_id=original.plan_id,
                uid=user_id,
                slot=original.slot,  # Same slot
                time_slot=replacement_action.get("time_slot", original.time_slot),
                category=category,
                title=replacement_action.get("title", "Wellness Action"),
                specific_action=replacement_action.get("specific_action", ""),
                purpose=replacement_action.get("purpose", ""),
                target_hormone=original.target_hormone,  # Must be same
                hormone_persona_intro=replacement_action.get("hormone_persona_intro", ""),
                hero_image_url=hero_url,
                hero_image_prompt=replacement_action.get("image_prompt"),
                research_studies=replacement_action.get("research_studies", []),
                conditions=action_conditions,
                symptoms=action_symptoms,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            # Add category-specific fields
            if category == "food":
                new_item.food_items = replacement_action.get("food_items", [])
                new_item.food_amounts = replacement_action.get("food_amounts", [])
            elif category == "movement":
                new_item.exercise_types = replacement_action.get("exercise_types", [])
                new_item.exercise_durations = replacement_action.get("exercise_durations", [])
                new_item.exercise_intensities = replacement_action.get("exercise_intensities", [])
            elif category == "mindfulness":
                new_item.mindfulness_techniques = replacement_action.get("mindfulness_techniques", [])
                new_item.mindfulness_durations = replacement_action.get("mindfulness_durations", [])
            
            db.add(new_item)
            await db.flush()
            
            # Generate variant images IN PARALLEL
            raw_variants = replacement_action.get("variants", [])
            valid_variants = [v for v in raw_variants[:3] if isinstance(v, dict)]
            
            # Prepare variant metadata
            variant_data = []
            for i, variant in enumerate(valid_variants):
                v_type = variant.get("variant_type")
                if not v_type or v_type == "alternative":
                    defaults = {
                        "food": ["healthy", "easy", "tasty"],
                        "movement": ["gentle", "quick", "energizing"],
                        "mindfulness": ["brief", "guided", "solo"]
                    }.get(category, ["alternative"])
                    v_type = defaults[i % len(defaults)]
                variant_data.append({"variant": variant, "v_type": v_type})
            
            # Generate variant images SEQUENTIALLY (not parallel) with retry logic
            # SQLAlchemy async sessions can't be shared across concurrent tasks
            variant_results = []
            FALLBACK_IMAGE_URLS = {
                "food": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_food_fzjqkl.jpg",
                "movement": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_movement_k8zq3n.jpg",
                "mindfulness": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_mindfulness_pqwz9m.jpg",
            }
            
            for vd in variant_data:
                variant_url = None
                MAX_VARIANT_RETRIES = 3
                
                # Safely extract variant dict and type with None checks
                variant_dict = vd.get("variant") or {}
                v_type_str = vd.get("v_type") or "alternative"
                
                # Use variant TITLE for cache matching
                variant_title = variant_dict.get("title") if variant_dict else None
                if not variant_title:
                    variant_title = f"{v_type_str} version"
                logger.info(f"[REPLACE] Generating variant: '{variant_title[:40]}' ({category})")
                
                for retry_attempt in range(MAX_VARIANT_RETRIES):
                    try:
                        result = await self.image_service.get_or_generate_image(
                            prompt=variant_title,  # Use TITLE for cache matching
                            category=category,
                            variant_type=v_type_str,
                            user_id=user_id,
                            db=db
                        )
                        variant_url, was_cached, cost = result
                        if variant_url:
                            variant_results.append(result)
                            break
                        else:
                            logger.warning(f"⚠️ Variant retry {retry_attempt + 1}/{MAX_VARIANT_RETRIES}: empty URL for '{variant_title[:30]}'")
                            await asyncio.sleep(1.0 * (retry_attempt + 1))
                    except Exception as e:
                        logger.error(f"Variant image generation error (attempt {retry_attempt + 1}): {e}")
                        await asyncio.sleep(1.0 * (retry_attempt + 1))
                
                # Apply fallback if still no URL after retries
                if not variant_url:
                    fallback_url = FALLBACK_IMAGE_URLS.get(category.lower(), FALLBACK_IMAGE_URLS["food"])
                    variant_results.append((fallback_url, False, 0.0))
                    logger.warning(f"⚠️ Using fallback for variant: '{variant_title[:30]}'")
            
            # Create variant records from results
            for i, result in enumerate(variant_results):
                variant_url, _, _ = result
                
                # Safely extract variant data with None checks
                vd = variant_data[i] if i < len(variant_data) else {}
                variant_dict = vd.get("variant") or {}
                v_type_str = vd.get("v_type") or "alternative"
                
                variant_record = ActionPlanItemVariant(
                    item_id=new_item.id,
                    variant_type=v_type_str,
                    title=variant_dict.get("title", "") if variant_dict else "",
                    description=variant_dict.get("description", "") if variant_dict else "",
                    image_url=variant_url,
                    image_prompt=variant_dict.get("image_prompt") if variant_dict else None,
                    created_at=datetime.utcnow()
                )
                db.add(variant_record)
            
            await db.commit()
            
            # Fetch the created variants to return with the response
            variants_result = await db.execute(
                select(ActionPlanItemVariant).where(
                    ActionPlanItemVariant.item_id == new_item.id
                )
            )
            created_variants = variants_result.scalars().all()
            
            logger.info(f"Replaced action {item_id} with {new_item.id} and {len(created_variants)} variants")
            
            return {
                "success": True,
                "original_id": item_id,
                "replacement_id": new_item.id,
                "replacement_action": {
                    "id": new_item.id,
                    "slot": new_item.slot,
                    "time_slot": new_item.time_slot,
                    "category": new_item.category,
                    "title": new_item.title,
                    "specific_action": new_item.specific_action,
                    "purpose": new_item.purpose,
                    "hero_image_url": new_item.hero_image_url,
                    "target_hormone": new_item.target_hormone,
                    "hormone_persona_intro": new_item.hormone_persona_intro,
                    "research_studies": new_item.research_studies or [],
                    "symptoms": new_item.symptoms or [],
                    "conditions": new_item.conditions or [],
                    # Category-specific fields
                    "food_items": new_item.food_items if category == "food" else None,
                    "food_amounts": new_item.food_amounts if category == "food" else None,
                    "exercise_types": new_item.exercise_types if category == "movement" else None,
                    "exercise_durations": new_item.exercise_durations if category == "movement" else None,
                    "exercise_intensities": new_item.exercise_intensities if category == "movement" else None,
                    "mindfulness_techniques": new_item.mindfulness_techniques if category == "mindfulness" else None,
                    "mindfulness_durations": new_item.mindfulness_durations if category == "mindfulness" else None,
                    "variants": [
                        {
                            "variant_type": v.variant_type,
                            "title": v.title,
                            "description": v.description,
                            "image_url": v.image_url
                        }
                        for v in created_variants
                    ]
                }
            }
            
        except Exception as e:
            import traceback
            logger.error(f"Error replacing action: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            await db.rollback()
            return {"success": False, "error": "Failed to replace action. Please try again."}

    async def generate_replacement_candidates(
        self,
        user_id: str,
        item_id: int,
        reason: Optional[str],
        n: int,
        db: AsyncSession,
        enforce_same_category: bool = True,  # Changed default to True
    ) -> Dict[str, Any]:
        """Generate N replacement candidates for a single plan item (preview-only).

        This does NOT mutate the plan and does NOT generate images.
        """
        from app.core.database import ActionPlanItem

        n = max(2, min(int(n or 3), 6))

        try:
            # Get the original action
            result = await db.execute(select(ActionPlanItem).where(ActionPlanItem.id == item_id))
            original = result.scalar_one_or_none()
            if not original:
                return {"success": False, "error": "Action not found"}
            if original.uid != user_id:
                return {"success": False, "error": "Unauthorized"}

            original_category = (getattr(original, "category", None) or "").strip().lower() or "food"
            original_hormone = getattr(original, "target_hormone", None) or "cortisol"

            # Load user context
            user_context = await self._load_user_context(user_id, db)
            if not user_context:
                return {"success": False, "error": "Could not load user context"}

            # ========================================================
            # FETCH ALL OTHER ACTIVE ACTIONS TODAY - TO TELL GPT NOT TO DUPLICATE
            # ========================================================
            other_active_actions = []
            other_action_titles = []
            try:
                other_items_result = await db.execute(
                    select(ActionPlanItem).where(
                        ActionPlanItem.plan_id == original.plan_id,
                        ActionPlanItem.uid == user_id,
                        ActionPlanItem.id != item_id,  # Exclude the one being replaced
                        ActionPlanItem.is_replaced != True  # Only active items
                    )
                )
                for item in other_items_result.scalars().all():
                    other_active_actions.append({
                        "title": item.title,
                        "category": item.category,
                        "target_hormone": item.target_hormone
                    })
                    other_action_titles.append(item.title)
                logger.info(f"[CANDIDATES] Found {len(other_active_actions)} other active actions: {other_action_titles}")
            except Exception as e:
                logger.warning(f"Could not load other plan items: {e}")

            # Fetch research paper for same category + hormone + user condition
            from app.services.pubmed_service import execute_pubmed_tool

            user_conditions = user_context.get("diagnosed_conditions", [])
            condition_str = user_conditions[0] if user_conditions else "womens health"
            
            # Build category-specific search query
            category_terms = {
                "food": "diet nutrition food",
                "movement": "exercise physical activity",
                "mindfulness": "meditation mindfulness relaxation"
            }
            cat_term = category_terms.get(original_category, "wellness")
            search_query = f"{original_hormone} {condition_str} {cat_term}"
            logger.info(f" CANDIDATES: Searching for '{search_query}' (category={original_category})")

            research_paper = await execute_pubmed_tool(
                {"action_title": f"{original_category} action for {original_hormone}", "search_query": search_query},
                db=db,
            )

            research_context = ""
            if research_paper and research_paper.get("title"):
                research_context = f"""
RESEARCH EVIDENCE (use as grounding for your recommendations):
Title: {research_paper.get('title')}
Journal: {research_paper.get('journal', 'Unknown')}
Year: {research_paper.get('year', 'Unknown')}
Participants: {research_paper.get('participants', 'Unknown')} women
Key Finding: {research_paper.get('finding', 'Evidence-based intervention')}
PMID: {research_paper.get('pmid', '')}
""".strip()

            # Category-specific field requirements
            category_fields = {
                "food": "food_items (array), food_amounts (array)",
                "movement": "exercise_types (array), exercise_durations (array), exercise_intensities (array)",
                "mindfulness": "mindfulness_techniques (array), mindfulness_durations (array)"
            }
            required_fields = category_fields.get(original_category, "")

            # Build OTHER ACTIVE ACTIONS context for GPT
            other_actions_context = ""
            if other_active_actions:
                other_actions_context = f"""
======================================================================
🚨 OTHER ACTIVE ACTIONS TODAY - NONE OF YOUR CANDIDATES CAN BE SIMILAR 🚨
======================================================================
The user already has these in their plan. ALL your candidate replacements
MUST be COMPLETELY DIFFERENT from all of these.

BANNED TITLES: {other_action_titles}
❌ Do NOT suggest similar foods/exercises/mindfulness to any of these
"""

            prompt = f"""Generate {n} DIFFERENT replacement wellness actions.

{other_actions_context}

╔══════════════════════════════════════════════════════════════════════════════╗
║  CRITICAL: ALL alternatives MUST be {original_category.upper()} category!              ║
║  User is replacing a {original_category} item → suggest ONLY other {original_category} options!          ║
╚══════════════════════════════════════════════════════════════════════════════╝

{research_context}

USER SPECIFIC REQUEST OVERRIDE:
If the user's request mentions a SPECIFIC activity type (e.g., "I want dance", "change to swimming", "try yoga"), 
then ALL {n} alternatives MUST be different variations of ONLY that specific activity.
Examples:
- Request: "change to dance" → ALL {n} must be dance types (Zumba, Hip Hop, Salsa, etc.)
- Request: "I want swimming" → ALL {n} must be swimming types (Laps, Water Aerobics, etc.)
- Request: "try yoga" → ALL {n} must be yoga styles (Vinyasa, Hatha, Power Yoga, etc.)

STRICT REQUIREMENTS:
1. Category MUST be: {original_category} (DO NOT suggest other categories!)
2. Target hormone MUST be: {original_hormone}
3. Must be DIFFERENT from: {original.title}
4. User Request / Context: {reason or 'user wants alternatives'}
    ⚠️ CRITICAL: If the request clearly specifies a replacement item or activity (e.g., "replace with cashews", "swap to yoga", "dance"),
    then ALL {n} alternatives MUST be variations of that specific item/activity. Do NOT mix unrelated options.
5. MUST include category-specific fields: {required_fields}

USER HEALTH PROFILE (tailor recommendations to this):
- Age: {user_context.get('age', 'unknown')}
- Cycle Phase: {user_context.get('cycle_phase', 'unknown')}
- Top Health Concern: {user_context.get('top_concern', 'general wellness')}
- Diagnosed Conditions: {', '.join(user_conditions) if user_conditions else 'none'}
- Diet Preference: {user_context.get('diet_preference', 'none')}
- Food Allergies/Restrictions: {user_context.get('food_allergies', 'none')}

OUTPUT FORMAT:
Return a JSON OBJECT with exactly this shape:
{{
  "actions": [ ...{n} action objects... ]
}}

Each action object MUST include:
- category: "{original_category}" (MUST be this exact value!)
- title, time_slot, specific_action, purpose
- target_hormone: "{original_hormone}" (MUST be this exact value!)
- hormone_persona_intro, image_prompt
- research_studies: array (include the research above if relevant)
- variants: array of 3 variant objects
- symptoms: array of 1-3 symptom keywords
- conditions: array

CATEGORY-SPECIFIC REQUIRED FIELDS:
IF category="food": food_items[], food_amounts[]
IF category="movement": exercise_types[], exercise_durations[], exercise_intensities[]
IF category="mindfulness": mindfulness_techniques[], mindfulness_durations[]

Respond with valid JSON only."""

            # Generate via OpenAI or Groq
            content = None
            openai_error = None

            if self.openai_api_key:
                try:
                    payload = self.build_openai_payload(
                        model=self.GPT_MODEL,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=4500,
                        temperature=0.5,
                        response_format={"type": "json_object"}
                    )
                    response = await self.client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.openai_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )

                    if response.status_code != 200:
                        openai_error = f"OpenAI returned {response.status_code}"
                        logger.warning(f" {openai_error}")
                    else:
                        data = response.json()
                        content = data["choices"][0]["message"].get("content", "{}")
                        logger.info(" Candidates generated via OpenAI")
                except Exception as e:
                    openai_error = str(e)
                    logger.warning(f" OpenAI exception: {openai_error[:200]}")
            else:
                openai_error = "No OpenAI API key"

            if openai_error and GROQ_API_KEY:
                try:
                    logger.info(f" Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                    enhanced = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown."
                    response = await self.client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {GROQ_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": GROQ_FALLBACK_MODEL,
                            "messages": [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": enhanced},
                            ],
                            "temperature": 0.5,
                            "max_tokens": 4500,
                        },
                        timeout=120.0,
                    )
                    if response.status_code != 200:
                        raise Exception(f"Groq returned {response.status_code}")
                    data = response.json()
                    content = data["choices"][0]["message"].get("content", "{}")
                    logger.info(" Candidates generated via Groq fallback")
                except Exception as e:
                    logger.error(f" Groq fallback failed: {e}")

            if not content:
                return {"success": False, "error": "Failed to generate alternate suggestions"}

            # Clean common fenced output
            if content.startswith("```"):
                content = content.split("```", 2)[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            try:
                response_data = json.loads(content)
            except Exception as e:
                logger.error(f"Candidates JSON parse error: {e}")
                return {"success": False, "error": "Model returned invalid JSON"}

            actions = None
            if isinstance(response_data, dict) and isinstance(response_data.get("actions"), list):
                actions = response_data.get("actions")
            elif isinstance(response_data, list):
                actions = response_data

            if not actions or not isinstance(actions, list):
                return {"success": False, "error": "No candidates returned"}

            actions = [a for a in actions if isinstance(a, dict)][:n]
            if not actions:
                return {"success": False, "error": "No valid candidates returned"}

            # Normalize and validate
            for a in actions:
                if "category" in a:
                    a["category"] = str(a.get("category") or "").lower()
                a["target_hormone"] = original.target_hormone

                # Inject research paper if present and missing
                if research_paper and research_paper.get("title"):
                    rs = a.get("research_studies")
                    if rs is None or rs == []:
                        a["research_studies"] = [research_paper]
                    elif isinstance(rs, dict):
                        a["research_studies"] = [rs]
                    elif not isinstance(rs, list):
                        a["research_studies"] = [research_paper]

            filled_actions = self._fill_missing_fields(actions)

            # Ensure we don't return actions that are totally invalid
            valid_actions: List[Dict[str, Any]] = []
            for a in filled_actions:
                cat = (a.get("category") or "food").lower()

                # For alternate-suggestions UX we want true category alternates (e.g. food -> food).
                if enforce_same_category and cat != original_category:
                    continue

                ok, _missing = self._validate_action_fields(a, cat)
                if ok:
                    valid_actions.append(a)

            if not valid_actions:
                return {"success": False, "error": "Generated candidates were missing required fields"}

            # ================================================================
            # POST-GENERATION DEDUPLICATION FOR CANDIDATES
            # Get other items in the plan and filter out duplicate candidates
            # ================================================================
            other_plan_items = []
            try:
                all_items_result = await db.execute(
                    select(ActionPlanItem).where(
                        ActionPlanItem.plan_id == original.plan_id,
                        ActionPlanItem.uid == user_id,
                        ActionPlanItem.id != item_id,  # Exclude the one being replaced
                        ActionPlanItem.is_replaced != True  # Only active items
                    )
                )
                for item in all_items_result.scalars().all():
                    other_plan_items.append({
                        "title": item.title,
                        "category": item.category,
                        "specific_action": item.specific_action,
                        "target_hormone": item.target_hormone
                    })
            except Exception as e:
                logger.warning(f"Could not load other plan items: {e}")
            
            # Trust the prompt - just return valid actions without extra deduplication
            logger.info(f"✅ Generated {len(valid_actions)} replacement candidates")
            return {"success": True, "actions": valid_actions[:n]}

        except Exception as e:
            import traceback
            logger.error(f"Error generating replacement candidates: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"success": False, "error": "Failed to generate alternate suggestions"}

    async def replace_action_from_action_dict(
        self,
        user_id: str,
        item_id: int,
        replacement_action: Dict[str, Any],
        reason: Optional[str],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Replace an action using a pre-generated replacement_action dict.

        This is used by the care-plan alternate-suggestions flow where the user picks
        a specific candidate.
        """
        from app.core.database import ActionPlanItem, ActionPlanFeedback

        try:
            result = await db.execute(select(ActionPlanItem).where(ActionPlanItem.id == item_id))
            original = result.scalar_one_or_none()
            if not original:
                return {"success": False, "error": "Action not found"}
            if original.uid != user_id:
                return {"success": False, "error": "Unauthorized"}

            category = (replacement_action.get("category") or original.category or "food").lower()
            replacement_action["category"] = category
            replacement_action["target_hormone"] = original.target_hormone

            # Validate and minimally fill missing fields
            ok, _missing = self._validate_action_fields(replacement_action, category)
            if not ok:
                replacement_action = self._fill_missing_fields([replacement_action])[0]

            # Normalize research_studies
            rs = replacement_action.get("research_studies")
            if rs is None:
                replacement_action["research_studies"] = []
            elif isinstance(rs, dict):
                replacement_action["research_studies"] = [rs]
            elif not isinstance(rs, list):
                replacement_action["research_studies"] = []

            # ================================================================
            # POST-GENERATION DEDUPLICATION FOR CANDIDATE REPLACEMENT
            # Check if the selected candidate duplicates any other plan item
            # ================================================================
            other_plan_items = []
            try:
                all_items_result = await db.execute(
                    select(ActionPlanItem).where(
                        ActionPlanItem.plan_id == original.plan_id,
                        ActionPlanItem.uid == user_id,
                        ActionPlanItem.id != item_id,  # Exclude the one being replaced
                        ActionPlanItem.is_replaced != True  # Only active items
                    )
                )
                for item in all_items_result.scalars().all():
                    other_plan_items.append({
                        "title": item.title,
                        "category": item.category,
                        "specific_action": item.specific_action,
                        "target_hormone": item.target_hormone
                    })
            except Exception as e:
                logger.warning(f"Could not load other plan items: {e}")

            # Record feedback
            feedback = ActionPlanFeedback(
                uid=user_id,
                plan_id=original.plan_id,
                item_id=item_id,
                feedback_type="dislike",
                action_title=original.title,
                action_category=original.category,
                target_hormone=original.target_hormone,
                replacement_reason=reason,
                was_replaced=True,
                feedback_given_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            db.add(feedback)

            # Deactivate original
            await db.execute(
                update(ActionPlanItem)
                .where(ActionPlanItem.id == item_id)
                .values(
                    is_replaced=True,
                    replaced_at=datetime.utcnow(),
                    replacement_reason=reason,
                )
            )

            # Generate images for replacement using TITLE for cache matching
            replacement_title = replacement_action.get("title", "Wellness Action")
            logger.info(f"[REPLACE] Generating image: '{replacement_title[:40]}' ({category})")
            hero_url, was_cached, _ = await self.image_service.get_or_generate_image(
                prompt=replacement_title,  # Use TITLE for cache matching
                category=category,
                variant_type="hero",
                user_id=user_id,
                db=db,
            )
            
            # Defensive check: ensure hero_url is never empty (fallback already handled by image service)
            if not hero_url:
                logger.warning(f"[REPLACE] hero_url empty after generation, using emergency fallback for {category}")
                hero_url = self.image_service.FALLBACK_IMAGE_URLS.get(
                    category, 
                    self.image_service.FALLBACK_IMAGE_URLS["food"]
                )
            
            logger.info(f"[REPLACE]  Image {'CACHE HIT' if was_cached else 'GENERATED'}: '{replacement_title[:30]}...'")

            from app.core.database import ActionPlanItemVariant

            new_item = ActionPlanItem(
                plan_id=original.plan_id,
                uid=user_id,
                slot=original.slot,
                time_slot=replacement_action.get("time_slot", original.time_slot),
                category=category,
                title=replacement_action.get("title", "Wellness Action"),
                specific_action=replacement_action.get("specific_action", ""),
                purpose=replacement_action.get("purpose", ""),
                target_hormone=original.target_hormone,
                hormone_persona_intro=replacement_action.get("hormone_persona_intro", ""),
                hero_image_url=hero_url,
                hero_image_prompt=replacement_action.get("image_prompt"),
                research_studies=replacement_action.get("research_studies", []),
                conditions=replacement_action.get("conditions", []) or [],
                symptoms=replacement_action.get("symptoms", []) or [],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

            if category == "food":
                new_item.food_items = replacement_action.get("food_items", [])
                new_item.food_amounts = replacement_action.get("food_amounts", [])
            elif category == "movement":
                new_item.exercise_types = replacement_action.get("exercise_types", [])
                new_item.exercise_durations = replacement_action.get("exercise_durations", [])
                new_item.exercise_intensities = replacement_action.get("exercise_intensities", [])
            elif category == "mindfulness":
                new_item.mindfulness_techniques = replacement_action.get("mindfulness_techniques", [])
                new_item.mindfulness_durations = replacement_action.get("mindfulness_durations", [])

            db.add(new_item)
            await db.flush()

            # Variants (up to 3) - if none provided, generate defaults
            raw_variants = replacement_action.get("variants", [])
            if isinstance(raw_variants, str) or not isinstance(raw_variants, list):
                raw_variants = []
            valid_variants = [v for v in raw_variants[:3] if isinstance(v, dict)]

            # Generate default variants if none provided
            if not valid_variants:
                variant_type_defaults = {
                    "food": ["healthy", "easy", "tasty"],
                    "movement": ["gentle", "quick", "energizing"],
                    "mindfulness": ["brief", "guided", "solo"],
                }.get(category, ["alternative", "simpler", "quick"])
                
                title = replacement_action.get("title", "Wellness Action")
                valid_variants = [
                    {"variant_type": vt, "title": f"{vt.title()} {title}", "description": f"A {vt} way to enjoy {title}"}
                    for vt in variant_type_defaults
                ]
                logger.info(f"[REPLACE] No variants provided, generating 3 default variants for '{title[:30]}'")

            variant_data = []
            for i, variant in enumerate(valid_variants):
                v_type = variant.get("variant_type")
                if not v_type or v_type == "alternative":
                    defaults = {
                        "food": ["healthy", "easy", "tasty"],
                        "movement": ["gentle", "quick", "energizing"],
                        "mindfulness": ["brief", "guided", "solo"],
                    }.get(category, ["alternative"])
                    v_type = defaults[i % len(defaults)]
                variant_data.append({"variant": variant, "v_type": v_type})

            for vd in variant_data:
                try:
                    # Defensive: build variant_prompt with explicit None checks
                    variant_dict = vd.get("variant") or {}
                    v_type_str = vd.get("v_type") or "alternative"
                    replacement_title = replacement_action.get("title") or "Action"
                    
                    variant_prompt = (
                        variant_dict.get("image_prompt")
                        or variant_dict.get("title")
                        or f"{v_type_str.title()} {replacement_title}"
                    )
                    
                    # Final fallback if still None
                    if not variant_prompt:
                        variant_prompt = f"{v_type_str.title()} {replacement_title}"
                    
                    variant_url, _, _ = await self.image_service.get_or_generate_image(
                        prompt=variant_prompt,
                        category=category,
                        variant_type=v_type_str,
                        user_id=user_id,
                        db=db,
                    )
                except Exception as e:
                    logger.error(f"Variant image generation failed: {e}")
                    variant_url = ""

                if not variant_url:
                    variant_url = self.image_service.FALLBACK_IMAGE_URLS.get(
                        category, self.image_service.FALLBACK_IMAGE_URLS["food"]
                    )
                    logger.warning(
                        f"[REPLACE] Using fallback image for variant '{v_type_str}' ({category})"
                    )

                # Safely extract variant data with None checks
                variant_title = variant_dict.get("title", "") if variant_dict else ""
                variant_description = variant_dict.get("description", "") if variant_dict else ""
                variant_image_prompt = variant_dict.get("image_prompt") if variant_dict else None

                variant_record = ActionPlanItemVariant(
                    item_id=new_item.id,
                    variant_type=v_type_str,
                    title=variant_title,
                    description=variant_description,
                    image_url=variant_url,
                    image_prompt=variant_image_prompt,
                    created_at=datetime.utcnow(),
                )
                db.add(variant_record)

            await db.commit()

            variants_result = await db.execute(select(ActionPlanItemVariant).where(ActionPlanItemVariant.item_id == new_item.id))
            created_variants = variants_result.scalars().all()

            return {
                "success": True,
                "original_id": item_id,
                "replacement_id": new_item.id,
                "replacement_action": {
                    "id": new_item.id,
                    "slot": new_item.slot,
                    "time_slot": new_item.time_slot,
                    "category": new_item.category,
                    "title": new_item.title,
                    "specific_action": new_item.specific_action,
                    "purpose": new_item.purpose,
                    "hero_image_url": new_item.hero_image_url,
                    "target_hormone": new_item.target_hormone,
                    "hormone_persona_intro": new_item.hormone_persona_intro,
                    "research_studies": new_item.research_studies or [],
                    "symptoms": new_item.symptoms or [],
                    "conditions": new_item.conditions or [],
                    "food_items": new_item.food_items if category == "food" else None,
                    "food_amounts": new_item.food_amounts if category == "food" else None,
                    "exercise_types": new_item.exercise_types if category == "movement" else None,
                    "exercise_durations": new_item.exercise_durations if category == "movement" else None,
                    "exercise_intensities": new_item.exercise_intensities if category == "movement" else None,
                    "mindfulness_techniques": new_item.mindfulness_techniques if category == "mindfulness" else None,
                    "mindfulness_durations": new_item.mindfulness_durations if category == "mindfulness" else None,
                    "variants": [
                        {
                            "variant_type": v.variant_type,
                            "title": v.title,
                            "description": v.description,
                            "image_url": v.image_url,
                        }
                        for v in created_variants
                    ],
                },
            }

        except Exception as e:
            import traceback
            logger.error(f"Error replacing action from dict: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            await db.rollback()
            return {"success": False, "error": "Failed to replace action. Please try again."}
    
    async def batch_replace_actions(
        self,
        user_id: str,
        plan_id: int,
        item_ids: List[int],
        reasons: Optional[Dict[int, str]] = None,
        db: AsyncSession = None
    ) -> Dict[str, Any]:
        """
        Replace multiple actions at once (30-second feedback flow).
        
        Each replacement targets the SAME hormone as the original.
        Generates new actions and images for all items in batch.
        """
        from app.core.database import ActionPlanItem, ActionPlanItemVariant
        
        if not item_ids:
            return {"success": False, "error": "No items to replace"}
        
        reasons = reasons or {}
        total_cost = 0.0
        replacements = []
        
        try:
            # Get all original items
            result = await db.execute(
                select(ActionPlanItem).where(
                    ActionPlanItem.id.in_(item_ids),
                    ActionPlanItem.plan_id == plan_id,
                    ActionPlanItem.uid == user_id
                )
            )
            original_items = result.scalars().all()
            
            if len(original_items) != len(item_ids):
                return {"success": False, "error": "Some items not found or unauthorized"}
            
            # Get ALL items in this plan to avoid generating duplicates
            all_items_result = await db.execute(
                select(ActionPlanItem).where(
                    ActionPlanItem.plan_id == plan_id,
                    ActionPlanItem.uid == user_id
                )
            )
            all_plan_items = all_items_result.scalars().all()
            
            # Build list of OTHER actions user already has (not being replaced)
            item_ids_set = set(item_ids)
            other_current_actions = []
            for item in all_plan_items:
                if item.id not in item_ids_set:
                    other_current_actions.append({
                        "title": item.title,
                        "category": item.category,
                        "target_hormone": item.target_hormone
                    })
            
            # Load user context
            user_context = await self._load_user_context(user_id, db)
            if not user_context:
                return {"success": False, "error": "Could not load user context"}
            
            # Build batch replacement prompt
            items_to_replace = []
            for item in original_items:
                items_to_replace.append({
                    "slot": item.slot,
                    "original_title": item.title,
                    "original_category": item.category,
                    "target_hormone": item.target_hormone,
                    "reason": reasons.get(item.id, "user disliked")
                })
            
            # Build batch replacement prompt with full context and schema
            batch_prompt = f"""
USER PROFILE:
- Age: {user_context.get('age', 'Unknown')}
- Cycle Day: {user_context.get('cycle_day', 'Unknown')}
- Cycle Phase: {user_context.get('cycle_phase', 'Unknown')}
- Primary Hormone: {user_context.get('primary_hormone', 'Unknown')}
- Conditions: {user_context.get('diagnosed_conditions', [])}
- Food Allergies: {user_context.get('food_allergies', [])}
- Diet: {user_context.get('diet_preference', 'None')}

ITEMS TO REPLACE:
{json.dumps(items_to_replace, indent=2)}

TASK:
For each item in the list above, generate a BRAND NEW replacement action.
1. Must target the SAME hormone (`target_hormone`) as the original.
2. Must be suitable for the user's conditions (SAFE).
3. Must address the specific `reason` for dislike/replacement.
4. DO NOT repeat the original action.
5. Use the `search_research_paper` tool to find REAL scientific backing.

OUTPUT FORMAT (JSON Array):
[
  {{
    "title": "Action Title",
    "category": "food" | "movement" | "mindfulness",
    "time_slot": "morning" | "afternoon" | "evening",
    "specific_action": "Detailed description of what to do",
    "purpose": "Scientific explanation of WHY this helps the target hormone",
    "target_hormone": "Name of hormone",
    "hormone_persona": "First-person intro from the hormone (e.g., 'I am Cortisol...')",
    "benefits": ["benefit 1", "benefit 2"],
    "research_studies": [], 
    "image_prompt": "Detailed prompt for generating an image of this action",
    "food_items": ["item1"], 
    "food_amounts": ["amount1"],
    "exercise_types": [],
    "exercise_durations": [],
    "mindfulness_techniques": [],
    "mindfulness_durations": []
  }}
]
"""

            # Generate replacements via GPT with retry logic
            replacement_actions = None
            gpt_cost = 0.0
            
            for attempt in range(1, self.MAX_RETRIES + 1):
                logger.info(f" Replacement generation attempt {attempt}/{self.MAX_RETRIES}")
                
                # Try OpenAI first, fallback to Groq
                openai_error = None
                content = None
                
                if self.openai_api_key:
                    try:
                        # Generate replacement actions WITH tool calling for real citations
                        response = await self.client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.openai_api_key}",
                                "Content-Type": "application/json"
                            },
                            json={
                                "model": self.GPT_MODEL,
                                "messages": [
                                    {"role": "system", "content": SYSTEM_PROMPT},
                                    {"role": "user", "content": batch_prompt}
                                ],
                                "tools": [PUBMED_SEARCH_TOOL],
                                "tool_choice": "auto",
                                "temperature": 0.3,
                                "max_completion_tokens": 8000
                            }
                        )
                        
                        if response.status_code != 200:
                            openai_error = f"OpenAI returned {response.status_code}"
                            logger.warning(f" {openai_error}")
                        else:
                            data = response.json()
                            
                            # Calculate GPT cost
                            input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
                            output_tokens = data.get("usage", {}).get("completion_tokens", 0)
                            attempt_cost = (input_tokens * 0.00015 / 1000) + (output_tokens * 0.0006 / 1000)
                            gpt_cost += attempt_cost
                            
                            message = data["choices"][0]["message"]
                            
                            # Handle tool calls if GPT wants to search for papers
                            if message.get("tool_calls"):
                                logger.info(f" GPT requested {len(message['tool_calls'])} tool calls for replacement citations")
                                
                                logger.info(f" GPT requested {len(message['tool_calls'])} tool calls for replacement citations")
                                
                                async def process_tool_call(tool_call):
                                    """Helper to process a single tool call in parallel."""
                                    try:
                                        if tool_call["function"]["name"] == "search_research_paper":
                                            args = json.loads(tool_call["function"]["arguments"])
                                            # logger.info(f"   Searching for: {args.get('action_title', 'unknown')}")
                                            
                                            # CRITICAL: Pass db=None to safe-guard against SQLAlchemy AsyncSession race conditions
                                            # We rely on Memory Cache + Parallel API fetching here.
                                            paper = await execute_pubmed_tool(args, db=None)
                                            
                                            if paper and paper.get("title"):
                                                logger.info(f"   Found: {paper.get('title', '')[:50]}... (PMID: {paper.get('pmid', 'N/A')})")
                                            else:
                                                logger.warning(f"   No paper found for: {args.get('action_title', 'unknown')}")
                                            
                                            return {
                                                "tool_call_id": tool_call["id"],
                                                "role": "tool",
                                                "content": json.dumps(paper) if paper else json.dumps({"error": "No papers found"})
                                            }
                                        else:
                                            # Unknown tool
                                            return {
                                                "tool_call_id": tool_call["id"],
                                                "role": "tool",
                                                "content": json.dumps({"error": "Unknown tool"})
                                            }
                                    except Exception as e:
                                        logger.error(f"Error processing tool call {tool_call['id']}: {e}")
                                        return {
                                            "tool_call_id": tool_call["id"],
                                            "role": "tool",
                                            "content": json.dumps({"error": f"Error: {str(e)}"})
                                        }

                                # Execute all tool calls in parallel
                                tasks = [process_tool_call(tc) for tc in message["tool_calls"]]
                                tool_results = await asyncio.gather(*tasks)
                                
                                # Send tool results back to GPT
                                assistant_message = {
                                    "role": "assistant",
                                    "content": message.get("content"),
                                    "tool_calls": message.get("tool_calls")
                                }
                                
                                response2 = await self.client.post(
                                    "https://api.openai.com/v1/chat/completions",
                                    headers={
                                        "Authorization": f"Bearer {self.openai_api_key}",
                                        "Content-Type": "application/json"
                                    },
                                    json={
                                        "model": self.GPT_MODEL,
                                        "messages": [
                                            {"role": "system", "content": SYSTEM_PROMPT},
                                            {"role": "user", "content": batch_prompt},
                                            assistant_message,
                                            *tool_results
                                        ],
                                        "temperature": 0.3,
                                        "max_completion_tokens": 8000,
                                        "response_format": {"type": "json_object"}
                                    }
                                )
                                
                                if response2.status_code != 200:
                                    openai_error = f"OpenAI second call returned {response2.status_code}"
                                    logger.warning(f" {openai_error}")
                                else:
                                    data = response2.json()
                                    
                                    # Add second call cost
                                    input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
                                    output_tokens = data.get("usage", {}).get("completion_tokens", 0)
                                    gpt_cost += (input_tokens * 0.00015 / 1000) + (output_tokens * 0.0006 / 1000)
                                    
                                    content = data["choices"][0]["message"]["content"]
                                    logger.info(" Batch replacements generated via OpenAI")
                            else:
                                # GPT didn't call tools - use response as-is
                                logger.warning(" GPT did not call tools - replacement citations may be fabricated")
                                content = message.get("content", "{}")
                    except Exception as e:
                        openai_error = str(e)
                        logger.warning(f" OpenAI exception: {openai_error[:200]}")
                else:
                    openai_error = "No OpenAI API key"
                
                # Groq fallback (no tool calling support - will generate without PubMed research)
                if openai_error and GROQ_API_KEY:
                    try:
                        logger.info(f" Falling back to Groq ({GROQ_FALLBACK_MODEL}) - no tool calling")
                        
                        # gpt-oss-120b doesn't support response_format, add JSON instructions
                        enhanced_prompt = batch_prompt + "\n\nIMPORTANT: Respond with valid JSON array only. No markdown, no explanation. Set research_studies to empty array []."
                        
                        response = await self.client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {GROQ_API_KEY}",
                                "Content-Type": "application/json"
                            },
                            json={
                                "model": GROQ_FALLBACK_MODEL,
                                "messages": [
                                    {"role": "system", "content": SYSTEM_PROMPT},
                                    {"role": "user", "content": enhanced_prompt}
                                ],
                                "temperature": 0.3,
                                "max_tokens": 8000
                            },
                            timeout=120.0
                        )
                        
                        if response.status_code != 200:
                            raise Exception(f"Groq returned {response.status_code}")
                        
                        data = response.json()
                        content = data["choices"][0]["message"]["content"]
                        
                        # Clean reasoning model output
                        if content.startswith("```json"):
                            content = content[7:]
                        if content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        content = content.strip()
                        
                        logger.info(" Batch replacements generated via Groq fallback")
                    except Exception as e:
                        logger.error(f" Groq fallback also failed: {e}")
                        continue  # Try next attempt
                elif openai_error:
                    logger.error(f" OpenAI failed and no Groq fallback: {openai_error}")
                    continue  # Try next attempt
                
                if not content:
                    continue
                
                # Parse response
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                
                logger.info(f" First 300 chars of content: {content[:300]}")
                
                # Parse JSON
                response_data = json.loads(content.strip())
                
                # Extract actions array (GPT may use 'actions' or 'replacements' key, or return a single action)
                # Extract actions array (flexible parsing)
                if isinstance(response_data, list):
                    attempt_actions = response_data
                elif isinstance(response_data, dict):
                    if "actions" in response_data:
                        attempt_actions = response_data["actions"]
                    elif "replacements" in response_data:
                        attempt_actions = response_data["replacements"]
                    elif "category" in response_data:
                        # GPT returned a single action dict instead of list
                        logger.info("GPT returned single action dict, wrapping in list")
                        attempt_actions = [response_data]
                    else:
                        # Fallback: Find first list value in dict
                        attempt_actions = None
                        for key, value in response_data.items():
                            if isinstance(value, list):
                                logger.info(f"Found specific list under key '{key}'")
                                attempt_actions = value
                                break
                        
                        if not attempt_actions:
                            logger.error(f"Unexpected response format: {type(response_data)}, keys={response_data.keys()}")
                            continue
                else:
                    logger.error(f"Unexpected response format: {type(response_data)}")
                    continue
                
                if not attempt_actions:
                    logger.warning(f" Attempt {attempt}: No actions generated")
                    continue
                
                logger.info(f" Successfully parsed JSON - got {len(attempt_actions)} actions")
                
                if not isinstance(attempt_actions, list):
                    attempt_actions = [attempt_actions]
                
                # Normalize categories to lowercase (same as _generate_actions_via_gpt)
                for action in attempt_actions:
                    if "category" in action:
                        action["category"] = action["category"].lower()
                
                # Validate all replacement actions
                all_valid = True
                validation_errors = []
                
                for i, action in enumerate(attempt_actions):
                    category = action.get("category", "unknown").lower()
                    valid, missing = self._validate_action_fields(action, category)
                    
                    if not valid:
                        all_valid = False
                        validation_errors.append(
                            f"Replacement {i+1} '{action.get('title', 'Untitled')}' [{category}]: missing {missing}"
                        )
                
                if all_valid:
                    logger.info(f" Attempt {attempt}: All {len(attempt_actions)} replacements valid")
                    replacement_actions = attempt_actions
                    break
                
                # Log validation errors
                logger.warning(f" Attempt {attempt} validation failed:")
                for error in validation_errors:
                    logger.warning(f"    {error}")
                
                if attempt < self.MAX_RETRIES:
                    delay = (2 ** attempt) + random.uniform(0, 1)
                    logger.info(f" Retrying generation in {delay:.2f}s...")
                    await asyncio.sleep(delay)
                else:
                    # Max retries exceeded - NO fallbacks, fail clearly
                    logger.error(f" Max retries ({self.MAX_RETRIES}) exceeded, NOT applying fallbacks - prompt needs fixing")
                    replacement_actions = None  # Fail clearly instead of masking with garbage defaults
            
            total_cost += gpt_cost
            
            if not replacement_actions:
                return {"success": False, "error": "Failed to generate replacement actions"}
            
            # Trust the prompt to generate unique replacements - no deduplication needed
            # The prompt already includes other_current_actions to avoid
            
            # Debug: Log all fields for each replacement action to verify GPT response
            logger.info(f"✅ GPT returned {len(replacement_actions)} replacement actions")
            for i, replacement_action in enumerate(replacement_actions):
                research = replacement_action.get("research_studies", [])
                category = replacement_action.get("category", "unknown")
                
                # Log category-specific fields to verify GPT is returning them
                if category == "food":
                    logger.info(f"  Replacement {i+1} '{replacement_action.get('title')}' [FOOD]: "
                               f"food_amounts={replacement_action.get('food_amounts', 'MISSING')}, "
                               f"food_items={replacement_action.get('food_items', 'MISSING')}")
                elif category == "movement":
                    logger.info(f"  Replacement {i+1} '{replacement_action.get('title')}' [MOVEMENT]: "
                               f"exercise_durations={replacement_action.get('exercise_durations', 'MISSING')}, "
                               f"exercise_types={replacement_action.get('exercise_types', 'MISSING')}")
                elif category == "mindfulness":
                    logger.info(f"  Replacement {i+1} '{replacement_action.get('title')}' [MINDFULNESS]: "
                               f"mindfulness_durations={replacement_action.get('mindfulness_durations', 'MISSING')}")
                
                logger.info(f"    {len(research)} research studies, "
                           f"variants={len(replacement_action.get('variants', []))}, "
                           f"hormone_persona_intro={bool(replacement_action.get('hormone_persona_intro'))}")
            
            # Process each replacement
            new_actions = []
            
            # ===== PARALLEL IMAGE GENERATION WITH RETRY =====
            # First, generate all hero images in parallel for speed
            from app.core.database import async_engine
            from sqlalchemy.ext.asyncio import AsyncSession
            logger.info(f" Generating {len(replacement_actions)} hero images in PARALLEL...")
            
            # Fallback URLs
            FALLBACK_IMAGE_URLS = {
                "food": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_food_fzjqkl.jpg",
                "movement": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_movement_k8zq3n.jpg",
                "mindfulness": "https://res.cloudinary.com/dxr2gmqjl/image/upload/v1736711935/action-plan-images/fallback_mindfulness_pqwz9m.jpg",
            }
            
            async def generate_hero_image(replacement_action, index):
                """Generate hero image for a replacement action."""
                replacement_title = replacement_action.get("title", "")
                replacement_category = replacement_action.get("category", "food")
                logger.info(f"[BATCH_REPLACE] Generating image {index+1}: '{replacement_title[:40]}' ({replacement_category})")
                
                async with AsyncSession(async_engine) as image_db:
                    hero_url, was_cached, image_cost = await self.image_service.get_or_generate_image(
                        prompt=replacement_title,
                        category=replacement_category,
                        variant_type="hero",
                        user_id=user_id,
                        db=image_db
                    )
                    logger.info(f"[BATCH_REPLACE]  Image {index+1} {'CACHE HIT' if was_cached else 'GENERATED'}: '{replacement_title[:30]}...'")
                    return (hero_url, was_cached, image_cost)
            
            # Run all hero image generations in parallel
            image_tasks = [
                generate_hero_image(replacement_action, i)
                for i, replacement_action in enumerate(replacement_actions)
            ]
            image_results = await asyncio.gather(*image_tasks, return_exceptions=True)
            
            # Process results and calculate total cost
            hero_images = []
            for i, result in enumerate(image_results):
                if isinstance(result, Exception):
                    logger.error(f"Image {i+1} failed: {result}")
                    hero_images.append((None, False, 0))
                else:
                    hero_images.append(result)
                    total_cost += result[2]  # image_cost
            
            # RETRY LOOP: Retry failed hero images up to 3 times
            MAX_HERO_RETRIES = 3
            for retry_attempt in range(MAX_HERO_RETRIES):
                # Find indices with missing hero images
                missing_indices = [i for i, (url, _, _) in enumerate(hero_images) if not url]
                
                if not missing_indices:
                    logger.info(f"✅ All {len(replacement_actions)} hero images generated successfully")
                    break
                
                logger.warning(f"⚠️ Hero retry {retry_attempt + 1}/{MAX_HERO_RETRIES}: {len(missing_indices)} images missing")
                
                # Exponential backoff
                await asyncio.sleep(1.0 * (retry_attempt + 1))
                
                # Retry failed images
                retry_tasks = [
                    generate_hero_image(replacement_actions[i], i)
                    for i in missing_indices
                ]
                retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
                
                # Update results
                for idx, result in zip(missing_indices, retry_results):
                    if isinstance(result, Exception):
                        logger.error(f"Retry failed for image {idx+1}: {result}")
                    else:
                        hero_images[idx] = result
                        if result[0]:  # url exists
                            total_cost += result[2]
                            logger.info(f"✅ Hero retry succeeded for image {idx+1}")
            
            # Apply fallbacks for still-missing images
            for i, (url, was_cached, cost) in enumerate(hero_images):
                if not url:
                    category = replacement_actions[i].get("category", "food").lower()
                    fallback_url = FALLBACK_IMAGE_URLS.get(category, FALLBACK_IMAGE_URLS["food"])
                    hero_images[i] = (fallback_url, False, 0)
                    logger.warning(f"⚠️ Using fallback for hero {i+1}: '{replacement_actions[i].get('title', 'Unknown')[:30]}'")
            
            logger.info(f" All {len(replacement_actions)} hero images ready (with retries/fallbacks)")
            
            # ===== PROCESS EACH REPLACEMENT WITH PRE-GENERATED IMAGES =====
            for i, replacement_action in enumerate(replacement_actions):
                original = original_items[i] if i < len(original_items) else original_items[0]
                hero_url, was_cached, _ = hero_images[i]
                
                # Log the raw replacement_action for debugging
                logger.info(f" Processing replacement {i}: category={replacement_action.get('category')}")
                logger.info(f" Variants raw: {replacement_action.get('variants')}")
                logger.info(f" Symptoms from GPT: {replacement_action.get('symptoms', [])}")
                logger.info(f" Conditions from GPT: {replacement_action.get('conditions', [])}")
                logger.info(f"[BATCH_REPLACE]  Using pre-generated image: '{replacement_action.get('title', '')[:30]}...'")

                
                # SQL-direct deactivation of original item
                await db.execute(
                    update(ActionPlanItem)
                    .where(ActionPlanItem.id == original.id)
                    .values(
                        is_replaced=True,
                        replaced_at=datetime.utcnow(),
                        replacement_reason=reasons.get(original.id, "user disliked")
                    )
                )
                
                # Create new action item
                # Get conditions from user context with type safety
                raw_conditions = user_context.get("diagnosed_conditions", [])
                if isinstance(raw_conditions, str):
                    action_conditions = [raw_conditions] if raw_conditions and raw_conditions.lower() != "none of the above" else []
                elif isinstance(raw_conditions, list):
                    action_conditions = [c for c in raw_conditions if c and str(c).lower() != "none of the above"]
                else:
                    action_conditions = []
                # Get symptoms from GPT (action-specific) with fallback to users top concern
                action_symptoms = replacement_action.get("symptoms", [])
                if not action_symptoms:
                    # Fallback to top concern if no specific symptoms generated
                    top_concern = user_context.get("top_concern")
                    if top_concern and top_concern.lower() != "general wellness":
                        action_symptoms = [top_concern]
                
                new_item = ActionPlanItem(
                    plan_id=plan_id,
                    uid=user_id,
                    slot=replacement_action.get("slot", original.slot),
                    time_slot=replacement_action.get("time_slot", original.time_slot),
                    category=replacement_action.get("category", "food"),
                    title=replacement_action.get("title", ""),
                    specific_action=replacement_action.get("specific_action", ""),
                    purpose=replacement_action.get("purpose", ""),
                    target_hormone=original.target_hormone,  # MUST be same
                    hormone_persona_intro=replacement_action.get("hormone_persona_intro", ""),
                    hero_image_url=hero_url,
                    hero_image_prompt=replacement_action.get("image_prompt"),
                    research_studies=replacement_action.get("research_studies", []),
                    conditions=action_conditions,
                    symptoms=action_symptoms,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                # Add category-specific fields
                category = replacement_action.get("category", "food")
                if category == "food":
                    new_item.food_items = replacement_action.get("food_items", [])
                    new_item.food_amounts = replacement_action.get("food_amounts", [])
                    logger.info(f"  Food fields set: items={new_item.food_items}, amounts={new_item.food_amounts}")
                elif category == "movement":
                    new_item.exercise_types = replacement_action.get("exercise_types", [])
                    new_item.exercise_durations = replacement_action.get("exercise_durations", [])
                    new_item.exercise_intensities = replacement_action.get("exercise_intensities", [])
                    logger.info(f"  Movement fields set: durations={new_item.exercise_durations}, types={new_item.exercise_types}")
                elif category == "mindfulness":
                    new_item.mindfulness_techniques = replacement_action.get("mindfulness_techniques", [])
                    new_item.mindfulness_durations = replacement_action.get("mindfulness_durations", [])
                    logger.info(f"  Mindfulness fields set: durations={new_item.mindfulness_durations}, techniques={new_item.mindfulness_techniques}")
                
                
                db.add(new_item)
                await db.flush()
                
                # Generate variant images with retry (up to 3 variants)
                raw_variants = replacement_action.get("variants", [])
                # Ensure variants is a list
                if isinstance(raw_variants, str):
                    raw_variants = []
                elif not isinstance(raw_variants, list):
                    raw_variants = []
                
                for variant in raw_variants[:3]:
                    # Skip if variant is not a dict
                    if not isinstance(variant, dict):
                        logger.warning(f"Skipping invalid variant: {type(variant)}")
                        continue
                    
                    v_type = variant.get("variant_type")
                    if not v_type or v_type == "alternative":
                        category = replacement_action.get("category", "food")
                        defaults = {
                            "food": ["healthy", "easy", "tasty"],
                            "movement": ["gentle", "quick", "energizing"],
                            "mindfulness": ["brief", "guided", "solo"]
                        }.get(category, ["alternative"])
                        v_type = defaults[raw_variants.index(variant) % len(defaults)]
                    
                    # Use variant TITLE for cache matching
                    replacement_title = replacement_action.get("title", "Action")
                    variant_title = variant.get("title", f"{v_type} {replacement_title}")
                    logger.info(f"[BATCH_REPLACE] Generating variant: '{variant_title[:40]}' ({category})")
                    
                    # Try to generate variant image with retry
                    variant_url = None
                    MAX_VARIANT_RETRIES = 3
                    for retry_attempt in range(MAX_VARIANT_RETRIES):
                        try:
                            variant_url, was_cached, variant_cost = await self.image_service.get_or_generate_image(
                                prompt=variant_title,  # Use TITLE for cache matching
                                category=replacement_action.get("category", "food"),
                                variant_type=v_type,
                                user_id=user_id,
                                db=db
                            )
                            if variant_url:
                                total_cost += variant_cost
                                break
                            else:
                                logger.warning(f"⚠️ Variant retry {retry_attempt + 1}/{MAX_VARIANT_RETRIES}: empty URL for '{variant_title[:30]}'")
                                await asyncio.sleep(1.0 * (retry_attempt + 1))
                        except Exception as e:
                            logger.error(f"Variant image error (attempt {retry_attempt + 1}): {e}")
                            await asyncio.sleep(1.0 * (retry_attempt + 1))
                    
                    # Apply fallback if still no URL
                    if not variant_url:
                        category_lower = replacement_action.get("category", "food").lower()
                        variant_url = FALLBACK_IMAGE_URLS.get(category_lower, FALLBACK_IMAGE_URLS["food"])
                        logger.warning(f"⚠️ Using fallback for variant: '{variant_title[:30]}'")
                    
                    variant_record = ActionPlanItemVariant(
                        item_id=new_item.id,
                        variant_type=v_type,
                        title=variant.get("title", ""),
                        description=variant.get("description", ""),
                        image_url=variant_url,
                        image_prompt=variant.get("image_prompt"),
                        created_at=datetime.utcnow()
                    )
                    db.add(variant_record)
                
                replacements.append({
                    "original_id": original.id,
                    "new_id": new_item.id,
                    "new_action": {
                        "id": new_item.id,
                        "slot": new_item.slot,
                        "category": new_item.category,
                        "title": new_item.title,
                        "specific_action": new_item.specific_action,
                        "purpose": new_item.purpose,
                        "target_hormone": new_item.target_hormone,
                        "hormone_persona_intro": new_item.hormone_persona_intro,
                        "hero_image_url": new_item.hero_image_url,
                        "time_slot": new_item.time_slot,
                        "symptoms": new_item.symptoms or [],
                        "conditions": new_item.conditions or []
                    }
                })
                
                new_actions.append({
                    "id": new_item.id,
                    "slot": new_item.slot,
                    "category": new_item.category,
                    "title": new_item.title,
                    "specific_action": new_item.specific_action,
                    "purpose": new_item.purpose,
                    "target_hormone": new_item.target_hormone,
                    "hormone_persona_intro": new_item.hormone_persona_intro,
                    "hero_image_url": new_item.hero_image_url,
                    "time_slot": new_item.time_slot,
                    "research_studies": new_item.research_studies or [],
                    # Add category-specific fields
                    "food_items": new_item.food_items if category == "food" else None,
                    "food_amounts": new_item.food_amounts if category == "food" else None,
                    "exercise_types": new_item.exercise_types if category == "movement" else None,
                    "exercise_durations": new_item.exercise_durations if category == "movement" else None,
                    "exercise_intensities": new_item.exercise_intensities if category == "movement" else None,
                    "mindfulness_techniques": new_item.mindfulness_techniques if category == "mindfulness" else None,
                    "mindfulness_durations": new_item.mindfulness_durations if category == "mindfulness" else None,
                    # Add symptoms and conditions
                    "symptoms": new_item.symptoms or [],
                    "conditions": new_item.conditions or []
                })
                
                # Log what we're returning
                if category == "food":
                    logger.info(f" Response includes food_amounts={new_item.food_amounts}, food_items={new_item.food_items}")
                elif category == "movement":
                    logger.info(f" Response includes exercise_durations={new_item.exercise_durations}")
                elif category == "mindfulness":
                    logger.info(f" Response includes mindfulness_durations={new_item.mindfulness_durations}")
            
            
            await db.commit()
            
            # Fetch variants for each new action to include in response
            from app.core.database import ActionPlanItemVariant
            for action_dict in new_actions:
                result = await db.execute(
                    select(ActionPlanItemVariant).where(ActionPlanItemVariant.item_id == action_dict["id"])
                )
                variants = result.scalars().all()
                action_dict["variants"] = [
                    {
                        "variant_type": v.variant_type,
                        "title": v.title,
                        "description": v.description,
                        "image_url": v.image_url
                    }
                    for v in variants
                ]
            
            logger.info(f"Batch replaced {len(replacements)} actions, cost: ${total_cost:.4f}")
            
            return {
                "success": True,
                "replaced_count": len(replacements),
                "replacements": replacements,
                "new_actions": new_actions,
                "generation_cost": f"${total_cost:.4f}"
            }
            
        except Exception as e:
            logger.error(f"Error in batch replacement: {e}")
            await db.rollback()
            return {"success": False, "error": "Failed to replace actions. Please try again."}
    
    async def record_feedback(
        self,
        user_id: str,
        item_id: int,
        feedback_type: str,  # "like", "dislike", "loved", "completed", "skipped", "not_for_me"
        time_shown: Optional[datetime],
        db: AsyncSession,
        feedback_text: Optional[str] = None,  # NEW: Users written feedback
        feedback_source: str = "home"  # NEW: "home" or "detail"
    ) -> Dict[str, Any]:
        # Record user feedback.
        from app.core.database import ActionPlanItem, ActionPlanFeedback
        
        try:
            # Get the action
            result = await db.execute(
                select(ActionPlanItem).where(ActionPlanItem.id == item_id)
            )
            item = result.scalar_one_or_none()
            
            if not item or item.uid != user_id:
                return {"success": False, "error": "Action not found"}
            
            # Calculate time to feedback (if time_shown provided)
            now = datetime.utcnow()
            time_to_feedback = None
            if time_shown:
                time_to_feedback = int((now - time_shown).total_seconds())
            
            # Get cycle context from the plan
            from app.core.database import ActionPlan
            plan_result = await db.execute(
                select(ActionPlan).where(ActionPlan.id == item.plan_id)
            )
            plan = plan_result.scalar_one_or_none()
            
            # Create feedback record with all fields
            feedback = ActionPlanFeedback(
                uid=user_id,
                plan_id=item.plan_id,
                item_id=item_id,
                feedback_type=feedback_type,
                action_title=item.title,
                action_category=item.category,
                target_hormone=item.target_hormone,
                # NEW: Text feedback
                feedback_text=feedback_text,
                feedback_source=feedback_source,
                # Cycle context
                cycle_day=plan.cycle_day if plan else None,
                cycle_phase=plan.cycle_phase if plan else None,
                # Time tracking
                action_shown_at=time_shown,
                feedback_given_at=now,
                time_to_feedback_seconds=time_to_feedback,
                created_at=now
            )
            
            db.add(feedback)
            await db.commit()
            
            # Log text feedback for monitoring
            if feedback_text:
                logger.info(f" User feedback text for '{item.title}': \"{feedback_text[:100]}...\"" if len(feedback_text) > 100 else f" User feedback text for '{item.title}': \"{feedback_text}\"")
            
            return {
                "success": True,
                "feedback_id": feedback.id,
                "time_to_feedback_seconds": time_to_feedback or 0
            }
            
        except Exception as e:
            logger.error(f"Error recording feedback: {e}")
            await db.rollback()
            return {"success": False, "error": "Failed to record feedback. Please try again."}


# Global instance
_action_plan_generator: Optional[ActionPlanGenerator] = None


def get_action_plan_generator() -> ActionPlanGenerator:
    # Get or create the action plan generator singleton.
    global _action_plan_generator
    if _action_plan_generator is None:
        _action_plan_generator = ActionPlanGenerator()
    return _action_plan_generator
