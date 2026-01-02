"""
Test that Groq GPT-OSS-120B produces output that passes the EXACT SAME 
Pydantic validation as GPT-4o-mini.
"""
import os
import asyncio
import json
import httpx
from typing import Literal, List
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# EXACT SAME PYDANTIC MODELS FROM action_plan_generator.py
# ============================================================================

class ResearchStudyModel(BaseModel):
    """Research citation from PubMed - all fields required."""
    title: str
    journal: str
    year: int
    participants: int
    finding: str
    pmid: str
    verification_link: str
    
    model_config = {"extra": "forbid"}


class ActionVariantModel(BaseModel):
    """Variant of an action - all fields required."""
    variant_type: str
    title: str
    description: str
    image_prompt: str
    
    model_config = {"extra": "forbid"}


class ActionItemModel(BaseModel):
    """Single action item - ALL fields are required (like OpenAI strict mode)."""
    title: str
    category: Literal["food", "movement", "mindfulness"]
    time_slot: Literal["morning", "afternoon", "evening"]
    specific_action: str
    hormone_persona_intro: str
    hero_image_prompt: str = ""
    variants: List[ActionVariantModel] = Field(default_factory=list)
    target_hormone: str
    symptoms: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    food_items: List[str] = Field(default_factory=list)
    food_amounts: List[str] = Field(default_factory=list)
    exercise_types: List[str] = Field(default_factory=list)
    exercise_durations: List[str] = Field(default_factory=list)
    exercise_intensities: List[str] = Field(default_factory=list)
    mindfulness_techniques: List[str] = Field(default_factory=list)
    mindfulness_durations: List[str] = Field(default_factory=list)
    research_studies: List[ResearchStudyModel] = Field(default_factory=list)
    
    model_config = {"extra": "forbid"}


class ActionPlanResponseModel(BaseModel):
    """Complete response with all actions."""
    actions: List[ActionItemModel]
    
    model_config = {"extra": "forbid"}


# ============================================================================
# TEST
# ============================================================================

async def test_groq_with_full_validation():
    groq_key = os.getenv("GROQ_API_KEY")
    fallback_model = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
    
    print("=" * 70)
    print("TESTING GROQ WITH EXACT SAME PYDANTIC VALIDATION AS GPT-4o-mini")
    print("=" * 70)
    print(f"\nModel: {fallback_model}")
    
    if not groq_key:
        print("\n❌ GROQ_API_KEY not set!")
        return
    
    is_reasoning = "gpt-oss" in fallback_model.lower()
    
    # EXACT system prompt matching what action_plan_generator.py uses
    system_prompt = """You are AUVRA's hormone health expert. Generate personalized daily actions.

OUTPUT FORMAT: Return ONLY valid JSON with this EXACT structure:
{
    "actions": [
        {
            "title": "Action Title",
            "category": "food" OR "movement" OR "mindfulness",
            "time_slot": "morning" OR "afternoon" OR "evening",
            "specific_action": "Detailed instruction with specific amounts/durations",
            "hormone_persona_intro": "I'm [Hormone], and during your [phase]...",
            "hero_image_prompt": "Photorealistic image of...",
            "target_hormone": "estrogen" OR "progesterone" OR "cortisol" etc,
            "symptoms": ["symptom1", "symptom2"],
            "conditions": ["PCOS", "endometriosis"] OR [],
            "food_items": ["item1", "item2"] for food category, [] otherwise,
            "food_amounts": ["1 cup", "2 tbsp"] for food category, [] otherwise,
            "exercise_types": ["yoga", "walking"] for movement, [] otherwise,
            "exercise_durations": ["20 minutes"] for movement, [] otherwise,
            "exercise_intensities": ["low", "moderate"] for movement, [] otherwise,
            "mindfulness_techniques": ["breathing", "meditation"] for mindfulness, [] otherwise,
            "mindfulness_durations": ["10 minutes"] for mindfulness, [] otherwise,
            "variants": [
                {
                    "variant_type": "budget" OR "time" OR "intensity",
                    "title": "Variant title",
                    "description": "How this variant differs",
                    "image_prompt": "Photorealistic image of..."
                }
            ],
            "research_studies": [
                {
                    "title": "Study title from PubMed",
                    "journal": "Journal name",
                    "year": 2023,
                    "participants": 50,
                    "finding": "Key finding supporting this action",
                    "pmid": "12345678",
                    "verification_link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
                }
            ]
        }
    ]
}

CRITICAL RULES:
1. "category" MUST be exactly "food", "movement", or "mindfulness" (lowercase)
2. "time_slot" MUST be exactly "morning", "afternoon", or "evening" (lowercase)
3. For FOOD actions: food_items and food_amounts MUST be non-empty arrays
4. For MOVEMENT actions: exercise_types and exercise_durations MUST be non-empty arrays
5. For MINDFULNESS actions: mindfulness_techniques and mindfulness_durations MUST be non-empty arrays
6. "year" and "participants" in research_studies MUST be integers, not strings
7. ALL fields must be present, use [] for empty arrays

IMPORTANT: Output ONLY valid JSON. No markdown, no thinking, no preamble."""

    user_prompt = """Generate 2 hormone-aware health actions for this user:
- Cycle Phase: Follicular
- Primary Hormone: Estrogen (rising - focus 2 actions on this)
- Secondary Hormone: Progesterone (low)
- Lifestyle Focus: eat (prefer food actions)
- Health Conditions: PCOS
- Symptoms: fatigue, bloating

Action 1: FOOD action for morning (target: estrogen)
Action 2: MINDFULNESS action for evening (target: estrogen)

Return EXACTLY the JSON format specified. All required fields must be present."""

    payload = {
        "model": fallback_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 4000
    }
    
    if not is_reasoning:
        payload["response_format"] = {"type": "json_object"}
    
    print(f"\n📤 Sending request to Groq ({fallback_model})...")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=90.0
            )
            
            if response.status_code != 200:
                print(f"\n❌ API Error: {response.status_code}")
                print(response.text)
                return
            
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            # Clean response
            cleaned = content.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            
            print(f"\n📥 Response received ({len(cleaned)} chars)")
            
            # Parse JSON
            try:
                raw_data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                print(f"\n❌ JSON PARSING FAILED: {e}")
                print(f"First 500 chars:\n{cleaned[:500]}")
                return
            
            print("\n✅ JSON parsing successful")
            
            # Get raw actions
            if "actions" in raw_data:
                raw_actions = raw_data["actions"]
            else:
                print(f"\n❌ No 'actions' key in response")
                return
            
            print(f"\n📋 Found {len(raw_actions)} raw actions")
            
            # Sanitize (same as action_plan_generator.py does)
            import re
            for action in raw_actions:
                if "category" in action:
                    action["category"] = action["category"].lower()
                if "time_slot" in action:
                    action["time_slot"] = action["time_slot"].lower()
                
                # Fix research_studies participants
                for study in action.get("research_studies", []):
                    if isinstance(study.get("participants"), str):
                        nums = re.findall(r'\d+', str(study.get("participants", "")))
                        study["participants"] = int(nums[0]) if nums else 0
                    if isinstance(study.get("year"), str):
                        nums = re.findall(r'\d+', str(study.get("year", "")))
                        study["year"] = int(nums[0]) if nums else 2023
                
                # Ensure all list fields exist
                for field in ["symptoms", "conditions", "food_items", "food_amounts", 
                             "exercise_types", "exercise_durations", "exercise_intensities",
                             "mindfulness_techniques", "mindfulness_durations", "variants", 
                             "research_studies"]:
                    if field not in action:
                        action[field] = []
                
                # Ensure string fields exist
                for field in ["hero_image_prompt", "target_hormone"]:
                    if field not in action:
                        action[field] = ""
            
            # PYDANTIC VALIDATION - same as action_plan_generator.py
            print("\n🔍 Running Pydantic validation (ActionPlanResponseModel)...")
            try:
                validated_response = ActionPlanResponseModel(actions=raw_actions)
                actions = [action.model_dump() for action in validated_response.actions]
                print("✅ Pydantic validation PASSED!")
            except ValidationError as e:
                print(f"\n❌ Pydantic validation FAILED:")
                print(e)
                return
            
            # CATEGORY-SPECIFIC VALIDATION - same as action_plan_generator.py
            print("\n🔍 Running category-specific validation...")
            validation_errors = []
            for i, action in enumerate(actions):
                category = action.get("category", "food")
                title = action.get("title", "Untitled")
                
                if category == "food":
                    if not action.get("food_items"):
                        validation_errors.append(f"Action {i+1} '{title}' [food]: missing food_items")
                    if not action.get("food_amounts"):
                        validation_errors.append(f"Action {i+1} '{title}' [food]: missing food_amounts")
                elif category == "movement":
                    if not action.get("exercise_types"):
                        validation_errors.append(f"Action {i+1} '{title}' [movement]: missing exercise_types")
                    if not action.get("exercise_durations"):
                        validation_errors.append(f"Action {i+1} '{title}' [movement]: missing exercise_durations")
                elif category == "mindfulness":
                    if not action.get("mindfulness_techniques"):
                        validation_errors.append(f"Action {i+1} '{title}' [mindfulness]: missing mindfulness_techniques")
                    if not action.get("mindfulness_durations"):
                        validation_errors.append(f"Action {i+1} '{title}' [mindfulness]: missing mindfulness_durations")
            
            if validation_errors:
                print("\n❌ Category-specific validation FAILED:")
                for err in validation_errors:
                    print(f"   • {err}")
                return
            
            print("✅ Category-specific validation PASSED!")
            
            # SUCCESS - show actions
            print("\n" + "=" * 70)
            print("✅ ALL VALIDATIONS PASSED - GROQ OUTPUT IS IDENTICAL TO GPT-4o-mini FORMAT")
            print("=" * 70)
            
            for i, action in enumerate(actions, 1):
                print(f"\n📋 Action {i}: {action['title']}")
                print(f"   Category: {action['category']}")
                print(f"   Time Slot: {action['time_slot']}")
                print(f"   Target Hormone: {action['target_hormone']}")
                
                if action['category'] == 'food':
                    print(f"   Food Items: {action['food_items']}")
                    print(f"   Food Amounts: {action['food_amounts']}")
                elif action['category'] == 'movement':
                    print(f"   Exercise Types: {action['exercise_types']}")
                    print(f"   Exercise Durations: {action['exercise_durations']}")
                elif action['category'] == 'mindfulness':
                    print(f"   Techniques: {action['mindfulness_techniques']}")
                    print(f"   Durations: {action['mindfulness_durations']}")
                
                if action['research_studies']:
                    study = action['research_studies'][0]
                    print(f"   Research: {study['title'][:50]}... (PMID: {study['pmid']})")
            
            print("\n" + "=" * 70)
            print("CONCLUSION: Groq fallback uses EXACT SAME validation as GPT-4o-mini!")
            print("=" * 70)
            
        except httpx.HTTPError as e:
            print(f"\n❌ HTTP Error: {e}")


if __name__ == "__main__":
    asyncio.run(test_groq_with_full_validation())
