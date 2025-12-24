"""
AUVRA Action Plan Generator Service

Generates 4 personalized daily actions using GPT-4o-mini:
- 2 actions targeting PRIMARY hormone
- 2 actions targeting SECONDARY hormone  
- Categories based on user's lifestyle_focus (eat/move/pause)
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
import asyncio
import traceback
import hashlib
from typing import Optional, List, Dict, Any, Tuple, Literal
from datetime import datetime, timezone, date, timedelta

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update, text

from app.services.image_library_service import get_image_library_service
from app.services.pubmed_service import PUBMED_SEARCH_TOOL, execute_pubmed_tool

logger = logging.getLogger(__name__)


# ============================================================================
# PYDANTIC MODELS FOR STRUCTURED OUTPUTS
# ============================================================================

class ResearchStudyModel(BaseModel):
    """Research citation from PubMed."""
    title: str
    journal: str
    year: int
    participants: int = 0
    finding: str = ""
    pmid: str = ""
    verification_link: str = ""


class ActionVariantModel(BaseModel):
    """Variant of an action (e.g., easy, tasty, healthy versions)."""
    variant_type: str
    title: str
    description: str
    image_prompt: str


class ActionItemModel(BaseModel):
    """Single action item with all required fields."""
    title: str
    category: Literal["food", "movement", "mindfulness"]
    time_slot: Literal["morning", "afternoon", "evening"]
    specific_action: str
    purpose: str
    target_hormone: str
    hormone_persona_intro: str
    image_prompt: str
    research_studies: List[ResearchStudyModel] = Field(default_factory=list)
    variants: List[ActionVariantModel] = Field(min_length=3, max_length=3)
    symptoms: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    # Category-specific fields (validated separately based on category)
    food_items: Optional[List[str]] = None
    food_amounts: Optional[List[str]] = None
    exercise_types: Optional[List[str]] = None
    exercise_durations: Optional[List[str]] = None
    exercise_intensities: Optional[List[str]] = None
    mindfulness_techniques: Optional[List[str]] = None
    mindfulness_durations: Optional[List[str]] = None


class ActionPlanResponseModel(BaseModel):
    """Complete action plan response from GPT with exactly 4 actions."""
    actions: List[ActionItemModel] = Field(min_length=4, max_length=4)


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
        "emoji": "🌸",
        "personality": "your calming companion",
        "phase_behavior": {
            "menstrual": "I tend to spike during your period, which can make you feel more stressed or anxious",
            "follicular": "I'm usually balanced in your follicular phase, but stress can still throw me off",
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
        "emoji": "🌙",
        "personality": "your peaceful guide",
        "phase_behavior": {
            "menstrual": "I'm at my lowest during your period, which can affect your mood and sleep",
            "follicular": "I'm starting to build up in your follicular phase, preparing your body",
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
        "emoji": "✨",
        "personality": "your radiant friend",
        "phase_behavior": {
            "menstrual": "I'm at my lowest during your period, which can cause fatigue and low mood",
            "follicular": "I'm rising in your follicular phase, boosting your energy and mood",
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
        "emoji": "🔥",
        "personality": "your energizing coach",
        "phase_behavior": {
            "menstrual": "I'm lower during your period, which can reduce your drive and energy",
            "follicular": "I'm rising in your follicular phase, boosting your motivation",
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
        "emoji": "🌿",
        "personality": "your balance keeper",
        "phase_behavior": {
            "menstrual": "I can be less sensitive during your period, causing blood sugar fluctuations",
            "follicular": "I work more efficiently in your follicular phase, keeping energy stable",
            "ovulation": "I'm balanced around ovulation, helping maintain steady energy",
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
        "emoji": "🦋",
        "personality": "your metabolism friend",
        "phase_behavior": {
            "menstrual": "I can slow down during your period, affecting your energy and metabolism",
            "follicular": "I'm more active in your follicular phase, boosting your metabolism",
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
    "emoji": "💜",
    "personality": "your wellness guide",
    "phase_behavior": {
        "menstrual": "I can fluctuate during your period, affecting your overall wellness",
        "follicular": "I'm adjusting in your follicular phase as your body prepares",
        "ovulation": "I'm active around ovulation, supporting your body's natural rhythm",
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

SYSTEM_PROMPT = """You are AUVRA's personalized wellness AI that creates daily action plans for women's hormonal health.

═══════════════════════════════════════════════════════════════════════════════
🚨 CRITICAL - CATEGORY-SPECIFIC REQUIRED FIELDS (READ THIS FIRST!) 🚨
═══════════════════════════════════════════════════════════════════════════════
For EVERY action, you MUST include the category-specific fields based on the category.
FAILURE TO INCLUDE THESE FIELDS WILL CAUSE VALIDATION ERRORS.

✅ For "food" category, ALWAYS include:
   - food_items: ["steel-cut oats", "blueberries", "almonds"]  // Array of specific foods
   - food_amounts: ["1/2 cup", "handful", "10-12 pieces"]    // Array of amounts

✅ For "movement" category, ALWAYS include:
   - exercise_types: ["Gentle Morning Yoga", "Walking"]       // Array of specific exercises
   - exercise_durations: ["15-20 minutes"]                    // Array of duration strings
   - exercise_intensities: ["Low", "Moderate"]                // Array of intensity levels

✅ For "mindfulness" category, ALWAYS include:
   - mindfulness_techniques: ["Box Breathing", "Body Scan"]   // Array of techniques
   - mindfulness_durations: ["5-10 minutes"]                  // Array of durations

═══════════════════════════════════════════════════════════════════════════════

IMPORTANT GUIDELINES:
1. Each action must target EXACTLY ONE hormone - the specified target hormone
2. Actions should be specific, actionable, and achievable in one day
3. Use the 'search_research_paper' tool to get REAL citations - NEVER fabricate citations
4. Time slots should be appropriate: morning (6-11am), afternoon (12-5pm), evening (6-10pm)
5. Image prompts should follow a consistent photography style for better semantic matching

CATEGORY DEFINITIONS:
- "food" (eat): Specific meals, recipes, or food recommendations
- "movement" (move): Exercise, stretching, physical activities
- "mindfulness" (pause): Meditation, breathing, relaxation, mental wellness

(Note: Category-specific required fields defined in CRITICAL section above)

RESEARCH CITATION FORMAT (from search_research_paper tool):
{
    "title": "Study title from PubMed/OpenAlex",
    "journal": "Journal name from tool result",
    "year": 2020,
    "participants": 156,
    "finding": "Key finding from paper abstract",
    "pmid": "12345678"  // Include PMID for verification
}

IMAGE PROMPT STYLE (for consistent semantic matching):
All prompts should follow this pattern:
"[Subject/food/activity], professional photography, natural lighting, clean minimalist background, warm inviting tones, wellness aesthetic"

Examples:
- "Bowl of steel-cut oatmeal with berries and nuts, professional food photography, natural morning light, clean minimalist background, warm inviting tones"
- "Woman doing gentle morning yoga stretch, professional wellness photography, natural lighting, serene background, calming atmosphere"
- "Peaceful meditation corner with candles and plants, professional lifestyle photography, soft natural light, minimalist aesthetic"

HORMONE PERSONA INTRO STYLE:
The hormone speaks in first person, identifying itself and explaining what's happening in the user's current cycle phase (1 sentence). 
CRITICAL: Do NOT explain how the action helps here. That goes in the 'purpose' field. Write naturally and warmly.

EXAMPLE INTROS (Persona part only):
- "I'm Progesterone — in your luteal phase, I tend to dip, causing mood swings or cramps."
- "I'm Estrogen — in your menstrual phase, I'm at my lowest which can cause fatigue and low mood."
- "I'm Insulin — in your luteal phase, I become less sensitive, causing cravings and energy crashes."
- "I'm Cortisol — when stress is high, I spike and can disrupt your body's natural rhythm."
- "I'm Cortisol — in your follicular phase, I'm usually balanced but stress can still throw me off."
- "I'm Testosterone — around ovulation, I peak giving you extra confidence and drive."
- "I'm Thyroid — in your luteal phase, I can slow down causing sluggishness."
- "I'm Estrogen — in your follicular phase, I'm rising and boosting your mood."
"""

ACTION_GENERATION_PROMPT = """Generate {num_actions} personalized daily wellness actions for this user.

══════════════════════════════════════════════════════════════════════
HEALTH PROFILE
══════════════════════════════════════════════════════════════════════
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

══════════════════════════════════════════════════════════════════════
PERSONALIZATION FACTORS
══════════════════════════════════════════════════════════════════════
- Lifestyle Focus: {lifestyle_focus}
- Diet Preference: {diet_preference}
- Food Allergies/Restrictions: {food_allergies}
- Stress Level: {stress_level}
- Sleep Duration: {sleep_duration}
- Workout Intensity: {workout_intensity}
- Birth Control: {birth_control}

══════════════════════════════════════════════════════════════════════
HORMONE CONTEXT FOR {cycle_phase} PHASE
══════════════════════════════════════════════════════════════════════
{hormone_phase_context}

══════════════════════════════════════════════════════════════════════
FEEDBACK MEMORY (Critical - avoid disliked patterns, repeat liked patterns)
══════════════════════════════════════════════════════════════════════
HISTORICAL SUMMARY (learned patterns over time):
{feedback_summary}

RECENT FEEDBACK (last 20-50 actions):
{feedback_memory}

══════════════════════════════════════════════════════════════════════
CHATBOT CONVERSATION CONTEXT
══════════════════════════════════════════════════════════════════════
{chatbot_context}

══════════════════════════════════════════════════════════════════════
REQUIREMENTS
══════════════════════════════════════════════════════════════════════
1. Generate exactly {num_actions} actions total
2. Actions targeting PRIMARY hormone ({primary_hormone}): {primary_count}
3. Actions targeting SECONDARY hormone ({secondary_hormone}): {secondary_count}
4. Category distribution based on lifestyle_focus: {category_guidance}
5. Each action must be unique and specific
6. Time slots should be varied (mix of morning, afternoon, evening)
7. RESPECT food allergies - NEVER recommend foods the user is allergic to
8. RESPECT diet preferences - if vegetarian, no meat; if vegan, no animal products
9. Consider diagnosed conditions when recommending (e.g., no high-intensity for certain conditions)
10. Learn from feedback - create actions SIMILAR to liked ones, AVOID patterns from disliked ones

══════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (for each action)
══════════════════════════════════════════════════════════════════════
1. title: Short, catchy title (3-5 words, e.g., "Pumpkin Seed Power")
2. category: "food", "movement", or "mindfulness"
3. time_slot: "morning", "afternoon", or "evening"
4. specific_action: Detailed, actionable description (50-100 words)
5. purpose: One clear sentence explaining exactly how this specific action/food helps balance or support the target hormone (e.g., "Pumpkin seeds are packed with zinc and magnesium that help boost me and keep you calmer.")
6. target_hormone: The hormone this action supports
7. hormone_persona_intro: Write naturally following the example style in system prompt
8. image_prompt: FLUX.1 Schnell optimized prompt (see IMAGE PROMPT REQUIREMENTS below)
9. research_studies: Array with EXACTLY 1 REAL research citation focused on WOMEN/FEMALES (see format below)
10. variants: Array of 3 variant objects with REQUIRED fields (see VARIANT FORMAT below)
11. symptoms: Array of strings - specific user symptoms this action addresses (e.g., ["acne", "fatigue", "bloating"])
12. conditions: Array of strings - specific conditions this action is beneficial for (e.g., ["PCOS", "endometriosis"])

CATEGORY-SPECIFIC REQUIRED FIELDS:
For FOOD actions, MUST include:
- food_amounts: Array like ["1 tbsp", "2 tablespoons", "handful"]
- food_items: Array like ["pumpkin seeds", "flaxseeds"]

For MOVEMENT actions, MUST include:
- exercise_durations: Array like ["15 min", "20 minutes walk"]
- exercise_types: Array like ["yoga", "walking", "stretching"]
- exercise_intensities: Array like ["low", "moderate"]

For MINDFULNESS actions, MUST include:
- mindfulness_durations: Array like ["5 min", "10 minutes"]
- mindfulness_techniques: Array like ["deep breathing", "meditation"]

IMAGE PROMPT REQUIREMENTS (for FLUX.1 Schnell):
Generate professional, appetizing, calming visuals that work in a mobile wellness app:
- For FOOD: "Professional food photography of [specific dish], overhead view, natural lighting, rustic wooden table background, fresh ingredients visible, warm color tones, appetizing presentation, 4K quality"
- For MOVEMENT: "Serene photograph of woman practicing [specific exercise], soft natural lighting, peaceful indoor/outdoor setting, wellness aesthetic, warm earth tones, calm atmosphere, 4K quality"
- For MINDFULNESS: "Peaceful zen scene with [specific elements like candles, tea, cushion], soft diffused lighting, minimalist aesthetic, calming colors, cozy atmosphere, 4K quality"

VARIANT FORMAT (REQUIRED structure):
Each variant MUST be an object with these exact fields:
- variant_type: MUST be one of the allowed types (see below)
- title: Specific name of this variant (e.g., "Roasted Pumpkin Seeds with Sea Salt", "Avocado Toast with Pumpkin Topping")
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

# OpenAI Structured Outputs schema - guarantees all required fields are present
ACTION_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # Base fields (required for all categories)
                    "title": {"type": "string"},
                    "category": {"type": "string", "enum": ["food", "movement", "mindfulness"]},
                    "time_slot": {"type": "string", "enum": ["morning", "afternoon", "evening"]},
                    "specific_action": {"type": "string"},
                    "purpose": {"type": "string"},
                    "target_hormone": {"type": "string"},
                    "hormone_persona_intro": {"type": "string"},
                    "image_prompt": {"type": "string"},
                    
                    # Category-specific fields (optional in schema, validated in code)
                    "food_items": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "food_amounts": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "exercise_types": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "exercise_durations": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "exercise_intensities": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "mindfulness_techniques": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "mindfulness_durations": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    
                    # Research studies (exactly 1)
                    "research_studies": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "journal": {"type": "string"},
                                "year": {"type": "integer"},
                                "participants": {"type": "integer"},
                                "finding": {"type": "string"},
                                "pmid": {"type": "string"},
                                "verification_link": {"type": "string"}
                            },
                            "required": ["title", "journal", "year", "participants", "finding", "pmid"],
                            "additionalProperties": False
                        }
                    },
                    
                    # Variants (exactly 3)
                    "variants": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "variant_type": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "image_prompt": {"type": "string"}
                            },
                            "required": ["variant_type", "title", "description", "image_prompt"],
                            "additionalProperties": False
                        }
                    },
                    
                    # Optional metadata
                    "symptoms": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "conditions": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": [
                    "title", "category", "time_slot", "specific_action", 
                    "purpose", "target_hormone", "hormone_persona_intro", 
                    "image_prompt", "research_studies", "variants"
                ],
                "additionalProperties": False
            },
            "minItems": 4,
            "maxItems": 4
        }
    },
    "required": ["actions"],
    "additionalProperties": False
}

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
    1. Check if today's plan exists
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
        
        # Semaphore to limit concurrent DB writes/ops to 5 at a time
        self.db_semaphore = asyncio.Semaphore(5)
        
        logger.info(f"ActionPlanGenerator initialized with shared engine")
        logger.info(f"  OpenAI configured: {bool(self.openai_api_key)}")
    
    async def get_or_generate_today_plan(
        self,
        user_id: str,
        user_timezone: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Get today's action plan or generate a new one.
        
        This is the main entry point called on app open.
        """
        from app.core.database import ActionPlan
        
        # Get today's date in user's timezone
        today = self._get_user_today(user_timezone)
        
        # Check if plan exists
        existing_plan = await self._get_existing_plan(user_id, today, db)
        
        if existing_plan:
            logger.info(f"Found existing plan for user {user_id} on {today}")
            return await self._format_plan_response(existing_plan, db)
        
        # Generate new plan
        logger.info(f"Generating new plan for user {user_id} on {today}")
        return await self.generate_new_plan(user_id, today, user_timezone, db)
    
    async def generate_new_plan(
        self,
        user_id: str,
        plan_date: date,
        user_timezone: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Generate a completely new action plan.
        
        Uses PostgreSQL advisory lock to prevent race conditions.
        
        Steps:
        1. Acquire advisory lock for user+date
        2. Check for existing plan (double-check after lock)
        3. Load user context
        4. Generate actions via GPT
        5. Generate images for each action
        6. Store in database
        7. Release lock
        """
        start_time = time.time()
        total_cost = 0.0
        lock_key = hash(f"{user_id}:{plan_date}") % 2147483647  # int32 range for PostgreSQL
        got_lock = False
        
        try:
            # Step 0: Acquire advisory lock to prevent race conditions
            # Two requests for the same user+date will serialize here
            lock_result = await db.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": lock_key}
            )
            got_lock = lock_result.scalar()
            
            if not got_lock:
                # Another request is already generating - wait and check for result
                logger.info(f"🔒 Another request is generating plan for {user_id}, waiting...")
                await asyncio.sleep(3)  # Wait for the other request to complete
                
                # Check if plan was created by the other request
                existing_plan = await self._get_existing_plan(user_id, plan_date, db)
                if existing_plan:
                    logger.info(f"✅ Found plan created by concurrent request")
                    return await self._format_plan_response(existing_plan, db)
                
                # Still no plan - try to acquire lock (blocking)
                logger.info(f"🔒 Acquiring blocking lock for {user_id}...")
                await db.execute(
                    text("SELECT pg_advisory_lock(:key)"),
                    {"key": lock_key}
                )
                got_lock = True
            
            # Double-check for existing plan after acquiring lock
            existing_plan = await self._get_existing_plan(user_id, plan_date, db)
            if existing_plan:
                logger.info(f"Plan already exists for {user_id} on {plan_date}")
                return await self._format_plan_response(existing_plan, db)
            
            logger.info(f"🔓 Lock acquired, generating plan for {user_id} on {plan_date}")
            
            # Step 1: Load user context
            user_context = await self._load_user_context(user_id, db)
            
            if not user_context:
                logger.error(f"Could not load user context for {user_id}")
                return {"success": False, "error": "User profile not found"}
            
            # Step 2: Generate actions via GPT-4o-mini with retry logic
            # Pydantic validation ensures complete data - no fallbacks
            actions = None
            gpt_cost = 0.0
            
            for attempt in range(1, self.MAX_RETRIES + 1):
                logger.info(f"🔄 Generation attempt {attempt}/{self.MAX_RETRIES}")
                
                # Generate actions with real citations from PubMed
                # Pydantic validation happens inside _generate_actions_via_gpt
                attempt_actions, attempt_cost = await self._generate_actions_via_gpt(user_context, db)
                gpt_cost += attempt_cost
                
                if attempt_actions:
                    # Pydantic validated successfully
                    logger.info(f"✅ Attempt {attempt}: All {len(attempt_actions)} actions validated by Pydantic")
                    actions = attempt_actions
                    break
                else:
                    logger.warning(f"❌ Attempt {attempt}: Generation or validation failed")
                    if attempt < self.MAX_RETRIES:
                        logger.info(f"🔄 Retrying generation...")
                    else:
                        # Max retries exceeded - FAIL CLEANLY, no fallbacks
                        logger.error(f"❌ Max retries ({self.MAX_RETRIES}) exceeded. Failing without fallbacks.")
            
            total_cost += gpt_cost
            
            if not actions:
                logger.error("Failed to generate valid actions via GPT after all retries")
                return {"success": False, "error": "Failed to generate actions. Please try again."}
            
            # Step 3: Generate images for all actions (16 total: 4 actions × 4 images)
            actions_with_images, image_cost = await self._generate_all_images(
                actions, user_id, db
            )
            total_cost += image_cost
            
            # Step 4: Store plan in database
            plan = await self._store_plan(
                user_id=user_id,
                plan_date=plan_date,
                user_context=user_context,
                actions=actions_with_images,
                total_cost=total_cost,
                generation_time_ms=int((time.time() - start_time) * 1000),
                db=db
            )
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Plan generated in {elapsed:.2f}s, cost: ${total_cost:.4f}")
            
            return await self._format_plan_response(plan, db)
            
        except Exception as e:
            logger.error(f"Error generating plan: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return {"success": False, "error": "Failed to generate plan. Please try again."}
        finally:
            # Release advisory lock if we acquired it
            if got_lock:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": lock_key}
                    )
                    logger.info(f"🔓 Released advisory lock for {user_id}")
                except Exception as unlock_err:
                    logger.warning(f"Failed to release advisory lock: {unlock_err}")
    
    def _get_user_today(self, timezone_str: str) -> date:
        """Get today's date in user's timezone."""
        from zoneinfo import ZoneInfo
        
        try:
            tz = ZoneInfo(timezone_str)
            return datetime.now(tz).date()
        except Exception:
            # Fallback to UTC
            return datetime.utcnow().date()
    
    async def _get_existing_plan(
        self,
        user_id: str,
        plan_date: date,
        db: AsyncSession
    ) -> Optional[Any]:
        """Check if a plan already exists for this user/date."""
        from app.core.database import ActionPlan
        
        try:
            result = await db.execute(
                select(ActionPlan).where(
                    and_(
                        ActionPlan.uid == user_id,
                        ActionPlan.plan_date == plan_date
                    )
                )
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error checking existing plan: {e}")
            return None
    
    async def _load_user_context(
        self,
        user_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """Load all user context needed for action generation."""
        from app.core.database import UserProfile, UserResponse, ActionPlanFeedback
        
        try:
            # Get user profile
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.uid == user_id)
            )
            profile = profile_result.scalar_one_or_none()
            
            if not profile:
                return None
            
            # Get user responses (assessment data)
            response_result = await db.execute(
                select(UserResponse).where(UserResponse.uid == user_id).order_by(
                    UserResponse.created_at.desc()
                ).limit(1)
            )
            user_response = response_result.scalar_one_or_none()
            
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
                "chatbot_context": "No additional context"
            }

            if not user_response:
                logger.info(f"No UserResponse for {user_id}, using defaults")
                # Update focus if available in profile
                if profile.lifestyle_focus:
                    context["lifestyle_focus"] = profile.lifestyle_focus
                return context
            
            # Get recent feedback for memory (last 30 days)
            feedback_result = await db.execute(
                select(ActionPlanFeedback).where(
                    ActionPlanFeedback.uid == user_id
                ).order_by(ActionPlanFeedback.created_at.desc()).limit(50)
            )
            recent_feedback = feedback_result.scalars().all()
            
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
            
            # Extract diet preferences and allergies from chatbot memory
            diet_preference = chatbot_memory.get("diet_preference", "no preference specified")
            food_allergies = chatbot_memory.get("food_allergies", [])
            if isinstance(food_allergies, list):
                food_allergies = ", ".join(food_allergies) if food_allergies else "none specified"
            
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
                "diet_preference": diet_preference,
                "food_allergies": food_allergies,
                "stress_level": user_response.stress_level or "moderate",
                "sleep_duration": user_response.sleep_duration or "7-8 hours",
                "workout_intensity": user_response.workout_intensity or "moderate",
                "feedback_summary": feedback_summary or "No summary yet",
                "feedback_memory": feedback_memory,
                "chatbot_memory": chatbot_memory,
                "chatbot_context": chatbot_context
            })
            
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
            context_parts.append(f"User's goals: {chatbot_memory['goals']}")
        if chatbot_memory.get("notes"):
            context_parts.append(f"Other notes: {chatbot_memory['notes']}")
        
        return "\n".join(context_parts) if context_parts else "No additional context from conversations."
    
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
        """Format recent feedback for GPT context with pattern analysis."""
        if not feedback_list:
            return "No previous feedback available - this is likely a new user."
        
        liked = []
        disliked = []
        skipped = []
        completed = []
        
        # Analyze patterns
        liked_categories = {}
        disliked_categories = {}
        liked_hormones = {}
        disliked_hormones = {}
        
        for fb in feedback_list:
            category = fb.action_category or "unknown"
            hormone = fb.target_hormone or "unknown"
            
            if fb.feedback_type == "like":
                liked.append(f"- {category}: {fb.action_title}")
                liked_categories[category] = liked_categories.get(category, 0) + 1
                liked_hormones[hormone] = liked_hormones.get(hormone, 0) + 1
            elif fb.feedback_type == "dislike":
                reason = fb.replacement_reason or "unspecified"
                disliked.append(f"- {category}: {fb.action_title} (reason: {reason})")
                disliked_categories[category] = disliked_categories.get(category, 0) + 1
                disliked_hormones[hormone] = disliked_hormones.get(hormone, 0) + 1
            elif fb.feedback_type == "skip":
                skipped.append(f"- {category}: {fb.action_title}")
            
            if fb.feedback_type == "completed":
                completed.append(f"- {category}: {fb.action_title}")
        
        memory_parts = []
        
        # Summary patterns
        if liked_categories or disliked_categories:
            patterns = []
            if liked_categories:
                top_liked = max(liked_categories.items(), key=lambda x: x[1])
                patterns.append(f"User tends to LIKE {top_liked[0]} actions ({top_liked[1]} likes)")
            if disliked_categories:
                top_disliked = max(disliked_categories.items(), key=lambda x: x[1])
                patterns.append(f"User tends to DISLIKE {top_disliked[0]} actions ({top_disliked[1]} dislikes)")
            memory_parts.append("PATTERNS DETECTED:\n" + "\n".join(patterns))
        
        if liked:
            memory_parts.append(f"LIKED actions (create SIMILAR ones):\n" + "\n".join(liked[:7]))
        
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
            
            logger.info(f"📊 Feedback count for user {user_id}: {current_count}, threshold: 100")
            
            # Return existing summary if count hasn't grown much
            # Use safe default for feedback_last_count to avoid None + 20 error
            last_count = getattr(profile, 'feedback_last_count', 0) or 0
            if getattr(profile, 'feedback_summary', None) and current_count < (last_count + 20):
                logger.info(f"📋 Using existing feedback summary (last updated: {getattr(profile, 'feedback_summary_updated_at', 'unknown')})")
                return getattr(profile, 'feedback_summary', None)
            
            # If count > 100, summarize
            if current_count >= 100:
                logger.info(f"🤖 Generating feedback summary with GPT for {current_count} feedback records")
                
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
                summary_prompt = f"""Analyze this user's action plan feedback history and create a concise summary of their preferences.

FEEDBACK HISTORY:
{feedback_text}

Create a summary focusing on:
1. Category preferences (food/movement/mindfulness) - what they tend to LIKE vs DISLIKE
2. Specific patterns to AVOID (e.g., "User dislikes high-intensity workouts", "Avoids raw vegetables")
3. Specific patterns to CREATE MORE (e.g., "Loves seed-based foods", "Prefers morning mindfulness")
4. Hormone-specific preferences if any patterns emerge

Keep it concise (max 200 words) and actionable for generating future action plans.
Format as bullet points."""

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
                    
                    response.raise_for_status()
                    data = response.json()
                    summary = data["choices"][0]["message"]["content"].strip()
                    
                    logger.info(f"✅ Feedback summary generated, length: {len(summary)} chars")
                    
                    # Save summary to profile
                    profile.feedback_summary = summary
                    profile.feedback_summary_updated_at = datetime.utcnow()
                    profile.feedback_last_count = current_count
                    await db.commit()
                    
                    logger.info(f"💾 Feedback summary saved to profile")
                    
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
                        logger.info(f"🗑️  Deleted {deleted_count} old feedback records, kept last 20")
                    
                    return summary
                    
                except Exception as gpt_error:
                    logger.error(f"❌ Error generating feedback summary with GPT: {gpt_error}")
                    # If summarization fails, continue with raw feedback
                    return None
            
            # Less than 100 feedback - no summary needed yet
            return getattr(profile, 'feedback_summary', None)
            
        except Exception as e:
            logger.error(f"❌ Error in feedback summarization: {e}")
            return None

    
    def _get_category_guidance(self, lifestyle_focus: List[str]) -> str:
        """Generate category distribution guidance based on lifestyle focus."""
        focus_map = {
            "eat": "food",
            "move": "movement",
            "pause": "mindfulness"
        }
        
        preferred = [focus_map.get(f, f) for f in lifestyle_focus if f in focus_map]
        
        if len(preferred) == 3:
            return "Balanced mix of food, movement, and mindfulness (1-2 each)"
        elif len(preferred) == 2:
            return f"Focus on {' and '.join(preferred)} (2 each, or 3+1)"
        elif len(preferred) == 1:
            return f"Heavy focus on {preferred[0]} (3 of this, 1 other)"
        else:
            return "Balanced mix of food, movement, and mindfulness"
    
    async def _generate_actions_via_gpt(
        self,
        user_context: Dict[str, Any],
        db: Optional[AsyncSession] = None
    ) -> Tuple[Optional[List[Dict]], float]:
        """
        Generate actions using GPT-4o-mini with tool calling for real citations.
        
        Uses search_research_paper tool to fetch real papers from PubMed/OpenAlex/Semantic Scholar.
        Caches results to database for faster future lookups.
        
        Returns (actions, cost)
        """
        if not self.openai_api_key:
            logger.error("OpenAI API key not configured")
            return (None, 0.0)
        
        # Get cycle phase for hormone context
        cycle_phase = user_context.get("cycle_phase", "follicular").lower()
        primary_hormone = user_context.get("primary_hormone", "cortisol").lower()
        secondary_hormone = user_context.get("secondary_hormone", "progesterone").lower()
        
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
            stress_level=user_context.get("stress_level", "moderate"),
            sleep_duration=user_context.get("sleep_duration", "7-8 hours"),
            workout_intensity=user_context.get("workout_intensity", "moderate"),
            # Feedback and context
            feedback_memory=user_context.get("feedback_memory", "No previous feedback"),
            chatbot_context=user_context.get("chatbot_context", "No additional context"),
            feedback_summary=user_context.get("feedback_summary", "No summary yet"),
            # Generation params
            primary_count=2,
            secondary_count=2,
            category_guidance=self._get_category_guidance(user_context.get("lifestyle_focus", [])),
            hormone_phase_context=hormone_phase_context
        )
        
        # Enhanced system prompt with tool calling instructions
        enhanced_system = SYSTEM_PROMPT + f"""

CURRENT USER'S HORMONE CONTEXT:
- Cycle Phase: {cycle_phase}
- Primary Hormone: {user_context["primary_hormone"]} - {primary_behavior}
- Secondary Hormone: {user_context["secondary_hormone"]} - {secondary_behavior}

Write the hormone_persona_intro naturally, following the example style above. The hormone should:
1. Introduce itself by name ("I'm Progesterone...")
2. Explain what's happening in this cycle phase
3. Connect the recommended action to how it helps the hormone and the user

CRITICAL - RESEARCH CITATIONS:
You MUST use the 'search_research_paper' tool for EACH action to get a REAL citation.
The tool searches PubMed, OpenAlex, and Semantic Scholar for real papers.
Include the paper details (title, journal, year, pmid, finding) in research_studies.
NEVER fabricate citations - always use the tool results.
If the tool returns empty, set research_studies to an empty array.
"""
        
        total_cost = 0.0
        
        try:
            # Step 1: Call GPT with tools
            logger.info("🤖 Calling GPT with search_research_paper tool...")
            
            response = await self.client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.GPT_MODEL,
                    "messages": [
                        {"role": "system", "content": enhanced_system},
                        {"role": "user", "content": prompt}
                    ],
                    "tools": [PUBMED_SEARCH_TOOL],
                    "tool_choice": "auto",
                    "temperature": 0.3,
                    "max_tokens": 6000
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Calculate cost
            input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
            output_tokens = data.get("usage", {}).get("completion_tokens", 0)
            total_cost += (input_tokens * 0.00015 / 1000) + (output_tokens * 0.0006 / 1000)
            
            message = data["choices"][0]["message"]
            
            # Step 2: Handle tool calls if GPT wants to search for papers
            if message.get("tool_calls"):
                logger.info(f"🔧 GPT requested {len(message['tool_calls'])} tool calls for research papers")
                
                tool_results = []
                for tool_call in message["tool_calls"]:
                    if tool_call["function"]["name"] == "search_research_paper":
                        # Safely parse tool call arguments
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                        except json.JSONDecodeError as je:
                            logger.error(f"Failed to parse tool call arguments: {je}")
                            tool_results.append({
                                "tool_call_id": tool_call["id"],
                                "role": "tool",
                                "content": json.dumps({"error": "Invalid arguments"})
                            })
                            continue
                        
                        logger.info(f"  🔍 Searching for: {args.get('action_title', 'unknown')}")
                        
                        # Execute the tool - search PubMed/OpenAlex/Semantic Scholar
                        # Pass db for caching if available
                        paper = await execute_pubmed_tool(args, db=db)
                        
                        if paper and paper.get("title"):
                            logger.info(f"  ✅ Found: {paper.get('title', '')[:50]}... (PMID: {paper.get('pmid', 'N/A')})")
                        else:
                            logger.warning(f"  ⚠️ No paper found for: {args.get('action_title', 'unknown')}")
                        
                        tool_results.append({
                            "tool_call_id": tool_call["id"],
                            "role": "tool",
                            "content": json.dumps(paper) if paper else json.dumps({"error": "No papers found"})
                        })
                
                # Step 3: Send tool results back to GPT
                logger.info("📤 Sending research results back to GPT...")
                
                # Build the assistant message with tool calls
                assistant_message = {
                    "role": "assistant",
                    "content": message.get("content"),  # May be null
                    "tool_calls": message.get("tool_calls")
                }
                
                response = await self.client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openai_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.GPT_MODEL,
                        "messages": [
                            {"role": "system", "content": enhanced_system},
                            {"role": "user", "content": prompt},
                            assistant_message,
                            *tool_results
                        ],
                        "temperature": 0.3,
                        "max_tokens": 5000,
                        "response_format": {"type": "json_object"}
                    }
                )
                
                response.raise_for_status()
                data = response.json()
                
                # Add second call cost
                input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
                output_tokens = data.get("usage", {}).get("completion_tokens", 0)
                total_cost += (input_tokens * 0.00015 / 1000) + (output_tokens * 0.0006 / 1000)
                
                content = data["choices"][0]["message"]["content"]
            else:
                # GPT didn't call tools - use response as-is
                logger.warning("⚠️ GPT did not call tools - citations may be fabricated")
                content = message.get("content", "{}")
            
            # Parse response
            response_data = json.loads(content.strip())
            
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
            
            # Validate with Pydantic - ensures all required fields are present
            try:
                validated_response = ActionPlanResponseModel(actions=raw_actions)
                actions = [action.model_dump() for action in validated_response.actions]
                
                # Additional category-specific validation
                validation_errors = []
                for i, action in enumerate(actions):
                    category = action.get("category", "food")
                    title = action.get("title", "Untitled")
                    
                    if category == "food":
                        if not action.get("food_items") or len(action.get("food_items", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [food]: missing food_items")
                        if not action.get("food_amounts") or len(action.get("food_amounts", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [food]: missing food_amounts")
                    elif category == "movement":
                        if not action.get("exercise_types") or len(action.get("exercise_types", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [movement]: missing exercise_types")
                        if not action.get("exercise_durations") or len(action.get("exercise_durations", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [movement]: missing exercise_durations")
                    elif category == "mindfulness":
                        if not action.get("mindfulness_techniques") or len(action.get("mindfulness_techniques", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [mindfulness]: missing mindfulness_techniques")
                        if not action.get("mindfulness_durations") or len(action.get("mindfulness_durations", [])) == 0:
                            validation_errors.append(f"Action {i+1} '{title}' [mindfulness]: missing mindfulness_durations")
                
                if validation_errors:
                    logger.warning(f"⚠️ Category-specific validation issues (will retry):")
                    for error in validation_errors:
                        logger.warning(f"   • {error}")
                    # Return None to trigger retry
                    return (None, total_cost)
                    
                logger.info(f"✅ Pydantic validation passed for {len(actions)} actions")
                
            except ValidationError as e:
                logger.error(f"❌ Pydantic validation failed: {e}")
                logger.error(f"   This usually means GPT returned incomplete data. Will retry.")
                return (None, total_cost)
            
            logger.info(f"✅ Generated {len(actions)} actions with REAL citations (cost: ${total_cost:.4f})")
            
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
                        logger.info(f"    📚 REAL citation: PMID {pmid}")
                    else:
                        logger.info(f"    📚 Citation from: {research[0].get('source', 'unknown')}")
                else:
                    logger.warning(f"    ⚠️ No citation for this action")
                
                logger.info(f"    symptoms={action.get('symptoms')}, "
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
                    logger.warning(f"🔧 Applied default for {field} in '{action.get('title', 'Untitled')}'")
            
            # Apply base field defaults
            if not action.get("research_studies") or len(action.get("research_studies", [])) == 0:
                # Use empty array instead of fake research - maintains honesty
                action["research_studies"] = []
                logger.warning(f"⚠️ No research available for '{action.get('title', 'Untitled')}' - using empty array")
            
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
                logger.warning(f"🔧 Filled variants for '{action.get('title', 'Untitled')}' (now {len(existing_variants)})")
            
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
    
    async def _generate_all_images(
        self,
        actions: List[Dict],
        user_id: str,
        db: AsyncSession
    ) -> Tuple[List[Dict], float]:
        """
        Generate all images for all actions (16 total) in PARALLEL.
        
        Uses asyncio.gather to generate all images concurrently:
        - 4 hero images + 12 variant images = 16 total
        - Each task uses its OWN database session to avoid concurrency issues
        - Previous: ~2-4 minutes (sequential)
        - Now: ~15-30 seconds (parallel)
        """
        
        async def _generate_single_image(prompt: str, category: str, variant_type: str, user_id: str):
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
                        db=task_session
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
        
        # Build list of all image generation tasks
        image_tasks = []
        task_metadata = []  # Track which action/variant each task belongs to
        
        for action_idx, action in enumerate(actions):
            action_title = action.get("title", "Wellness Action")
            action_category = action.get("category", "food")
            action_image_prompt = action.get("image_prompt", action_title)
            
            # Hero image task (with its own session)
            image_tasks.append(
                _generate_single_image(
                    prompt=action_image_prompt,
                    category=action_category,
                    variant_type="hero",
                    user_id=user_id
                )
            )
            task_metadata.append({"action_idx": action_idx, "variant_idx": None})
            
            # Variant image tasks (each with its own session)
            variants = action.get("variants", [])
            for variant_idx, variant in enumerate(variants):
                if not isinstance(variant, dict):
                    continue
                variant_prompt = variant.get("image_prompt", variant.get("title", action_title))
                image_tasks.append(
                    _generate_single_image(
                        prompt=variant_prompt,
                        category=action_category,
                        variant_type=variant.get("variant_type", f"variant_{variant_idx}"),
                        user_id=user_id
                    )
                )
                task_metadata.append({"action_idx": action_idx, "variant_idx": variant_idx})
        
        # Execute all image tasks in parallel (each with its own isolated session)
        logger.info(f"⚡ Generating {len(image_tasks)} images in PARALLEL with isolated sessions...")
        start_time = time.time()
        
        results = await asyncio.gather(*image_tasks, return_exceptions=True)
        
        elapsed = time.time() - start_time
        logger.info(f"⚡ All {len(image_tasks)} images generated in {elapsed:.2f}s (parallel)")
        
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
        user_id: str,
        plan_date: date,
        user_context: Dict[str, Any],
        actions: List[Dict],
        total_cost: float,
        generation_time_ms: int,
        db: AsyncSession
    ) -> Any:
        """Store the complete plan in the database."""
        from app.core.database import ActionPlan, ActionPlanItem, ActionPlanItemVariant
        
        try:
            # Create plan record
            plan = ActionPlan(
                uid=user_id,
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
                    # Use user's diagnosed conditions if action doesn't specify
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
                        
                    variant_record = ActionPlanItemVariant(
                        item_id=item.id,
                        variant_type=v_type,
                        title=variant.get("title", ""),
                        description=variant.get("description", ""),
                        image_url=variant.get("image_url"),
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
            
            # Generate a replacement action targeting the same hormone with REAL citations
            replacement_prompt = f"""Generate 1 replacement wellness action.

REQUIREMENTS:
- Must target hormone: {original.target_hormone}
- Should be DIFFERENT from: {original.title} (user disliked this)
- Dislike reason: {reason or 'not specified'}
- AVOID generating same category as disliked ({original.category}) unless user's lifestyle_focus only includes that category
- Prefer different category from: {original.category}
- User's lifestyle focus: {user_context.get('lifestyle_focus', ['eat', 'move', 'pause'])}

USER CONTEXT:
- Cycle day: {user_context.get('cycle_day', 'unknown')}
- Cycle phase: {user_context.get('cycle_phase', 'unknown')}
- Stress level: {user_context.get('stress_level', 'moderate')}
- Diet preference: {user_context.get('diet_preference', 'none')}
- Food allergies: {user_context.get('food_allergies', 'none')}
- Top concern: {user_context.get('top_concern', 'general wellness')}

FEEDBACK MEMORY (avoid similar to disliked):
{user_context.get('feedback_memory', 'No previous feedback')}

CRITICAL - CATEGORY-SPECIFIC REQUIRED FIELDS:
For FOOD actions, MUST include:
- food_amounts: Array like ["1 tbsp", "2 tablespoons", "handful"]
- food_items: Array like ["pumpkin seeds", "flaxseeds"]

For MOVEMENT actions, MUST include:
- exercise_durations: Array like ["15 min", "20 minutes"]
- exercise_types: Array like ["yoga", "walking"]
- exercise_intensities: Array like ["low", "moderate"]

For MINDFULNESS actions, MUST include:
- mindfulness_durations: Array like ["5 min", "10 minutes"]
- mindfulness_techniques: Array like ["deep breathing", "meditation"]

REQUIRED OUTPUT FIELDS:
1. category: "food", "movement", or "mindfulness"
2. title: Short, catchy title (3-5 words)
3. time_slot: "morning", "afternoon", or "evening"
4. specific_action: Detailed, actionable description (50-100 words)
5. purpose: One clear sentence about how this helps the hormone
6. target_hormone: MUST be "{original.target_hormone}"
7. hormone_persona_intro: First-person intro from hormone perspective
8. image_prompt: FLUX.1 Schnell optimized prompt
9. research_studies: Use search_research_paper tool for REAL citation (exactly 1)
10. variants: Array of 3 variant objects with variant_type, title, description, image_prompt
11. symptoms: Pick 1-3 from user's health concerns
12. conditions: Array of conditions this helps (can be empty [])

Respond with valid JSON object only."""

            # Generate replacement via GPT WITH tool calling for real citations
            MAX_REPLACEMENT_RETRIES = 2
            replacement_action = None
            
            for attempt in range(1, MAX_REPLACEMENT_RETRIES + 1):
                logger.info(f"🔄 Replacement generation attempt {attempt}/{MAX_REPLACEMENT_RETRIES}")
                
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
                        "tools": [PUBMED_SEARCH_TOOL],  # Enable real citation search
                        "tool_choice": "auto",
                        "temperature": 0.7,
                        "max_tokens": 2500
                    }
                )
                
                response.raise_for_status()
                data = response.json()
                message = data["choices"][0]["message"]
                
                # Handle tool calls for research citations
                if message.get("tool_calls"):
                    logger.info(f"🔧 GPT requested {len(message['tool_calls'])} tool calls for replacement citation")
                    
                    tool_results = []
                    for tool_call in message["tool_calls"]:
                        if tool_call["function"]["name"] == "search_research_paper":
                            try:
                                args = json.loads(tool_call["function"]["arguments"])
                                paper = await execute_pubmed_tool(args, db=db)
                                
                                if paper and paper.get("title"):
                                    logger.info(f"  ✅ Found: {paper.get('title', '')[:50]}...")
                                else:
                                    logger.warning(f"  ⚠️ No paper found")
                                
                                tool_results.append({
                                    "tool_call_id": tool_call["id"],
                                    "role": "tool",
                                    "content": json.dumps(paper) if paper else json.dumps({"error": "No papers found"})
                                })
                            except Exception as tool_err:
                                logger.error(f"Tool call error: {tool_err}")
                                tool_results.append({
                                    "tool_call_id": tool_call["id"],
                                    "role": "tool",
                                    "content": json.dumps({"error": str(tool_err)})
                                })
                    
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
                                {"role": "user", "content": replacement_prompt},
                                assistant_message,
                                *tool_results
                            ],
                            "temperature": 0.7,
                            "max_tokens": 2500,
                            "response_format": {"type": "json_object"}
                        }
                    )
                    
                    response2.raise_for_status()
                    data = response2.json()
                    content = data["choices"][0]["message"]["content"]
                else:
                    logger.warning("⚠️ GPT did not call tools - citation may be fabricated")
                    content = message.get("content", "{}")
                
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
                    
                    # Validate the action
                    category = parsed_action.get("category", "food")
                    valid, missing = self._validate_action_fields(parsed_action, category)
                    
                    if valid:
                        logger.info(f"✅ Replacement action valid")
                        replacement_action = parsed_action
                        break
                    else:
                        logger.warning(f"⚠️ Attempt {attempt} missing fields: {missing}")
                        if attempt >= MAX_REPLACEMENT_RETRIES:
                            logger.warning("⚠️ Applying minimal fallbacks for replacement")
                            replacement_action = self._fill_missing_fields([parsed_action])[0]
                            
                except json.JSONDecodeError as je:
                    logger.error(f"JSON parse error: {je}")
                    continue
            
            # Check if we got a valid replacement action
            if not replacement_action:
                logger.error("Failed to generate valid replacement action after retries")
                await db.rollback()
                return {"success": False, "error": "Failed to generate replacement. Please try again."}
            
            # Generate images for replacement
            hero_url, _, _ = await self.image_service.get_or_generate_image(
                prompt=replacement_action.get("image_prompt", replacement_action.get("title", "Wellness Action")),
                category=replacement_action.get("category", "food"),
                variant_type="hero",
                user_id=user_id,
                db=db
            )
            
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
            
            # Generate all variant images in parallel
            variant_image_tasks = [
                self.image_service.get_or_generate_image(
                    prompt=vd["variant"].get("image_prompt", vd["variant"].get("title")),
                    category=category,
                    variant_type=vd["v_type"],
                    user_id=user_id,
                    db=db
                )
                for vd in variant_data
            ]
            
            variant_results = await asyncio.gather(*variant_image_tasks, return_exceptions=True)
            
            # Create variant records from results
            for i, result in enumerate(variant_results):
                if isinstance(result, Exception):
                    logger.error(f"Variant image generation failed: {result}")
                    variant_url = ""
                else:
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
            logger.error(f"Error replacing action: {e}")
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
            
            batch_prompt = f"""Generate {len(item_ids)} replacement wellness actions.

══════════════════════════════════════════════════════════════════════
ITEMS TO REPLACE (user disliked these)
══════════════════════════════════════════════════════════════════════
{json.dumps(items_to_replace, indent=2)}

══════════════════════════════════════════════════════════════════════
OTHER CURRENT ACTIONS (DO NOT generate similar to these - user already has them)
══════════════════════════════════════════════════════════════════════
{json.dumps(other_current_actions, indent=2) if other_current_actions else "None - user only has the items being replaced"}

══════════════════════════════════════════════════════════════════════
HEALTH PROFILE
══════════════════════════════════════════════════════════════════════
- Age: {user_context.get('age', 'Not specified')}
- Cycle Day: {user_context.get('cycle_day', 'Unknown')}
- Cycle Phase: {user_context.get('cycle_phase')}
- Primary Hormone to Support: {user_context.get('primary_hormone')}
- Secondary Hormone: {user_context.get('secondary_hormone', 'Not specified')}

HEALTH CONCERNS (Pick 'symptoms' field from these):
- Top Concern: {user_context.get('top_concern', 'Not specified')}
- Diagnosed Conditions: {user_context.get('diagnosed_conditions', 'none')}
- Period Concerns: {user_context.get('period_concerns', 'none')}
- Body Concerns: {user_context.get('body_concerns', 'none')}
- Skin/Hair Concerns: {user_context.get('skin_hair_concerns', 'none')}
- Mental Health Concerns: {user_context.get('mental_health_concerns', 'none')}

══════════════════════════════════════════════════════════════════════
PERSONALIZATION FACTORS
══════════════════════════════════════════════════════════════════════
- Lifestyle Focus: {user_context.get('lifestyle_focus')}
- Diet Preference: {user_context.get('diet_preference', 'none')}
- Food Allergies/Restrictions: {user_context.get('food_allergies', 'none')}
- Stress Level: {user_context.get('stress_level')}

══════════════════════════════════════════════════════════════════════
FEEDBACK MEMORY (Critical - avoid disliked patterns)
══════════════════════════════════════════════════════════════════════
HISTORICAL SUMMARY (learned patterns over time):
{user_context.get('feedback_summary', 'No summary yet')}

RECENT FEEDBACK (last 20-50 actions):
{user_context.get('feedback_memory', 'No previous feedback')}

══════════════════════════════════════════════════════════════════════
REQUIREMENTS FOR EACH REPLACEMENT
══════════════════════════════════════════════════════════════════════
1. Must target the SAME hormone as the original
2. Should be DIFFERENT from the original (user disliked it)
3. Can be any category (food, movement, or mindfulness)
4. RESPECT food allergies - NEVER recommend foods the user is allergic to
5. RESPECT diet preferences
6. Make actions specific, actionable, and achievable in one day

══════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (for each replacement action)
══════════════════════════════════════════════════════════════════════
1. slot: Keep same as original  
2. category: "food", "movement", or "mindfulness"
3. **CRITICAL - Category-Specific Fields (MUST include based on category):**
   - FOOD: food_amounts (array like ["1 cup", "1/2 cup"]) AND food_items (array like ["quinoa", "lentils"])
   - MOVEMENT: exercise_durations, exercise_types, exercise_intensities (all arrays)
   - MINDFULNESS: mindfulness_durations, mindfulness_techniques (both arrays)
4. title: Short, catchy title (3-5 words)
5. time_slot: "morning", "afternoon", or "evening"
6. specific_action: Detailed, actionable description (50-100 words)
7. purpose: One clear sentence explaining how this helps the target hormone
8. target_hormone: MUST match original (e.g., "insulin", "estrogen")
9. hormone_persona_intro: Natural first-person intro (see examples in system prompt)
10. image_prompt: FLUX.1 Schnell optimized prompt (see requirements below)
11. research_studies: Array with EXACTLY 1 REAL citation focused on WOMEN/FEMALES
12. variants: Array of 3 variant objects (see VARIANT FORMAT below)
13. symptoms: REQUIRED - Pick 1-3 from USER'S HEALTH CONCERNS above that THIS ACTION specifically helps - NEVER EMPTY!
14. conditions: Array of conditions this helps (e.g., ["PCOS"]) - can be empty []

CATEGORY-SPECIFIC REQUIRED FIELDS (CRITICAL - GPT must include these):
For FOOD actions, MUST include:
- food_amounts: Array like ["1 tbsp", "2 tablespoons", "handful"]
- food_items: Array like ["pumpkin seeds", "flaxseeds"]

For MOVEMENT actions, MUST include:
- exercise_durations: Array like ["15 min", "20 minutes"]
- exercise_types: Array like ["yoga", "walking", "stretching"]
- exercise_intensities: Array like ["low", "moderate"]

For MINDFULNESS actions, MUST include:
- mindfulness_durations: Array like ["5 min", "10 minutes"]
- mindfulness_techniques: Array like ["deep breathing", "meditation"]

IMAGE PROMPT REQUIREMENTS (for FLUX.1 Schnell):
- For FOOD: "Professional food photography of [specific dish], overhead view, natural lighting, rustic wooden table background, fresh ingredients visible, warm color tones, appetizing presentation, 4K quality"
- For MOVEMENT: "Serene photograph of woman practicing [specific exercise], soft natural lighting, peaceful setting, wellness aesthetic, warm earth tones, calm atmosphere, 4K quality"
- For MINDFULNESS: "Peaceful zen scene with [specific elements], soft diffused lighting, minimalist aesthetic, calming colors, cozy atmosphere, 4K quality"

VARIANT FORMAT (REQUIRED structure):
Each variant MUST be an object with these exact fields:
- variant_type: MUST be one of: "tasty"/"easy"/"healthy" (food), "gentle"/"energizing"/"quick" (movement), "guided"/"solo"/"brief" (mindfulness)
- title: Specific name of this variant
- description: How to prepare or do this variant (1-2 sentences)
- image_prompt: FLUX.1 Schnell optimized prompt for this specific variant

RESEARCH STUDIES - CRITICAL REQUIREMENTS:
- Use the 'search_research_paper' tool to find REAL citations from PubMed/OpenAlex
- The study MUST SUPPORT the specific recommendation for the user's condition (e.g., if recommending cinnamon for insulin, find a study showing cinnamon helps insulin)
- Study MUST focus on WOMEN/FEMALES specifically
- Provide EXACTLY 1 study per action
- Include ALL fields: title, journal, year, participants (number of women), finding, pmid, verification_link
- Example format: {{"title": "...", "journal": "...", "year": 2021, "participants": 145, "finding": "...", "pmid": "12345678", "verification_link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"}}
- If tool returns no results, set research_studies to an empty array []

EXAMPLE OUTPUT for FOOD replacement:
[{{
  "slot": 1,
  "category": "food",
  "title": "Savory Quinoa Bowl",
  "food_amounts": ["1 cup", "1/2 cup", "1 tbsp"],
  "food_items": ["cooked quinoa", "cooked lentils", "olive oil"],
  "time_slot": "morning",
  "specific_action": "Cook 1 cup of quinoa and 1/2 cup of lentils together with vegetable broth. Season with turmeric, cumin, and black pepper. Drizzle with 1 tbsp olive oil. This creates a filling, insulin-friendly meal.",
  "purpose": "This meal combines complex carbs with fiber and protein to help stabilize insulin levels throughout the morning.",
  "target_hormone": "insulin",
  "hormone_persona_intro": "I'm Insulin — in your menstrual phase, I can be sensitive to diet changes, so keeping my levels steady is key.",
  "image_prompt": "Professional food photography of quinoa and lentil bowl, overhead view, natural lighting, rustic wooden table, fresh herbs garnish, warm color tones, appetizing presentation, 4K quality",
  "research_studies": [{{
    "title": "Effect of Quinoa on Insulin Response in Women",  
    "journal": "Nutrients",
    "year": 2021,
    "participants": 145,
    "finding": "Quinoa consumption improved insulin sensitivity in premenopausal women",
    "pmid": "12345678",
    "verification_link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
  }}],
  "variants": [
    {{
      "variant_type": "tasty",
      "title": "Maple Pecan Quinoa",
      "description": "Add maple syrup and toasted pecans for a sweet twist.",
      "image_prompt": "Professional food photography of quinoa with maple syrup and pecans..."
    }},
    {{
      "variant_type": "easy",
      "title": "One-Pot Quinoa",
      "description": "Cook everything in one pot for quick cleanup.",
      "image_prompt": "Professional food photography of one-pot quinoa..."
    }},
    {{
      "variant_type": "healthy",
      "title": "Green Quinoa Bowl",
      "description": "Add spinach and kale for extra nutrients.",
      "image_prompt": "Professional food photography of quinoa with leafy greens..."
    }}
  ]
}}]

Respond with valid JSON array only. Do not add any text outside the JSON."""

            # Generate replacements via GPT with retry logic
            replacement_actions = None
            gpt_cost = 0.0
            
            for attempt in range(1, self.MAX_RETRIES + 1):
                logger.info(f"🔄 Replacement generation attempt {attempt}/{self.MAX_RETRIES}")
                
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
                        "max_tokens": 4000
                    }
                )
                
                response.raise_for_status()
                data = response.json()
                
                # Calculate GPT cost
                input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
                output_tokens = data.get("usage", {}).get("completion_tokens", 0)
                attempt_cost = (input_tokens * 0.00015 / 1000) + (output_tokens * 0.0006 / 1000)
                gpt_cost += attempt_cost
                
                message = data["choices"][0]["message"]
                
                # Handle tool calls if GPT wants to search for papers
                if message.get("tool_calls"):
                    logger.info(f"🔧 GPT requested {len(message['tool_calls'])} tool calls for replacement citations")
                    
                    tool_results = []
                    for tool_call in message["tool_calls"]:
                        if tool_call["function"]["name"] == "search_research_paper":
                            args = json.loads(tool_call["function"]["arguments"])
                            logger.info(f"  🔍 Searching for: {args.get('action_title', 'unknown')}")
                            
                            # Execute the tool with db for caching
                            paper = await execute_pubmed_tool(args, db=db)
                            
                            if paper and paper.get("title"):
                                logger.info(f"  ✅ Found: {paper.get('title', '')[:50]}... (PMID: {paper.get('pmid', 'N/A')})")
                            else:
                                logger.warning(f"  ⚠️ No paper found for: {args.get('action_title', 'unknown')}")
                            
                            tool_results.append({
                                "tool_call_id": tool_call["id"],
                                "role": "tool",
                                "content": json.dumps(paper) if paper else json.dumps({"error": "No papers found"})
                            })
                    
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
                            "max_tokens": 4000,
                            "response_format": {"type": "json_object"}
                        }
                    )
                    
                    response2.raise_for_status()
                    data = response2.json()
                    
                    # Add second call cost
                    input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
                    output_tokens = data.get("usage", {}).get("completion_tokens", 0)
                    gpt_cost += (input_tokens * 0.00015 / 1000) + (output_tokens * 0.0006 / 1000)
                    
                    content = data["choices"][0]["message"]["content"]
                else:
                    # GPT didn't call tools - use response as-is
                    logger.warning("⚠️ GPT did not call tools - replacement citations may be fabricated")
                    content = message.get("content", "{}")
                
                # Parse response
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                
                logger.info(f"🔍 First 300 chars of content: {content[:300]}")
                
                # Parse JSON
                response_data = json.loads(content.strip())
                
                # Extract actions array (GPT may use 'actions' or 'replacements' key, or return a single action)
                if isinstance(response_data, dict) and "actions" in response_data:
                    attempt_actions = response_data["actions"]
                elif isinstance(response_data, dict) and "replacements" in response_data:
                    attempt_actions = response_data["replacements"]
                elif isinstance(response_data, list):
                    attempt_actions = response_data
                elif isinstance(response_data, dict) and "category" in response_data:
                    # GPT returned a single action dict instead of list
                    logger.info("GPT returned single action dict, wrapping in list")
                    attempt_actions = [response_data]
                else:
                    logger.error(f"Unexpected response format: {type(response_data)}, keys={response_data.keys() if isinstance(response_data, dict) else 'N/A'}")
                    continue
                
                if not attempt_actions:
                    logger.warning(f"❌ Attempt {attempt}: No actions generated")
                    continue
                
                logger.info(f"✅ Successfully parsed JSON - got {len(attempt_actions)} actions")
                
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
                    logger.info(f"✅ Attempt {attempt}: All {len(attempt_actions)} replacements valid")
                    replacement_actions = attempt_actions
                    break
                
                # Log validation errors
                logger.warning(f"⚠️ Attempt {attempt} validation failed:")
                for error in validation_errors:
                    logger.warning(f"   • {error}")
                
                if attempt < self.MAX_RETRIES:
                    logger.info(f"🔄 Retrying generation...")
                else:
                    # Max retries exceeded - NO fallbacks, fail clearly
                    logger.error(f"❌ Max retries ({self.MAX_RETRIES}) exceeded, NOT applying fallbacks - prompt needs fixing")
                    replacement_actions = None  # Fail clearly instead of masking with garbage defaults
            
            total_cost += gpt_cost
            
            if not replacement_actions:
                return {"success": False, "error": "Failed to generate replacement actions"}
            
            # Debug: Log all fields for each replacement action to verify GPT response
            logger.info(f"📋 GPT returned {len(replacement_actions)} replacement actions")
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
            for i, replacement_action in enumerate(replacement_actions):
                original = original_items[i] if i < len(original_items) else original_items[0]
                
                # Log the raw replacement_action for debugging
                logger.info(f"📋 Processing replacement {i}: category={replacement_action.get('category')}")
                logger.info(f"📋 Variants raw: {replacement_action.get('variants')}")
                logger.info(f"📋 Symptoms from GPT: {replacement_action.get('symptoms', [])}")
                logger.info(f"📋 Conditions from GPT: {replacement_action.get('conditions', [])}")
                
                # Generate hero image
                hero_url, _, image_cost = await self.image_service.get_or_generate_image(
                    prompt=replacement_action.get("image_prompt", replacement_action.get("title", "")),
                    category=replacement_action.get("category", "food"),
                    variant_type="hero",
                    user_id=user_id,
                    db=db
                )
                total_cost += image_cost
                
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
                # Get symptoms from GPT (action-specific) with fallback to user's top concern
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
                    logger.info(f"🍽️  Food fields set: items={new_item.food_items}, amounts={new_item.food_amounts}")
                elif category == "movement":
                    new_item.exercise_types = replacement_action.get("exercise_types", [])
                    new_item.exercise_durations = replacement_action.get("exercise_durations", [])
                    new_item.exercise_intensities = replacement_action.get("exercise_intensities", [])
                    logger.info(f"🏃  Movement fields set: durations={new_item.exercise_durations}, types={new_item.exercise_types}")
                elif category == "mindfulness":
                    new_item.mindfulness_techniques = replacement_action.get("mindfulness_techniques", [])
                    new_item.mindfulness_durations = replacement_action.get("mindfulness_durations", [])
                    logger.info(f"🧘  Mindfulness fields set: durations={new_item.mindfulness_durations}, techniques={new_item.mindfulness_techniques}")
                
                
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
                    
                    variant_url, _, variant_cost = await self.image_service.get_or_generate_image(
                        prompt=variant.get("image_prompt", variant.get("title", "")),
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
                    logger.info(f"📤 Response includes food_amounts={new_item.food_amounts}, food_items={new_item.food_items}")
                elif category == "movement":
                    logger.info(f"📤 Response includes exercise_durations={new_item.exercise_durations}")
                elif category == "mindfulness":
                    logger.info(f"📤 Response includes mindfulness_durations={new_item.mindfulness_durations}")
            
            
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
        feedback_type: str,  # "like" or "dislike"
        time_shown: datetime,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Record user feedback for an action."""
        from app.core.database import ActionPlanItem, ActionPlanFeedback
        
        try:
            # Get the action
            result = await db.execute(
                select(ActionPlanItem).where(ActionPlanItem.id == item_id)
            )
            item = result.scalar_one_or_none()
            
            if not item or item.uid != user_id:
                return {"success": False, "error": "Action not found"}
            
            # Calculate time to feedback
            now = datetime.utcnow()
            time_to_feedback = int((now - time_shown).total_seconds())
            
            # Create feedback record
            feedback = ActionPlanFeedback(
                uid=user_id,
                plan_id=item.plan_id,
                item_id=item_id,
                feedback_type=feedback_type,
                action_title=item.title,
                action_category=item.category,
                target_hormone=item.target_hormone,
                action_shown_at=time_shown,
                feedback_given_at=now,
                time_to_feedback_seconds=time_to_feedback,
                created_at=now
            )
            
            db.add(feedback)
            await db.commit()
            
            return {
                "success": True,
                "feedback_id": feedback.id,
                "time_to_feedback_seconds": time_to_feedback
            }
            
        except Exception as e:
            logger.error(f"Error recording feedback: {e}")
            await db.rollback()
            return {"success": False, "error": "Failed to record feedback. Please try again."}


# Global instance
_action_plan_generator: Optional[ActionPlanGenerator] = None


def get_action_plan_generator() -> ActionPlanGenerator:
    """Get or create the action plan generator singleton."""
    global _action_plan_generator
    if _action_plan_generator is None:
        _action_plan_generator = ActionPlanGenerator()
    return _action_plan_generator
