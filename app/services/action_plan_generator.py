"""
AUVRA Action Plan Generator Service

Generates 4 personalized daily actions using GPT-4o-mini:
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
from app.services.pubmed_service import PUBMED_SEARCH_TOOL, execute_pubmed_tool
from app.core.config import settings

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
        "supportive_foods": ["phytoestrogen foods", "cruciferous vegetables", "flaxseed", "berries"],
        "supportive_movement": ["strength training", "HIIT", "dancing", "cardio"],
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
        "supportive_foods": ["protein-rich foods", "zinc foods", "vitamin D foods", "healthy fats"],
        "supportive_movement": ["weight training", "HIIT", "sprints", "power yoga"],
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

SYSTEM_PROMPT = """You are AUVRAs personalized wellness AI that creates daily action plans for womens hormonal health.

===============================================================================
 CORE PRINCIPLE: TRUE PERSONALIZATION
===============================================================================
You must create UNIQUE, TAILORED recommendations based on:
- Users specific diagnosed conditions (PCOS, endometriosis, thyroid issues, etc.)
- Their health concerns and symptoms
- Their cycle phase and hormones to support
- Their diet preferences and allergies
- Their feedback history (what they liked/disliked before)
- Their stress level, sleep, and workout intensity

DO NOT give generic recommendations. Every action should feel like it was made FOR THIS USER.

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
{
    "title": "Study title from PubMed/OpenAlex",
    "journal": "Journal name from tool result",
    "year": 2020,
    "participants": 156,
    "finding": "Key finding from paper abstract",
    "pmid": "12345678"
}

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
REQUIREMENTS (READ CAREFULLY)
======================================================================
1. Generate exactly {num_actions} actions total
2. Actions targeting PRIMARY hormone ({primary_hormone}): {primary_count}
3. Actions targeting SECONDARY hormone ({secondary_hormone}): {secondary_count}
4. Category distribution based on lifestyle_focus: {category_guidance}
5. Each action must be UNIQUE - no two actions should have similar foods/exercises
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
 ANTI-REPETITION & HALLUCINATION RULES (CRITICAL)
======================================================================
1. DO NOT COPY EXAMPLES: The examples in this prompt are for FORMATTING ONLY. You MUST select the BEST action for THIS specific user from your medical knowledge - NOT from the examples.
2. AVOID RECENTLY RECOMMENDED: Check the "RECENTLY RECOMMENDED" section below. Do NOT suggest any item from that list.
3. VARIETY IS KEY: Each days plan should feel fresh and different. Draw from the FULL spectrum of evidence-based interventions.
4. STRICT SYMPTOM WHITELIST: In the 'symptoms' output array, you may ONLY use symptoms from this exact list:
   {allowed_symptoms}
   If a symptom is not in this list, DO NOT include it.
5. STRICT CONDITION WHITELIST: In the 'conditions' output array, you may ONLY use conditions from this exact list:
   {allowed_conditions}
   If no conditions are listed, this array MUST be empty [].

======================================================================
 RECENTLY RECOMMENDED (DO NOT REPEAT THESE)
======================================================================
{recently_recommended}
Choose DIFFERENT items that are equally or more beneficial for this user.

======================================================================
 CORE PRINCIPLE: TITLE vs SPECIFIC_ACTION
======================================================================
+---------------------------------------------------------------------+
|  TITLE = WHAT it is (the thing itself - noun)                       |
|  SPECIFIC_ACTION = HOW to use it (3 different methods - verbs)      |
+---------------------------------------------------------------------+

FOOD:
   Title: Raw ingredient  "Salmon", "Quinoa", "Blueberries"
   specific_action: 3 consumption methods  grilled, baked, in smoothie, etc.

MOVEMENT:
   Title: Activity type  "Yoga", "Walking", "Stretching"
   specific_action: 3 ways to do it  gentle flow, hip openers, sun salutations, etc.

MINDFULNESS:
   Title: Technique name  "Deep Breathing", "Meditation", "Body Scan"
   specific_action: 3 practice methods  4-7-8 technique, box breathing, belly breathing, etc.

======================================================================
OUTPUT FORMAT (for each action)
======================================================================
1. title: SIMPLE, CLEAN NAME ONLY (see TITLE RULES below)
2. category: "food", "movement", or "mindfulness"
3. time_slot: "morning", "afternoon", or "evening"
4. specific_action: MUST include 3 DIFFERENT WAYS to consume/do this action! (80-120 words)
   FORMAT: Start with main benefit, then list 3 methods like:
   "Try it as: (1) [method 1], (2) [method 2], or (3) [method 3]."
   
    GOOD EXAMPLE for "Walnuts":
   "Walnuts are rich in omega-3s and melatonin precursors that support hormonal balance and sleep. Try them as: (1) Raw handful - eat 7-10 walnuts as a morning snack, (2) Salad topper - add chopped walnuts to your lunch salad with olive oil, or (3) Walnut butter - spread 1 tbsp on apple slices or whole grain toast."
   
    GOOD EXAMPLE for "Spinach":
   "Spinach is packed with magnesium and folate that support progesterone production. Try it as: (1) Green smoothie - blend 2 cups with banana and almond milk, (2) Sauted side - quickly saut with garlic and olive oil, or (3) Raw salad - use as a base for lunch topped with avocado."
   
    BAD (no consumption methods): "Ginger helps reduce stress. Consume it daily."
   
5. purpose: CRITICAL - Explain the SCIENTIFIC MECHANISM of how this action helps the users specific condition + hormone. Be specific about WHY this works for THEIR situation. Avoid generic phrases like "promotes wellness" - instead explain the actual biochemical/physiological benefit.
6. target_hormone: CRITICAL - You MUST set this exactly as follows:
   - Action 1 and 2: MUST be "{primary_hormone}" (the PRIMARY hormone)
   - Action 3 and 4: MUST be "{secondary_hormone}" (the SECONDARY hormone)
   DO NOT deviate from this. The mascot image shown depends on this field matching correctly.
7. hormone_persona_intro: Write naturally following the example style in system prompt
8. image_prompt: FLUX.1 Schnell optimized prompt (see IMAGE PROMPT REQUIREMENTS below)
9. research_studies: Array with EXACTLY 1 REAL research citation focused on WOMEN/FEMALES. Fields: title, journal, year, participants (int), finding, pmid, verification_link.
10. variants: Array of 3 variants showing DIFFERENT WAYS to consume/do this action. CRITICAL: Do NOT include 'specific_action' in variants. Only: variant_type, title, description, image_prompt.
11. symptoms: Array of strings - specific user symptoms this action addresses (e.g., ["acne", "fatigue", "bloating"])
12. conditions: Array of strings - specific conditions this action is beneficial for (e.g., ["PCOS", "endometriosis"])

======================================================================
 TITLE RULES (CRITICAL - INGREDIENT/ACTIVITY NAME ONLY!)
======================================================================
REMINDER: The items below are EXAMPLES of formatting. Do NOT just pick from this list. Choose what is best for the user.

Titles MUST be the RAW INGREDIENT or ACTIVITY NAME ONLY.
 NO preparation methods (latte, tea, smoothie, etc.)
 NO adjectives (powerful, amazing, gentle, etc.)

 GOOD FOOD TITLES (RAW INGREDIENT ONLY - these are FORMAT examples, choose whats best for user):
- "Walnuts" (NOT "Walnut Butter")
- "Spinach" (NOT "Spinach Smoothie")
- "Ginger" (NOT "Ginger Tea")
- "Salmon" (NOT "Grilled Salmon")
- "Avocado" (NOT "Avocado Toast")
- "Quinoa" (NOT "Quinoa Salad")
- "Berries" (NOT "Berry Smoothie")
- "Eggs" (NOT "Scrambled Eggs")
- "Almonds" (NOT "Almond Butter")
- "Sweet Potato" (NOT "Baked Sweet Potato")

 BAD FOOD TITLES (includes preparation method - WRONG!):
- "Ginger Tea"  should be "Ginger"
- "Walnut Butter"  should be "Walnuts"
- "Spinach Smoothie"  should be "Spinach"
- "Grilled Salmon"  should be "Salmon"
- "Avocado Toast"  should be "Avocado"
- "Quinoa Bowl"  should be "Quinoa"

 GOOD MOVEMENT TITLES (simple activity name):
- "Post-Meal Walk"
- "Morning Yoga"
- "Gentle Stretching"
- "Swimming"
- "Pilates"
- "Evening Walk"
- "Hip Stretches"
- "Strength Training"

 BAD MOVEMENT TITLES (too descriptive):
- "Gentle Morning Yoga Flow"  should be "Morning Yoga"
- "Relaxing Evening Stretch"  should be "Evening Stretching"

 GOOD MINDFULNESS TITLES (technique name only):
- "Deep Breathing"
- "Box Breathing"
- "Body Scan"
- "Gratitude Journal"
- "Meditation"
- "Progressive Relaxation"
- "4-7-8 Breathing"

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
COMPLETE OUTPUT EXAMPLES (FOLLOW THIS EXACT STRUCTURE)
======================================================================

EXAMPLE FOOD ACTION (notice specific_action includes 3 ways to consume):
{{
  "title": "Walnuts",
  "category": "food",
  "time_slot": "morning",
  "specific_action": "Walnuts are rich in omega-3 fatty acids and melatonin precursors that support hormonal balance. Try them today as: (1) Raw handful - eat 7-10 walnuts as a quick morning snack with your coffee, (2) Smoothie boost - blend 1/4 cup into your morning smoothie with banana and spinach, or (3) Oatmeal topper - sprinkle 2 tbsp crushed walnuts on your oatmeal with a drizzle of honey.",
  "purpose": "Walnuts contain alpha-linolenic acid (ALA) which converts to DHA, supporting brain-ovary communication and reducing inflammation that disrupts hormone signaling in your follicular phase.",
  "target_hormone": "Estrogen",
  "hormone_persona_intro": "Good morning! Its Estrogen here. I am rising in your follicular phase, and I need some healthy fats to help me do my job properly.",
  "image_prompt": "Professional close-up food photography of whole walnuts and walnut halves in a small white ceramic bowl, showing the brain-like texture of the walnut meat clearly visible, warm wooden table surface, soft morning window light creating gentle shadows, some cracked shells beside the bowl, the distinctive brown wrinkled walnut texture is the unmistakable hero filling 70% of frame, appetizing natural food styling, 4K quality",
  "food_items": ["walnuts", "raw walnuts", "walnut halves"],
  "food_amounts": ["7-10 pieces", "a handful (30g)", "1/4 cup"],
  "research_studies": [{{"title": "Walnut consumption and hormonal health", "journal": "J Nutr Biochem", "year": 2023, "participants": 90, "finding": "Walnut consumption improved estrogen metabolism in premenopausal women", "pmid": "36789012"}}],
  "variants": [
    {{"variant_type": "tasty", "title": "Maple Candied Walnuts", "description": "Toast walnuts in a pan with maple syrup and a pinch of sea salt until caramelized", "image_prompt": "Professional food photography of golden-brown candied walnuts glistening with maple glaze in a rustic ceramic dish, caramelized coating clearly visible, sea salt crystals sparkling on top, warm kitchen lighting, irresistible sweet snack presentation with maple syrup bottle blurred in background, 4K quality"}},
    {{"variant_type": "easy", "title": "Grab-and-Go Portion", "description": "Pre-portion walnuts into small containers for easy daily snacking", "image_prompt": "Professional food photography of raw walnut halves in a small clear glass jar with cork lid, portable snack container on a clean white desk surface, office-friendly healthy snack setup, natural daylight, convenient wellness moment clearly shown, 4K quality"}},
    {{"variant_type": "healthy", "title": "Soaked Walnuts", "description": "Soak walnuts overnight in water to reduce phytic acid and improve nutrient absorption", "image_prompt": "Professional food photography of plump soaked walnuts in a clear glass bowl filled with water, some drained walnuts on a white plate beside it, fresh morning light through window, clean kitchen counter, health-conscious preparation with visible softened texture, 4K quality"}}
  ],
  "symptoms": ["fatigue", "low mood"],
  "conditions": []
}}

EXAMPLE MOVEMENT ACTION (notice specific_action includes 3 ways to do the exercise):
{{
  "title": "Morning Yoga",
  "category": "movement",
  "time_slot": "morning",
  "specific_action": "Morning yoga activates your parasympathetic nervous system to reduce cortisol and calm your mind. Try it today as: (1) Gentle flow - 15 min of cat-cow, childs pose, and forward folds, (2) Hip openers - focus on pigeon pose and butterfly stretch for 10 min, or (3) Energizing sequence - 5 rounds of sun salutations to wake up your body and boost circulation.",
  "purpose": "Forward folds and hip openers activate the parasympathetic nervous system, directly lowering cortisol release and helping prevent the stress-induced disruption of your reproductive hormones.",
  "target_hormone": "Cortisol",
  "hormone_persona_intro": "Good morning! Its Cortisol here. Instead of spiking your stress, lets channel my energy into something calming that will help you feel centered all day.",
  "image_prompt": "Serene photograph of woman in childs pose on a purple yoga mat in a bright living room, arms stretched forward, forehead resting on mat, wearing comfortable grey athletic wear, green indoor plants visible, soft morning sunlight through sheer white curtains, clearly showing the exact yoga pose, peaceful wellness aesthetic, 4K quality",
  "exercise_types": ["yoga", "gentle stretching", "sun salutations"],
  "exercise_durations": ["15 minutes", "10 min", "5 min"],
  "exercise_intensities": ["low", "gentle", "moderate"],
  "research_studies": [{{"title": "Yoga and cortisol reduction in women", "journal": "Psychoneuroendocrinology", "year": 2021, "participants": 80, "finding": "Yoga reduced cortisol levels significantly", "pmid": "98765432"}}],
  "variants": [
    {{"variant_type": "gentle", "title": "Restorative Yoga", "description": "Use blankets and bolsters for fully supported poses, hold each for 3-5 minutes", "image_prompt": "Serene photograph of woman lying in supported bridge pose with a yoga bolster under her lower back, cozy blanket draped over legs, soft candles in background, eyes closed in relaxation, peaceful bedroom setting, restorative yoga clearly shown, 4K quality"}},
    {{"variant_type": "energizing", "title": "Vinyasa Flow", "description": "Link breath with movement through flowing sun salutations and warrior poses", "image_prompt": "Dynamic photograph of woman in warrior II pose on yoga mat, arms extended strongly, looking over front hand, bright morning light, powerful athletic stance clearly visible, energetic wellness moment, vibrant colors, 4K quality"}},
    {{"variant_type": "quick", "title": "5-Minute Stretch", "description": "Cat-cow (1 min), childs pose (2 min), forward fold (2 min) - perfect for busy mornings", "image_prompt": "Serene photograph of woman doing cat-cow pose on yoga mat, back arched upward in cat position, comfortable clothing, minimalist bright room, quick morning stretch clearly demonstrated, efficient wellness moment, 4K quality"}}
  ],
  "symptoms": ["stress", "tension"],
  "conditions": []
}}

EXAMPLE MINDFULNESS ACTION (notice specific_action includes 3 ways to practice):
{{
  "title": "Deep Breathing",
  "category": "mindfulness",
  "time_slot": "evening",
  "specific_action": "Deep breathing activates your vagus nerve to signal safety and reduce cortisol for better sleep. Try it tonight as: (1) 4-4-6 technique - inhale 4 counts, hold 4 counts, exhale 6 counts for 5 minutes, (2) Box breathing - inhale 4, hold 4, exhale 4, hold 4 for 10 cycles, or (3) Belly breathing - place hands on belly, breathe deeply until hands rise, exhale slowly for 3 minutes.",
  "purpose": "Deep breathing signals safety to your nervous system, helping me (Cortisol) decrease so you can rest and restore.",
  "target_hormone": "Cortisol",
  "hormone_persona_intro": "Hey, its Cortisol checking in for the evening. Lets work together to wind down so you can get the restorative sleep you deserve.",
  "image_prompt": "Peaceful close-up photograph of woman sitting cross-legged on a meditation cushion with both hands placed on her belly, practicing deep diaphragmatic breathing, eyes gently closed, serene peaceful expression, soft candlelight glowing nearby, cozy bedroom with string lights in background, clearly showing the belly breathing technique, calming atmosphere, 4K quality",
  "mindfulness_techniques": ["4-4-6 breathing", "box breathing", "belly breathing"],
  "mindfulness_durations": ["5 minutes", "10 cycles", "3 minutes"],
  "research_studies": [{{"title": "Breathing exercises and stress reduction", "journal": "Frontiers Psychol", "year": 2023, "participants": 95, "finding": "Deep breathing reduced perceived stress in women", "pmid": "11223344"}}],
  "variants": [
    {{"variant_type": "guided", "title": "App-Guided", "description": "Follow along with a breathing app like Calm or Headspace for guided sessions", "image_prompt": "Peaceful photograph of woman wearing headphones practicing guided breathing meditation, eyes closed, phone showing meditation app on cushion beside her, soft ambient lighting, cozy corner setup, relaxation moment, 4K quality"}},
    {{"variant_type": "solo", "title": "Silent Practice", "description": "Practice in complete silence, focusing only on your breath counts", "image_prompt": "Peaceful photograph of woman sitting in meditation pose in complete silence, hands on knees in mudra position, minimal zen room with single plant, early morning soft light, deep focus expression, tranquil atmosphere, 4K quality"}},
    {{"variant_type": "brief", "title": "5-Breath Reset", "description": "Take just 5 slow, deep breaths whenever you feel stressed during the day", "image_prompt": "Peaceful photograph of woman taking a deep breath at her desk, one hand on chest, eyes briefly closed, office or home workspace background, moment of calm during busy day, stress relief break, 4K quality"}}
  ],
  "symptoms": ["anxiety", "insomnia"],
  "conditions": []
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

 GOOD EXAMPLES (food is the clear hero, instantly recognizable - USE SIMILAR STRUCTURE):
- "Professional close-up food photography of whole walnuts and walnut halves showing their distinctive brain-like wrinkled texture, in a small wooden bowl on marble surface, warm morning light highlighting the brown ridges, the walnuts fill 70% of the frame and are unmistakably identifiable, shallow depth of field, 4K quality"
- "Professional close-up food photography of fresh vibrant green spinach leaves piled in a white ceramic colander, water droplets visible on leaves showing freshness, bright kitchen lighting, the spinach is clearly recognizable with its distinctive leaf shape filling most of frame, healthy green color prominent, 4K quality"
- "Professional close-up food photography of fresh ginger root with one piece sliced to show the fibrous yellow interior, on a light wooden cutting board, the distinctive knobby tan skin texture is clearly visible, natural kitchen lighting, ginger fills 65% of frame making it instantly identifiable, 4K quality"
- "Professional close-up food photography of fresh salmon fillet showing vibrant pink-orange flesh with distinctive white fat marbling lines, on a slate serving board with lemon wedge, the fish texture and color are appetizing and unmistakable, 4K quality"

 BAD EXAMPLES (food not clear, too generic, or too zoomed out):
- "Professional food photography of healthy food" (What food?!)
- "Bowl of food on a table" (Can't tell whats in it!)
- "Overhead shot of breakfast spread" (Too much, can't focus on any item)

=======================================================================
FOR MOVEMENT - Show a WOMAN DOING the exact exercise/pose:
=======================================================================
Template: "Serene photograph of woman [EXACT POSE/MOVEMENT DESCRIPTION], [setting], soft natural lighting, wellness aesthetic, warm earth tones, 4K quality"

 GOOD EXAMPLES (specific and illustrative):
- "Serene photograph of woman in childs pose on a yoga mat, arms extended forward, peaceful living room with plants, soft morning light through window, wellness aesthetic, 4K quality"
- "Serene photograph of woman walking briskly in a park after eating, casual athletic wear, trees and greenery, golden hour lighting, healthy active lifestyle, 4K quality"
- "Serene photograph of woman doing hip stretches on a yoga mat, one leg extended, peaceful bedroom setting, soft natural lighting, relaxed atmosphere, 4K quality"
- "Serene photograph of woman doing gentle swimming laps in a pool, clear blue water, peaceful indoor pool setting, natural lighting, refreshing wellness vibe, 4K quality"

 BAD EXAMPLES (too generic):
- "Woman exercising" (What exercise?!)
- "Yoga pose" (Which one?!)

=======================================================================
FOR MINDFULNESS - Show the TECHNIQUE/SETUP clearly:
=======================================================================
Template: "Peaceful photograph of [EXACT MINDFULNESS SETUP/TECHNIQUE visualization], [calming elements], soft diffused lighting, minimalist aesthetic, calming colors, 4K quality"

 GOOD EXAMPLES (specific and illustrative):
- "Peaceful photograph of woman sitting cross-legged with hands on belly practicing deep breathing, eyes closed, serene expression, soft candles nearby, minimalist room, calming atmosphere, 4K quality"
- "Peaceful close-up of hands writing in a gratitude journal with a cup of tea nearby, cozy blanket, soft evening lighting, peaceful bedroom setting, journaling moment, 4K quality"
- "Peaceful photograph of woman lying down with eyes closed doing body scan meditation, comfortable cushion, dim peaceful room with fairy lights, relaxation moment, 4K quality"
- "Peaceful photograph of woman practicing 4-7-8 breathing with one hand on chest, peaceful expression, morning light, zen corner with plants, calming wellness aesthetic, 4K quality"

 BAD EXAMPLES (too generic):
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
    3. Generate 4 actions via GPT-4o-mini
    4. Generate images for each action (16 total)
    5. Store plan in database
    """
    
    GPT_MODEL = "gpt-4o-mini"
    GPT_TEMPERATURE = 0.7
    MAX_RETRIES = 3
    
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
    
    async def get_or_generate_today_plan(
        self,
        user_id: str,
        user_timezone: str,
        db: AsyncSession,
        image_mode: Literal["full", "hero_only", "none"] = "full",
        skip_quality_check: bool = False,
    ) -> Dict[str, Any]:
        """
        Get todays action plan or generate a new one.
        
        This is the main entry point called on app open.
        
        NEW: If yesterday was frozen and had incomplete items, those items
        carry forward to todays plan instead of generating new ones.
        
        Args:
            skip_quality_check: If True, skip model quality evaluation (faster for first-time users)
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
        
        # NEW: Check if user has fresh session recommendations that can be converted
        # This happens when user just signed up and session recs were migrated
        
        # [DISABLED BY USER REQUEST - "Z NEED TO BE Y"]
        # User requested to ALWAYS use the full generation engine (Function Y) instead of 
        # converting session recommendations (Function X/Z) which were effectively a "lite" version.
        # This ensures everyone gets the full 16-image experience immediately.
        
        # conversion_result = await self._convert_session_recommendations_to_plan(
        #     user_id, today, user_timezone, db, image_mode
        # )
        
        # if conversion_result and conversion_result.get("success"):
        #     logger.info(f" Converted session recommendations to action plan for {user_id}")
        #     conversion_result["plan_source"] = "session_conversion"
        #     return conversion_result
        
        conversion_result = None  # Force skip conversion
        
        # Generate new plan
        logger.info(f"Generating new plan for user {user_id} on {today}")
        gen_result = await self.generate_new_plan(
            user_id=user_id,
            plan_date=today,
            user_timezone=user_timezone,
            db=db,
            image_mode=image_mode,
            skip_quality_check=skip_quality_check,
        )
        if isinstance(gen_result, dict) and gen_result.get("success"):
            gen_result.setdefault("plan_source", "generated_new")
        return gen_result
    
    async def generate_new_plan(
        self,
        user_id: Optional[str],
        plan_date: date,
        user_timezone: str,
        db: AsyncSession,
        image_mode: Literal["full", "hero_only", "none"] = "full",
        skip_quality_check: bool = False,
        session_id: Optional[str] = None  # NEW: For guest users
    ) -> Dict[str, Any]:
        """
        Generate a completely new action plan.
        
        Uses PostgreSQL advisory lock to prevent race conditions.
        
        Steps:
        1. Acquire advisory lock for user+date (or session+date)
        2. Check for existing plan (double-check after lock)
        3. Load user context
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
            
            # Step 1: Load user context
            logger.info(f"[GENERATE] Step 1: Loading user context...")
            user_context = await self._load_user_context(user_id, db, session_id=session_id)
            
            if not user_context:
                logger.error(f"[GENERATE]  Could not load user context for {user_id}")
                return {"success": False, "error": "User profile not found"}
            logger.info(f"[GENERATE]  User context loaded successfully")
            
            # Step 2: Generate actions via GPT-4o-mini with retry logic
            # Pydantic validation ensures complete data - no fallbacks
            logger.info(f"[GENERATE] Step 2: Generating actions via GPT...")
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
            
            # Log the generated actions for debugging
            logger.info(f"[GENERATE] ==========================================================================")
            logger.info(f"[GENERATE]  GENERATED ACTIONS SUMMARY ({len(actions)} actions):")
            for i, action in enumerate(actions):
                logger.info(f"[GENERATE]   Action {i+1}: '{action['title']}' | Category: {action['category']} | Hormone: {action['target_hormone']}")
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
    
    async def _convert_session_recommendations_to_plan(
        self,
        user_id: str,
        today: date,
        user_timezone: str,
        db: AsyncSession,
        image_mode: str = "hero_only"
    ) -> Optional[Dict[str, Any]]:
        """Convert freshly migrated session recommendations to an ActionPlan."""
        from datetime import timedelta
        from app.core.database import ActionPlan, ActionPlanItem, ActionPlanItemVariant, RecommendationRecord
        
        start_time = time.time()
        logger.info(f"[SESSION_CONVERT] Starting conversion check for user {user_id}")
        t0 = time.time()
        
        try:
            # Step 1: Check if user has any existing action plans
            existing_plans_result = await db.execute(
                select(ActionPlan.id).where(ActionPlan.uid == user_id).limit(1)
            )
            has_existing_plans = existing_plans_result.scalar_one_or_none() is not None
            
            if has_existing_plans:
                logger.info(f"[SESSION_CONVERT] User {user_id} has existing plans, skipping conversion")
                return None
            
            logger.info(f"[SESSION_CONVERT] User {user_id} has no existing plans, checking for fresh session recs")
            
            # Step 2: Find fresh session recommendations for this user
            # Only consider recs created in the last 10 minutes (fresh from onboarding)
            # CRITICAL: Use naive UTC to match the database column (created_at is naive)
            cutoff_time = datetime.utcnow() - timedelta(minutes=10)
            logger.info(f"[SESSION_CONVERT] Looking for recs created after {cutoff_time} (10 min cutoff)")
            
            fresh_recs_result = await db.execute(
                select(RecommendationRecord).where(
                    and_(
                        RecommendationRecord.uid == user_id,
                        RecommendationRecord.session_id.is_(None),  # Already migrated
                        RecommendationRecord.created_at >= cutoff_time
                    )
                ).order_by(RecommendationRecord.created_at.desc())
            )
            fresh_recs = fresh_recs_result.scalars().all()
            logger.info(f"[SESSION_CONVERT] Query returned {len(fresh_recs)} fresh recs for user {user_id}")
            
            if len(fresh_recs) < 2:
                logger.info(f"[SESSION_CONVERT] Only {len(fresh_recs)} fresh recs for {user_id}, need at least 2. Skipping conversion.")
                return None
            
            logger.info(f"[SESSION_CONVERT]  Found {len(fresh_recs)} fresh session recommendations for {user_id}")
            logger.info(f"[SESSION_CONVERT] Converting to ActionPlan (saves ~100s of GPT generation)")
            
            # Step 3: Load user context for hormone info
            user_context = await self._load_user_context(user_id, db)
            if not user_context:
                logger.error(f"[SESSION_CONVERT] Could not load user context for {user_id}")
                return None
            
            primary_hormone = user_context.get("primary_hormone", "cortisol").lower()
            secondary_hormone = user_context.get("secondary_hormone", "progesterone").lower()
            cycle_day = user_context.get("cycle_day", 1)
            cycle_phase = user_context.get("cycle_phase", "menstrual")
            lifestyle_focus = user_context.get("lifestyle_focus", ["eat", "move", "pause"])
            
            # Step 4: Select up to 4 recommendations (2 food, 2 movement ideally)
            selected_recs = []
            categories_needed = {"food": 2, "movement": 2, "mindfulness": 0}
            
            # Adjust based on lifestyle_focus
            if "eat" in lifestyle_focus and "move" in lifestyle_focus and "pause" not in lifestyle_focus:
                categories_needed = {"food": 2, "movement": 2, "mindfulness": 0}
            elif "eat" in lifestyle_focus and "pause" in lifestyle_focus and "move" not in lifestyle_focus:
                categories_needed = {"food": 2, "movement": 0, "mindfulness": 2}
            elif "move" in lifestyle_focus and "pause" in lifestyle_focus and "eat" not in lifestyle_focus:
                categories_needed = {"food": 0, "movement": 2, "mindfulness": 2}
            elif len(lifestyle_focus) == 3:
                categories_needed = {"food": 2, "movement": 1, "mindfulness": 1}
            
            for rec in fresh_recs:
                cat = rec.category.lower() if rec.category else "food"
                if cat in categories_needed and categories_needed[cat] > 0:
                    selected_recs.append(rec)
                    categories_needed[cat] -= 1
                    if sum(categories_needed.values()) == 0:
                        break
            
            # Fill remaining slots with any category
            remaining_needed = 4 - len(selected_recs)
            if remaining_needed > 0:
                for rec in fresh_recs:
                    if rec not in selected_recs:
                        selected_recs.append(rec)
                        remaining_needed -= 1
                        if remaining_needed == 0:
                            break
            
            if len(selected_recs) < 2:
                logger.info(f"[SESSION_CONVERT] Could only select {len(selected_recs)} recs, need at least 2. Skipping.")
                return None
            
            logger.info(f"[SESSION_CONVERT] Selected {len(selected_recs)} recommendations for conversion")
            for i, rec in enumerate(selected_recs):
                logger.info(f"[SESSION_CONVERT]   {i+1}. {rec.title} ({rec.category})")
            
            # Step 5: Create ActionPlan
            new_plan = ActionPlan(
                uid=user_id,
                plan_date=today,
                primary_hormone=primary_hormone,
                secondary_hormones=[secondary_hormone] if secondary_hormone else [],
                cycle_day=cycle_day,
                cycle_phase=cycle_phase,
                lifestyle_focus=lifestyle_focus,
                generation_cost="$0.00 (converted)",
                generation_time_ms=int((time.time() - start_time) * 1000),
                gpt_model_used="session_conversion",
                is_regenerated=False,
                feedback_collected=False,
                review_completed=False
            )
            db.add(new_plan)
            await db.flush()  # Get plan ID
            
            # Step 6: Convert each recommendation to ActionPlanItem
            time_slots = ["morning", "afternoon", "evening", "morning"]  # Cycle through
            
            for slot_idx, rec in enumerate(selected_recs[:4]):  # Max 4 items
                target_hormone = primary_hormone if slot_idx < 2 else secondary_hormone
                
                # Build hormone persona intro
                hormone_intros = {
                    "cortisol": "Hi, I am Cortisol! I help regulate your stress response and energy levels.",
                    "progesterone": "Hi, I am Progesterone! I help with mood stability and sleep quality.",
                    "estrogen": "Hi, I am Estrogen! I support your mood, skin, and overall well-being.",
                    "testosterone": "Hi, I am Testosterone! I support your energy, strength, and motivation.",
                    "androgens": "Hi, I am Androgens! I help regulate your skin, energy, and metabolism.",
                    "insulin": "Hi, I am Insulin! I help manage your blood sugar and energy balance.",
                    "thyroid": "Hi, I am Thyroid! I regulate your metabolism and energy production.",
                }
                
                hormone_intro = hormone_intros.get(target_hormone, f"Hi, I am {target_hormone.title()}! I help support your hormonal health.")
                
                # Generate image prompt from recommendation data
                category = rec.category.lower() if rec.category else "food"
                title = rec.title or "Healthy Choice"
                
                if category == "food":
                    image_prompt = f"Professional close-up food photography of {title}, appetizing presentation, natural lighting, soft shadows, 4K quality"
                elif category == "movement":
                    image_prompt = f"Serene photograph of a woman doing {title}, natural setting, peaceful atmosphere, soft morning light"
                else:
                    image_prompt = f"Calming photograph representing {title}, peaceful atmosphere, soft natural lighting"
                
                item = ActionPlanItem(
                    plan_id=new_plan.id,
                    uid=user_id,
                    slot=slot_idx + 1,
                    time_slot=time_slots[slot_idx],
                    category=category,
                    title=title,
                    specific_action=rec.specific_action or f"Try {title} today",
                    purpose=rec.purpose or f"Supports your {target_hormone} balance",
                    target_hormone=target_hormone,
                    hormone_persona_intro=hormone_intro,
                    food_amounts=rec.food_amounts or [],
                    food_items=rec.food_items or [],
                    exercise_durations=rec.exercise_durations or [],
                    exercise_types=rec.exercise_types or [],
                    exercise_intensities=rec.exercise_intensities or [],
                    mindfulness_durations=rec.mindfulness_durations or [],
                    mindfulness_techniques=rec.mindfulness_techniques or [],
                    conditions=rec.conditions or [],
                    symptoms=rec.symptoms or [],
                    hero_image_url=None,  # Will be generated below
                    hero_image_prompt=image_prompt,
                    research_studies=rec.research_studies or [],
                    is_completed=False,
                    is_replaced=False
                )
                db.add(item)
                await db.flush()
                
                # Add default variants
                variant_types = {
                    "food": ["easy", "tasty", "healthy"],
                    "movement": ["gentle", "energizing", "quick"],
                    "mindfulness": ["guided", "silent", "brief"]
                }
                
                variants_for_cat = variant_types.get(category, ["alternative", "simpler", "advanced"])
                for v_idx, v_type in enumerate(variants_for_cat):
                    variant = ActionPlanItemVariant(
                        item_id=item.id,
                        variant_type=v_type,
                        title=f"{v_type.title()} {title}",
                        description=f"A {v_type} way to enjoy {title}",
                        image_url=None,
                        image_prompt=f"{v_type.title()} version of {title}, professional photography"
                    )
                    db.add(variant)
            
            await db.commit()
            
            # Step 7: Generate hero images (quick, in parallel)
            logger.info(f"[SESSION_CONVERT] Generating hero images for {len(selected_recs)} items...")
            
            # Reload plan with items for image generation
            plan_result = await db.execute(
                select(ActionPlan).where(ActionPlan.id == new_plan.id)
            )
            plan = plan_result.scalar_one()
            
            items_result = await db.execute(
                select(ActionPlanItem).where(ActionPlanItem.plan_id == plan.id)
            )
            items = items_result.scalars().all()
            
            # Generate images in parallel using proper image service call
            if image_mode != "none":
                all_image_tasks = []
                
                async def generate_hero_image(item_id, item_title, item_category):
                    """Generate hero image for an item using proper image service."""
                    task_session = None
                    try:
                        async with self.db_semaphore:
                            task_session = await _create_async_session(self.async_session_maker)
                            logger.info(f"[SESSION_CONVERT] Generating hero for: '{item_title[:40]}' ({item_category})")
                            url, was_cached, cost = await self.image_service.get_or_generate_image(
                                prompt=item_title,
                                category=item_category or "food",
                                variant_type="hero",
                                user_id=user_id,
                                db=task_session
                            )
                        
                        if url:
                            # Update in a separate session to avoid conflicts
                            async with self.async_session_maker() as update_session:
                                await update_session.execute(
                                    update(ActionPlanItem)
                                    .where(ActionPlanItem.id == item_id)
                                    .values(hero_image_url=url)
                                )
                                await update_session.commit()
                            return url
                        return None
                    except Exception as e:
                        logger.warning(f"[SESSION_CONVERT] Image generation failed for item {item_id}: {e}")
                        return None
                    finally:
                        if task_session:
                            await task_session.close()

                async def generate_variant_image(variant_id, variant_title, item_category, variant_type):
                    """Generate image for a variant."""
                    task_session = None
                    try:
                        async with self.db_semaphore:
                            task_session = await _create_async_session(self.async_session_maker)
                            url, was_cached, cost = await self.image_service.get_or_generate_image(
                                prompt=variant_title,
                                category=item_category or "food",
                                variant_type=variant_type,
                                user_id=user_id,
                                db=task_session
                            )
                        
                        if url:
                            async with self.async_session_maker() as update_session:
                                await update_session.execute(
                                    update(ActionPlanItemVariant)
                                    .where(ActionPlanItemVariant.id == variant_id)
                                    .values(image_url=url)
                                )
                                await update_session.commit()
                            return url
                        return None
                    except Exception as e:
                        logger.warning(f"[SESSION_CONVERT] Variant image failed for variant {variant_id}: {e}")
                        return None
                    finally:
                        if task_session:
                            await task_session.close()

                # Collect all tasks
                for item in items:
                    if item.title:
                        all_image_tasks.append(generate_hero_image(item.id, item.title, item.category))
                    
                    variants_result = await db.execute(
                        select(ActionPlanItemVariant).where(ActionPlanItemVariant.item_id == item.id)
                    )
                    variants = variants_result.scalars().all()
                    for variant in variants:
                        if variant.title:
                            all_image_tasks.append(generate_variant_image(variant.id, variant.title, item.category, variant.variant_type))
                
                # Execute all 16+ image generations in a single parallel burst!
                if all_image_tasks:
                    logger.info(f"[SESSION_CONVERT] Launching {len(all_image_tasks)} image generations in parallel...")
                    await asyncio.gather(*all_image_tasks, return_exceptions=True)
                    
                # Finally reload items in the main session to ensure we have the URLs
                await db.commit()
                # Refresh items to see changes from other sessions
                for item in items:
                    await db.refresh(item)
            
            total_time_ms = int((time.time() - start_time) * 1000)
            logger.info(f"[SESSION_CONVERT] Plan conversion complete in {total_time_ms}ms (saved ~100s)")
            
            # Step 8: Format and return response
            return await self._format_plan_response(plan, db)
            
        except Exception as e:
            logger.error(f"[SESSION_CONVERT] Error converting session recs: {e}", exc_info=True)
            await db.rollback()
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
        
        prompt = f"""Generate exactly {num_actions} wellness action(s) for this user.

======================================================================
EXISTING ACTIONS (user already has these - DO NOT duplicate similar content)
======================================================================
{existing_summary}

======================================================================
USER PROFILE
======================================================================
- Age: {user_context.get('age', 'Not specified')}
- Cycle Day: {user_context.get('cycle_day', 'Unknown')}
- Cycle Phase: {cycle_phase}
- Primary Hormone: {primary_hormone}
- Secondary Hormone: {secondary_hormone}
- Top Concern: {user_context.get('top_concern', 'general wellness')}
- Conditions: {', '.join(user_context.get('diagnosed_conditions', [])) or 'none'}
- Current Streak: {user_context.get('current_streak', 0)} days
- Longest Streak: {user_context.get('longest_streak', 0)} days

======================================================================
RECENT INSIGHTS
======================================================================
Weekly Check-ins:
{user_context.get('weekly_checkin_insights', 'None')}

Daily Reviews:
{user_context.get('daily_review_insights', 'None')}

======================================================================
{hormone_instruction}
======================================================================

======================================================================
REQUIREMENTS
======================================================================
1. Generate exactly {num_actions} NEW action(s) - DIFFERENT content from existing ones
2. Mix categories (food, movement, mindfulness) to complement existing
3. STRICTLY follow the hormone count requirement above
4. Each action needs:
   - title: Clear action title
   - category: "food" or "movement" or "mindfulness"
   - time_slot: "morning" or "afternoon" or "evening"
   - specific_action: Detailed instruction
   - purpose: Why this helps the user
   - target_hormone: "{primary_hormone}" OR "{secondary_hormone}" (follow counts above)
   - hormone_persona_intro: Brief hormone benefit statement
   - image_prompt: Description for image generation
   - research_studies: [] (empty array)
   - variants: [] (empty array - will be filled later)
   - food_items/food_amounts for food category
   - exercise_types/exercise_durations/exercise_intensities for movement category
   - mindfulness_techniques/mindfulness_durations for mindfulness category
   - symptoms: ["symptom1", "symptom2"] (2 from user concerns)
   - conditions: [] (empty array)

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
                    
                    response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": "You are a womens wellness expert. Generate personalized health actions. Follow hormone balance requirements EXACTLY."},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.7,
                        max_tokens=3000,
                        response_format={"type": "json_object"}
                    )
                    
                    content = response.choices[0].message.content
                    cost = (response.usage.prompt_tokens * 0.00015 + response.usage.completion_tokens * 0.0006) / 1000
                    logger.info(" Partial actions generated via OpenAI")
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
                    enhanced_prompt = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown." if is_reasoning_model else prompt
                    
                    response = await groq_client.chat.completions.create(
                        model=GROQ_FALLBACK_MODEL,
                        messages=[
                            {"role": "system", "content": "You are a womens wellness expert. Generate personalized health actions. Follow hormone balance requirements EXACTLY."},
                            {"role": "user", "content": enhanced_prompt}
                        ],
                        temperature=0.7,
                        max_tokens=3000
                    )
                    
                    content = response.choices[0].message.content
                    
                    # Clean reasoning model output
                    if is_reasoning_model:
                        if content.startswith("```json"):
                            content = content[7:]
                        if content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        content = content.strip()
                    
                    logger.info(" Partial actions generated via Groq fallback")
                except Exception as e:
                    logger.error(f" Groq fallback also failed: {e}")
                    return (None, 0.0)
            elif openai_error:
                logger.error(f" OpenAI failed and no Groq fallback: {openai_error}")
                return (None, 0.0)
            
            if not content:
                return (None, 0.0)
            
            # Parse response
            parsed = json.loads(content)
            actions = parsed.get("actions", parsed if isinstance(parsed, list) else [parsed])
            
            # Ensure we have a list
            if not isinstance(actions, list):
                actions = [actions]
            
            logger.info(f"Generated {len(actions)} partial actions for carryforward plan")
            return (actions[:num_actions], cost)
            
        except Exception as e:
            logger.error(f"Failed to generate partial actions: {e}")
            return (None, 0.0)
    
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
            
            # Load base context with defaults
            context = {
                "user_id": user_id,
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
                        response = await self.client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.openai_api_key}",
                                "Content-Type": "application/json"
                            },
                            json={
                                "model": "gpt-4o-mini",
                                "messages": [
                                    {"role": "system", "content": "You are a wellness AI analyzing user feedback patterns."},
                                    {"role": "user", "content": summary_prompt}
                                ],
                                "temperature": 0.3,
                                "max_tokens": 500
                            }
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
        
        # Enhanced system prompt with tool calling instructions
        enhanced_system = SYSTEM_PROMPT + f"""

CURRENT USERS HORMONE CONTEXT:
- Cycle Phase: {cycle_phase}
- Primary Hormone: {user_context["primary_hormone"]} - {primary_behavior}
- Secondary Hormone: {user_context["secondary_hormone"]} - {secondary_behavior}

Write the hormone_persona_intro naturally, following the example style above. The hormone should:
1. Introduce itself by name ("I am Progesterone...")
2. Explain what is happening in this cycle phase
3. Connect the recommended action to how it helps the hormone and the user

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
        
        # Get users conditions for research queries
        diagnosed_conditions = ", ".join(user_context.get("diagnosed_conditions", [])) or "womens health"
        
        try:
            # =======================================================================
            # STEP 1: RESEARCH DISCOVERY PHASE
            # Search for evidence-based interventions BEFORE deciding what to recommend
            # =======================================================================
            logger.info(" STEP 1: Research Discovery Phase - Finding what works for this user...")
            
            # Define research queries based on users specific context
            research_queries = [
                # Food interventions for primary hormone + condition
                f"{primary_hormone} food nutrition {diagnosed_conditions} women intervention",
                # Food interventions for secondary hormone
                f"{secondary_hormone} diet nutrition {diagnosed_conditions} women",
                # Movement interventions
                f"exercise physical activity {primary_hormone} {diagnosed_conditions} women",
                # Mindfulness interventions
                f"mindfulness stress reduction {primary_hormone} {diagnosed_conditions} women"
            ]
            
            categories = ["food", "food", "movement", "mindfulness"]
            hormones = [primary_hormone, secondary_hormone, primary_hormone, primary_hormone]
            
            # =======================================================================
            # PARALLEL EXECUTION: Run all PubMed searches concurrently for speed
            # Before: ~2000ms (4 x 500ms sequential)
            # After:  ~500ms  (parallel)
            # =======================================================================
            async def fetch_research_paper(index: int, query: str) -> Dict[str, Any]:
                """Fetch a single research paper for a query."""
                try:
                    paper = await execute_pubmed_tool({
                        "query": query,
                        "action_title": f"Research {index + 1}",
                        "category": categories[index],
                        "target_hormone": hormones[index]
                    }, db=db)
                    
                    if paper and paper.get("title"):
                        return {
                            "query": query,
                            "category": categories[index],
                            "hormone": hormones[index],
                            "paper": paper
                        }
                    return None
                except Exception as e:
                    logger.warning(f"Research query failed: {query[:40]}... Error: {e}")
                    return None
            
            # Execute all searches in parallel
            logger.info(f"   Searching {len(research_queries)} queries in parallel...")
            results = await asyncio.gather(
                *[fetch_research_paper(i, q) for i, q in enumerate(research_queries)],
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
                logger.info(f"   Found: {result['paper'].get('title', '')[:50]}...")
            
            logger.info(f" Research complete: Found {len(research_findings)} relevant papers")
            
            # Build research summary for GPT
            research_summary = "\\n\\n======================================================================\\n"
            research_summary += "RESEARCH FINDINGS - USE THESE TO INFORM YOUR RECOMMENDATIONS\\n"
            research_summary += "======================================================================\\n"
            
            for finding in research_findings:
                paper = finding["paper"]
                research_summary += f"""
 Research for {finding['hormone'].upper()} ({finding['category']}):
   Title: {paper.get('title', 'Unknown')}
   Journal: {paper.get('journal', 'Unknown')} ({paper.get('year', 'N/A')})
   Finding: {paper.get('finding', 'No finding extracted')}
   PMID: {paper.get('pmid', 'N/A')}
   
    Use this to inform your {finding['category']} recommendation for {finding['hormone']}
"""
            
            research_summary += "\nIMPORTANT: Your recommendations MUST be based on the research findings above.\n"
            research_summary += "Include the paper details in the research_studies field for each action.\n"
            
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
            openai_payload = {
                "model": self.GPT_MODEL,
                "messages": [
                    {"role": "system", "content": enhanced_system_with_research},
                    {"role": "user", "content": prompt}
                ],
                "temperature": self.GPT_TEMPERATURE,
                "max_tokens": 4000,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "action_plan",
                        "strict": True,
                        "schema": ACTION_PLAN_SCHEMA
                    }
                }
            }
            
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
        category: str
    ) -> Tuple[bool, List[str]]:
        """
        Validate that all required fields are present for the given category.
        
        Args:
            action: Action dictionary from GPT
            category: Category type (food/movement/mindfulness)
            
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
                # Use empty array instead of fake research - maintains honesty
                action["research_studies"] = []
                logger.warning(f" No research available for '{action.get('title', 'Untitled')}' - using empty array")
            
            if not action.get("variants") or len(action.get("variants", [])) < 3:
                # Fill up to 3 variants
                existing_variants = action.get("variants", [])
                variant_types = {
                    "food": ["healthy", "easy", "tasty"],
                    "movement": ["gentle", "quick", "energizing"],
                    "mindfulness": ["brief", "guided", "solo"]
                }.get(category, ["alternative", "alternative", "alternative"])
                
                while len(existing_variants) < 3:
                    idx = len(existing_variants)
                    existing_variants.append({
                        "variant_type": variant_types[idx] if idx < len(variant_types) else "alternative",
                        "title": f"Variation {idx + 1}",
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
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are a health plan quality evaluator. Output ONLY JSON, no explanation."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 150,  # Enough for 5 scores
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
        task_data_list = []
        
        for action_idx, action in enumerate(actions):
            action_title = action.get("title", "Wellness Action")
            action_category = action.get("category", "food")
            
            # Hero image task data
            if image_mode != "variants_only":
                task_data_list.append({
                    "prompt": action_title,
                    "category": action_category,
                    "variant_type": "hero",
                    "meta": {"action_idx": action_idx, "variant_idx": None}
                })
            
            # Variant image tasks data
            if image_mode in ["full", "variants_only"]:
                variants = action.get("variants", [])
                for variant_idx, variant in enumerate(variants):
                    if not isinstance(variant, dict):
                        continue
                    variant_title = variant.get("title", f"{variant.get('variant_type', 'variant')} {action_title}")
                    task_data_list.append({
                        "prompt": variant_title,
                        "category": action_category,
                        "variant_type": variant.get("variant_type", f"variant_{variant_idx}"),
                        "meta": {"action_idx": action_idx, "variant_idx": variant_idx}
                    })

        if not task_data_list:
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
            replacement_prompt = f"""Generate 1 replacement wellness action BASED ON THE RESEARCH BELOW.

 CRITICAL WARNING 
You MUST include ALL category-specific fields or your response will be REJECTED and regenerated.
Previous failures happened because you forgot exercise_types, exercise_durations, exercise_intensities for movement.

{research_context}

REQUIREMENTS:
- Must target hormone: {original.target_hormone}
- Should be DIFFERENT from: {original.title} (user disliked this)
- Dislike reason: {reason or 'not specified'}
- AVOID generating same category as disliked ({original.category}) unless users lifestyle_focus only includes that category
- Prefer different category from: {original.category}
- Users lifestyle focus: {user_context.get('lifestyle_focus', ['eat', 'move', 'pause'])}

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
                                    {"role": "user", "content": replacement_prompt}
                                ],
                                # No tools needed - research already pre-fetched
                                "temperature": 0.7,
                                "max_tokens": 2500,
                                "response_format": {"type": "json_object"}
                            }
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

            
            # Generate images for replacement using TITLE for cache matching
            replacement_title = replacement_action.get("title", "Wellness Action")
            replacement_category = replacement_action.get("category", "food")
            logger.info(f"[REPLACE] Generating image: '{replacement_title[:40]}' ({replacement_category})")
            
            hero_url, was_cached, _ = await self.image_service.get_or_generate_image(
                prompt=replacement_title,  # Use TITLE for cache matching
                category=replacement_category,
                variant_type="hero",
                user_id=user_id,
                db=db
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
            
            # Generate variant images SEQUENTIALLY (not parallel) to avoid db session conflicts
            # SQLAlchemy async sessions can't be shared across concurrent tasks
            variant_results = []
            for vd in variant_data:
                try:
                    # Use variant TITLE for cache matching
                    variant_title = vd["variant"].get("title", f"{vd['v_type']} version")
                    logger.info(f"[REPLACE] Generating variant: '{variant_title[:40]}' ({category})")
                    result = await self.image_service.get_or_generate_image(
                        prompt=variant_title,  # Use TITLE for cache matching
                        category=category,
                        variant_type=vd["v_type"],
                        user_id=user_id,
                        db=db
                    )
                    variant_results.append(result)
                except Exception as e:
                    logger.error(f"Variant image generation failed: {e}")
                    variant_results.append(("", False, 0.0))
            
            # Create variant records from results
            for i, result in enumerate(variant_results):
                variant_url, _, _ = result
                
                variant_record = ActionPlanItemVariant(
                    item_id=new_item.id,
                    variant_type=variant_data[i]["v_type"],
                    title=variant_data[i]["variant"].get("title", ""),
                    description=variant_data[i]["variant"].get("description", ""),
                    image_url=variant_url,
                    image_prompt=variant_data[i]["variant"].get("image_prompt"),
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

            prompt = f"""Generate {n} DIFFERENT replacement wellness actions.

╔══════════════════════════════════════════════════════════════════════════════╗
║  CRITICAL: ALL alternatives MUST be {original_category.upper()} category!              ║
║  User is replacing a {original_category} item → suggest ONLY other {original_category} options!          ║
╚══════════════════════════════════════════════════════════════════════════════╝

{research_context}

STRICT REQUIREMENTS:
1. Category MUST be: {original_category} (DO NOT suggest other categories!)
2. Target hormone MUST be: {original_hormone}
3. Must be DIFFERENT from: {original.title}
4. Dislike reason: {reason or 'user wants alternatives'}
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
                    response = await self.client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.openai_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.GPT_MODEL,
                            "messages": [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": 0.5,
                            "max_tokens": 4500,
                            "response_format": {"type": "json_object"},
                        },
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
                    variant_url, _, _ = await self.image_service.get_or_generate_image(
                        prompt=vd["variant"].get("image_prompt", vd["variant"].get("title")),
                        category=category,
                        variant_type=vd["v_type"],
                        user_id=user_id,
                        db=db,
                    )
                except Exception as e:
                    logger.error(f"Variant image generation failed: {e}")
                    variant_url = ""

                variant_record = ActionPlanItemVariant(
                    item_id=new_item.id,
                    variant_type=vd["v_type"],
                    title=vd["variant"].get("title", ""),
                    description=vd["variant"].get("description", ""),
                    image_url=variant_url,
                    image_prompt=vd["variant"].get("image_prompt"),
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
                                "max_tokens": 8000
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
                                        "max_tokens": 8000,
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
            
            # Debug: Log all fields for each replacement action to verify GPT response
            logger.info(f" GPT returned {len(replacement_actions)} replacement actions")
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
            
            # ===== PARALLEL IMAGE GENERATION =====
            # First, generate all hero images in parallel for speed
            from app.core.database import async_engine
            from sqlalchemy.ext.asyncio import AsyncSession
            logger.info(f" Generating {len(replacement_actions)} hero images in PARALLEL...")
            
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
            
            logger.info(f" All {len(replacement_actions)} hero images generated in parallel")
            
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
                
                # Generate variant images (up to 3)
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
                    
                    variant_url, was_cached, variant_cost = await self.image_service.get_or_generate_image(
                        prompt=variant_title,  # Use TITLE for cache matching
                        category=replacement_action.get("category", "food"),
                        variant_type=v_type,
                        user_id=user_id,
                        db=db
                    )
                    total_cost += variant_cost
                    
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
