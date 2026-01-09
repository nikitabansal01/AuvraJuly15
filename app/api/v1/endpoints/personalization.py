"""
Personalization API endpoints for the 2026 Vision.

Provides endpoints for:
- Profile summary (traits, profile density)
- Discovery prompts (gaps to explore)
- Unlock status for gated features
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from collections.abc import Mapping

from app.core.database import get_db, UserProfile
from app.api.v1.endpoints.auth import get_current_user
from app.services.chat.intelligence.memory_engine import MemoryEngine, IDEAL_PROFILE_FIELDS
from app.services.streak_service import StreakService, REWARDS_CONFIG
from app.services.reward_service import RewardService
from app.api.v1.endpoints.preferences import PREFERENCE_REWARD_MAP

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class TraitChip(BaseModel):
    """A learned trait about the user."""
    id: str
    label: str
    icon: str
    source: str  # "inferred" or "explicit"
    confidence: Optional[str] = None


class DiscoveryPrompt(BaseModel):
    """A curiosity prompt for exploring gaps."""
    id: str
    title: str
    question: str
    icon: str
    priority: str  # "high", "medium", "low"


class UnlockStatus(BaseModel):
    """Status for a gated feature."""
    feature: str
    accessible: bool
    type: str  # "streak_preference" or "pro_feature"
    current_streak: Optional[int] = None
    required_days: Optional[int] = None
    days_remaining: Optional[int] = None


class ProfileSummaryResponse(BaseModel):
    """Full profile summary for the PersonalizeScreen."""
    known_traits: List[TraitChip]
    profile_density: float  # 0-100
    discovery_prompts: List[DiscoveryPrompt]
    unlock_statuses: List[UnlockStatus]
    current_streak: int
    is_pro: bool


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_trait_icon(field: str) -> str:
    """Get emoji icon for a profile field."""
    icons = {
        "fitness_habits": "💪",
        "stress_landscape": "😰",
        "circadian_rhythm": "🌙",
        "sleep_profile": "😴",
        "long_term_goals": "🎯",
        "advice_style": "💬",
        "life_archetype": "👤",
        "diet_preference": "🥗",
        "food_allergies": "🥜",
        "cuisine_preference": "🍜",
        "cultural_background": "🌍",
        "body_metrics": "📊",
        "cravings": "🍫"
    }
    return icons.get(field, "✨")


def _get_discovery_question(field: str, field_info: dict) -> str:
    """Generate a natural curiosity question for a gap."""
    questions = {
        "fitness_habits": "How does movement feel for you during different phases of your cycle?",
        "stress_landscape": "What tends to trigger stress for you most?",
        "circadian_rhythm": "Are you more of a morning person or night owl?",
        "sleep_profile": "How has your sleep been lately?",
        "long_term_goals": "What's your biggest hope from tracking your cycle?",
        "advice_style": "Do you prefer plain data, warm encouragement, or straight-to-the-point advice?",
        "life_archetype": "Tell me about a typical day for you."
    }
    return questions.get(field, f"Tell me about your {field.replace('_', ' ')}")


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/profile-summary", response_model=ProfileSummaryResponse)
async def get_profile_summary(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get comprehensive profile summary for the PersonalizeScreen.
    
    Returns:
    - known_traits: Visual chips showing what AUVRA knows
    - profile_density: 0-100% score
    - discovery_prompts: Gaps to explore via chat
    - unlock_statuses: What's locked/unlocked
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Get user profile
    profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    raw_memory = profile.chatbot_memory if profile else None
    # `chatbot_memory` is nullable; it can be NULL/None for existing users.
    # Normalize to a real dict so we never call `.get()` on None.
    memory: Dict[str, Any] = dict(raw_memory) if isinstance(raw_memory, Mapping) else {}
    is_pro = profile.is_pro if profile and hasattr(profile, 'is_pro') else False
    
    # Get streak info
    streak_service = StreakService(db)
    streak_data = streak_service.get_full_streak_status(uid)
    current_streak = streak_data.get("current_streak", 0)
    
    # Get reward service for unlock checks
    reward_service = RewardService(db)
    
    # Build known traits
    known_traits: List[TraitChip] = []
    inferred = memory.get("inferred_profile") or {}
    
    # Add inferred traits
    for field, data in inferred.items():
        if isinstance(data, dict) and data.get("value"):
            known_traits.append(TraitChip(
                id=field,
                label=str(data.get("value", ""))[:30],
                icon=_get_trait_icon(field),
                source="inferred",
                confidence=data.get("confidence")
            ))
    
    # Add explicit preferences
    explicit_fields = ["diet_preference", "food_allergies", "cuisine_preference", 
                       "cultural_background", "body_metrics", "cravings"]
    for field in explicit_fields:
        if memory.get(field):
            value = memory.get(field)
            label = value if isinstance(value, str) else str(value)[:30]
            known_traits.append(TraitChip(
                id=field,
                label=label,
                icon=_get_trait_icon(field),
                source="explicit"
            ))
    
    # Calculate profile density
    total_fields = len(IDEAL_PROFILE_FIELDS) + len(explicit_fields)
    filled_fields = len(known_traits)
    profile_density = round((filled_fields / total_fields) * 100, 1) if total_fields > 0 else 0
    
    # Build discovery prompts for gaps
    discovery_prompts: List[DiscoveryPrompt] = []
    for field, info in IDEAL_PROFILE_FIELDS.items():
        if field not in [t.id for t in known_traits]:
            discovery_prompts.append(DiscoveryPrompt(
                id=field,
                title=info.get("name", field.replace("_", " ").title()),
                question=_get_discovery_question(field, info),
                icon=_get_trait_icon(field),
                priority="high" if len(discovery_prompts) < 2 else "medium"
            ))
    
    # Limit to top 5 prompts
    discovery_prompts = discovery_prompts[:5]
    
    # Build unlock statuses
    unlock_statuses: List[UnlockStatus] = []
    for pref, reward_id in PREFERENCE_REWARD_MAP.items():
        reward_config = next((r for r in REWARDS_CONFIG if r["id"] == reward_id), None)
        required_days = reward_config["days"] if reward_config else 0
        is_unlocked = reward_service.is_reward_unlocked(uid, reward_id)
        
        unlock_statuses.append(UnlockStatus(
            feature=pref,
            accessible=is_unlocked,
            type="streak_preference",
            current_streak=current_streak,
            required_days=required_days,
            days_remaining=max(0, required_days - current_streak) if not is_unlocked else 0
        ))
    
    return ProfileSummaryResponse(
        known_traits=known_traits,
        profile_density=profile_density,
        discovery_prompts=discovery_prompts,
        unlock_statuses=unlock_statuses,
        current_streak=current_streak,
        is_pro=is_pro
    )


@router.get("/discovery-prompts", response_model=List[DiscoveryPrompt])
async def get_discovery_prompts(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get discovery prompts for profile gaps.
    
    Returns list of curiosity questions AUVRA wants to explore.
    Each prompt can be used to start a focused chat conversation.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Get profile gaps using MemoryEngine
    memory_engine = MemoryEngine(db)
    
    try:
        import asyncio
        full_memory = asyncio.get_event_loop().run_until_complete(
            memory_engine.load_full_memory(uid, None)
        )
    except:
        full_memory = {}
    
    profile_gaps = full_memory.get("profile_gaps", {})
    missing_fields = profile_gaps.get("missing_fields", {})
    
    prompts: List[DiscoveryPrompt] = []
    for field, info in missing_fields.items():
        prompts.append(DiscoveryPrompt(
            id=field,
            title=info.get("name", field.replace("_", " ").title()),
            question=_get_discovery_question(field, info),
            icon=_get_trait_icon(field),
            priority="high" if field == profile_gaps.get("priority_gap") else "medium"
        ))
    
    return prompts[:5]  # Limit to 5
