"""
User Preferences API - Gated by Streak Rewards

Each preference type requires a specific reward to be claimed first.
Preferences are stored in UserProfile.chatbot_memory and used by:
- Action Plan Generator (for personalized recommendations)
- Chatbot (for context-aware conversations)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.core.database import get_db, UserProfile
from app.api.v1.endpoints.auth import get_current_user
from app.services.reward_service import RewardService

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# PREFERENCE TYPE → REWARD GATING MAP
# ═══════════════════════════════════════════════════════════════════════════════

PREFERENCE_REWARD_MAP = {
    # Preference type -> Required reward ID
    "diet_preference": "diet_prefs",           # 7 days
    "food_allergies": "food_allergies",        # 8 days
    "cuisine_preference": "cuisine_prefs",     # 12 days
    "dine_out_frequency": "dine_out",          # 14 days
    "cultural_background": "ethnicity",        # 18 days
    "body_metrics": "bmi_ratio",               # 18 days
    "cravings": "cravings_healthy",            # 18 days
}

# Preference options for each type
PREFERENCE_OPTIONS = {
    "diet_preference": [
        {"id": "none", "label": "No specific diet", "icon": "🍽️"},
        {"id": "vegetarian", "label": "Vegetarian", "icon": "🥬"},
        {"id": "vegan", "label": "Vegan", "icon": "🌱"},
        {"id": "pescatarian", "label": "Pescatarian", "icon": "🐟"},
        {"id": "keto", "label": "Keto", "icon": "🥑"},
        {"id": "paleo", "label": "Paleo", "icon": "🥩"},
        {"id": "mediterranean", "label": "Mediterranean", "icon": "🫒"},
        {"id": "gluten_free", "label": "Gluten-Free", "icon": "🌾"},
        {"id": "dairy_free", "label": "Dairy-Free", "icon": "🥛"},
        {"id": "halal", "label": "Halal", "icon": "☪️"},
        {"id": "kosher", "label": "Kosher", "icon": "✡️"},
    ],
    "food_allergies": [
        {"id": "nuts", "label": "Nuts", "icon": "🥜"},
        {"id": "peanuts", "label": "Peanuts", "icon": "🥜"},
        {"id": "dairy", "label": "Dairy/Lactose", "icon": "🥛"},
        {"id": "gluten", "label": "Gluten/Wheat", "icon": "🌾"},
        {"id": "shellfish", "label": "Shellfish", "icon": "🦐"},
        {"id": "fish", "label": "Fish", "icon": "🐟"},
        {"id": "eggs", "label": "Eggs", "icon": "🥚"},
        {"id": "soy", "label": "Soy", "icon": "🫘"},
        {"id": "sesame", "label": "Sesame", "icon": "🫘"},
    ],
    "cuisine_preference": [
        {"id": "asian", "label": "Asian", "icon": "🍜"},
        {"id": "indian", "label": "Indian", "icon": "🍛"},
        {"id": "mediterranean", "label": "Mediterranean", "icon": "🫒"},
        {"id": "mexican", "label": "Mexican/Latin", "icon": "🌮"},
        {"id": "italian", "label": "Italian", "icon": "🍝"},
        {"id": "american", "label": "American", "icon": "🍔"},
        {"id": "middle_eastern", "label": "Middle Eastern", "icon": "🧆"},
        {"id": "african", "label": "African", "icon": "🍲"},
        {"id": "european", "label": "European", "icon": "🥐"},
    ],
    "dine_out_frequency": [
        {"id": "rarely", "label": "Rarely (1-2x/month)", "icon": "🏠"},
        {"id": "sometimes", "label": "Sometimes (1x/week)", "icon": "🍽️"},
        {"id": "often", "label": "Often (2-3x/week)", "icon": "🍴"},
        {"id": "very_often", "label": "Very Often (4+/week)", "icon": "🏪"},
    ],
    "cultural_background": [
        {"id": "south_asian", "label": "South Asian", "icon": "🍛"},  # Curry
        {"id": "east_asian", "label": "East Asian", "icon": "🍜"},  # Noodles
        {"id": "southeast_asian", "label": "Southeast Asian", "icon": "🍲"},  # Stew/Pho
        {"id": "middle_eastern", "label": "Middle Eastern", "icon": "🧆"},  # Falafel
        {"id": "african", "label": "African", "icon": "🥘"},  # Traditional dish
        {"id": "european", "label": "European", "icon": "🥐"},  # Croissant
        {"id": "latin_american", "label": "Latin American", "icon": "🌮"},  # Taco
        {"id": "north_american", "label": "North American", "icon": "🍔"},  # Burger
        {"id": "caribbean", "label": "Caribbean", "icon": "🥭"},  # Mango
        {"id": "pacific_islander", "label": "Pacific Islander", "icon": "🥥"},  # Coconut
        {"id": "mixed", "label": "Mixed Heritage", "icon": "🌎"},  # Globe
        {"id": "other", "label": "Other", "icon": "🍽️"},  # Plate
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SetPreferenceRequest(BaseModel):
    preference_type: str = Field(..., description="Type of preference: diet_preference, food_allergies, etc.")
    value: Any = Field(..., description="Preference value - string for single select, list for multi-select")


class BodyMetricsRequest(BaseModel):
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    waist_cm: Optional[float] = None


class CravingsRequest(BaseModel):
    cravings: List[str] = Field(..., description="List of common cravings")


class PreferenceResponse(BaseModel):
    success: bool
    preference_type: str
    value: Any
    message: Optional[str] = None


class AllPreferencesResponse(BaseModel):
    unlocked_preferences: List[str]
    preferences: Dict[str, Any]
    preference_options: Dict[str, List[Dict[str, str]]]


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("")
async def get_all_preferences(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all user preferences and which are unlocked.
    
    Returns:
    - unlocked_preferences: List of preference types user can set
    - preferences: Current preference values
    - preference_options: Available options for each type
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    reward_service = RewardService(db)
    unlocked_ids = reward_service.get_unlocked_reward_ids(uid)
    
    # Map unlocked rewards to preference types
    unlocked_preferences = []
    for pref_type, reward_id in PREFERENCE_REWARD_MAP.items():
        if reward_id in unlocked_ids:
            unlocked_preferences.append(pref_type)
    
    # Get current preferences from chatbot_memory
    profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    chatbot_memory = profile.chatbot_memory or {} if profile else {}
    
    # Extract current preference values
    preferences = {}
    for pref_type in PREFERENCE_REWARD_MAP.keys():
        preferences[pref_type] = chatbot_memory.get(pref_type)
    
    # Also include body_metrics and cravings
    preferences["body_metrics"] = chatbot_memory.get("body_metrics")
    preferences["cravings"] = chatbot_memory.get("cravings")
    
    return {
        "unlocked_preferences": unlocked_preferences,
        "preferences": preferences,
        "preference_options": PREFERENCE_OPTIONS
    }


@router.post("", response_model=PreferenceResponse)
async def set_preference(
    request: SetPreferenceRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Set a user preference. Requires the corresponding reward to be claimed.
    
    For multi-select preferences (allergies, cuisine, cravings), pass a list.
    For single-select (diet, dine_out, cultural), pass a string.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    pref_type = request.preference_type
    
    # Check if preference type requires a reward
    if pref_type in PREFERENCE_REWARD_MAP:
        required_reward = PREFERENCE_REWARD_MAP[pref_type]
        reward_service = RewardService(db)
        
        if not reward_service.is_reward_unlocked(uid, required_reward):
            raise HTTPException(
                status_code=403, 
                detail=f"This feature requires the '{required_reward}' reward to be claimed first"
            )
    
    # Get or create user profile
    profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    if not profile:
        profile = UserProfile(uid=uid, chatbot_memory={})
        db.add(profile)
    
    # Update chatbot_memory
    memory = profile.chatbot_memory or {}
    memory[pref_type] = request.value
    memory[f"{pref_type}_updated_at"] = datetime.utcnow().isoformat()
    profile.chatbot_memory = memory
    
    # Force SQLAlchemy to detect the JSONB change
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(profile, "chatbot_memory")
    
    db.commit()
    
    return PreferenceResponse(
        success=True,
        preference_type=pref_type,
        value=request.value,
        message=f"Preference '{pref_type}' updated successfully"
    )


@router.post("/body-metrics", response_model=PreferenceResponse)
async def set_body_metrics(
    request: BodyMetricsRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Set body metrics (height, weight, waist). Requires BMI/waist ratio reward.
    
    All fields are optional - user can update any combination.
    BMI is calculated automatically if height and weight are provided.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Check reward is unlocked
    reward_service = RewardService(db)
    if not reward_service.is_reward_unlocked(uid, "bmi_ratio"):
        raise HTTPException(
            status_code=403,
            detail="This feature requires the 'bmi_ratio' reward to be claimed first"
        )
    
    # Get profile
    profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    if not profile:
        profile = UserProfile(uid=uid, chatbot_memory={})
        db.add(profile)
    
    memory = profile.chatbot_memory or {}
    body_metrics = memory.get("body_metrics", {})
    
    # Update metrics
    if request.height_cm:
        body_metrics["height_cm"] = request.height_cm
    if request.weight_kg:
        body_metrics["weight_kg"] = request.weight_kg
    if request.waist_cm:
        body_metrics["waist_cm"] = request.waist_cm
    
    # Calculate BMI if we have height and weight
    if body_metrics.get("height_cm") and body_metrics.get("weight_kg"):
        height_m = body_metrics["height_cm"] / 100
        body_metrics["bmi"] = round(body_metrics["weight_kg"] / (height_m ** 2), 1)
        
        # BMI category
        bmi = body_metrics["bmi"]
        if bmi < 18.5:
            body_metrics["bmi_category"] = "underweight"
        elif bmi < 25:
            body_metrics["bmi_category"] = "normal"
        elif bmi < 30:
            body_metrics["bmi_category"] = "overweight"
        else:
            body_metrics["bmi_category"] = "obese"
    
    # Calculate waist-to-height ratio if we have both
    if body_metrics.get("waist_cm") and body_metrics.get("height_cm"):
        body_metrics["waist_height_ratio"] = round(
            body_metrics["waist_cm"] / body_metrics["height_cm"], 2
        )
    
    memory["body_metrics"] = body_metrics
    memory["body_metrics_updated_at"] = datetime.utcnow().isoformat()
    profile.chatbot_memory = memory
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(profile, "chatbot_memory")
    db.commit()
    
    return PreferenceResponse(
        success=True,
        preference_type="body_metrics",
        value=body_metrics,
        message="Body metrics updated"
    )


@router.get("/options/{preference_type}")
async def get_preference_options(preference_type: str):
    """
    Get available options for a preference type.
    
    Public endpoint - no auth required.
    """
    if preference_type not in PREFERENCE_OPTIONS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown preference type: {preference_type}"
        )
    
    return {
        "preference_type": preference_type,
        "options": PREFERENCE_OPTIONS[preference_type]
    }
