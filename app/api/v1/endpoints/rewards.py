"""
Rewards API endpoints.

Provides endpoints for fetching reward status and claiming rewards.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional

from app.core.database import get_db
from app.api.v1.endpoints.auth import get_current_user
from app.services.reward_service import RewardService
from app.services.streak_service import REWARDS_CONFIG

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class RewardResponse(BaseModel):
    id: str
    title: str
    required_streak: int
    category: str
    icon: str
    state: str  # "locked", "available", "claimed"
    days_remaining: int


class RefreshStatus(BaseModel):
    limit: int
    used: int
    remaining: int
    can_refresh: bool


class RewardsStatusResponse(BaseModel):
    current_streak: int
    longest_streak: int
    freeze_count: int
    last_activity_date: Optional[str]
    refresh_status: RefreshStatus  # Plan refresh tracking
    # Streak risk status (for freeze prompts)
    streak_at_risk: Optional[bool] = False
    missed_days_count: Optional[int] = 0
    missed_dates: Optional[List[str]] = []
    can_freeze: Optional[bool] = False
    freezes_needed: Optional[int] = 0
    today_completed: Optional[bool] = False
    today_frozen: Optional[bool] = False
    # Rewards list
    rewards: List[RewardResponse]


class ClaimRequest(BaseModel):
    reward_id: str


class ClaimResponse(BaseModel):
    success: bool
    reward_id: Optional[str] = None
    title: Optional[str] = None
    icon: Optional[str] = None
    error: Optional[str] = None
    effect: Optional[str] = None
    effect_result: Optional[str] = None


class FreezeResponse(BaseModel):
    """Response for freeze usage endpoints."""
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    freeze_count: int
    days_frozen: Optional[int] = None
    frozen_dates: Optional[List[str]] = None


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=RewardsStatusResponse)
async def get_rewards_status(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all rewards with current streak status.
    
    Returns the user's current streak, longest streak, available freeze tokens,
    plan refresh status, and all rewards with their current state.
    """
    from app.core.database import UserProfile
    
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Get user's timezone for accurate streak calculations
    profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    user_timezone = profile.current_timezone if profile else None
    
    reward_service = RewardService(db)
    result = reward_service.get_all_rewards_status(uid, user_timezone)
    
    # Add refresh status
    result["refresh_status"] = reward_service.get_refresh_status(uid)
    
    return result


@router.post("/use-freeze", response_model=FreezeResponse)
async def use_freeze_proactive(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Use a freeze token proactively for TODAY.
    
    Call this when user knows they won't complete actions today.
    Freezes today so no actions are needed to maintain streak.
    """
    from app.services.streak_service import StreakService
    from app.core.database import UserProfile
    
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Get user's timezone from profile for accurate date calculations
    profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    user_timezone = profile.current_timezone if profile else None
    
    streak_service = StreakService(db)
    result = streak_service.use_freeze_proactive(uid, user_timezone)
    
    if not result.get("success"):
        return FreezeResponse(
            success=False,
            error=result.get("error"),
            freeze_count=result.get("freeze_count", 0)
        )
    
    return FreezeResponse(
        success=True,
        message=result.get("message"),
        freeze_count=result.get("freeze_count", 0),
        days_frozen=1,
        frozen_dates=[result.get("frozen_date")] if result.get("frozen_date") else []
    )


@router.post("/use-freeze-reactive", response_model=FreezeResponse)
async def use_freeze_reactive(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Use freeze token(s) reactively to protect streak from missed days.
    
    Call this when user missed 1 or more days and wants to protect their streak.
    Will use X freezes for X missed days (multi-day support).
    """
    from app.services.streak_service import StreakService
    
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Get user's timezone from profile for accurate date calculations
    from app.core.database import UserProfile
    profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    user_timezone = profile.current_timezone if profile else None
    
    streak_service = StreakService(db)
    result = streak_service.use_freeze_reactive(uid, user_timezone)
    
    if not result.get("success"):
        return FreezeResponse(
            success=False,
            error=result.get("error"),
            freeze_count=result.get("freeze_count", 0),
            days_frozen=result.get("days_needed")
        )
    
    return FreezeResponse(
        success=True,
        message=result.get("message"),
        freeze_count=result.get("freeze_count", 0),
        days_frozen=result.get("days_frozen"),
        frozen_dates=result.get("frozen_dates", [])
    )


@router.post("/claim", response_model=ClaimResponse)
async def claim_reward(
    request: ClaimRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Claim a reward if eligible.
    
    Checks if user has met the streak requirement and hasn't already claimed.
    Returns success status and any special effects (like freeze tokens).
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    reward_service = RewardService(db)
    result = reward_service.claim_reward(uid, request.reward_id)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result


@router.get("/config")
async def get_rewards_config():
    """
    Get rewards configuration (public, no auth needed).
    
    Returns the list of all available rewards with their requirements.
    Useful for frontend to display reward information without auth.
    """
    return {
        "rewards": [
            {
                "id": r["id"],
                "title": r["title"],
                "required_streak": r["days"],
                "category": r["category"],
                "icon": r["icon"],
                "effect": r["effect"]
            }
            for r in REWARDS_CONFIG
        ]
    }


@router.get("/claimed")
async def get_claimed_rewards(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get list of rewards claimed by user (for badge display).
    
    Returns array of claimed rewards with id, title, icon, and claimed_at.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    service = RewardService(db)
    return service.get_claimed_rewards(uid)
