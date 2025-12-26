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
    refresh_status: RefreshStatus  # NEW: Plan refresh tracking
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
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    reward_service = RewardService(db)
    result = reward_service.get_all_rewards_status(uid)
    
    # Add refresh status
    result["refresh_status"] = reward_service.get_refresh_status(uid)
    
    return result


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
    current_user: dict = Depends(verify_firebase_token)
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
